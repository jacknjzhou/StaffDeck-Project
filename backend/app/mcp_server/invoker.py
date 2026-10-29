from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_version_ids
from app.db.models import AgentProfile, KnowledgeBaseVersion
from app.knowledge.schema import KnowledgeSearchRequest
from app.knowledge.service import KnowledgeService
from app.mcp_server.catalog import (
    KNOWLEDGE_SEARCH_TOOL,
    build_tool_catalog,
    tool_name_map,
)
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall


def extract_agent_id(params: dict[str, Any], principal_agent_id: str | None) -> str | None:
    """解析一次 ``tools/call`` 应归属的数字员工。

    优先取 MCP ``_meta.agentId``；凭据绑定的员工只作兜底，不额外猜测。
    """
    meta = params.get("_meta")
    if isinstance(meta, dict):
        candidate = meta.get("agentId")
        if isinstance(candidate, str) and candidate:
            return candidate
    return principal_agent_id


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    """把任意负载包装成 MCP 的 text 内容块，非字符串负载序列化为 JSON。"""
    body = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def _narrow_to_requested_bases(
    db: Session, tenant_id: str, allowed: list[str], requested: list[str]
) -> list[str]:
    """只保留调用方点名知识库下、且对员工可见的版本 id。"""
    rows = db.exec(
        select(KnowledgeBaseVersion.id).where(
            KnowledgeBaseVersion.tenant_id == tenant_id,
            KnowledgeBaseVersion.knowledge_base_id.in_(requested),  # type: ignore[attr-defined]
        )
    ).all()
    wanted = set(rows)
    return [version_id for version_id in allowed if version_id in wanted]


def _knowledge_search(
    db: Session, tenant_id: str, agent_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """直接检索员工已同步的知识库，不经过 FastAPI 的 JWT 依赖层。

    ``app.api.knowledge.search_knowledge`` 首步即 ``require_agent_scope_viewer``，
    但 MCP 调用方持有的是凭据而非 ``User``；这里改用同一个 branching 助手自行收窄可见范围。
    """
    query = str(arguments.get("query") or "")
    allowed = list(visible_knowledge_base_version_ids(db, tenant_id, agent_id))

    requested = [str(item) for item in (arguments.get("knowledgeBaseIds") or [])]
    if requested:
        allowed = _narrow_to_requested_bases(db, tenant_id, allowed, requested)

    if not allowed:
        return _text_result({"citations": [], "chunks": [], "query": query})

    request = KnowledgeSearchRequest(
        tenant_id=tenant_id,
        agent_id=agent_id,
        query=query,
        knowledge_base_version_ids=allowed,
        max_chunks=max(1, min(int(arguments.get("maxChunks") or 8), 20)),
    )
    result = KnowledgeService(db).search(request, None)
    dumped = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    dumped["citations"] = dumped.get("okf_citations") or dumped.get("evidence_pack") or []
    return _text_result(dumped)


def call_tool(
    db: Session,
    tenant_id: str,
    agent_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """为单个数字员工执行一次 MCP 工具调用。

    返回 MCP ``CallToolResult`` 结构；未知员工、未暴露的工具名等协议级失败以
    ``isError`` 返回而不抛异常，因为工具错误本身也是合法的 JSON-RPC 响应。
    """
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        return _text_result(
            {"code": "AGENT_NOT_FOUND", "message": "unknown digital employee"},
            is_error=True,
        )

    catalog = build_tool_catalog(db, tenant_id, agent_id)
    if not any(item["name"] == name for item in catalog):
        return _text_result(
            {"code": "TOOL_NOT_FOUND", "message": f"{name} is not exposed"}, is_error=True
        )

    if name == KNOWLEDGE_SEARCH_TOOL:
        return _knowledge_search(db, tenant_id, agent_id, arguments)

    # The raw slug cannot always be recovered from the namespaced name
    # (normalisation is lossy), so the catalog map is the source of truth.
    bare = tool_name_map(db, tenant_id, agent_id)[name]
    result = ToolExecutor(db).execute(
        tenant_id=tenant_id,
        tool_call=ToolCall(name=bare, arguments=arguments),
        agent_id=agent_id,
    )
    if not result.success:
        code = result.error.code if result.error else "EXECUTION_ERROR"
        message = result.error.message if result.error else "tool execution failed"
        return _text_result({"code": code, "message": message}, is_error=True)
    return _text_result(result.data)
