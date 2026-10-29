from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, text
from sqlmodel import Session, select

from app.agents.branching import (
    ensure_agent_private_knowledge_branch,
    ensure_open_gallery_binding,
    is_open_gallery_resource,
    knowledge_version_for_upload,
    mark_resource_open_gallery,
    mark_resource_private_for_agent,
    metadata_preserving_creator,
    user_creator_metadata,
    visible_knowledge_base_versions,
    visible_knowledge_base_version_ids,
)
from app.async_jobs import enqueue_async_job
from app.db import get_session
from app.db.models import (
    KnowledgeBucket,
    KnowledgeChunk,
    KnowledgeConcept,
    KnowledgeDiscoverySuggestion,
    KnowledgeDocument,
    KnowledgeIngestJob,
    KnowledgeBase,
    KnowledgeBaseVersion,
    ModelConfig,
    User,
    utc_now,
)
from app.llm.model_config_resolver import resolve_model_config_for_runtime
from app.knowledge.schema import (
    KnowledgeBucketRead,
    KnowledgeChunkRead,
    KnowledgeChunkUpdateRequest,
    KnowledgeDiscoveryRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentUpdateRequest,
    KnowledgeDocumentUploadRequest,
    KnowledgeBucketUpdateRequest,
    KnowledgeOkfImportRequest,
    KnowledgeIngestJobRead,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.knowledge.okf import (
    build_okf_for_document,
    create_concept_evidence_rows,
    parse_okf_bundle,
    upsert_concepts,
)
from app.knowledge.service import (
    IngestPayload,
    KnowledgeDiscoveryConflictError,
    KnowledgeDiscoveryValidationError,
    KnowledgeService,
    bucket_read,
    chunk_read,
    validate_discovered_skill,
)
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.permissions import (
    ensure_agent_scope_manager,
    ensure_open_gallery_admin,
    require_agent_scope_viewer,
)
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/knowledge",
    tags=["enterprise:knowledge"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/documents", response_model=KnowledgeIngestJobRead)
def upload_document(
    request: KnowledgeDocumentUploadRequest,
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KnowledgeIngestJobRead:
    """上传文档并创建异步入库任务。

    未指定知识库时按标题或文件名新建（名称在租户内去重），随后解析上传版本并排队
    knowledge_ingest 任务；接口立即返回任务视图，解析进度需轮询 /jobs。
    """
    ensure_tenant(db, request.tenant_id)
    creator_metadata = user_creator_metadata(current_user, request.metadata or {})
    knowledge_base = _resolve_upload_knowledge_base(
        db,
        request,
        agent_id,
        current_user,
        creator_metadata=creator_metadata,
    )
    version = knowledge_version_for_upload(
        db,
        request.tenant_id,
        knowledge_base.id,
        agent_id,
        metadata_json=creator_metadata,
    )
    db.commit()
    service = KnowledgeService(db)
    job = service.create_ingest_job(
        IngestPayload(
            tenant_id=request.tenant_id,
            knowledge_base_id=knowledge_base.id,
            knowledge_base_version_id=version.id,
            filename=request.filename,
            content_base64=request.content_base64,
            title=request.title,
            metadata=creator_metadata,
        )
    )
    enqueue_async_job(
        "knowledge_ingest",
        service.run_ingest_job,
        job.id,
        metadata={"tenant_id": request.tenant_id, "filename": request.filename},
    )
    return job_read(job)


@router.post("/okf/import")
def import_okf_bundle(
    request: KnowledgeOkfImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """把 OKF bundle 导入为一份文档与若干概念页。

    先解码并解析概念 markdown，再建文档、批量 upsert 概念并生成证据行；解码失败或
    包内没有概念文件时返回 400。返回文档 id、版本与概念数量的导入摘要。
    """
    ensure_tenant(db, request.tenant_id)
    try:
        content = base64.b64decode(request.content_base64)
        parsed_docs = parse_okf_bundle(request.filename, content)
    except Exception as exc:  # noqa: BLE001 - surface stable import failures.
        raise HTTPException(status_code=400, detail=f"OKF import failed: {exc}") from exc
    if not parsed_docs:
        raise HTTPException(
            status_code=400, detail="OKF bundle does not contain concept markdown files"
        )

    upload_request = KnowledgeDocumentUploadRequest(
        tenant_id=request.tenant_id,
        knowledge_base_id=request.knowledge_base_id,
        filename=request.filename,
        title=Path(request.filename).stem or "OKF Bundle",
        content_base64="",
        metadata={"okf_import": True, "source_filename": request.filename},
    )
    creator_metadata = user_creator_metadata(current_user, upload_request.metadata or {})
    knowledge_base = _resolve_upload_knowledge_base(
        db,
        upload_request,
        request.agent_id,
        current_user,
        creator_metadata=creator_metadata,
    )
    version = knowledge_version_for_upload(
        db,
        request.tenant_id,
        knowledge_base.id,
        request.agent_id,
        metadata_json=creator_metadata,
    )
    document = KnowledgeDocument(
        tenant_id=request.tenant_id,
        knowledge_base_id=knowledge_base.id,
        knowledge_base_version_id=version.id,
        filename=request.filename,
        file_type="okf",
        title=Path(request.filename).stem or request.filename,
        status="processing",
        metadata_json={
            **creator_metadata,
            "okf_import": True,
            "document_card": {
                "title": Path(request.filename).stem or request.filename,
                "filename": request.filename,
                "file_type": "okf",
                "summary": f"从 OKF bundle 导入 {len(parsed_docs)} 个概念页。",
                "outline": [
                    {
                        "section_id": item.concept_id,
                        "title": item.frontmatter.get("title") or item.concept_id,
                        "path": item.concept_id,
                        "level": 1,
                        "summary": item.frontmatter.get("description") or "",
                    }
                    for item in parsed_docs[:80]
                ],
                "applicable_scenarios": ["OKF Wiki", "业务知识检索"],
                "key_entities": sorted(
                    {str(item.frontmatter.get("type") or "Topic") for item in parsed_docs}
                ),
                "section_count": len(parsed_docs),
            },
            "okf": {"version": "0.1", "concept_count": len(parsed_docs)},
        },
    )
    db.add(document)
    db.flush()
    concept_rows = upsert_concepts(
        db,
        request.tenant_id,
        knowledge_base.id,
        version.id,
        [
            {
                "concept_id": item.concept_id,
                "content_md": item.content_md,
                "document_id": document.id,
                "source_refs": [{"document_id": document.id, "okf_file": f"{item.concept_id}.md"}],
            }
            for item in parsed_docs
        ],
    )
    create_concept_evidence_rows(
        db, request.tenant_id, knowledge_base.id, version.id, document, concept_rows
    )
    return {
        "status": "imported",
        "knowledge_base_id": knowledge_base.id,
        "knowledge_base_version_id": version.id,
        "version": version.version,
        "document_id": document.id,
        "concept_count": len(concept_rows),
    }


def _resolve_upload_knowledge_base(
    db: Session,
    request: KnowledgeDocumentUploadRequest,
    agent_id: str | None,
    current_user: object | None = None,
    creator_metadata: dict[str, Any] | None = None,
) -> KnowledgeBase:
    """解析上传的目标知识库，必要时按上传信息新建并绑定分支。

    指定 knowledge_base_id 时校验租户、归档状态与管理员权限；未指定时创建知识库，
    agent 私有调用会打私有标记并建私有分支，否则登记为 open gallery 资源。
    """
    agent = (
        ensure_agent_scope_manager(db, request.tenant_id, agent_id, current_user)
        if agent_id
        else None
    )
    if request.knowledge_base_id:
        knowledge_base = db.get(KnowledgeBase, request.knowledge_base_id)
        if (
            not knowledge_base
            or knowledge_base.tenant_id != request.tenant_id
            or knowledge_base.status == "archived"
        ):
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if not (agent and not agent.is_overall):
            _ensure_open_gallery_knowledge_admin(
                db, request.tenant_id, knowledge_base.id, current_user
            )
        return knowledge_base

    if not (agent and not agent.is_overall):
        ensure_open_gallery_admin(request.tenant_id, current_user)
    base_name = _knowledge_base_name_from_upload(request)
    name = _unique_knowledge_base_name(db, request.tenant_id, base_name)
    knowledge_base = KnowledgeBase(
        tenant_id=request.tenant_id,
        name=name,
        description=f"由文档 {request.filename} 创建",
        status="active",
        capability_scope=request.capability_scope,
        metadata_json={
            **(creator_metadata or user_creator_metadata(current_user, request.metadata or {})),
            "created_from_document_upload": True,
            "source_filename": request.filename,
        },
    )
    db.add(knowledge_base)
    db.flush()

    if agent and not agent.is_overall:
        mark_resource_private_for_agent(knowledge_base, agent.id, creator_metadata)
        ensure_agent_private_knowledge_branch(
            db,
            request.tenant_id,
            agent.id,
            knowledge_base,
            metadata_json=creator_metadata,
        )
    else:
        mark_resource_open_gallery(knowledge_base, creator_metadata)
        ensure_open_gallery_binding(
            db,
            request.tenant_id,
            "knowledge_base",
            knowledge_base.id,
            "active",
            metadata_json=creator_metadata,
        )
    return knowledge_base


def _knowledge_base_name_from_upload(request: KnowledgeDocumentUploadRequest) -> str:
    """取上传标题或文件名主干作为新知识库名称，都为空时回落到“未命名知识库”。"""
    title = (request.title or "").strip()
    if title:
        return title
    stem = Path(request.filename).stem.strip()
    return stem or request.filename.strip() or "未命名知识库"


def _unique_knowledge_base_name(db: Session, tenant_id: str, base_name: str) -> str:
    """返回租户内尚未占用的知识库名称，重名时依次追加 " 2"、" 3" 等后缀。"""
    normalized_base = base_name.strip() or "未命名知识库"
    existing_names = set(
        db.exec(select(KnowledgeBase.name).where(KnowledgeBase.tenant_id == tenant_id)).all()
    )
    if normalized_base not in existing_names:
        return normalized_base
    index = 2
    while True:
        candidate = f"{normalized_base} {index}"
        if candidate not in existing_names:
            return candidate
        index += 1


@router.get(
    "/jobs",
    response_model=list[KnowledgeIngestJobRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def list_jobs(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_session),
) -> list[KnowledgeIngestJobRead]:
    """列出当前可见知识库版本下的入库任务，按创建时间倒序供前端轮询。

    先收敛长期停留在取消请求中的任务；status 支持逗号分隔多值过滤，limit 上限 50。
    """
    ensure_tenant(db, tenant_id)
    KnowledgeService(db).finalize_stale_cancel_requested_jobs(tenant_id)
    visible_version_ids = visible_knowledge_base_version_ids(
        db, tenant_id, agent_id, include_inactive=True
    )
    if not visible_version_ids:
        return []
    statement = select(KnowledgeIngestJob).where(
        KnowledgeIngestJob.tenant_id == tenant_id,
        KnowledgeIngestJob.knowledge_base_version_id.in_(visible_version_ids),
    )
    if status:
        statuses = [item.strip() for item in status.split(",") if item.strip()]
        if statuses:
            statement = statement.where(KnowledgeIngestJob.status.in_(statuses))
    rows = db.exec(
        statement.order_by(
            KnowledgeIngestJob.created_at.desc(), KnowledgeIngestJob.id.desc()
        ).limit(limit)
    ).all()
    return [job_read(row) for row in rows]


@router.get(
    "/jobs/{job_id}",
    response_model=KnowledgeIngestJobRead,
    dependencies=[Depends(require_agent_scope_viewer)],
)
def get_job(
    job_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
) -> KnowledgeIngestJobRead:
    """读取单个入库任务，校验租户与版本可见性，并顺带收敛陈旧的取消请求。"""
    ensure_tenant(db, tenant_id)
    job = db.get(KnowledgeIngestJob, job_id)
    if not job or job.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge ingest job not found")
    _ensure_knowledge_version_visible(db, tenant_id, job.knowledge_base_version_id, agent_id)
    KnowledgeService(db).finalize_stale_cancel_requested_job(job)
    return job_read(job)


@router.post("/jobs/{job_id}/cancel", response_model=KnowledgeIngestJobRead)
def cancel_job(
    job_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KnowledgeIngestJobRead:
    """取消入库任务，仅该知识库的管理角色可操作；任务不存在时返回 404。"""
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    existing = db.get(KnowledgeIngestJob, job_id)
    if existing and existing.tenant_id == tenant_id:
        _ensure_open_gallery_knowledge_admin(
            db, tenant_id, existing.knowledge_base_id, current_user
        )
    job = KnowledgeService(db).cancel_ingest_job(job_id, tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Knowledge ingest job not found")
    return job_read(job)


@router.get(
    "/documents",
    response_model=list[KnowledgeDocumentRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def list_documents(
    tenant_id: str = Query(...),
    knowledge_base_id: str | None = Query(None),
    agent_id: str | None = Query(None),
    include_all_versions: bool = Query(False),
    db: Session = Depends(get_session),
) -> list[KnowledgeDocumentRead]:
    """列出当前范围可见的知识文档。

    include_all_versions 为真时按知识库过滤（含历史版本），否则只取可见版本下的文档；
    无可见版本或知识库不在范围内时返回空列表。
    """
    ensure_tenant(db, tenant_id)
    visible_versions = visible_knowledge_base_versions(
        db, tenant_id, agent_id, include_inactive=True
    )
    if not visible_versions:
        return []
    if knowledge_base_id and knowledge_base_id not in visible_versions:
        return []
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
    if include_all_versions:
        visible_knowledge_base_ids = (
            [knowledge_base_id] if knowledge_base_id else list(visible_versions)
        )
        stmt = stmt.where(KnowledgeDocument.knowledge_base_id.in_(visible_knowledge_base_ids))
    else:
        visible_version_ids = [
            version.id
            for base_id, version in visible_versions.items()
            if not knowledge_base_id or base_id == knowledge_base_id
        ]
        stmt = stmt.where(KnowledgeDocument.knowledge_base_version_id.in_(visible_version_ids))
    rows = db.exec(stmt.order_by(KnowledgeDocument.created_at.desc())).all()
    return [document_read(row) for row in rows]


@router.get(
    "/documents/{document_id}",
    response_model=KnowledgeDocumentRead,
    dependencies=[Depends(require_agent_scope_viewer)],
)
def get_document(
    document_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
) -> KnowledgeDocumentRead:
    """按 id 读取文档，并校验其知识库版本在当前 agent 或画廊范围内可见。"""
    row = _get_document(db, tenant_id, document_id)
    _ensure_knowledge_version_visible(db, tenant_id, row.knowledge_base_version_id, agent_id)
    return document_read(row)


@router.put("/documents/{document_id}", response_model=KnowledgeDocumentRead)
def update_document(
    document_id: str,
    request: KnowledgeDocumentUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    agent_id: str | None = Query(None),
) -> KnowledgeDocumentRead:
    """更新文档内容、标题、状态或元数据。

    非 overall 的 agent 会先在本分支复制出文档再写入；content_md 交给服务层重建分块，
    传入 expected_updated_at 时做乐观并发校验，版本不一致返回 409。
    """
    source_row = _get_document(db, request.tenant_id, document_id)
    if request.expected_updated_at and source_row.updated_at.isoformat() != request.expected_updated_at:
        raise HTTPException(
            status_code=409,
            detail="文档已被其他人修改，请刷新后再保存。",
        )
    resolved_agent_id = agent_id if isinstance(agent_id, str) and agent_id else None
    agent = (
        ensure_agent_scope_manager(
            db,
            request.tenant_id,
            resolved_agent_id,
            current_user,
        )
        if resolved_agent_id
        else None
    )
    if agent and not agent.is_overall:
        version = knowledge_version_for_upload(
            db,
            request.tenant_id,
            source_row.knowledge_base_id,
            agent.id,
            metadata_json=user_creator_metadata(current_user),
        )
        db.commit()
        row = _document_for_version(db, source_row, version.id)
    else:
        _ensure_open_gallery_knowledge_admin(
            db,
            request.tenant_id,
            source_row.knowledge_base_id,
            current_user,
        )
        row = source_row

    if request.content_md is not None:
        try:
            row = KnowledgeService(db).replace_document_content(
                row,
                request.content_md,
                title=request.title,
                status=request.status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return document_read(row)

    metadata = dict(row.metadata_json or {})
    if request.metadata is not None:
        metadata = metadata_preserving_creator(row.metadata_json, request.metadata)
    if request.title is not None:
        row.title = request.title.strip() or row.filename
        document_card = (
            metadata.get("document_card") if isinstance(metadata.get("document_card"), dict) else {}
        )
        metadata["document_card"] = {**document_card, "title": row.title}
    if request.status is not None:
        row.status = request.status
    if request.metadata is not None or request.title is not None:
        row.metadata_json = metadata
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    _refresh_document_okf_concepts(db, row)
    return document_read(row)


def _document_for_version(
    db: Session,
    source: KnowledgeDocument,
    version_id: str,
) -> KnowledgeDocument:
    """在目标版本中定位源文档的分支副本。

    版本相同直接返回源文档；否则按文件名与类型匹配，优先标题和创建时间都一致的行，
    找不到副本时抛 404。
    """
    if source.knowledge_base_version_id == version_id:
        return source
    rows = db.exec(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.tenant_id == source.tenant_id,
            KnowledgeDocument.knowledge_base_id == source.knowledge_base_id,
            KnowledgeDocument.knowledge_base_version_id == version_id,
            KnowledgeDocument.filename == source.filename,
            KnowledgeDocument.file_type == source.file_type,
        )
        .order_by(KnowledgeDocument.created_at.asc())
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Knowledge document branch copy not found")
    exact = [row for row in rows if row.title == source.title and row.created_at == source.created_at]
    return exact[0] if exact else rows[0]


@router.get(
    "/documents/{document_id}/buckets",
    response_model=list[KnowledgeBucketRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def get_document_buckets(
    document_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
) -> list[KnowledgeBucketRead]:
    """列出文档下各知识桶，并带上分块数量与 ready/incomplete 状态。"""
    document = _get_document(db, tenant_id, document_id)
    _ensure_knowledge_version_visible(db, tenant_id, document.knowledge_base_version_id, agent_id)
    rows = _safe_document_bucket_rows(db, tenant_id, document_id)
    chunk_counts = dict(
        db.exec(
            select(KnowledgeChunk.bucket_id, func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.document_id == document_id)
            .group_by(KnowledgeChunk.bucket_id)
        ).all()
    )
    return [
        _bucket_read_mapping_with_stats(row, int(chunk_counts.get(str(row.get("id")), 0)))
        for row in rows
    ]


@router.put("/buckets/{bucket_id}", response_model=KnowledgeBucketRead)
def update_bucket(
    bucket_id: str,
    request: KnowledgeBucketUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KnowledgeBucketRead:
    """更新知识桶标题、摘要与元数据，并回写文档派生的 OKF 概念。

    返回带分块统计的桶视图；桶不存在或不属于该租户时返回 404。
    """
    ensure_tenant(db, request.tenant_id)
    row = db.get(KnowledgeBucket, bucket_id)
    if not row or row.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge bucket not found")
    _ensure_open_gallery_knowledge_admin(db, request.tenant_id, row.knowledge_base_id, current_user)
    if request.title is not None:
        row.title = request.title.strip() or row.title
    if request.summary is not None:
        row.summary = request.summary
    if request.metadata is not None:
        row.metadata_json = metadata_preserving_creator(
            row.metadata_json,
            request.metadata,
        )
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    document = db.get(KnowledgeDocument, row.document_id)
    if document:
        _refresh_document_okf_concepts(db, document)
    chunk_count = db.exec(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.tenant_id == request.tenant_id,
            KnowledgeChunk.bucket_id == bucket_id,
        )
    ).one()
    return bucket_read_with_stats(row, int(chunk_count or 0))


@router.get(
    "/buckets/{bucket_id}/chunks",
    response_model=list[KnowledgeChunkRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def get_bucket_chunks(
    bucket_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
) -> list[KnowledgeChunkRead]:
    """按 chunk_index 顺序返回知识桶内的分块；桶需属于该租户且版本可见。"""
    ensure_tenant(db, tenant_id)
    bucket = db.get(KnowledgeBucket, bucket_id)
    if not bucket or bucket.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge bucket not found")
    _ensure_knowledge_version_visible(db, tenant_id, bucket.knowledge_base_version_id, agent_id)
    rows = _safe_bucket_chunk_rows(db, tenant_id, bucket_id)
    return [_chunk_read_mapping(row) for row in rows]


@router.put("/chunks/{chunk_id}", response_model=KnowledgeChunkRead)
def update_chunk(
    chunk_id: str,
    request: KnowledgeChunkUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KnowledgeChunkRead:
    """更新分块内容、摘要与元数据，并用分块内容回填所属桶的聚合文本。

    同时刷新文档派生的 OKF 概念；分块或租户不匹配时返回 404。
    """
    ensure_tenant(db, request.tenant_id)
    row = db.get(KnowledgeChunk, chunk_id)
    if not row or row.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge chunk not found")
    _ensure_open_gallery_knowledge_admin(db, request.tenant_id, row.knowledge_base_id, current_user)
    if request.content is not None:
        row.content = request.content
    if request.summary is not None:
        row.summary = request.summary
    if request.metadata is not None:
        row.metadata_json = metadata_preserving_creator(
            row.metadata_json,
            request.metadata,
        )
    row.updated_at = utc_now()
    db.add(row)
    bucket = _sync_bucket_content_from_chunks(db, request.tenant_id, row.bucket_id)
    db.commit()
    db.refresh(row)
    if bucket:
        db.refresh(bucket)
        document = db.get(KnowledgeDocument, bucket.document_id)
        if document:
            _refresh_document_okf_concepts(db, document)
    return chunk_read(row)


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    request: KnowledgeSearchRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> KnowledgeSearchResponse:
    """在可见知识范围内执行检索问答。

    先校验调用者的 agent 视图权限，再把请求的版本 id 收敛到可见集合；无可见知识时
    直接返回带 trace 的空响应，不触达检索模型。
    """
    require_agent_scope_viewer(request.tenant_id, request.agent_id, current_user, db)
    ensure_tenant(db, request.tenant_id)
    model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
    visible_version_ids = visible_knowledge_base_version_ids(
        db,
        request.tenant_id,
        request.agent_id,
    )
    if not visible_version_ids:
        trace = [{"phase": "no_visible_knowledge", "message": "当前范围没有可见知识"}]
        return KnowledgeSearchResponse(trace=trace, route_trace=trace)
    if request.knowledge_base_version_ids:
        allowed_ids = set(visible_version_ids)
        request.knowledge_base_version_ids = [
            version_id
            for version_id in request.knowledge_base_version_ids
            if version_id in allowed_ids
        ]
    else:
        request.knowledge_base_version_ids = visible_version_ids
    return KnowledgeService(db).search(request, model_config)


def _get_default_model(db: Session, tenant_id: str) -> ModelConfig | None:
    """取租户内启用的默认模型并解析为运行时配置，未配置时返回 None。"""
    row = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    return _runtime_model(db, tenant_id, row) if row else None


def _get_request_model(
    db: Session, tenant_id: str, model_config_id: str | None = None
) -> ModelConfig | None:
    """按 id 解析请求指定的模型配置，未指定时回落到默认模型。

    配置不存在、跨租户或已禁用时抛 404。
    """
    if not model_config_id:
        return _get_default_model(db, tenant_id)
    model_config = db.get(ModelConfig, model_config_id)
    if not model_config or model_config.tenant_id != tenant_id or not model_config.enabled:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _runtime_model(db, tenant_id, model_config)


def _runtime_model(db: Session, tenant_id: str, row: ModelConfig):
    """把数据库中的模型配置解析成运行时配置（含密钥与端点覆盖）。"""
    return resolve_model_config_for_runtime(db, tenant_id, row.id)


@router.get(
    "/discoveries",
    response_model=list[KnowledgeDiscoveryRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def list_discoveries(
    tenant_id: str = Query(...),
    knowledge_base_id: str | None = Query(None),
    status: str | None = Query(None),
    agent_id: str | None = Query(None),
    db: Session = Depends(get_session),
) -> list[KnowledgeDiscoveryRead]:
    """列出知识发现建议，只包含当前范围可见版本的数据。

    status 可精确过滤；pending 的 skill 类建议会先校验 payload，不合法的直接跳过，
    避免前端展示无法确认的条目。
    """
    ensure_tenant(db, tenant_id)
    visible_versions = visible_knowledge_base_versions(
        db, tenant_id, agent_id, include_inactive=True
    )
    if knowledge_base_id and knowledge_base_id not in visible_versions:
        return []
    visible_version_ids = [
        version.id
        for base_id, version in visible_versions.items()
        if not knowledge_base_id or base_id == knowledge_base_id
    ]
    if not visible_version_ids:
        return []
    stmt = select(KnowledgeDiscoverySuggestion).where(
        KnowledgeDiscoverySuggestion.tenant_id == tenant_id,
        KnowledgeDiscoverySuggestion.knowledge_base_version_id.in_(visible_version_ids),
    )
    if status:
        stmt = stmt.where(KnowledgeDiscoverySuggestion.status == status)
    rows = db.exec(stmt.order_by(KnowledgeDiscoverySuggestion.created_at.desc())).all()
    visible_rows: list[KnowledgeDiscoverySuggestion] = []
    for row in rows:
        if row.status == "pending" and row.suggestion_type == "skill":
            payload = row.payload_json or {}
            skill_payload = payload.get("draft_skill") if isinstance(payload.get("draft_skill"), dict) else payload
            try:
                validate_discovered_skill(skill_payload)
            except KnowledgeDiscoveryValidationError:
                continue
        visible_rows.append(row)
    return [discovery_read(row) for row in visible_rows]


@router.post("/discoveries/{suggestion_id}/confirm")
def confirm_discovery(
    suggestion_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """确认一条知识发现建议并落库；payload 校验失败返回 422，重复确认等冲突返回 409。"""
    row = _get_discovery(db, tenant_id, suggestion_id)
    _ensure_open_gallery_knowledge_admin(db, tenant_id, row.knowledge_base_id, current_user)
    try:
        result = KnowledgeService(db).confirm_discovery(row)
    except KnowledgeDiscoveryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KnowledgeDiscoveryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "confirmed", "result": result}


@router.post("/discoveries/{suggestion_id}/reject")
def reject_discovery(
    suggestion_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """拒绝一条知识发现建议；建议已被处理时返回 409。"""
    row = _get_discovery(db, tenant_id, suggestion_id)
    _ensure_open_gallery_knowledge_admin(db, tenant_id, row.knowledge_base_id, current_user)
    try:
        KnowledgeService(db).reject_discovery(row)
    except KnowledgeDiscoveryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected"}


def _refresh_document_okf_concepts(db: Session, document: KnowledgeDocument) -> None:
    """按文档当前章节树与知识桶重建其 Source Document/Source Section 概念。

    先删除旧派生概念再整体 upsert；文档既无章节树也无桶时直接返回。
    """
    metadata = document.metadata_json or {}
    section_nodes = (
        metadata.get("section_tree") if isinstance(metadata.get("section_tree"), list) else []
    )
    buckets = db.exec(
        select(KnowledgeBucket)
        .where(
            KnowledgeBucket.tenant_id == document.tenant_id,
            KnowledgeBucket.knowledge_base_id == document.knowledge_base_id,
            KnowledgeBucket.knowledge_base_version_id == document.knowledge_base_version_id,
            KnowledgeBucket.document_id == document.id,
        )
        .order_by(KnowledgeBucket.created_at.asc())
    ).all()
    if not section_nodes and not buckets:
        return
    db.exec(
        delete(KnowledgeConcept).where(
            KnowledgeConcept.tenant_id == document.tenant_id,
            KnowledgeConcept.knowledge_base_id == document.knowledge_base_id,
            KnowledgeConcept.knowledge_base_version_id == document.knowledge_base_version_id,
            KnowledgeConcept.document_id == document.id,
            KnowledgeConcept.concept_type.in_(["Source Document", "Source Section"]),
        )
    )
    db.flush()
    upsert_concepts(
        db,
        document.tenant_id,
        document.knowledge_base_id,
        document.knowledge_base_version_id,
        build_okf_for_document(document, section_nodes, buckets),
    )


def _sync_bucket_content_from_chunks(
    db: Session,
    tenant_id: str,
    bucket_id: str,
) -> KnowledgeBucket | None:
    """用桶内分块内容重算桶的聚合文本、分块数与代表分块 id。

    聚合文本截断到 6000 字符；桶缺失或租户不匹配时返回 None，事务由调用方提交。
    """
    bucket = db.get(KnowledgeBucket, bucket_id)
    if not bucket or bucket.tenant_id != tenant_id:
        return None
    chunks = db.exec(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.bucket_id == bucket_id)
        .order_by(KnowledgeChunk.chunk_index.asc())
    ).all()
    content = "\n\n".join(chunk.content for chunk in chunks if chunk.content.strip()).strip()
    metadata = dict(bucket.metadata_json or {})
    metadata["content"] = content[:6000]
    metadata["chunk_count"] = len(chunks)
    metadata["representative_chunk_ids"] = [chunk.id for chunk in chunks[:3]]
    bucket.metadata_json = metadata
    bucket.token_estimate = max(1, len(content) // 2) if content else bucket.token_estimate
    bucket.updated_at = utc_now()
    db.add(bucket)
    return bucket


def job_read(row: KnowledgeIngestJob) -> KnowledgeIngestJobRead:
    """把入库任务记录转成 API 返回模型，并剔除大字段 content_base64。"""
    return KnowledgeIngestJobRead(
        id=row.id,
        tenant_id=row.tenant_id,
        knowledge_base_id=row.knowledge_base_id,
        document_id=row.document_id,
        filename=row.filename,
        status=row.status,
        stage=row.stage,
        progress=row.progress,
        error=row.error,
        metadata={
            key: value
            for key, value in (row.metadata_json or {}).items()
            if key != "content_base64"
        },
        created_at=row.created_at.isoformat(),
        started_at=row.started_at.isoformat() if row.started_at else None,
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        updated_at=row.updated_at.isoformat(),
    )


def document_read(row: KnowledgeDocument) -> KnowledgeDocumentRead:
    """把文档记录转成 API 返回模型，时间统一序列化为 ISO 字符串。"""
    return KnowledgeDocumentRead(
        id=row.id,
        tenant_id=row.tenant_id,
        knowledge_base_id=row.knowledge_base_id,
        knowledge_base_version_id=row.knowledge_base_version_id,
        filename=row.filename,
        file_type=row.file_type,
        title=row.title,
        status=row.status,
        bucket_count=row.bucket_count,
        chunk_count=row.chunk_count,
        metadata=row.metadata_json or {},
        error=row.error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def bucket_read_with_stats(row: KnowledgeBucket, chunk_count: int) -> KnowledgeBucketRead:
    """用分块数量补充桶视图并推导状态：有分块且摘要非空才算 ready，否则 incomplete。"""
    item = bucket_read(row)
    item.chunk_count = chunk_count
    item.status = "ready" if chunk_count > 0 and row.summary.strip() else "incomplete"
    return item


def _blob_cast(name: str, dialect_name: str) -> str:
    """按方言生成列表达式，SQLite 下把文本列强转为 BLOB 以便容错解码。

    SQLite 可能存入非法编码文本，转成字节后由 _safe_text 依次尝试 utf-8 与 gb18030；
    PostgreSQL 的 TEXT 始终合法，原样返回列名。
    """
    # SQLite: read text columns as raw bytes so _safe_text can decode robustly.
    # PostgreSQL has no BLOB type and TEXT columns are always valid text.
    if dialect_name == "sqlite":
        return f"CAST({name} AS BLOB) AS {name}"
    return name


def _safe_document_bucket_rows(
    db: Session, tenant_id: str, document_id: str
) -> list[Mapping[str, Any]]:
    """用原生 SQL 按创建时间读取文档下的知识桶，绕开 ORM 以容忍脏编码文本。"""
    dialect = db.get_bind().dialect.name
    return list(
        db.execute(
            text(
                f"""
                SELECT
                    id,
                    tenant_id,
                    knowledge_base_id,
                    knowledge_base_version_id,
                    document_id,
                    {_blob_cast("bucket_key", dialect)},
                    {_blob_cast("title", dialect)},
                    {_blob_cast("summary", dialect)},
                    token_estimate,
                    {_blob_cast("metadata_json", dialect)},
                    created_at,
                    updated_at
                FROM knowledge_buckets
                WHERE tenant_id = :tenant_id AND document_id = :document_id
                ORDER BY created_at ASC
                """
            ),
            {"tenant_id": tenant_id, "document_id": document_id},
        )
        .mappings()
        .all()
    )


def _safe_bucket_chunk_rows(db: Session, tenant_id: str, bucket_id: str) -> list[Mapping[str, Any]]:
    """用原生 SQL 读取桶内分块，按 chunk_index 升序返回。"""
    dialect = db.get_bind().dialect.name
    return list(
        db.execute(
            text(
                f"""
                SELECT
                    id,
                    tenant_id,
                    knowledge_base_id,
                    knowledge_base_version_id,
                    document_id,
                    bucket_id,
                    chunk_index,
                    {_blob_cast("content", dialect)},
                    {_blob_cast("summary", dialect)},
                    {_blob_cast("source_ref", dialect)},
                    {_blob_cast("metadata_json", dialect)},
                    created_at,
                    updated_at
                FROM knowledge_chunks
                WHERE tenant_id = :tenant_id AND bucket_id = :bucket_id
                ORDER BY chunk_index ASC
                """
            ),
            {"tenant_id": tenant_id, "bucket_id": bucket_id},
        )
        .mappings()
        .all()
    )


def _bucket_read_mapping_with_stats(
    row: Mapping[str, Any], chunk_count: int
) -> KnowledgeBucketRead:
    """把原生 SQL 行转成桶视图，文本容错解码并补齐分块数与状态。

    分块数优先用实测值，为 0 时回落到 metadata 中的 chunk_count 或代表分块数量。
    """
    summary = _safe_text(row.get("summary"))
    metadata = _safe_json_object(row.get("metadata_json"))
    return KnowledgeBucketRead(
        id=_safe_text(row.get("id")),
        tenant_id=_safe_text(row.get("tenant_id")),
        knowledge_base_id=_safe_text(row.get("knowledge_base_id")),
        document_id=_safe_text(row.get("document_id")),
        bucket_key=_safe_text(row.get("bucket_key")),
        title=_safe_text(row.get("title"), "未命名片段"),
        summary=summary,
        token_estimate=_safe_int(row.get("token_estimate")),
        chunk_count=chunk_count
        or int(
            metadata.get("chunk_count") or len(metadata.get("representative_chunk_ids") or []) or 0
        ),
        status="ready" if chunk_count > 0 and summary.strip() else "incomplete",
        metadata=metadata,
        created_at=_safe_datetime_text(row.get("created_at")),
        updated_at=_safe_datetime_text(row.get("updated_at")),
    )


def _chunk_read_mapping(row: Mapping[str, Any]) -> KnowledgeChunkRead:
    """把原生 SQL 行转成分块视图，摘要与来源为空时保留 None。"""
    return KnowledgeChunkRead(
        id=_safe_text(row.get("id")),
        tenant_id=_safe_text(row.get("tenant_id")),
        knowledge_base_id=_safe_text(row.get("knowledge_base_id")),
        document_id=_safe_text(row.get("document_id")),
        bucket_id=_safe_text(row.get("bucket_id")),
        chunk_index=_safe_int(row.get("chunk_index")),
        content=_safe_text(row.get("content")),
        summary=_safe_optional_text(row.get("summary")),
        source_ref=_safe_optional_text(row.get("source_ref")),
        metadata=_safe_json_object(row.get("metadata_json")),
        created_at=_safe_datetime_text(row.get("created_at")),
        updated_at=_safe_datetime_text(row.get("updated_at")),
    )


def _safe_text(value: Any, fallback: str = "") -> str:
    """把任意值容错转成字符串。

    bytes 先按 utf-8 再按 gb18030 解码，仍失败则替换非法字节；None 返回 fallback。
    """
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_optional_text(value: Any) -> str | None:
    """同 _safe_text，但空值返回 None，用于区分“没有值”和空字符串。"""
    if value is None:
        return None
    text_value = _safe_text(value)
    return text_value if text_value else None


def _safe_json_object(value: Any) -> dict[str, Any]:
    """把可能是 JSON 文本的值解析成 dict。

    已是 dict 时原样返回；解析失败或结果不是对象时返回空 dict。
    """
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        parsed = json.loads(_safe_text(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: Any) -> int:
    """把值转成 int，None 或无法解析时返回 0。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_datetime_text(value: Any) -> str:
    """把 datetime 转成 ISO 字符串，其它值走 _safe_text 容错。"""
    if isinstance(value, datetime):
        return value.isoformat()
    return _safe_text(value)


def discovery_read(row: KnowledgeDiscoverySuggestion) -> KnowledgeDiscoveryRead:
    """把知识发现建议记录转成 API 返回模型。"""
    return KnowledgeDiscoveryRead(
        id=row.id,
        tenant_id=row.tenant_id,
        knowledge_base_id=row.knowledge_base_id,
        document_id=row.document_id,
        bucket_id=row.bucket_id,
        suggestion_type=row.suggestion_type,  # type: ignore[arg-type]
        title=row.title,
        status=row.status,
        payload=row.payload_json or {},
        source_refs=row.source_refs_json or [],
        reason=row.reason,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _get_document(db: Session, tenant_id: str, document_id: str) -> KnowledgeDocument:
    """按 id 取文档并校验租户，缺失或跨租户一律抛 404。"""
    ensure_tenant(db, tenant_id)
    row = db.get(KnowledgeDocument, document_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    return row


def _ensure_knowledge_version_visible(
    db: Session,
    tenant_id: str,
    knowledge_base_version_id: str | None,
    agent_id: str | None,
) -> None:
    """校验知识库版本存在、属于该租户且在给定 agent 或画廊范围内可见。

    版本绑定缺失、版本不存在或不在可见范围都按 404 处理，避免泄露其它范围资源。
    """
    if not knowledge_base_version_id:
        raise HTTPException(
            status_code=404,
            detail="Knowledge resource has no version binding; re-ingest the document or restore its knowledge-base version",
        )
    version = db.get(KnowledgeBaseVersion, knowledge_base_version_id)
    if not version or version.tenant_id != tenant_id:
        raise HTTPException(
            status_code=404,
            detail=f"Knowledge-base version {knowledge_base_version_id} does not exist in tenant {tenant_id}",
        )
    visible_base_ids = set(
        visible_knowledge_base_versions(db, tenant_id, agent_id, include_inactive=True)
    )
    if version.knowledge_base_id not in visible_base_ids:
        scope = agent_id or "open-gallery"
        raise HTTPException(
            status_code=404,
            detail=(
                f"Knowledge-base version {knowledge_base_version_id} belongs to resource "
                f"{version.knowledge_base_id}, which is not visible in scope {scope}"
            ),
        )


def _get_discovery(db: Session, tenant_id: str, suggestion_id: str) -> KnowledgeDiscoverySuggestion:
    """按 id 取知识发现建议并校验租户，缺失或跨租户抛 404。"""
    ensure_tenant(db, tenant_id)
    row = db.get(KnowledgeDiscoverySuggestion, suggestion_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge discovery not found")
    return row


def _ensure_open_gallery_knowledge_admin(
    db: Session,
    tenant_id: str,
    knowledge_base_id: str,
    current_user: object | None,
) -> None:
    """校验当前用户有权管理该知识库。

    配置了 owner_agent_id 时要求该 agent 的管理员身份；属于 open gallery 时要求画廊
    管理员；其余情况直接返回，由调用方的租户与范围校验兜底。
    """
    knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
    metadata = (
        knowledge_base.metadata_json
        if knowledge_base and isinstance(knowledge_base.metadata_json, dict)
        else {}
    )
    owner_agent_id = metadata.get("owner_agent_id")
    if isinstance(owner_agent_id, str) and owner_agent_id:
        ensure_agent_scope_manager(db, tenant_id, owner_agent_id, current_user)
        return
    if (
        knowledge_base
        and knowledge_base.tenant_id == tenant_id
        and is_open_gallery_resource(db, tenant_id, "knowledge_base", knowledge_base)
    ):
        ensure_open_gallery_admin(tenant_id, current_user)
