from __future__ import annotations

import base64
import binascii
import json
import queue
import re
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from html import unescape
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.agents.branching import (
    ensure_open_gallery_binding,
    ensure_private_resource_binding,
    get_agent,
    hide_open_gallery_binding,
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
    mark_resource_open_gallery,
    mark_resource_private_for_agent,
    metadata_preserving_creator,
    require_overall_agent,
    user_creator_metadata,
)
from app.capabilities.local_general_skill import (
    GeneralSkillRuntimeSnapshot,
    local_runtime_snapshot,
)
from app.capability_scope import normalize_capability_scope
from app.core import AgentLoop
from app.db import engine, get_session
from app.db.models import (
    AgentEvent,
    AgentResourceBinding,
    ChatSession,
    GeneralSkill,
    Message,
    ModelConfig,
    User,
    new_id,
    utc_now,
)
from app.general_skills import (
    GeneralSkillClawHubImportRequest,
    GeneralSkillImportRequest,
    GeneralSkillPackageUploadRequest,
    GeneralSkillRead,
    GeneralSkillRunRequest,
    GeneralSkillRunResponse,
)
from app.general_skills.runner import GeneralSkillReader, GeneralSkillRunner
from app.general_skills.schema import GeneralSkillFile
from app.llm.model_config_resolver import resolve_model_config_for_runtime
from app.security.auth import get_current_user
from app.security.permissions import (
    ensure_agent_scope_manager,
    ensure_open_gallery_admin,
    require_agent_scope_viewer,
)
from app.security.tenant import ensure_tenant
from app.session.session_schema import ChatTurnRequest, ChatTurnResponse

router = APIRouter(
    prefix="/api/enterprise/general-skills",
    tags=["enterprise:general-skills"],
    dependencies=[Depends(get_current_user)],
)

MAX_CLAWHUB_PACKAGE_BYTES = 96 * 1024 * 1024
MAX_CLAWHUB_FILE_BYTES = 2 * 1024 * 1024
MAX_CLAWHUB_FILES = 240
REMOTE_SKILL_DOWNLOAD_TIMEOUT_SECONDS = 120
GENERAL_SKILL_STREAM_IDLE_TIMEOUT_SECONDS = 600
GENERAL_SKILL_DEBUG_CHANNEL = "skill_test"
GITHUB_HOSTS = {"github.com", "www.github.com"}
RAW_GITHUB_HOST = "raw.githubusercontent.com"
CLAWHUB_HOSTS = {"clawhub.ai", "www.clawhub.ai"}
SKILLHUB_HOSTS = {"skillhub.ai", "www.skillhub.ai"}
REMOTE_SKILLHUB_HOSTS = CLAWHUB_HOSTS | SKILLHUB_HOSTS
CLAWHUB_DOWNLOAD_ENDPOINT = "https://wry-manatee-359.convex.site/api/v1/download"
LOCAL_REFERENCE_PATTERN = re.compile(r"(?<![\w./-])(?:\./)?references/[^\s`'\"<>|]+")


def _agent_id_or_none(agent_id: object | None) -> str | None:
    """把可选的 agent_id 归一化：仅保留非空字符串，其余返回 None。"""
    return agent_id if isinstance(agent_id, str) and agent_id else None


def general_skill_read(row: GeneralSkill, status_override: str | None = None) -> GeneralSkillRead:
    """把 GeneralSkill 行转换为对外响应模型，可覆盖展示状态（如 Agent 作用域内的发布态）。"""
    return GeneralSkillRead(
        id=row.id,
        tenant_id=row.tenant_id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        homepage=row.homepage,
        skill_markdown=row.skill_markdown,
        skill_files=[
            GeneralSkillFile.model_validate(item) for item in _skill_files_or_markdown(row)
        ],
        skill_directories=_skill_directories(row),
        metadata=dict(row.metadata_json or {}),
        status=status_override or row.status,
        capability_scope=normalize_capability_scope(row.capability_scope),
        permissions=row.permissions_json or {},
        runtime_config=row.runtime_config_json or {},
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.post("/import", response_model=GeneralSkillRead)
def import_general_skill(
    request: GeneralSkillImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """按 slug 新增或更新通用技能，并维护开放画廊 / Agent 私有绑定。

    Agent 私有作用域下，画廊技能或他人私有技能会被复制成新 slug 后重建，
    原技能行保留；仅替换 markdown 时只改包内 SKILL.md，其余文件原样保留。
    """
    ensure_tenant(db, request.tenant_id)
    files = _normalize_skill_files(request.files, request.markdown)
    markdown = _skill_markdown_from_files(files)
    parsed_metadata = _parse_skill_metadata(markdown)
    metadata = user_creator_metadata(current_user, parsed_metadata)
    name = (
        _optional_text(request.name)
        or _metadata_text(metadata, "name", "title")
        or "未命名通用技能"
    )
    slug = _optional_text(request.slug) or _metadata_text(metadata, "slug", "id") or _slugify(name)
    description = _optional_text(request.description) or _metadata_text(
        metadata, "description", "summary"
    )
    homepage = _optional_text(request.homepage) or _metadata_text(
        metadata, "homepage", "url", "source"
    )
    _validate_slug(slug)
    lookup_slug = _optional_text(request.original_slug)
    agent_id = _agent_id_or_none(request.agent_id)
    agent = ensure_agent_scope_manager(db, request.tenant_id, agent_id, current_user)
    is_private_agent_scope = bool(agent and not agent.is_overall)
    if not is_private_agent_scope:
        ensure_open_gallery_admin(request.tenant_id, current_user)
    row = None
    package_source_row = None
    inherited_capability_scope = "general"
    if lookup_slug:
        row = db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == request.tenant_id,
                GeneralSkill.slug == lookup_slug,
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="General skill to update was not found")
        inherited_capability_scope = normalize_capability_scope(row.capability_scope)
        if slug != row.slug:
            raise HTTPException(status_code=400, detail="General skill slug cannot be modified")
        if is_private_agent_scope:
            if is_open_gallery_resource(db, request.tenant_id, "general_skill", row):
                package_source_row = row
                row = None
                slug = _unique_slug(db, request.tenant_id, slug)
            elif not _general_skill_editable_by_agent(db, request.tenant_id, agent.id, row):
                raise HTTPException(
                    status_code=404, detail="General skill not visible to this agent"
                )
            elif not _private_skill_owned_by_agent(
                db, request.tenant_id, row, agent.id
            ):
                package_source_row = row
                row = None
                slug = _unique_slug(db, request.tenant_id, slug)
    else:
        conflict = db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == request.tenant_id,
                GeneralSkill.slug == slug,
            )
        ).first()
        if conflict:
            if is_private_agent_scope and is_open_gallery_resource(
                db,
                request.tenant_id,
                "general_skill",
                conflict,
            ):
                slug = _unique_slug(db, request.tenant_id, slug)
            elif is_private_agent_scope and _private_skill_owned_by_agent(
                db, request.tenant_id, conflict, agent.id
            ):
                # A private skill removed from this agent keeps its entity so
                # other references remain stable; re-import should restore it.
                row = conflict
            else:
                raise HTTPException(status_code=409, detail="General skill slug already exists")
    package_source = row or package_source_row
    if package_source is not None and not request.files and request.markdown is not None:
        files = _replace_skill_markdown_in_package(package_source, request.markdown)
        markdown = _skill_markdown_from_files(files)
    requested_directories = (
        _skill_directories_from_values(request.directories, files)
        if request.directories is not None
        else None
    )
    _validate_skill_package_references(files)
    now = utc_now()
    if row:
        metadata = metadata_preserving_creator(row.metadata_json, parsed_metadata)
        directories = (
            requested_directories
            if requested_directories is not None
            else _skill_directories(row)
        )
        if directories:
            metadata["skill_directories"] = directories
        else:
            metadata.pop("skill_directories", None)
        if slug != row.slug:
            conflict = db.exec(
                select(GeneralSkill).where(
                    GeneralSkill.tenant_id == request.tenant_id,
                    GeneralSkill.slug == slug,
                )
            ).first()
            if conflict:
                if is_private_agent_scope and is_open_gallery_resource(
                    db,
                    request.tenant_id,
                    "general_skill",
                    conflict,
                ):
                    slug = _unique_slug(db, request.tenant_id, slug)
                else:
                    raise HTTPException(status_code=409, detail="General skill slug already exists")
        row.slug = slug
        row.name = name
        row.description = description
        row.homepage = homepage
        row.skill_markdown = markdown
        row.skill_files_json = [file.model_dump(mode="json") for file in files]
        row.metadata_json = metadata
        row.status = request.status
        if request.capability_scope is not None:
            row.capability_scope = request.capability_scope
        row.updated_at = now
    else:
        if requested_directories:
            metadata["skill_directories"] = requested_directories
        row = GeneralSkill(
            tenant_id=request.tenant_id,
            slug=slug,
            name=name,
            description=description,
            homepage=homepage,
            skill_markdown=markdown,
            skill_files_json=[file.model_dump(mode="json") for file in files],
            metadata_json=metadata,
            status=request.status,
            capability_scope=normalize_capability_scope(
                request.capability_scope or inherited_capability_scope
            ),
            permissions_json={"network": True, "python": True},
            runtime_config_json={"runtime": "python", "timeout_seconds": 12},
            created_at=now,
            updated_at=now,
        )
    if is_private_agent_scope:
        mark_resource_private_for_agent(row, agent.id, metadata)
    else:
        mark_resource_open_gallery(row, metadata)
    db.add(row)
    db.flush()
    if is_private_agent_scope:
        ensure_private_resource_binding(
            db,
            request.tenant_id,
            agent.id,
            "general_skill",
            row.id,
            "active" if request.status == "published" else "inactive",
            metadata_json=metadata,
            revive=True,
        )
        if package_source_row is not None and package_source_row.id != row.id:
            replaced_binding = db.exec(
                select(AgentResourceBinding).where(
                    AgentResourceBinding.tenant_id == request.tenant_id,
                    AgentResourceBinding.agent_id == agent.id,
                    AgentResourceBinding.resource_type == "general_skill",
                    AgentResourceBinding.resource_id == package_source_row.id,
                    AgentResourceBinding.status != "deleted",
                )
            ).first()
            if replaced_binding is not None:
                replaced_binding.status = "deleted"
                replaced_binding.updated_at = now
                db.add(replaced_binding)
    else:
        ensure_open_gallery_binding(
            db,
            request.tenant_id,
            "general_skill",
            row.id,
            "active" if request.status == "published" else "inactive",
            metadata_json=metadata,
        )
    db.commit()
    db.refresh(row)
    return general_skill_read(row)


@router.post("/import-skillhub", response_model=GeneralSkillRead)
def import_skillhub_skill(
    request: GeneralSkillClawHubImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """从开源平台来源加载技能包并创建租户内新技能，slug 冲突时自动去重。"""
    ensure_tenant(db, request.tenant_id)
    raw_files = _load_clawhub_source(request.source)
    files = _normalize_skill_files(raw_files, None)
    return _create_imported_general_skill(
        db,
        tenant_id=request.tenant_id,
        files=files,
        import_source=request.source,
        agent_id=request.agent_id,
        status=request.status,
        name=request.name,
        slug=request.slug,
        description=request.description,
        homepage=request.homepage,
        capability_scope=request.capability_scope,
        current_user=current_user,
    )


@router.post("/import-clawhub", response_model=GeneralSkillRead)
def import_clawhub_skill(
    request: GeneralSkillClawHubImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """ClawHub 旧端点别名，复用 SkillHub 导入逻辑以保持兼容。"""
    return import_skillhub_skill(request, db, current_user)


@router.post("/import-package", response_model=GeneralSkillRead)
def import_general_skill_package(
    request: GeneralSkillPackageUploadRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """导入上传的 .zip 或 Markdown 技能包并创建新技能。"""
    ensure_tenant(db, request.tenant_id)
    filename = _clean_source_filename(request.filename)
    data = _decode_base64_payload(request.content_base64)
    if filename.lower().endswith(".zip"):
        raw_files = _files_from_zip(data)
    elif filename.lower().endswith((".md", ".markdown", ".txt")):
        text = _decode_text(data)
        raw_files = [
            GeneralSkillFile(
                path="SKILL.md",
                content=text,
                size=len(data),
                mime_type=_guess_mime_type(filename),
            )
        ]
    else:
        raise HTTPException(
            status_code=400, detail="Uploaded skill package must be a .zip or Markdown file"
        )
    files = _normalize_skill_files(raw_files, None)
    return _create_imported_general_skill(
        db,
        tenant_id=request.tenant_id,
        files=files,
        import_source=f"upload:{filename}",
        agent_id=request.agent_id,
        status=request.status,
        name=request.name,
        slug=request.slug,
        description=request.description,
        homepage=request.homepage,
        capability_scope=request.capability_scope,
        current_user=current_user,
    )


def _create_imported_general_skill(
    db: Session,
    *,
    tenant_id: str,
    files: list[GeneralSkillFile],
    import_source: str,
    agent_id: str | None,
    status: str,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    homepage: str | None = None,
    capability_scope: str = "general",
    current_user: object | None = None,
) -> GeneralSkillRead:
    """为远端或上传的包创建技能行与绑定，返回新技能视图。

    名称、slug、描述缺失时依次回退到 front matter 元数据与来源推导值。
    """
    _validate_skill_package_references(files)
    markdown = _skill_markdown_from_files(files)
    metadata = _parse_skill_metadata(markdown)
    resolved_name = (
        _optional_text(name)
        or _metadata_text(metadata, "name", "title")
        or _source_name(import_source)
    )
    source_slug = _clawhub_slug_from_source(import_source)
    slug_base = (
        _optional_text(slug)
        or _metadata_text(metadata, "slug", "id")
        or source_slug
        or _slugify(resolved_name)
    )
    resolved_slug = _unique_slug(db, tenant_id, slug_base)
    resolved_description = _optional_text(description) or _metadata_text(
        metadata, "description", "summary"
    )
    resolved_homepage = (
        _optional_text(homepage)
        or _metadata_text(metadata, "homepage", "url", "source")
        or _clawhub_homepage_from_source(import_source)
    )
    _validate_slug(resolved_slug)
    now = utc_now()
    resolved_agent_id = _agent_id_or_none(agent_id)
    agent = ensure_agent_scope_manager(db, tenant_id, resolved_agent_id, current_user)
    row = GeneralSkill(
        tenant_id=tenant_id,
        slug=resolved_slug,
        name=resolved_name,
        description=resolved_description,
        homepage=resolved_homepage,
        skill_markdown=markdown,
        skill_files_json=[file.model_dump(mode="json") for file in files],
        metadata_json=user_creator_metadata(
            current_user, {**metadata, "import_source": import_source}
        ),
        status=status,
        capability_scope=normalize_capability_scope(capability_scope),
        permissions_json={"network": True, "python": True},
        runtime_config_json={"runtime": "python", "timeout_seconds": 12},
        created_at=now,
        updated_at=now,
    )
    if not (agent and not agent.is_overall):
        ensure_open_gallery_admin(tenant_id, current_user)
    if agent and not agent.is_overall:
        mark_resource_private_for_agent(row, agent.id, row.metadata_json or {})
    else:
        mark_resource_open_gallery(row, row.metadata_json or {})
    db.add(row)
    db.flush()
    if agent and not agent.is_overall:
        ensure_private_resource_binding(
            db,
            tenant_id,
            agent.id,
            "general_skill",
            row.id,
            "active" if status == "published" else "inactive",
            metadata_json=row.metadata_json or {},
        )
    else:
        ensure_open_gallery_binding(
            db,
            tenant_id,
            "general_skill",
            row.id,
            "active" if status == "published" else "inactive",
            metadata_json=row.metadata_json or {},
        )
    db.commit()
    db.refresh(row)
    return general_skill_read(row)


@router.get(
    "", response_model=list[GeneralSkillRead], dependencies=[Depends(require_agent_scope_viewer)]
)
def list_general_skills(
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = Query(None),
) -> list[GeneralSkillRead]:
    """列出技能：非 overall Agent 只看自己可见的绑定，其余只返回开放画廊技能。"""
    ensure_tenant(db, tenant_id)
    agent_id = _agent_id_or_none(agent_id)
    agent = get_agent(db, tenant_id, agent_id)
    if agent and not agent.is_overall:
        bindings = db.exec(
            select(AgentResourceBinding)
            .where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == "general_skill",
            )
            .order_by(AgentResourceBinding.updated_at.desc())
        ).all()
        if not bindings:
            return []
        rows_by_id = {
            row.id: row
            for row in db.exec(
                select(GeneralSkill).where(
                    GeneralSkill.tenant_id == tenant_id,
                    GeneralSkill.id.in_([binding.resource_id for binding in bindings]),
                )
            ).all()
        }
        visible_rows: list[GeneralSkillRead] = []
        for binding in bindings:
            row = rows_by_id.get(binding.resource_id)
            if not row:
                continue
            if not is_bound_resource_visible_for_agent(
                db, tenant_id, "general_skill", row, binding
            ):
                continue
            visible_rows.append(
                general_skill_read(
                    row,
                    status_override=(
                        "published"
                        if binding.status == "active" and row.status == "published"
                        else "archived"
                    ),
                )
            )
        return visible_rows
    rows = db.exec(
        select(GeneralSkill)
        .where(GeneralSkill.tenant_id == tenant_id)
        .order_by(GeneralSkill.updated_at.desc())
    ).all()
    rows = [row for row in rows if is_open_gallery_resource(db, tenant_id, "general_skill", row)]
    return [general_skill_read(row) for row in rows]


@router.get(
    "/{slug}", response_model=GeneralSkillRead, dependencies=[Depends(require_agent_scope_viewer)]
)
def get_general_skill(
    slug: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = Query(None),
) -> GeneralSkillRead:
    """按 slug 取单个技能，校验可见性后返回。"""
    row = _get_general_skill(db, tenant_id, slug)
    _ensure_general_skill_visible(db, tenant_id, row, agent_id)
    return general_skill_read(row)


@router.post("/{slug}/publish", response_model=GeneralSkillRead)
def publish_general_skill(
    slug: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """发布技能：Agent 作用域内只激活该 Agent 的绑定，开放画廊则改全局状态并重建绑定。"""
    row = _get_general_skill(db, tenant_id, slug)
    agent_id = _agent_id_or_none(agent_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        binding = _ensure_general_skill_binding(
            db,
            tenant_id,
            agent.id,
            row.id,
            metadata_json=row.metadata_json or {},
        )
        binding.status = "active"
        binding.updated_at = utc_now()
        db.add(binding)
        db.commit()
        return general_skill_read(row, status_override="published")
    ensure_open_gallery_admin(tenant_id, current_user)
    row.status = "published"
    mark_resource_open_gallery(row, row.metadata_json or {})
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(
        db,
        tenant_id,
        "general_skill",
        row.id,
        "active",
        metadata_json=row.metadata_json or {},
    )
    db.commit()
    db.refresh(row)
    return general_skill_read(row)


@router.post("/{slug}/archive", response_model=GeneralSkillRead)
def archive_general_skill(
    slug: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """归档技能：Agent 作用域内只停用绑定，开放画廊则把全局状态置为 archived。"""
    row = _get_general_skill(db, tenant_id, slug)
    agent_id = _agent_id_or_none(agent_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        binding = _ensure_general_skill_binding(db, tenant_id, agent.id, row.id)
        binding.status = "inactive"
        binding.updated_at = utc_now()
        db.add(binding)
        db.commit()
        return general_skill_read(row, status_override="archived")
    ensure_open_gallery_admin(tenant_id, current_user)
    row.status = "archived"
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(db, tenant_id, "general_skill", row.id, "inactive")
    db.commit()
    db.refresh(row)
    return general_skill_read(row)


@router.post("/{slug}/publish-to-gallery", response_model=GeneralSkillRead)
def publish_general_skill_to_gallery(
    slug: str,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    """把 Agent 私有技能提升到租户开放画廊，仅技能所有者可操作。"""
    row = _get_general_skill(db, tenant_id, slug)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent is None or agent.is_overall:
        raise HTTPException(status_code=400, detail="A non-overall agent is required")
    if is_open_gallery_resource(db, tenant_id, "general_skill", row):
        return general_skill_read(row)
    if not _private_skill_owned_by_agent(db, tenant_id, row, agent.id):
        raise HTTPException(status_code=403, detail="Only the skill owner can publish it")

    row.status = "published"
    mark_resource_open_gallery(row, row.metadata_json or {})
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(
        db,
        tenant_id,
        "general_skill",
        row.id,
        "active",
        metadata_json=row.metadata_json or {},
        revive=True,
    )
    db.commit()
    db.refresh(row)
    return general_skill_read(row)


@router.delete("/{slug}")
def delete_general_skill(
    slug: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除或隐藏技能：Agent 绑定与开放画廊收录都只隐藏，画廊管理员才真正删除技能行。"""
    agent_id = _agent_id_or_none(agent_id)
    row = _get_general_skill(db, tenant_id, slug)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        binding = _ensure_general_skill_binding(db, tenant_id, agent.id, row.id)
        binding.status = "deleted"
        binding.updated_at = utc_now()
        db.add(binding)
        db.commit()
        return {"status": "hidden", "slug": slug}
    if agent and agent.is_overall:
        if not is_open_gallery_resource(db, tenant_id, "general_skill", row):
            raise HTTPException(status_code=404, detail="General skill not visible in open gallery")
        ensure_open_gallery_admin(tenant_id, current_user)
        hide_open_gallery_binding(db, tenant_id, "general_skill", row.id)
        db.commit()
        return {"status": "hidden", "slug": slug}

    require_overall_agent(db, tenant_id, agent_id)
    ensure_open_gallery_admin(tenant_id, current_user)
    db.delete(row)
    db.commit()
    return {"status": "deleted", "slug": slug}


@router.post("/{slug}/run", response_model=GeneralSkillRunResponse)
def run_general_skill(
    slug: str,
    request: GeneralSkillRunRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRunResponse:
    """同步执行已发布技能：校验可见性与模型配置后返回运行结果。"""
    skill = _get_general_skill(db, request.tenant_id, slug)
    if skill.status != "published":
        raise HTTPException(status_code=400, detail="General skill is not published")
    require_agent_scope_viewer(request.tenant_id, request.agent_id, current_user, db)
    _ensure_general_skill_visible(db, request.tenant_id, skill, request.agent_id)
    model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
    skill_snapshot = _general_skill_snapshot(skill)
    return _run_general_skill_operation(
        skill_snapshot,
        request,
        model_config,
        current_user.id,
    )


@router.post("/{slug}/run/stream")
def run_general_skill_stream(
    slug: str,
    request: GeneralSkillRunRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """以 SSE 流式执行技能：read 直接返回，execute 走 Harness v2 并回放 trace。

    轮询持久化 AgentEvent 推送 trace；收到终态事件或空闲超时即结束。
    """
    skill = _get_general_skill(db, request.tenant_id, slug)
    if skill.status != "published":
        raise HTTPException(status_code=400, detail="General skill is not published")
    require_agent_scope_viewer(request.tenant_id, request.agent_id, current_user, db)
    _ensure_general_skill_visible(db, request.tenant_id, skill, request.agent_id)
    skill_snapshot = _general_skill_snapshot(skill)
    # Validate the explicitly selected model before starting a durable Harness
    # turn. AgentLoop resolves the same config again in its worker session.
    _get_request_model(db, request.tenant_id, request.model_config_id)
    if request.operation == "read":
        model_config = _get_request_model(db, request.tenant_id, request.model_config_id)

        def read_stream_events() -> Iterator[str]:
            """把同步 read 结果包装成单条 complete SSE 事件流。"""
            response = _run_general_skill_operation(
                skill_snapshot,
                request,
                model_config,
                current_user.id,
            )
            yield _sse("complete", response.model_dump(mode="json"))

        return StreamingResponse(read_stream_events(), media_type="text/event-stream")

    session_id = new_id("session")
    client_turn_id = f"skill-test-{uuid.uuid4().hex}"
    debug_session = ChatSession(
        id=session_id,
        tenant_id=request.tenant_id,
        user_id=current_user.id,
        agent_id=request.agent_id,
        title=f"技能测试 · {skill.name}",
        channel=GENERAL_SKILL_DEBUG_CHANNEL,
    )
    db.add(debug_session)
    db.commit()

    harness_request = ChatTurnRequest(
        tenant_id=request.tenant_id,
        session_id=session_id,
        agent_id=request.agent_id,
        model_config_id=request.model_config_id,
        client_turn_id=client_turn_id,
        user_id=current_user.id,
        message=f"/skill {skill.slug} {request.query.strip()}",
        channel=GENERAL_SKILL_DEBUG_CHANNEL,
        message_visibility="internal",
        debug=True,
    )

    def stream_events() -> Iterator[str]:
        """启动后台 worker 执行对话轮次，同时轮询事件表并推送 SSE。"""
        terminal: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            """在独立会话中执行一轮 AgentLoop，并把终态结果放入队列。"""
            try:
                with Session(engine) as worker_db:
                    response = AgentLoop(worker_db).handle_turn(harness_request)
                    assistant = worker_db.exec(
                        select(Message)
                        .where(
                            Message.tenant_id == request.tenant_id,
                            Message.session_id == session_id,
                            Message.role == "assistant",
                        )
                        .order_by(Message.created_at.desc())
                    ).first()
                    terminal.put(
                        (
                            "complete",
                            _harness_skill_run_response(
                                skill.slug,
                                response,
                                dict(assistant.metadata_json or {}) if assistant else {},
                            ),
                        )
                    )
            except Exception as exc:  # pragma: no cover - defensive stream boundary
                terminal.put(("error", {"message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        yield _sse(
            "stream_started",
            {
                "skill_slug": skill_snapshot.slug,
                "operation": request.operation,
                "execution_engine": "harness_v2",
                "session_id": session_id,
                "client_turn_id": client_turn_id,
                "idle_timeout_seconds": GENERAL_SKILL_STREAM_IDLE_TIMEOUT_SECONDS,
            },
        )
        last_event_at = time.monotonic()
        last_heartbeat_at = 0.0
        cursor: tuple[object, str] | None = None
        pending_terminal: tuple[str, object] | None = None
        with Session(engine) as poll_db:
            while True:
                rows = _skill_debug_events_after(
                    poll_db,
                    request.tenant_id,
                    session_id,
                    cursor,
                )
                for row in rows:
                    cursor = (row.created_at, row.id)
                    last_event_at = time.monotonic()
                    yield _sse("trace", _skill_debug_trace(row))

                if pending_terminal is None:
                    try:
                        pending_terminal = terminal.get_nowait()
                    except queue.Empty:
                        # The worker is still running; keep polling persisted trace events.
                        pass
                if pending_terminal is not None and not rows:
                    event, payload = pending_terminal
                    if isinstance(payload, GeneralSkillRunResponse):
                        payload = payload.model_dump(mode="json")
                    yield _sse(event, payload)
                    return

                now = time.monotonic()
                if now - last_event_at > GENERAL_SKILL_STREAM_IDLE_TIMEOUT_SECONDS:
                    yield _sse(
                        "error",
                        {
                            "message": "技能测试 10 分钟内未收到新的执行事件，请检查模型配置或稍后重试。",
                            "code": "general_skill_stream_timeout",
                            "session_id": session_id,
                            "client_turn_id": client_turn_id,
                        },
                    )
                    return
                if now - last_heartbeat_at >= 5:
                    last_heartbeat_at = now
                    yield _sse(
                        "heartbeat",
                        {
                            "phase": "harness_v2",
                            "session_id": session_id,
                            "client_turn_id": client_turn_id,
                        },
                    )
                time.sleep(0.1)

    return StreamingResponse(stream_events(), media_type="text/event-stream")


def _skill_debug_events_after(
    db: Session,
    tenant_id: str,
    session_id: str,
    cursor: tuple[object, str] | None,
) -> list[AgentEvent]:
    """按 (created_at, id) 游标增量读取该会话的调试事件，最多 200 条。"""
    statement = select(AgentEvent).where(
        AgentEvent.tenant_id == tenant_id,
        AgentEvent.session_id == session_id,
    )
    if cursor is not None:
        created_at, event_id = cursor
        statement = statement.where(
            (AgentEvent.created_at > created_at)
            | ((AgentEvent.created_at == created_at) & (AgentEvent.id > event_id))
        )
    db.expire_all()
    return list(
        db.exec(statement.order_by(AgentEvent.created_at, AgentEvent.id).limit(200)).all()
    )


def _skill_debug_trace(row: AgentEvent) -> dict[str, object]:
    """把 AgentEvent 整理为 trace 事件负载，缺失字段回退到事件类型与文本。"""
    payload = dict(row.payload_json or {})
    phase = str(payload.get("phase") or row.event_type)
    message = str(payload.get("text") or payload.get("message") or phase)
    return {
        **payload,
        "event_type": row.event_type,
        "phase": phase,
        "message": message,
        "event_id": row.id,
        "execution_engine": "harness_v2",
    }


def _harness_skill_run_response(
    slug: str,
    response: ChatTurnResponse,
    assistant_metadata: dict[str, object],
) -> GeneralSkillRunResponse:
    """把 Harness v2 的对话轮次结果转换为技能执行响应，错误码映射为 success 与 stderr。"""
    citations = assistant_metadata.get("knowledge_citations")
    artifacts = assistant_metadata.get("harness_artifacts")
    success = not bool(response.runtime_error_code)
    return GeneralSkillRunResponse(
        skill_slug=slug,
        operation="execute",
        structured_result={
            "success": success,
            "execution_engine": "harness_v2",
            "session_id": response.session_id,
            "runtime_error_code": response.runtime_error_code,
            "step_result": (
                response.step_result.model_dump(mode="json")
                if response.step_result is not None
                else None
            ),
            "citations": citations if isinstance(citations, list) else [],
        },
        artifacts=artifacts if isinstance(artifacts, list) else [],
        stderr=response.reply if response.runtime_error_code else "",
        reply=response.reply,
    )


def _run_general_skill_operation(
    skill: GeneralSkillRuntimeSnapshot,
    request: GeneralSkillRunRequest,
    model_config: ModelConfig,
    user_id: str,
    event_sink: Callable[[dict[str, object]], None] | None = None,
) -> GeneralSkillRunResponse:
    """按 operation 分派到 GeneralSkillReader 或 GeneralSkillRunner，并透传事件回调。"""
    if request.operation == "read":
        response = GeneralSkillReader().read(skill, request.query, model_config)
        if event_sink is not None:
            for trace_item in response.execution_trace:
                event_sink(trace_item)
        return response
    return GeneralSkillRunner().run(
        skill,
        request.query,
        model_config,
        user_id,
        request.max_attempts,
        event_sink,
    )


def _get_general_skill(db: Session, tenant_id: str, slug: str) -> GeneralSkill:
    """按租户与 slug 取技能行，不存在时抛 404。"""
    ensure_tenant(db, tenant_id)
    row = db.exec(
        select(GeneralSkill).where(GeneralSkill.tenant_id == tenant_id, GeneralSkill.slug == slug)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="General skill not found")
    return row


def _private_skill_owned_by_agent(
    db: Session, tenant_id: str, row: GeneralSkill, agent_id: str
) -> bool:
    """判断技能是否私有属于该 Agent：技能行或绑定元数据命中 owner 即成立。"""
    metadata = row.metadata_json or {}
    if metadata.get("owner_agent_id") == agent_id and metadata.get("scope") == "agent_private":
        return True
    binding = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.resource_id == row.id,
        )
    ).first()
    binding_metadata = binding.metadata_json if binding else {}
    return bool(
        binding
        and binding_metadata.get("scope") == "agent_private"
        and binding_metadata.get("owner_agent_id") == agent_id
    )


def _ensure_general_skill_visible(
    db: Session,
    tenant_id: str,
    row: GeneralSkill,
    agent_id: str | None,
) -> None:
    """校验技能对该 Agent 作用域可见（overall 看画廊收录，私有看绑定），否则抛 404。"""
    agent = get_agent(db, tenant_id, _agent_id_or_none(agent_id))
    if not agent or agent.is_overall:
        if is_open_gallery_resource(db, tenant_id, "general_skill", row):
            return
        raise HTTPException(status_code=404, detail="General skill not visible in open gallery")
    binding = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.resource_id == row.id,
        )
    ).first()
    if not binding or not is_bound_resource_visible_for_agent(
        db, tenant_id, "general_skill", row, binding
    ):
        raise HTTPException(status_code=404, detail="General skill not visible to this agent")


def _ensure_general_skill_binding(
    db: Session,
    tenant_id: str,
    agent_id: str,
    general_skill_id: str,
    metadata_json: dict[str, object] | None = None,
) -> AgentResourceBinding:
    """取该 Agent 的技能绑定，不存在则新建，并写入私有归属元数据。"""
    row = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.resource_id == general_skill_id,
        )
    ).first()
    metadata = {
        **(metadata_json or {}),
        "scope": "agent_private",
        "visibility": "agent_private",
        "owner_agent_id": agent_id,
        "created_from_agent": True,
    }
    if row:
        merged_metadata = {
            **(row.metadata_json or {}),
            **metadata,
        }
        row.metadata_json = metadata_preserving_creator(
            row.metadata_json,
            merged_metadata,
        )
        return row
    row = AgentResourceBinding(
        tenant_id=tenant_id,
        agent_id=agent_id,
        resource_type="general_skill",
        resource_id=general_skill_id,
        status="active",
        metadata_json=metadata,
    )
    db.add(row)
    db.flush()
    return row


def _general_skill_editable_by_agent(
    db: Session, tenant_id: str, agent_id: str, row: GeneralSkill
) -> bool:
    """判断 Agent 能否编辑该技能：需私有归属且未被开放画廊收录。"""
    metadata = row.metadata_json or {}
    if metadata.get("owner_agent_id") == agent_id and metadata.get("scope") == "agent_private":
        return True
    binding = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.resource_id == row.id,
            AgentResourceBinding.status != "deleted",
        )
    ).first()
    return bool(
        binding
        and not is_open_gallery_resource(db, tenant_id, "general_skill", row)
        and is_bound_resource_visible_for_agent(db, tenant_id, "general_skill", row, binding)
    )


def _get_default_model(db: Session, tenant_id: str) -> ModelConfig:
    """取租户默认且启用的模型配置，缺失时抛 400。"""
    model_config = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    if not model_config:
        raise HTTPException(status_code=400, detail="No default model config")
    return _model_runtime_config(db, tenant_id, model_config)


def _get_request_model(
    db: Session, tenant_id: str, model_config_id: str | None = None
) -> ModelConfig:
    """解析请求指定的模型配置；未指定时回退到租户默认模型。"""
    if not model_config_id:
        return _get_default_model(db, tenant_id)
    model_config = db.get(ModelConfig, model_config_id)
    if not model_config or model_config.tenant_id != tenant_id or not model_config.enabled:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_runtime_config(db, tenant_id, model_config)


def _general_skill_snapshot(row: GeneralSkill) -> GeneralSkillRuntimeSnapshot:
    """生成技能运行期快照，作为 reader/runner 的只读输入。"""
    return local_runtime_snapshot(row)


def _model_runtime_config(db: Session, tenant_id: str, row: ModelConfig):
    """解析可用于运行时的模型配置副本（含运行时字段）。"""
    return resolve_model_config_for_runtime(db, tenant_id, row.id)


def _required_text(value: str | None, field: str) -> str:
    """去除首尾空白并校验非空，空值时抛出带字段名的 400。"""
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"General skill {field} cannot be empty")
    return cleaned


def _optional_text(value: str | None) -> str | None:
    """去除首尾空白，空字符串归一化为 None。"""
    cleaned = (value or "").strip()
    return cleaned or None


def _normalize_skill_files(
    requested_files: list[GeneralSkillFile],
    markdown: str | None,
) -> list[GeneralSkillFile]:
    """归一化技能包文件：清理路径，缺省时用 markdown 生成 SKILL.md。

    当 SKILL.md 位于子目录时剥离该前缀，使返回路径相对技能根目录。
    """
    if not requested_files:
        content = _required_text(markdown, "markdown")
        return [
            GeneralSkillFile(path="SKILL.md", content=content, size=len(content.encode("utf-8")))
        ]
    cleaned_files: list[GeneralSkillFile] = []
    for file in requested_files:
        path = _clean_package_path(file.path)
        content = file.content or ""
        cleaned_files.append(
            GeneralSkillFile(
                path=path,
                content=content,
                size=file.size if file.size is not None else len(content.encode("utf-8")),
                mime_type=file.mime_type,
            )
        )
    skill_file = _find_skill_file(cleaned_files)
    if not skill_file:
        raise HTTPException(status_code=400, detail="General skill folder must contain SKILL.md")
    base_dir = skill_file.path.rsplit("/", 1)[0] if "/" in skill_file.path else ""
    if not base_dir:
        return cleaned_files
    normalized: list[GeneralSkillFile] = []
    prefix = f"{base_dir}/"
    for file in cleaned_files:
        if file.path == base_dir or not file.path.startswith(prefix):
            continue
        normalized.append(file.model_copy(update={"path": file.path[len(prefix) :]}))
    return normalized


def _replace_skill_markdown_in_package(
    row: GeneralSkill,
    markdown: str,
) -> list[GeneralSkillFile]:
    """仅替换包内 SKILL.md 的正文，其余文件保持不变。"""
    existing = [
        GeneralSkillFile.model_validate(item) for item in _skill_files_or_markdown(row)
    ]
    files = _normalize_skill_files(existing, None)
    updated: list[GeneralSkillFile] = []
    for file in files:
        if file.path.rsplit("/", 1)[-1].lower() == "skill.md":
            encoded = markdown.encode("utf-8")
            updated.append(
                file.model_copy(
                    update={
                        "content": markdown,
                        "size": len(encoded),
                        "mime_type": file.mime_type or "text/markdown",
                    }
                )
            )
        else:
            updated.append(file)
    return updated


def _validate_skill_package_references(files: list[GeneralSkillFile]) -> None:
    """校验 SKILL.md 中引用的 references/ 文件都存在于包内。

    跳过通配符写法；有缺失时以 400 列出全部缺失路径。
    """
    skill_file = _find_skill_file(files)
    if skill_file is None:
        return
    package_paths = {file.path.replace("\\", "/").lstrip("./") for file in files}
    referenced_paths: set[str] = set()
    for match in LOCAL_REFERENCE_PATTERN.finditer(skill_file.content):
        raw_path = unquote(match.group(0)).replace("\\", "/")
        raw_path = raw_path.split("#", 1)[0].split("?", 1)[0]
        raw_path = raw_path.rstrip(")]}.,;:!?，。；：！？")
        if any(marker in raw_path for marker in ("*", "{", "}")):
            continue
        normalized = raw_path.removeprefix("./").strip("/")
        if normalized and not normalized.endswith("/"):
            referenced_paths.add(normalized)
    missing = sorted(referenced_paths - package_paths)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "General skill package is missing files referenced by SKILL.md: "
                + ", ".join(missing)
            ),
        )


def _skill_directories_from_values(
    values: list[str],
    files: list[GeneralSkillFile],
) -> list[str]:
    """规范化并去重目录声明，同时拒绝与包内文件路径冲突的目录。"""
    file_paths = {file.path for file in files}
    directories: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _clean_package_path(value)
        if path in file_paths or any(path.startswith(f"{file_path}/") for file_path in file_paths):
            raise HTTPException(
                status_code=400,
                detail=f"General skill directory conflicts with a file path: {value}",
            )
        if path not in seen:
            seen.add(path)
            directories.append(path)
    return directories


def _skill_directories(row: GeneralSkill) -> list[str]:
    """从技能行元数据读取目录声明；非法值静默降级为空列表。"""
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    values = metadata.get("skill_directories")
    if not isinstance(values, list):
        return []
    files = [GeneralSkillFile.model_validate(item) for item in _skill_files_or_markdown(row)]
    valid_values = [str(value) for value in values if isinstance(value, str) and value.strip()]
    try:
        return _skill_directories_from_values(valid_values, files)
    except HTTPException:
        return []


def _clean_package_path(path: str) -> str:
    """规范化包内相对路径，拒绝空路径和含 .. 的越界路径。"""
    cleaned = str(path or "").replace("\\", "/").strip().strip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail=f"Invalid general skill file path: {path}")
    return "/".join(parts)


def _find_skill_file(files: list[GeneralSkillFile]) -> GeneralSkillFile | None:
    """返回包内的 SKILL.md（文件名大小写不敏感），没有则返回 None。"""
    return next(
        (file for file in files if file.path.rsplit("/", 1)[-1].lower() == "skill.md"), None
    )


def _skill_markdown_from_files(files: list[GeneralSkillFile]) -> str:
    """取 SKILL.md 正文，缺失或为空白时抛 400。"""
    skill_file = _find_skill_file(files)
    if not skill_file or not skill_file.content.strip():
        raise HTTPException(status_code=400, detail="General skill SKILL.md cannot be empty")
    return skill_file.content


def _skill_files_or_markdown(row: GeneralSkill) -> list[dict[str, object]]:
    """返回行的文件列表；旧数据只有 markdown 时合成为单个 SKILL.md。"""
    files = row.skill_files_json or []
    if files:
        return files
    return [
        {
            "path": "SKILL.md",
            "content": row.skill_markdown,
            "size": len(row.skill_markdown.encode("utf-8")),
        }
    ]


def _parse_skill_metadata(markdown: str) -> dict[str, object]:
    """解析 SKILL.md 顶部 front matter 的简单键值元数据，无 front matter 返回空字典。"""
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, object] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = _parse_metadata_value(value.strip())
    return metadata


def _parse_metadata_value(value: str) -> object:
    """把 front matter 原始值解析为字符串或方括号内的字符串列表。"""
    cleaned = value.strip().strip("'\"")
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return [item.strip().strip("'\"") for item in cleaned[1:-1].split(",") if item.strip()]
    return cleaned


def _metadata_text(metadata: dict[str, object], *keys: str) -> str | None:
    """按给定键顺序返回首个非空字符串元数据值。"""
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _slugify(value: str) -> str:
    """把名称转换为小写、仅含字母数字下划线与连字符的 slug，空结果回退默认值。"""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return slug or "general-skill"


def _unique_slug(db: Session, tenant_id: str, base_slug: str) -> str:
    """在租户内生成唯一 slug，冲突时追加 -2、-3 等递增后缀。"""
    base = _slugify(base_slug)
    candidate = base
    suffix = 2
    while db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id, GeneralSkill.slug == candidate
        )
    ).first():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _source_name(source: str) -> str:
    """从导入来源（URL 或 upload: 标记）推导技能显示名。"""
    parsed = urlparse(source)
    path = parsed.path if parsed.scheme else source
    cleaned = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".zip").removesuffix(".md")
    if cleaned.startswith("upload:"):
        cleaned = cleaned.removeprefix("upload:")
    return cleaned or "开源平台通用技能"


def _clean_source_filename(filename: str) -> str:
    """取上传文件名的基名，空值抛 400。"""
    cleaned = str(filename or "").replace("\\", "/").strip().rsplit("/", 1)[-1]
    if not cleaned:
        raise HTTPException(status_code=400, detail="Uploaded skill package filename is required")
    return cleaned


def _decode_base64_payload(value: str) -> bytes:
    """解码 base64 上传内容（支持 data URL 前缀），校验非空与包体积上限。"""
    cleaned = str(value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Uploaded skill package content is required")
    if "," in cleaned and cleaned[:80].lower().startswith("data:"):
        cleaned = cleaned.split(",", 1)[1]
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Uploaded skill package content is not valid base64"
        ) from exc
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded skill package is empty")
    if len(data) > MAX_CLAWHUB_PACKAGE_BYTES:
        raise HTTPException(status_code=400, detail="Uploaded skill package is too large")
    return data


def _load_clawhub_source(source: str) -> list[GeneralSkillFile]:
    """把来源字符串分派到对应加载方式：平台 slug、GitHub 简写、zip 或 Markdown URL。

    无法识别的来源抛 400。
    """
    cleaned = _required_text(source, "source")
    clawhub_slug = _clawhub_slug_from_source(cleaned)
    if clawhub_slug:
        source_url = cleaned if cleaned.startswith(("http://", "https://")) else None
        return _load_clawhub_skill_package(clawhub_slug, source_url=source_url)
    if cleaned.startswith(("http://", "https://")):
        return _load_remote_skill_source(cleaned)
    if _looks_like_github_shorthand(cleaned):
        return _load_remote_skill_source(f"https://github.com/{cleaned}")
    raise HTTPException(
        status_code=400,
        detail="开源平台来源必须是开源平台 slug、GitHub URL、raw SKILL.md URL、zip URL 或 owner/repo 路径",
    )


def _clawhub_slug_from_source(source: str) -> str | None:
    """从来源字符串识别开源平台 slug，非平台来源返回 None。"""
    cleaned = source.strip()
    if not cleaned:
        return None
    if cleaned.startswith(("http://", "https://")):
        parsed = urlparse(cleaned)
        if parsed.netloc not in REMOTE_SKILLHUB_HOSTS:
            return None
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            slug = parts[1]
        elif len(parts) == 1:
            slug = parts[0]
        else:
            return None
        return _valid_clawhub_slug(slug)
    if "/" not in cleaned:
        return _valid_clawhub_slug(cleaned)
    return None


def _valid_clawhub_slug(value: str) -> str | None:
    """校验平台 slug 格式（去掉 .zip/.md 后缀），不合法返回 None。"""
    slug = value.strip().removesuffix(".zip").removesuffix(".md")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}", slug):
        return slug
    return None


def _clawhub_homepage_from_source(source: str) -> str | None:
    """推导平台首页：平台域名 URL 原样返回，slug 则拼成 skillhub 链接。"""
    cleaned = source.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme and parsed.netloc in REMOTE_SKILLHUB_HOSTS:
        return cleaned
    slug = _clawhub_slug_from_source(cleaned)
    if slug:
        return f"https://skillhub.ai/{slug}"
    return None


def _clawhub_download_url(slug: str) -> str:
    """拼出平台下载端点 URL，slug 做 URL 编码。"""
    return f"{CLAWHUB_DOWNLOAD_ENDPOINT}?slug={quote(slug, safe='')}"


def _load_clawhub_skill_package(slug: str, source_url: str | None = None) -> list[GeneralSkillFile]:
    """优先走平台下载端点；失败且给了源页面 URL 时回退抓取该页面。"""
    download_url = _clawhub_download_url(slug)
    try:
        return _load_remote_skill_source(download_url)
    except HTTPException as download_error:
        if source_url:
            try:
                return _load_remote_skill_source(source_url)
            except HTTPException:
                pass
        raise download_error


def _looks_like_github_shorthand(value: str) -> bool:
    """判断字符串是否形如 owner/repo[/path] 的 GitHub 简写。"""
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/.+)?", value.strip()))


def _load_remote_skill_source(url: str, visited: set[str] | None = None) -> list[GeneralSkillFile]:
    """按 URL 类型加载远端技能包：zip、GitHub 目录/文件或原始 Markdown。

    visited 记录已访问 URL，最多 5 次跳转，避免重定向成环。
    """
    normalized_url = url.strip()
    parsed = urlparse(normalized_url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Remote skill source must be a valid URL")
    visited = visited or set()
    if normalized_url in visited:
        raise HTTPException(status_code=400, detail="Remote skill source redirects to itself")
    if len(visited) >= 5:
        raise HTTPException(
            status_code=400, detail="Remote skill source contains too many indirections"
        )
    visited.add(normalized_url)
    if parsed.netloc in GITHUB_HOSTS or parsed.netloc == RAW_GITHUB_HOST:
        return _load_github_skill_source(parsed)
    data, content_type = _download_url(normalized_url)
    lower_content_type = content_type.lower()
    if parsed.path.lower().endswith(".zip") or "zip" in lower_content_type:
        return _files_from_zip(data)
    text = _decode_text(data)
    if _looks_like_html_response(text, lower_content_type):
        linked_source = _extract_skill_source_from_html(text, normalized_url)
        if linked_source:
            return _load_remote_skill_source(linked_source, visited)
        raise HTTPException(
            status_code=400,
            detail=(
                "开源平台页面没有暴露可下载的技能包或 GitHub 目录。"
                "HTML 页面不会被当作 SKILL.md 导入。"
            ),
        )
    if _looks_like_markdown_source(parsed.path, lower_content_type):
        file_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) or "SKILL.md"
        if not file_name.lower().endswith(".md"):
            file_name = "SKILL.md"
        return [
            GeneralSkillFile(
                path=_clean_package_path(file_name),
                content=text,
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    raise HTTPException(
        status_code=400,
        detail="Remote source must be a zip package, GitHub skill directory, or raw Markdown skill file",
    )


def _load_github_skill_source(parsed) -> list[GeneralSkillFile]:
    """解析 GitHub URL 并加载技能：raw 文件、blob/raw、tree 目录或仓库归档。

    普通仓库地址依次尝试 main、master 分支，最后回退 zip 归档。
    """
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc == RAW_GITHUB_HOST:
        if len(parts) < 4:
            raise HTTPException(
                status_code=400,
                detail="Raw GitHub source must include owner, repo, branch and path",
            )
        owner, repo, branch = parts[0], parts[1], parts[2]
        file_path = "/".join(parts[3:])
        data, content_type = _download_url(parsed.geturl())
        return [
            GeneralSkillFile(
                path=file_path.rsplit("/", 1)[-1] or "SKILL.md",
                content=_decode_text(data),
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    if len(parts) < 2:
        raise HTTPException(
            status_code=400, detail="GitHub source must include owner and repository"
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) >= 3 and parts[2] == "archive":
        data, _ = _download_url(parsed.geturl())
        return _files_from_zip(data)
    if len(parts) >= 5 and parts[2] in {"blob", "raw"}:
        branch = parts[3]
        file_path = "/".join(parts[4:])
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
        data, content_type = _download_url(raw_url)
        return [
            GeneralSkillFile(
                path=file_path.rsplit("/", 1)[-1] or "SKILL.md",
                content=_decode_text(data),
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    if len(parts) >= 5 and parts[2] == "tree":
        branch = parts[3]
        subtree = "/".join(parts[4:])
        return _download_github_directory(owner, repo, branch, subtree)
    subtree = "/".join(parts[2:]) if len(parts) > 2 else ""
    errors: list[str] = []
    for branch in ["main", "master"]:
        try:
            return _download_github_directory(owner, repo, branch, subtree)
        except HTTPException as exc:
            errors.append(str(exc.detail))
    return _download_github_archive(owner, repo, ["main", "master"], subtree)


def _download_github_directory(
    owner: str, repo: str, branch: str, subtree: str = ""
) -> list[GeneralSkillFile]:
    """优先用 GitHub Contents API 拉取目录，失败时回退 zip 归档。"""
    try:
        return _download_github_directory_contents(owner, repo, branch, subtree)
    except HTTPException as api_error:
        try:
            return _download_github_archive(owner, repo, [branch], subtree)
        except HTTPException:
            raise api_error


def _download_github_directory_contents(
    owner: str, repo: str, branch: str, subtree: str = ""
) -> list[GeneralSkillFile]:
    """递归下载 GitHub 目录内容，受文件数量与单文件体积上限约束。"""
    normalized_subtree = subtree.strip("/")
    files: list[GeneralSkillFile] = []
    visited_dirs: set[str] = set()

    def walk(path: str) -> None:
        """递归遍历单个目录，跳过应忽略的目录与超大文件。"""
        if len(files) >= MAX_CLAWHUB_FILES:
            return
        if path in visited_dirs:
            return
        visited_dirs.add(path)
        api_path = quote(path, safe="/")
        api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents"
        if api_path:
            api_url = f"{api_url}/{api_path}"
        api_url = f"{api_url}?ref={quote(branch, safe='')}"
        payload = _download_json(api_url)
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if len(files) >= MAX_CLAWHUB_FILES:
                break
            if not isinstance(entry, dict):
                continue
            item_type = str(entry.get("type") or "")
            item_path = str(entry.get("path") or "").strip("/")
            if not item_path or _skip_package_path(item_path):
                continue
            if item_type == "dir":
                walk(item_path)
                continue
            if item_type != "file":
                continue
            size = int(entry.get("size") or 0)
            if size > MAX_CLAWHUB_FILE_BYTES:
                continue
            download_url = str(entry.get("download_url") or "")
            if not download_url:
                continue
            relative = item_path
            if normalized_subtree and item_path.startswith(f"{normalized_subtree}/"):
                relative = item_path[len(normalized_subtree) + 1 :]
            data, content_type = _download_url(download_url)
            if len(data) > MAX_CLAWHUB_FILE_BYTES:
                continue
            files.append(
                GeneralSkillFile(
                    path=_clean_package_path(relative),
                    content=_decode_text(data),
                    size=len(data),
                    mime_type=content_type or _guess_mime_type(relative),
                )
            )

    walk(normalized_subtree)
    if not _find_skill_file(files):
        raise HTTPException(status_code=400, detail="GitHub directory does not contain SKILL.md")
    return files


def _download_github_archive(
    owner: str, repo: str, branches: list[str], subtree: str = ""
) -> list[GeneralSkillFile]:
    """按分支顺序下载仓库 zip 归档并抽取技能包，全部失败才抛错。"""
    errors: list[str] = []
    for branch in branches:
        archive_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        try:
            data, _ = _download_url(archive_url)
            return _files_from_zip(data, subtree=subtree)
        except HTTPException as exc:
            errors.append(str(exc.detail))
    raise HTTPException(
        status_code=400, detail=f"Unable to download GitHub skill package: {'; '.join(errors)}"
    )


def _download_json(url: str) -> object:
    """下载并解析 JSON 响应，解析失败抛 400。"""
    data, _ = _download_url(url)
    try:
        return json.loads(_decode_text(data))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Remote GitHub API returned invalid JSON"
        ) from exc


def _looks_like_markdown_source(path: str, content_type: str) -> bool:
    """按路径后缀与 content-type 判断响应能否直接当作 Markdown 技能。"""
    lower_path = path.lower()
    lower_content_type = content_type.lower()
    return (
        lower_path.endswith(".md")
        or lower_path.endswith("/skill")
        or "text/markdown" in lower_content_type
        or "text/plain" in lower_content_type
    )


def _looks_like_html_response(text: str, content_type: str) -> bool:
    """按 content-type 与正文开头判断响应是否为 HTML 页面。"""
    stripped = text.lstrip().lower()
    return (
        "text/html" in content_type
        or stripped.startswith("<!doctype html")
        or stripped.startswith("<html")
    )


def _extract_skill_source_from_html(text: str, base_url: str) -> str | None:
    """从 HTML 页面提取可下载技能包链接（raw、zip、GitHub、平台下载端点）。"""
    normalized = unescape(text).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    candidates: list[str] = []
    candidates.extend(re.findall(r"https?://[^\s\"'<>]+", normalized))
    for match in re.finditer(
        r"""(?:href|src)\s*=\s*["']([^"']+)["']""", normalized, flags=re.IGNORECASE
    ):
        candidates.append(urljoin(base_url, match.group(1)))
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.strip().rstrip("),.;]")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            continue
        lower_path = parsed.path.lower()
        if parsed.netloc == RAW_GITHUB_HOST:
            return cleaned
        if _is_clawhub_download_url(parsed):
            return cleaned
        if parsed.netloc in GITHUB_HOSTS and (
            "/tree/" in lower_path
            or "/blob/" in lower_path
            or lower_path.endswith(".zip")
            or "/archive/" in lower_path
        ):
            return cleaned
        if lower_path.endswith(".zip"):
            return cleaned
    return None


def _is_clawhub_download_url(parsed) -> bool:
    """判断 URL 是否为平台下载端点（路径匹配且带 slug 查询参数）。"""
    path = parsed.path.lower().rstrip("/")
    return path.endswith("/api/v1/download") and "slug=" in parsed.query.lower()


def _download_url(url: str) -> tuple[bytes, str]:
    """下载 URL 内容并校验包体积上限，网络错误统一转换为 400。"""
    try:
        request = Request(url, headers={"User-Agent": "StaffDeck-GeneralSkillImporter/1.0"})
        with urlopen(request, timeout=REMOTE_SKILL_DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310 - user-confirmed import source
            content_type = response.headers.get("content-type", "")
            data = response.read(MAX_CLAWHUB_PACKAGE_BYTES + 1)
    except HTTPError as exc:
        raise HTTPException(
            status_code=400, detail=f"Download failed with HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise HTTPException(status_code=400, detail=f"Download failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=400, detail="Download timed out") from exc
    if len(data) > MAX_CLAWHUB_PACKAGE_BYTES:
        raise HTTPException(status_code=400, detail="General skill package is too large")
    return data, content_type


def _files_from_zip(data: bytes, subtree: str = "") -> list[GeneralSkillFile]:
    """从 zip 中抽取以 SKILL.md 为根的文件，受单文件与文件总数上限约束。"""
    normalized_subtree = subtree.strip("/")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and not _skip_package_path(name)
        ]
        skill_candidates = [name for name in names if name.rsplit("/", 1)[-1].lower() == "skill.md"]
        if normalized_subtree:
            skill_candidates = [
                name
                for name in skill_candidates
                if _zip_relative_path(name, normalized_subtree) is not None
            ]
        if not skill_candidates:
            raise HTTPException(status_code=400, detail="Package does not contain SKILL.md")
        base = skill_candidates[0].rsplit("/", 1)[0] if "/" in skill_candidates[0] else ""
        files: list[GeneralSkillFile] = []
        for name in names:
            if base:
                if not name.startswith(f"{base}/"):
                    continue
                relative = name[len(base) + 1 :]
            else:
                relative = name
            if not relative or relative.endswith("/"):
                continue
            info = archive.getinfo(name)
            if info.file_size > MAX_CLAWHUB_FILE_BYTES:
                continue
            if len(files) >= MAX_CLAWHUB_FILES:
                break
            content = _decode_text(archive.read(name))
            files.append(
                GeneralSkillFile(
                    path=relative,
                    content=content,
                    size=info.file_size,
                    mime_type=_guess_mime_type(relative),
                )
            )
    return files


def _zip_relative_path(name: str, subtree: str) -> str | None:
    """在 zip 条目路径中找出匹配 subtree 的相对路径，未命中返回 None。"""
    parts = name.split("/")
    for index in range(1, len(parts)):
        candidate = "/".join(parts[index:])
        if candidate == subtree or candidate.startswith(f"{subtree}/"):
            return candidate
    return None


def _skip_package_path(path: str) -> bool:
    """判断路径是否位于需要跳过的目录（版本库、依赖、构建产物等）。"""
    parts = path.split("/")
    return any(
        part in {"__MACOSX", ".git", "node_modules", ".venv", "dist", "build"} for part in parts
    )


def _decode_text(data: bytes) -> str:
    """按 UTF-8 解码字节，非法字节用替换字符兜底。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _guess_mime_type(path: str) -> str:
    """按扩展名猜测 MIME 类型，未知类型回退 text/plain。"""
    lower = path.lower()
    if lower.endswith(".md"):
        return "text/markdown"
    if lower.endswith((".py", ".sh", ".js", ".ts", ".json", ".txt", ".yaml", ".yml")):
        return "text/plain"
    return "text/plain"


def _validate_slug(value: str) -> None:
    """校验 slug 不含空白字符与斜杠，违规抛 400。"""
    if any(char.isspace() for char in value) or "/" in value:
        raise HTTPException(
            status_code=400, detail="General skill slug cannot contain spaces or slashes"
        )


def _sse(event: object, data: object) -> str:
    """把事件名与负载序列化为 SSE 帧文本。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
