from __future__ import annotations

import base64
from time import sleep
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlmodel import Session, select

from app.api import general_skills as internal_general_skills
from app.api import knowledge as internal_knowledge
from app.api import knowledge_bases as internal_knowledge_bases
from app.api import scheduled_tasks as internal_scheduled_tasks
from app.api import tools as internal_tools
from app.db import get_session
from app.db.models import APIJob, AgentResourceBinding, KnowledgeIngestJob, Tool, utc_now
from app.general_skills.schema import GeneralSkillImportRequest, GeneralSkillRunRequest
from app.knowledge.schema import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
    KnowledgeBaseRollbackRequest,
    KnowledgeConceptUpdateRequest,
    KnowledgeDocumentUpdateRequest,
    KnowledgeDocumentUploadRequest,
    KnowledgeSearchRequest,
)
from app.public_api.auth import PublicPrincipal, enforce_agent_access, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.idempotency import replay_idempotent_response, store_idempotent_response
from app.public_api.jobs import create_job, job_read, register_job_handler, update_job
from app.public_api.runs import _job_actor
from app.public_api.schemas import KnowledgeEntriesUpsert, ScheduledTaskPublicCreate
from app.public_api.sessions import ensure_public_agent
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest, ScheduledTaskUpdateRequest
from app.tools.tool_schema import (
    MCPDiscoverRequest,
    MCPServerCreateRequest,
    MCPServerUpdateRequest,
    MCPSyncRequest,
    ToolCreateRequest,
    ToolTestRequest,
    ToolUpdateRequest,
)


router = APIRouter(tags=["resources"])


def _dump(value: Any) -> Any:
    """把 Pydantic 模型转成 JSON 友好的 dict 并剔除 tenant_id，其它值原样返回。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude={"tenant_id"})
    return value


def _masked_tool(value: Any) -> dict[str, Any]:
    """导出工具或 MCP 服务时把 headers、auth 与 connection 凭证替换为掩码。"""
    payload = _dump(value)
    payload["headers"] = {key: "********" for key in (payload.get("headers") or {})}
    payload["auth"] = {key: "********" for key in (payload.get("auth") or {})}
    connection = payload.get("connection")
    if isinstance(connection, dict):
        connection["headers"] = {key: "********" for key in (connection.get("headers") or {})}
        connection["env"] = {key: "********" for key in (connection.get("env") or {})}
    return payload


# Knowledge bases
@router.get("/agents/{agent_id}/knowledge-bases", response_model=dict)
def list_knowledge_bases(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出智能体可见的知识库，公开 API 只做鉴权与包装。"""
    enforce_agent_access(principal, agent_id)
    ensure_public_agent(db, principal, agent_id)
    rows = internal_knowledge_bases.list_knowledge_bases(principal.tenant_id, agent_id, db)
    return {"data": [_dump(row) for row in rows], "next_cursor": None}


@router.post("/agents/{agent_id}/knowledge-bases", response_model=dict, status_code=201)
def create_knowledge_base(
    agent_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """为智能体创建知识库，租户与操作者取自公开 API 主体。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = KnowledgeBaseCreateRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_knowledge_bases.create_knowledge_base(request, agent_id, db, principal.actor_user))


@router.patch("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}", response_model=dict)
def update_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """按 id 更新知识库，调用内部实现并统一序列化返回值。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = KnowledgeBaseUpdateRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_knowledge_bases.update_knowledge_base(knowledge_base_id, request, agent_id, db, principal.actor_user))


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}:archive", response_model=dict)
def archive_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把知识库置为 archived，复用更新端点。"""
    return update_knowledge_base(agent_id, knowledge_base_id, {"status": "archived"}, principal, db)


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}:search", response_model=dict)
def search_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """在指定知识库内检索，并把 okf_citations 归一化为 citations 字段。"""
    enforce_agent_access(principal, agent_id)
    request = KnowledgeSearchRequest(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        knowledge_base_ids=[knowledge_base_id],
        **body,
    )
    result = internal_knowledge.search_knowledge(request, db, principal.actor_user)
    payload = _dump(result)
    payload["citations"] = payload.get("okf_citations") or payload.get("evidence_pack") or []
    return payload


@router.post(
    "/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/entries",
    response_model=dict,
    status_code=202,
)
def upsert_knowledge_entries(
    agent_id: str,
    knowledge_base_id: str,
    body: KnowledgeEntriesUpsert,
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """批量写入知识条目，支持 Idempotency-Key 重放，异步执行并返回 202 任务信息。"""
    enforce_agent_access(principal, agent_id, write=True)
    replay = replay_idempotent_response(db, principal, request, body.model_dump(mode="json"))
    if replay:
        response.status_code = replay[0]
        return replay[1]
    job = create_job(
        db,
        principal,
        kind="knowledge.ingest",
        request_payload={
            "agent_id": agent_id,
            "knowledge_base_id": knowledge_base_id,
            "entries": body.model_dump(mode="json")["entries"],
        },
        agent_id=agent_id,
    )
    payload = job_read(job).model_dump(mode="json")
    store_idempotent_response(db, principal, request, body.model_dump(mode="json"), payload, status_code=202, resource_id=job.id)
    return payload


@router.post(
    "/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/documents",
    response_model=dict,
    status_code=202,
)
async def upload_knowledge_document(
    agent_id: str,
    knowledge_base_id: str,
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """上传知识文档（上限 20 MB），转 base64 后创建 knowledge.ingest 任务并返回 202。"""
    enforce_agent_access(principal, agent_id, write=True)
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise PublicAPIError(413, "DOCUMENT_TOO_LARGE", "Documents are limited to 20 MB.")
    job = create_job(
        db,
        principal,
        kind="knowledge.ingest",
        request_payload={
            "agent_id": agent_id,
            "knowledge_base_id": knowledge_base_id,
            "documents": [
                {
                    "filename": file.filename or "document.bin",
                    "title": title,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "metadata": {"content_type": file.content_type},
                }
            ],
            "idempotency_key": request.headers.get("Idempotency-Key"),
        },
        agent_id=agent_id,
    )
    return job_read(job).model_dump(mode="json")


@router.get("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/versions", response_model=dict)
def list_knowledge_versions(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出知识库历史版本，供回滚时选择。"""
    rows = internal_knowledge_bases.list_knowledge_base_versions(
        knowledge_base_id, principal.tenant_id, agent_id, db
    )
    return {"data": rows}


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}:rollback", response_model=dict)
def rollback_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:publish")),
    db: Session = Depends(get_session),
) -> dict:
    """把知识库回滚到指定版本，需要 knowledge:publish 作用域。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = KnowledgeBaseRollbackRequest(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        version=str(body.get("version") or ""),
    )
    return internal_knowledge_bases.rollback_knowledge_base(
        knowledge_base_id, request, db, principal.actor_user
    )


@router.get("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/documents", response_model=dict)
def list_knowledge_documents(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出知识库下未删除的文档，当前不分页。"""
    rows = internal_knowledge.list_documents(
        principal.tenant_id, knowledge_base_id, agent_id, False, db
    )
    return {"data": [_dump(row) for row in rows], "next_cursor": None}


@router.patch("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}", response_model=dict)
def update_knowledge_document(
    agent_id: str,
    knowledge_base_id: str,
    document_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """更新文档元数据或状态，归属与权限由内部实现校验。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = KnowledgeDocumentUpdateRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_knowledge.update_document(document_id, request, db, principal.actor_user))


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}:archive", response_model=dict)
def archive_knowledge_document(
    agent_id: str,
    knowledge_base_id: str,
    document_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把文档置为 archived，复用更新端点。"""
    return update_knowledge_document(
        agent_id, knowledge_base_id, document_id, {"status": "archived"}, principal, db
    )


@router.get("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/concepts", response_model=dict)
def list_knowledge_concepts(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出知识库的 OKF 概念。"""
    rows = internal_knowledge_bases.list_okf_concepts(
        knowledge_base_id, principal.tenant_id, agent_id, None, db
    )
    return {"data": [_dump(row) for row in rows]}


@router.get("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}", response_model=dict)
def get_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """读取知识库详情，不存在或不可见时由内部实现抛错。"""
    enforce_agent_access(principal, agent_id)
    return _dump(
        internal_knowledge_bases.get_knowledge_base(
            knowledge_base_id, principal.tenant_id, agent_id, db
        )
    )


@router.get(
    "/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/concepts/{concept_id:path}",
    response_model=dict,
)
def get_knowledge_concept(
    agent_id: str,
    knowledge_base_id: str,
    concept_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """按概念 id（可含路径）读取单个 OKF 概念。"""
    enforce_agent_access(principal, agent_id)
    return _dump(
        internal_knowledge_bases.get_okf_concept(
            knowledge_base_id, concept_id, principal.tenant_id, agent_id, db
        )
    )


@router.put(
    "/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/concepts/{concept_id:path}",
    response_model=dict,
)
def upsert_knowledge_concept(
    agent_id: str,
    knowledge_base_id: str,
    concept_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """新建或覆盖指定 id 的 OKF 概念。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = KnowledgeConceptUpdateRequest(tenant_id=principal.tenant_id, **body)
    return _dump(
        internal_knowledge_bases.upsert_okf_concept(
            knowledge_base_id, concept_id, request, agent_id, db, principal.actor_user
        )
    )


@router.get("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/okf:export")
def export_knowledge_okf(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> Response:
    """导出知识库的 OKF 包，直接返回文件响应。"""
    enforce_agent_access(principal, agent_id)
    return internal_knowledge_bases.export_okf(
        knowledge_base_id, principal.tenant_id, agent_id, db
    )


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}/okf:lint", response_model=dict)
def lint_knowledge_okf(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:read")),
    db: Session = Depends(get_session),
) -> dict:
    """对知识库做 OKF 一致性检查并返回问题列表。"""
    enforce_agent_access(principal, agent_id)
    return internal_knowledge_bases.lint_okf(
        knowledge_base_id, principal.tenant_id, agent_id, db
    )


@router.post("/agents/{agent_id}/knowledge-bases/{knowledge_base_id}:delete", response_model=dict)
def delete_knowledge_base(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """删除知识库，是否级联清理由内部实现决定。"""
    enforce_agent_access(principal, agent_id, write=True)
    return internal_knowledge_bases.delete_knowledge_base(
        knowledge_base_id, principal.tenant_id, agent_id, db, principal.actor_user
    )


@router.post(
    "/agents/{agent_id}/knowledge-bases/{knowledge_base_id}:sync-from-overall",
    response_model=dict,
)
def sync_knowledge_base_from_overall(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("knowledge:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把企业整体知识同步到该智能体的知识库。"""
    enforce_agent_access(principal, agent_id, write=True)
    return internal_knowledge_bases.sync_knowledge_base_from_overall(
        knowledge_base_id, principal.tenant_id, agent_id, db, principal.actor_user
    )


@register_job_handler("knowledge.ingest")
def execute_knowledge_ingest(db: Session, job: APIJob) -> dict[str, Any]:
    """knowledge.ingest 任务处理器：逐条上传条目或文档，并等待内部摄取任务完成。

    每条都上报进度；内部任务失败时抛错，让外层任务标记为失败。
    """
    _credential, actor = _job_actor(db, job)
    payload = dict(job.request_json or {})
    entries = list(payload.get("entries") or [])
    documents = list(payload.get("documents") or [])
    work_items = [
        {
            **entry,
            "filename": f"{entry.get('external_id') or job.id}-{index}.md",
            "content_base64": base64.b64encode(str(entry.get("content") or "").encode("utf-8")).decode("ascii"),
        }
        for index, entry in enumerate(entries)
    ] + documents
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(work_items):
        update_job(
            db,
            job,
            stage="ingesting",
            progress=index / max(1, len(work_items)),
            event_type="knowledge.ingest.entry.started",
            event_data={"index": index, "external_id": entry.get("external_id")},
        )
        upload = KnowledgeDocumentUploadRequest(
            tenant_id=job.tenant_id,
            knowledge_base_id=str(payload["knowledge_base_id"]),
            filename=str(entry.get("filename") or f"{job.id}-{index}.bin"),
            title=str(entry.get("title") or "Knowledge entry"),
            content_base64=str(entry.get("content_base64") or ""),
            metadata={**dict(entry.get("metadata") or {}), "source_ref": entry.get("source_ref"), "external_id": entry.get("external_id")},
        )
        inner = internal_knowledge.upload_document(upload, str(job.agent_id), db, actor)
        while True:
            db.expire_all()
            current = db.get(KnowledgeIngestJob, inner.id)
            if not current:
                raise RuntimeError("Knowledge ingest job disappeared")
            if current.status in {"succeeded", "failed", "cancelled"}:
                break
            sleep(0.1)
        if current.status != "succeeded":
            raise RuntimeError(f"Knowledge ingest failed at {current.stage}: {current.error or current.status}")
        results.append(
            {
                "external_id": entry.get("external_id"),
                "job_id": current.id,
                "document_id": current.document_id,
                "status": current.status,
                "stage": current.stage,
                "retryable": current.status == "failed",
            }
        )
    return {"knowledge_base_id": payload["knowledge_base_id"], "documents": results}


# General skills
@router.get("/agents/{agent_id}/general-skills", response_model=dict)
def list_general_skills(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("skills:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出智能体可用的通用技能。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_general_skills.list_general_skills(principal.tenant_id, db, agent_id)
    return {"data": [_dump(row) for row in rows], "next_cursor": None}


@router.post("/agents/{agent_id}/general-skills", response_model=dict, status_code=201)
def create_general_skill(
    agent_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("skills:write")),
    db: Session = Depends(get_session),
) -> dict:
    """导入通用技能到智能体，重名等校验由内部实现负责。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = GeneralSkillImportRequest(tenant_id=principal.tenant_id, agent_id=agent_id, **body)
    return _dump(internal_general_skills.import_general_skill(request, db, principal.actor_user))


@router.post("/agents/{agent_id}/general-skills/{slug}:publish", response_model=dict)
def publish_general_skill(
    agent_id: str,
    slug: str,
    principal: PublicPrincipal = Depends(require_scopes("skills:write")),
    db: Session = Depends(get_session),
) -> dict:
    """发布指定 slug 的通用技能。"""
    enforce_agent_access(principal, agent_id, write=True)
    return _dump(internal_general_skills.publish_general_skill(slug, principal.tenant_id, db, agent_id, principal.actor_user))


@router.post("/agents/{agent_id}/general-skills/{slug}:archive", response_model=dict)
def archive_general_skill(
    agent_id: str,
    slug: str,
    principal: PublicPrincipal = Depends(require_scopes("skills:write")),
    db: Session = Depends(get_session),
) -> dict:
    """归档指定 slug 的通用技能。"""
    enforce_agent_access(principal, agent_id, write=True)
    return _dump(
        internal_general_skills.archive_general_skill(
            slug, principal.tenant_id, db, agent_id, principal.actor_user
        )
    )


@router.post("/agents/{agent_id}/general-skills/{slug}:test", response_model=dict)
def test_general_skill(
    agent_id: str,
    slug: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("skills:test")),
    db: Session = Depends(get_session),
) -> dict:
    """以当前主体身份试运行通用技能。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = GeneralSkillRunRequest(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        user_id=principal.actor_user.id,
        **body,
    )
    return _dump(internal_general_skills.run_general_skill(slug, request, db, principal.actor_user))


# HTTP and MCP tools. Read responses deliberately mask all stored credentials.
@router.get("/agents/{agent_id}/tools", response_model=dict)
def list_tools(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("tools:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出智能体绑定的 HTTP/MCP 工具，返回前统一脱敏凭证。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_tools.list_tools(principal.tenant_id, None, agent_id, db)
    return {"data": [_masked_tool(row) for row in rows], "next_cursor": None}


@router.post("/agents/{agent_id}/tools", response_model=dict, status_code=201)
def create_tool(
    agent_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """创建工具并绑定到智能体，响应中的凭证已脱敏。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = ToolCreateRequest(tenant_id=principal.tenant_id, **body)
    return _masked_tool(internal_tools.create_tool(request, agent_id, db, principal.actor_user))


@router.put("/agents/{agent_id}/tools/{tool_id}", response_model=dict)
def update_tool(
    agent_id: str,
    tool_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """按 id 更新工具配置，响应中的凭证已脱敏。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = ToolUpdateRequest(tenant_id=principal.tenant_id, **body)
    return _masked_tool(internal_tools.update_tool(tool_id, request, agent_id, db, principal.actor_user))


@router.post("/agents/{agent_id}/tools/{tool_id}:test", response_model=dict)
def test_tool(
    agent_id: str,
    tool_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:test")),
    db: Session = Depends(get_session),
) -> dict:
    """试调用工具并返回执行结果，不持久化。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = ToolTestRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_tools.test_tool(tool_id, request, agent_id, db, principal.actor_user))


@router.post("/agents/{agent_id}/tools/{tool_id}:archive", response_model=dict)
def archive_tool(
    agent_id: str,
    tool_id: str,
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """归档工具：仅停用该智能体的绑定，不删除工具本身。"""
    enforce_agent_access(principal, agent_id, write=True)
    tool = db.get(Tool, tool_id)
    if not tool or tool.tenant_id != principal.tenant_id:
        raise PublicAPIError(404, "TOOL_NOT_FOUND", "Tool not found.")
    binding = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == principal.tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == "tool",
            AgentResourceBinding.resource_id == tool_id,
        )
    ).first()
    if not binding:
        raise PublicAPIError(404, "TOOL_NOT_FOUND", "Tool not found.")
    binding.status = "inactive"
    binding.updated_at = utc_now()
    db.add(binding)
    db.commit()
    return {"id": tool_id, "status": "archived"}


@router.get("/agents/{agent_id}/mcp-servers", response_model=dict)
def list_mcp_servers(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("tools:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出 MCP 服务及其配置，凭证统一脱敏。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_tools.list_mcp_servers(principal.tenant_id, db)
    return {"data": [_masked_tool(row) for row in rows]}


@router.post("/agents/{agent_id}/mcp-servers", response_model=dict, status_code=201)
def create_mcp_server(
    agent_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """创建 MCP 服务，响应中的凭证已脱敏。"""
    request = MCPServerCreateRequest(tenant_id=principal.tenant_id, **body)
    enforce_agent_access(principal, agent_id, write=True)
    return _masked_tool(internal_tools.create_mcp_server(request, db, principal.actor_user))


@router.put("/agents/{agent_id}/mcp-servers/{server_id}", response_model=dict)
def update_mcp_server(
    agent_id: str,
    server_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """按 id 更新 MCP 服务，响应中的凭证已脱敏。"""
    request = MCPServerUpdateRequest(tenant_id=principal.tenant_id, **body)
    enforce_agent_access(principal, agent_id, write=True)
    return _masked_tool(internal_tools.update_mcp_server(server_id, request, db, principal.actor_user))


@router.post("/agents/{agent_id}/mcp-servers/{server_id}:discover", response_model=dict)
def discover_mcp_server(
    agent_id: str,
    server_id: str,
    principal: PublicPrincipal = Depends(require_scopes("tools:test")),
    db: Session = Depends(get_session),
) -> dict:
    """连接 MCP 服务并发现其暴露的工具。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = MCPDiscoverRequest(tenant_id=principal.tenant_id)
    return _dump(internal_tools.discover_mcp_tools(server_id, request, db, principal.actor_user))


@router.post("/agents/{agent_id}/mcp-servers/{server_id}:sync", response_model=dict)
def sync_mcp_server(
    agent_id: str,
    server_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("tools:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把 MCP 服务发现的工具按请求范围同步进智能体。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = MCPSyncRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_tools.sync_mcp_tools(server_id, request, db, agent_id, principal.actor_user))


# Scheduled tasks reuse the exact existing service; that service already invokes AgentLoop/Harness v2.
@router.get("/agents/{agent_id}/scheduled-tasks", response_model=dict)
def list_scheduled_tasks(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出智能体的定时任务。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_scheduled_tasks.list_enterprise_scheduled_tasks(principal.tenant_id, agent_id, None, principal.actor_user, db)
    return {"data": [_dump(row) for row in rows], "next_cursor": None}


@router.post("/agents/{agent_id}/scheduled-tasks", response_model=dict, status_code=201)
def create_scheduled_task(
    agent_id: str,
    body: ScheduledTaskPublicCreate,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:write")),
    db: Session = Depends(get_session),
) -> dict:
    """创建定时任务，执行时复用既有 AgentLoop/Harness v2 服务。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = ScheduledTaskCreateRequest(tenant_id=principal.tenant_id, agent_id=agent_id, **body.model_dump(mode="json"))
    return _dump(internal_scheduled_tasks.create_enterprise_scheduled_task(request, principal.actor_user, db))


@router.patch("/agents/{agent_id}/scheduled-tasks/{task_id}", response_model=dict)
def update_scheduled_task(
    agent_id: str,
    task_id: str,
    body: dict[str, Any],
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:write")),
    db: Session = Depends(get_session),
) -> dict:
    """按 id 更新定时任务配置。"""
    enforce_agent_access(principal, agent_id, write=True)
    request = ScheduledTaskUpdateRequest(tenant_id=principal.tenant_id, **body)
    return _dump(internal_scheduled_tasks.update_enterprise_scheduled_task(task_id, request, principal.actor_user, db))


@router.post("/agents/{agent_id}/scheduled-tasks/{task_id}:run", response_model=dict)
def run_scheduled_task(
    agent_id: str,
    task_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:run")),
    db: Session = Depends(get_session),
) -> dict:
    """立即触发一次定时任务执行。"""
    enforce_agent_access(principal, agent_id, write=True)
    return _dump(internal_scheduled_tasks.run_enterprise_scheduled_task_now(task_id, principal.tenant_id, principal.actor_user, db))


@router.get("/agents/{agent_id}/scheduled-tasks/{task_id}/runs", response_model=dict)
def list_scheduled_task_runs(
    agent_id: str,
    task_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出定时任务的历史执行记录。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_scheduled_tasks.list_enterprise_scheduled_task_runs(
        task_id, principal.tenant_id, principal.actor_user, db
    )
    return {"data": [_dump(row) for row in rows]}


@router.post("/agents/{agent_id}/scheduled-tasks/{task_id}:archive", response_model=dict)
def archive_scheduled_task(
    agent_id: str,
    task_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:write")),
    db: Session = Depends(get_session),
) -> dict:
    """归档定时任务，不再触发。"""
    enforce_agent_access(principal, agent_id, write=True)
    return internal_scheduled_tasks.archive_enterprise_scheduled_task(
        task_id, principal.tenant_id, principal.actor_user, db
    )


@router.post("/agents/{agent_id}/scheduled-tasks/{task_id}:pause", response_model=dict)
def pause_scheduled_task(
    agent_id: str,
    task_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把定时任务置为 paused，复用更新端点。"""
    return update_scheduled_task(agent_id, task_id, {"status": "paused"}, principal, db)


@router.post("/agents/{agent_id}/scheduled-tasks/{task_id}:resume", response_model=dict)
def resume_scheduled_task(
    agent_id: str,
    task_id: str,
    principal: PublicPrincipal = Depends(require_scopes("scheduled_tasks:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把定时任务恢复为 active，复用更新端点。"""
    return update_scheduled_task(agent_id, task_id, {"status": "active"}, principal, db)
