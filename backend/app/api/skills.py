from __future__ import annotations

import base64
import json
import re
import zipfile
from collections.abc import Iterator
from io import BytesIO
from time import sleep
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.agents.branching import (
    branch_versions,
    ensure_agent_skill_branch,
    ensure_open_gallery_binding,
    ensure_private_resource_binding,
    get_agent,
    hide_open_gallery_binding,
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
    mark_resource_open_gallery,
    project_skill_with_branch,
    require_overall_agent,
    rollback_branch,
    update_branch_skill,
    user_creator_metadata,
    visible_knowledge_base_versions,
    visible_skill_rows,
    visible_tool_rows,
)
from app.async_jobs import enqueue_async_job
from app.db import get_session
from app.db.models import (
    AgentEvent,
    AgentResourceBinding,
    AgentSkillBranchVersion,
    ChannelBinding,
    ChannelIdentity,
    GeneralSkill,
    ModelConfig,
    Skill,
    SkillFeedback,
    SkillVersion,
    Tool,
    User,
    utc_now,
)
from app.llm.model_config_resolver import resolve_model_config_for_runtime
from app.llm import LLMError
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.permissions import (
    ensure_agent_scope_manager,
    ensure_open_gallery_admin,
    require_agent_scope_viewer,
)
from app.security.tenant import ensure_tenant
from app.skills import SkillDistiller, SkillEditor
from app.skills.skill_schema import (
    SkillCard,
    SkillCreateRequest,
    SkillDistillRequest,
    SkillDistillResponse,
    SkillFileExtractRequest,
    SkillFileExtractResponse,
    SkillRead,
    SkillRewriteRequest,
    SkillRewriteResponse,
    SkillVersionRead,
    SkillUpdateRequest,
    skill_card_from_persisted,
)
from app.skills.stream_jobs import SkillStreamEvent, SkillStreamJob, stream_jobs
from app.skills.step_ids import skill_card_with_unique_step_ids
from app.skills.nesting import (
    SopNestingError,
    nested_sop_ids,
    sop_capability_scope,
    validate_sop_nesting,
)

_CHANNEL_LABELS = {"wechat": "微信", "wecom": "企业微信", "feishu": "飞书", "dingtalk": "钉钉"}

router = APIRouter(
    prefix="/api/enterprise/skills",
    tags=["enterprise:skills"],
    dependencies=[Depends(get_current_user)],
)


def skill_read(
    row: Skill,
    stats: dict[str, dict[str, float | int]] | None = None,
    recent_stats: dict[str, dict[str, object]] | None = None,
) -> SkillRead:
    """把 Skill 主表行与统计信息映射为 SkillRead,并附加 agent 分支元数据。"""
    all_stats = stats or {}
    skill_stats = _stats_for(all_stats, row.skill_id, row.version)
    total_stats = all_stats.get(row.skill_id, {})
    recent_skill_stats = (recent_stats or {}).get(row.skill_id, {})
    content, _warnings = skill_card_with_unique_step_ids(
        skill_card_from_persisted(row.content_json)
    )
    branch_meta = getattr(row, "agent_branch_meta", {}) or {}
    return SkillRead(
        id=row.id,
        tenant_id=row.tenant_id,
        skill_id=row.skill_id,
        version=row.version,
        name=row.name,
        business_domain=row.business_domain,
        description=row.description,
        content=content,
        status=row.status,
        call_count=int(skill_stats.get("call_count", 0)),
        positive_feedback_count=int(skill_stats.get("positive_feedback_count", 0)),
        negative_feedback_count=int(skill_stats.get("negative_feedback_count", 0)),
        positive_rate=float(skill_stats.get("positive_rate", 0.0)),
        negative_rate=float(skill_stats.get("negative_rate", 0.0)),
        total_call_count=int(total_stats.get("call_count", 0)),
        total_positive_feedback_count=int(total_stats.get("positive_feedback_count", 0)),
        total_negative_feedback_count=int(total_stats.get("negative_feedback_count", 0)),
        total_positive_rate=float(total_stats.get("positive_rate", 0.0)),
        total_negative_rate=float(total_stats.get("negative_rate", 0.0)),
        recent_versions=list(recent_skill_stats.get("recent_versions", [])),
        recent_call_count=int(recent_skill_stats.get("call_count", 0)),
        recent_positive_feedback_count=int(recent_skill_stats.get("positive_feedback_count", 0)),
        recent_negative_feedback_count=int(recent_skill_stats.get("negative_feedback_count", 0)),
        recent_positive_rate=float(recent_skill_stats.get("positive_rate", 0.0)),
        recent_negative_rate=float(recent_skill_stats.get("negative_rate", 0.0)),
        agent_id=branch_meta.get("agent_id"),
        branch_status=branch_meta.get("status"),
        branch_sync_state=branch_meta.get("sync_state"),
        branch_base_version=branch_meta.get("base_version"),
        branch_head_version=branch_meta.get("head_version"),
        metadata=dict(branch_meta.get("metadata") or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def skill_version_read(
    row: SkillVersion, stats: dict[str, dict[str, float | int]] | None = None
) -> SkillVersionRead:
    """把 SkillVersion 快照行映射为 SkillVersionRead,统计按 skill_version 取。"""
    skill_stats = _stats_for(stats or {}, row.skill_id, row.version)
    content, _warnings = skill_card_with_unique_step_ids(
        skill_card_from_persisted(row.content_json)
    )
    return SkillVersionRead(
        id=row.id,
        tenant_id=row.tenant_id,
        skill_id=row.skill_id,
        version=row.version,
        name=row.name,
        business_domain=row.business_domain,
        description=row.description,
        content=content,
        status=row.status,
        call_count=int(skill_stats.get("call_count", 0)),
        positive_feedback_count=int(skill_stats.get("positive_feedback_count", 0)),
        negative_feedback_count=int(skill_stats.get("negative_feedback_count", 0)),
        positive_rate=float(skill_stats.get("positive_rate", 0.0)),
        negative_rate=float(skill_stats.get("negative_rate", 0.0)),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _branch_version_read(row: AgentSkillBranchVersion) -> SkillVersionRead:
    """把 agent 技能分支版本映射为 SkillVersionRead,统计字段统一填零。"""
    content, _warnings = skill_card_with_unique_step_ids(
        skill_card_from_persisted(row.content_json)
    )
    return SkillVersionRead(
        id=row.id,
        tenant_id=row.tenant_id,
        skill_id=row.skill_id,
        version=row.version,
        name=content.name,
        business_domain=content.business_domain,
        description=content.description,
        content=content,
        status=row.status,
        call_count=0,
        positive_feedback_count=0,
        negative_feedback_count=0,
        positive_rate=0.0,
        negative_rate=0.0,
        agent_id=row.agent_id,
        branch_sync_state=row.sync_state,
        branch_base_version=row.base_version,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.get("", response_model=list[SkillRead], dependencies=[Depends(require_agent_scope_viewer)])
def list_skills(
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = None,
) -> list[SkillRead]:
    """列出当前租户(可按 agent 作用域过滤)可见的 SOP,附带历史与近期统计。"""
    ensure_tenant(db, tenant_id)
    rows = visible_skill_rows(db, tenant_id, agent_id, include_inactive=True)
    stats = _skill_stats(db, tenant_id)
    recent_stats = _recent_skill_stats(db, tenant_id, stats)
    return [skill_read(row, stats, recent_stats) for row in rows]


def _validate_handoff_assignees(db: Session, content: SkillCard, tenant_id: str) -> None:
    """校验 SOP 人工节点的处理人:必须存在、同租户、source='web'(内部成员)。

    assignee_notify_channel 为 None 时按默认投递(网页收件箱,可达则渠道通知);
    为 "web" 时仅网页端;为具体渠道时要求该渠道支持私聊通知(有可用适配器),
    且该成员在租户内任一该渠道 active 员工绑定的作用域下绑定了非群聊渠道身份
    (scope 级可达,跨企业绑定不互通),否则渠道转接不可达。
    """
    assignee_specs = {
        (node.assignee_user_id.strip(), str(node.assignee_notify_channel or "").strip())
        for node in content.nodes
        if node.assignee_user_id and node.assignee_user_id.strip()
    }
    if not assignee_specs:
        return
    assignee_ids = {user_id for user_id, _ in assignee_specs}
    rows = db.exec(
        select(User).where(
            User.tenant_id == tenant_id,
            User.id.in_(assignee_ids),
        )
    ).all()
    found_ids = {row.id for row in rows}
    internal_ids = {row.id for row in rows if row.source == "web"}
    missing = assignee_ids - found_ids
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"人工节点处理人不存在或不在当前租户: {', '.join(sorted(missing))}",
        )
    non_internal = assignee_ids - internal_ids
    if non_internal:
        raise HTTPException(
            status_code=400,
            detail=f"人工节点处理人必须是内部成员(web 账号),不可使用渠道客户或群聊虚拟账号: {', '.join(sorted(non_internal))}",
        )
    channel_specs = {
        (user_id, channel)
        for user_id, channel in assignee_specs
        if channel and channel != "web"
    }
    if not channel_specs:
        return
    # 渠道转接通知要求渠道支持主动私聊(飞书/企微);钉钉/微信适配器只能回
    # 会话内消息,无法私聊处理人,其余渠道一律拒绝,避免"配置成功但收不到通知"。
    from app.channels.service_outbox import HANDOFF_NOTIFY_CHANNELS

    unsupported = sorted({channel for _, channel in channel_specs if channel not in HANDOFF_NOTIFY_CHANNELS})
    if unsupported:
        labels = {name: _CHANNEL_LABELS.get(name, name) for name in unsupported}
        unsupported_text = ", ".join(f"{labels[name]}({name})" for name in unsupported)
        raise HTTPException(
            status_code=400,
            detail=f"人工节点处理人通知渠道暂不支持私聊通知(当前支持飞书/企业微信): {unsupported_text}",
        )
    channel_user_ids = {user_id for user_id, _ in channel_specs}
    # scope 级可达性:成员身份必须挂在租户内该渠道某个 active 员工绑定的
    # external_account_scope 下(跨企业/跨应用绑定不互通,不能仅按"绑定过该渠道"判断)。
    from app.channels.service_identity import external_account_scope as _binding_scope

    reachable_by_user: dict[str, set[str]] = {}
    for channel in {channel for _, channel in channel_specs}:
        bindings = [
            binding
            for binding in db.exec(
                select(ChannelBinding).where(
                    ChannelBinding.tenant_id == tenant_id,
                    ChannelBinding.channel == channel,
                    ChannelBinding.status == "active",
                )
            ).all()
            if not binding.team_id
        ]
        if not bindings:
            continue
        scopes = {_binding_scope(db, binding) for binding in bindings}
        identities = db.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.tenant_id == tenant_id,
                ChannelIdentity.channel == channel,
                ChannelIdentity.staffdeck_user_id.in_(channel_user_ids),
                ~ChannelIdentity.external_user_id.startswith("group:"),
            )
        ).all()
        for identity in identities:
            if identity.external_account_scope in scopes:
                reachable_by_user.setdefault(identity.staffdeck_user_id, set()).add(channel)
    for user_id, channel in sorted(channel_specs):
        if channel not in reachable_by_user.get(user_id, set()):
            label = _CHANNEL_LABELS.get(channel, channel)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"人工节点处理人在租户内可用的{label}绑定作用域下未绑定渠道身份,"
                    f"无法按该渠道转接: {user_id}"
                ),
            )


@router.post("", response_model=SkillRead)
def create_skill(
    request: SkillCreateRequest,
    agent_id: str | None = None,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """创建 SOP:校验处理人、子 SOP 嵌套与 skill_id 唯一性后落库并生成版本快照。

    未指定 agent 或指定整体 agent 时走开放画廊(需管理员),否则建立私有资源绑定
    与该 agent 的技能分支。
    """
    ensure_tenant(db, request.tenant_id)
    existing = db.exec(
        select(Skill).where(
            Skill.tenant_id == request.tenant_id, Skill.skill_id == request.content.skill_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Skill ID already exists for this tenant")
    normalized_content, _warnings = skill_card_with_unique_step_ids(request.content)
    content = normalized_content.model_dump()
    _validate_handoff_assignees(db, normalized_content, request.tenant_id)
    agent = ensure_agent_scope_manager(db, request.tenant_id, agent_id, current_user)
    try:
        validate_sop_nesting(
            normalized_content.skill_id,
            content,
            visible_skill_rows(
                db,
                request.tenant_id,
                agent.id if agent else None,
                include_inactive=True,
            ),
        )
    except SopNestingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = Skill(
        tenant_id=request.tenant_id,
        skill_id=normalized_content.skill_id,
        version=normalized_content.version,
        name=normalized_content.name,
        business_domain=normalized_content.business_domain,
        description=normalized_content.description,
        content_json=content,
        status=request.status,
    )
    db.add(row)
    db.flush()
    _sync_skill_tool_bindings(db, request.tenant_id, row.skill_id, row.content_json)
    branch = None
    binding_status = "active" if request.status == "published" else "inactive"
    creator_metadata = user_creator_metadata(current_user)
    if agent and not agent.is_overall:
        ensure_private_resource_binding(
            db,
            request.tenant_id,
            agent.id,
            "skill",
            row.id,
            binding_status,
            metadata_json=creator_metadata,
        )
        branch = ensure_agent_skill_branch(
            db,
            request.tenant_id,
            agent.id,
            row,
            metadata_json=creator_metadata,
        )
    else:
        ensure_open_gallery_admin(request.tenant_id, current_user)
        mark_resource_open_gallery(row, creator_metadata)
        ensure_open_gallery_binding(
            db,
            request.tenant_id,
            "skill",
            row.id,
            binding_status,
            metadata_json=creator_metadata,
        )
    db.commit()
    db.refresh(row)
    _upsert_skill_version(db, row)
    stats = _skill_stats(db, request.tenant_id)
    if branch:
        row = project_skill_with_branch(row, branch, binding_status)
    return skill_read(row, stats, _recent_skill_stats(db, request.tenant_id, stats))


@router.get(
    "/{skill_id}", response_model=SkillRead, dependencies=[Depends(require_agent_scope_viewer)]
)
def get_skill(
    skill_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    db: Session = Depends(get_session),
) -> SkillRead:
    """读取单个 SOP 的卡片与统计;不可见时按 404 处理,不泄露存在性。"""
    row = _get_visible_skill_for_scope(db, tenant_id, skill_id, agent_id)
    stats = _skill_stats(db, tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, tenant_id, stats))


@router.put("/{skill_id}", response_model=SkillRead)
def update_skill(
    skill_id: str,
    request: SkillUpdateRequest,
    agent_id: str | None = None,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """改写 SOP 内容:私有 agent 作用域写分支版本,整体 SOP 直接覆写主表行。

    两条路径都会重新校验人工节点处理人与子 SOP 嵌套,并同步工具白名单。
    """
    if request.content.skill_id != skill_id:
        raise HTTPException(status_code=400, detail="SOP skill_id cannot be modified")
    row = _get_skill(db, request.tenant_id, skill_id)
    normalized_content, _warnings = skill_card_with_unique_step_ids(request.content)
    _validate_handoff_assignees(db, normalized_content, request.tenant_id)
    agent = ensure_agent_scope_manager(db, request.tenant_id, agent_id, current_user)
    try:
        validate_sop_nesting(
            normalized_content.skill_id,
            normalized_content.model_dump(),
            visible_skill_rows(
                db,
                request.tenant_id,
                agent.id if agent else None,
                include_inactive=True,
            ),
        )
    except SopNestingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent and not agent.is_overall:
        binding = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == request.tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == "skill",
                AgentResourceBinding.resource_id == row.id,
                AgentResourceBinding.status != "deleted",
            )
        ).first()
        if not binding:
            raise HTTPException(status_code=404, detail="Skill not visible to this agent")
        branch = update_branch_skill(
            db,
            request.tenant_id,
            agent.id,
            row,
            normalized_content.model_dump(),
            "技能分支改写",
        )
        _sync_skill_tool_bindings(
            db,
            request.tenant_id,
            row.skill_id,
            normalized_content.model_dump(),
        )
        db.commit()
        projected = project_skill_with_branch(row, branch, binding.status)
        stats = _skill_stats(db, request.tenant_id)
        return skill_read(projected, stats, _recent_skill_stats(db, request.tenant_id, stats))
    ensure_open_gallery_admin(request.tenant_id, current_user)
    row.version = normalized_content.version
    row.name = normalized_content.name
    row.business_domain = normalized_content.business_domain
    row.description = normalized_content.description
    row.content_json = normalized_content.model_dump()
    _sync_skill_tool_bindings(db, request.tenant_id, row.skill_id, row.content_json)
    if request.status:
        row.status = request.status
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    _upsert_skill_version(db, row)
    stats = _skill_stats(db, request.tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, request.tenant_id, stats))


@router.post("/{skill_id}/publish", response_model=SkillRead)
def publish_skill(
    skill_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """发布 SOP:整体 SOP 置为 published 并激活开放画廊绑定,私有分支置为 active。

    发布前按当前生效内容重新校验子 SOP 嵌套,非法嵌套返回 400。
    """
    row = _get_skill(db, tenant_id, skill_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    validation_content = (
        ensure_agent_skill_branch(db, tenant_id, agent.id, row).content_json
        if agent and not agent.is_overall
        else row.content_json
    )
    try:
        validate_sop_nesting(
            row.skill_id,
            validation_content,
            visible_skill_rows(
                db,
                tenant_id,
                agent.id if agent else None,
                include_inactive=True,
            ),
        )
    except SopNestingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent and not agent.is_overall:
        branch = ensure_agent_skill_branch(db, tenant_id, agent.id, row)
        branch.status = "active"
        branch.updated_at = utc_now()
        db.add(branch)
        _sync_skill_tool_bindings(db, tenant_id, row.skill_id, branch.content_json)
        ensure_private_resource_binding(db, tenant_id, agent.id, "skill", row.id, "active")
        db.commit()
        projected = project_skill_with_branch(row, branch, "active")
        stats = _skill_stats(db, tenant_id)
        return skill_read(projected, stats, _recent_skill_stats(db, tenant_id, stats))
    ensure_open_gallery_admin(tenant_id, current_user)
    row.status = "published"
    _sync_skill_tool_bindings(db, tenant_id, row.skill_id, row.content_json)
    mark_resource_open_gallery(row)
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(db, tenant_id, "skill", row.id, "active")
    db.commit()
    db.refresh(row)
    _upsert_skill_version(db, row)
    stats = _skill_stats(db, tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, tenant_id, stats))


@router.post("/{skill_id}/archive", response_model=SkillRead)
def archive_skill(
    skill_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """归档 SOP:整体 SOP 置为 archived,私有 agent 分支置为 inactive。"""
    row = _get_skill(db, tenant_id, skill_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        branch = ensure_agent_skill_branch(db, tenant_id, agent.id, row)
        branch.status = "inactive"
        branch.updated_at = utc_now()
        db.add(branch)
        ensure_private_resource_binding(db, tenant_id, agent.id, "skill", row.id, "inactive")
        db.commit()
        projected = project_skill_with_branch(row, branch, "inactive")
        stats = _skill_stats(db, tenant_id)
        return skill_read(projected, stats, _recent_skill_stats(db, tenant_id, stats))
    ensure_open_gallery_admin(tenant_id, current_user)
    row.status = "archived"
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(db, tenant_id, "skill", row.id, "inactive")
    db.commit()
    db.refresh(row)
    _upsert_skill_version(db, row)
    stats = _skill_stats(db, tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, tenant_id, stats))


@router.post("/{skill_id}/draft", response_model=SkillRead)
def draft_skill(
    skill_id: str,
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """把整体 SOP 退回草稿状态;私有 agent 分支调用直接返回 403。"""
    row = _get_skill(db, tenant_id, skill_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        raise HTTPException(status_code=403, detail="Only overall SOPs can be moved to draft")
    ensure_open_gallery_admin(tenant_id, current_user)
    row.status = "draft"
    row.updated_at = utc_now()
    db.add(row)
    db.flush()
    ensure_open_gallery_binding(db, tenant_id, "skill", row.id, "inactive")
    db.commit()
    db.refresh(row)
    _upsert_skill_version(db, row)
    stats = _skill_stats(db, tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, tenant_id, stats))


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = None,
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除 SOP:私有 agent 作用域只把绑定与分支标记 deleted,整体范围物理删除。

    整体删除会连带清理该 SOP 的反馈与历史版本行,返回最终处理状态。
    """
    row = _get_skill(db, tenant_id, skill_id)
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        binding = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == "skill",
                AgentResourceBinding.resource_id == row.id,
            )
        ).first()
        if not binding:
            binding = AgentResourceBinding(
                tenant_id=tenant_id,
                agent_id=agent.id,
                resource_type="skill",
                resource_id=row.id,
                status="deleted",
            )
        else:
            binding.status = "deleted"
            binding.updated_at = utc_now()
        branch = ensure_agent_skill_branch(db, tenant_id, agent.id, row)
        branch.status = "deleted"
        branch.updated_at = utc_now()
        db.add(binding)
        db.add(branch)
        db.commit()
        return {"status": "hidden"}
    if agent and agent.is_overall:
        if not is_open_gallery_resource(db, tenant_id, "skill", row):
            raise HTTPException(status_code=404, detail="Skill not visible in open gallery")
        ensure_open_gallery_admin(tenant_id, current_user)
        hide_open_gallery_binding(db, tenant_id, "skill", row.id)
        db.commit()
        return {"status": "hidden"}

    require_overall_agent(db, tenant_id, agent_id)
    ensure_open_gallery_admin(tenant_id, current_user)
    feedback_rows = db.exec(
        select(SkillFeedback).where(
            SkillFeedback.tenant_id == tenant_id,
            SkillFeedback.skill_id == skill_id,
        )
    ).all()
    for feedback in feedback_rows:
        db.delete(feedback)
    version_rows = db.exec(
        select(SkillVersion).where(
            SkillVersion.tenant_id == tenant_id, SkillVersion.skill_id == skill_id
        )
    ).all()
    for version_row in version_rows:
        db.delete(version_row)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


@router.get(
    "/{skill_id}/versions",
    response_model=list[SkillVersionRead],
    dependencies=[Depends(require_agent_scope_viewer)],
)
def list_skill_versions(
    skill_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = None,
) -> list[SkillVersionRead]:
    """列出 SOP 历史版本;私有 agent 作用域只返回该 agent 的分支版本。"""
    row = _get_visible_skill_for_scope(db, tenant_id, skill_id, agent_id)
    agent = get_agent(db, tenant_id, agent_id)
    if agent and not agent.is_overall:
        rows = branch_versions(db, tenant_id, agent.id, skill_id)
        return [_branch_version_read(row) for row in rows]
    current_snapshot = db.exec(
        select(SkillVersion).where(
            SkillVersion.tenant_id == tenant_id,
            SkillVersion.skill_id == skill_id,
            SkillVersion.version == row.version,
        )
    ).first()
    if not current_snapshot:
        _upsert_skill_version(db, row)
    rows = db.exec(
        select(SkillVersion)
        .where(SkillVersion.tenant_id == tenant_id, SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.created_at.desc())
    ).all()
    stats = _skill_stats(db, tenant_id)
    return [skill_version_read(version_row, stats) for version_row in rows]


@router.get(
    "/{skill_id}/versions/{version}",
    response_model=SkillVersionRead,
    dependencies=[Depends(require_agent_scope_viewer)],
)
def get_skill_version(
    skill_id: str,
    version: str,
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    db: Session = Depends(get_session),
) -> SkillVersionRead:
    """按版本号读取单个 SOP 版本;私有 agent 作用域从该 agent 的分支版本中查找。"""
    _get_visible_skill_for_scope(db, tenant_id, skill_id, agent_id)
    agent = get_agent(db, tenant_id, agent_id)
    if agent and not agent.is_overall:
        row = next(
            (
                item
                for item in branch_versions(db, tenant_id, agent.id, skill_id)
                if item.version == version
            ),
            None,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Skill version not found")
        return _branch_version_read(row)
    row = _get_skill_version(db, tenant_id, skill_id, version)
    return skill_version_read(row, _skill_stats(db, tenant_id))


@router.delete("/{skill_id}/versions/{version}")
def delete_skill_version(
    skill_id: str,
    version: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除指定历史版本;当前活跃版本不允许删除,返回 409。"""
    skill = _get_skill(db, tenant_id, skill_id)
    ensure_open_gallery_admin(tenant_id, current_user)
    if skill.version == version:
        raise HTTPException(status_code=409, detail="Cannot delete the active skill version")
    row = _get_skill_version(db, tenant_id, skill_id, version)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


@router.post("/{skill_id}/versions/{version}/rollback", response_model=SkillRead)
def rollback_skill_version(
    skill_id: str,
    version: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    agent_id: str | None = None,
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    """把 SOP 回滚到指定历史版本:私有 agent 回滚分支,整体范围覆写主表行。"""
    agent = ensure_agent_scope_manager(db, tenant_id, agent_id, current_user)
    if agent and not agent.is_overall:
        branch = rollback_branch(db, tenant_id, agent.id, skill_id, version)
        db.commit()
        skill = _get_skill(db, tenant_id, skill_id)
        projected = project_skill_with_branch(skill, branch)
        stats = _skill_stats(db, tenant_id)
        return skill_read(projected, stats, _recent_skill_stats(db, tenant_id, stats))
    ensure_open_gallery_admin(tenant_id, current_user)
    row = _get_skill(db, tenant_id, skill_id)
    version_row = _get_skill_version(db, tenant_id, skill_id, version)
    normalized_content, _warnings = skill_card_with_unique_step_ids(
        skill_card_from_persisted(version_row.content_json)
    )
    normalized_content = normalized_content.model_copy(
        update={
            "version": version_row.version,
            "name": version_row.name,
            "business_domain": version_row.business_domain,
            "description": version_row.description or normalized_content.description,
        }
    )
    row.version = version_row.version
    row.name = version_row.name
    row.business_domain = version_row.business_domain
    row.description = version_row.description
    row.content_json = normalized_content.model_dump()
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    stats = _skill_stats(db, tenant_id)
    return skill_read(row, stats, _recent_skill_stats(db, tenant_id, stats))


@router.post("/files/extract", response_model=SkillFileExtractResponse)
def extract_skill_file(request: SkillFileExtractRequest) -> SkillFileExtractResponse:
    """解析上传的 SOP 文件(md/txt/docx/doc)并返回纯文本,非 base64 或超 5MB 报错。"""
    try:
        data = base64.b64decode(request.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file content") from exc
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is too large")
    text = _extract_uploaded_skill_file(request.filename, data)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No readable text found in file")
    return SkillFileExtractResponse(filename=request.filename, text=text)


@router.post("/distill", response_model=SkillDistillResponse)
def distill_skill(
    request: SkillDistillRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillDistillResponse:
    """同步调用 SkillDistiller 蒸馏新 SOP,LLM 调用失败以 502 暴露。"""
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_distill_agent_scope(db, request, current_user)
    model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
    request = _with_available_context_for_distill(db, request)
    try:
        return SkillDistiller().distill(request, model_config)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/distill/stream")
def distill_skill_stream(
    request: SkillDistillRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """以 SSE 流式返回 SOP 蒸馏过程:先建后台任务,再持续推送该任务事件。"""
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_distill_agent_scope(db, request, current_user)
    job_id = _start_distill_stream_job(request, current_user)
    return StreamingResponse(_stream_skill_job(job_id), media_type="text/event-stream")


@router.post("/{skill_id}/rewrite/stream")
def rewrite_skill_stream(
    skill_id: str,
    request: SkillRewriteRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """以 SSE 流式返回 SOP 改写过程,路径 skill_id 必须与请求体 current_skill 一致。"""
    if request.current_skill.skill_id != skill_id:
        raise HTTPException(
            status_code=400, detail="Path skill_id must match current_skill.skill_id"
        )
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_rewrite_agent_scope(db, request, current_user)
    request = _with_available_context_for_rewrite(db, request)
    job_id = _start_rewrite_stream_job(skill_id, request, current_user)
    return StreamingResponse(_stream_skill_job(job_id), media_type="text/event-stream")


@router.post("/distill/jobs")
def create_distill_job(
    request: SkillDistillRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """创建 SOP 蒸馏后台任务并返回 job_id,供前端轮询或订阅事件流。"""
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_distill_agent_scope(db, request, current_user)
    return {"job_id": _start_distill_stream_job(request, current_user)}


@router.post("/{skill_id}/rewrite/jobs")
def create_rewrite_job(
    skill_id: str,
    request: SkillRewriteRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """创建 SOP 改写后台任务并返回 job_id,路径 skill_id 需与请求体一致。"""
    if request.current_skill.skill_id != skill_id:
        raise HTTPException(
            status_code=400, detail="Path skill_id must match current_skill.skill_id"
        )
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_rewrite_agent_scope(db, request, current_user)
    request = _with_available_context_for_rewrite(db, request)
    return {"job_id": _start_rewrite_stream_job(skill_id, request, current_user)}


@router.get("/jobs/{job_id}")
def get_skill_stream_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """读取流式任务的状态与最后一个事件序号,仅任务所有者可访问。"""
    job = _owned_stream_job(job_id, current_user)
    return {
        "job_id": job.id,
        "name": job.name,
        "status": job.status,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "last_seq": job.events[-1].seq if job.events else 0,
    }


@router.get("/jobs/{job_id}/stream")
def stream_existing_skill_job(
    job_id: str,
    after_seq: int = Query(0),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """从 after_seq 起续传已有流式任务的事件,用于页面断线重连。"""
    _owned_stream_job(job_id, current_user)
    return StreamingResponse(_stream_skill_job(job_id, after_seq), media_type="text/event-stream")


@router.post("/jobs/{job_id}/cancel")
def cancel_skill_stream_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """请求取消流式生成,并向事件流追加一条停止提示;取消是协作式的,不等待退出。"""
    _owned_stream_job(job_id, current_user)
    stream_jobs.cancel(job_id)
    stream_jobs.append(job_id, "status", {"text": "已请求停止生成"})
    return {"status": "cancel_requested", "job_id": job_id}


@router.post("/{skill_id}/rewrite", response_model=SkillRewriteResponse)
def rewrite_skill(
    skill_id: str,
    request: SkillRewriteRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillRewriteResponse:
    """同步调用 SkillEditor 改写 SOP,LLM 或校验错误统一返回 502。"""
    if request.current_skill.skill_id != skill_id:
        raise HTTPException(
            status_code=400, detail="Path skill_id must match current_skill.skill_id"
        )
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_rewrite_agent_scope(db, request, current_user)
    model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
    request = _with_available_context_for_rewrite(db, request)
    try:
        return SkillEditor().rewrite(request, model_config)
    except (LLMError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _owned_stream_job(job_id: str, current_user: User) -> SkillStreamJob:
    """取出属于当前用户的流式任务;不存在或跨租户、跨用户一律返回 404。"""
    job = stream_jobs.get(job_id)
    if not job or job.tenant_id != current_user.tenant_id or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _start_distill_stream_job(request: SkillDistillRequest, current_user: User) -> str:
    """创建蒸馏任务并投递异步执行作业,返回任务 id 供 SSE 与轮询使用。"""
    job = stream_jobs.create("skill.distill", request.tenant_id, current_user.id)
    stream_jobs.append(job.id, "job_started", {"job_id": job.id, "name": job.name})
    enqueue_async_job(
        "skill.distill_stream",
        _run_distill_stream_job,
        job.id,
        request.model_dump(mode="json"),
        metadata={"tenant_id": request.tenant_id, "job_id": job.id},
    )
    return job.id


def _start_rewrite_stream_job(
    skill_id: str, request: SkillRewriteRequest, current_user: User
) -> str:
    """创建改写任务并投递异步执行作业,返回任务 id 供 SSE 与轮询使用。"""
    job = stream_jobs.create("skill.rewrite", request.tenant_id, current_user.id)
    stream_jobs.append(
        job.id, "job_started", {"job_id": job.id, "name": job.name, "skill_id": skill_id}
    )
    enqueue_async_job(
        "skill.rewrite_stream",
        _run_rewrite_stream_job,
        job.id,
        skill_id,
        request.model_dump(mode="json"),
        metadata={"tenant_id": request.tenant_id, "job_id": job.id, "skill_id": skill_id},
    )
    return job.id


def _run_distill_stream_job(job_id: str, request_data: dict[str, object]) -> None:
    """后台执行蒸馏任务:把模型流式输出逐条写入事件流,收到取消即提前结束。

    异常统一转为任务失败状态,避免订阅端无限等待;结果本身通过事件流下发。
    """
    stream_jobs.start(job_id)
    try:
        request = SkillDistillRequest.model_validate(request_data)
        with Session(get_session_engine()) as db:
            ensure_tenant(db, request.tenant_id)
            model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
            enriched_request = _with_available_context_for_distill(db, request)
            stream_jobs.append(job_id, "status", {"text": "正在调用模型生成新技能"})
            for item in SkillDistiller().stream_text(enriched_request, model_config):
                if stream_jobs.is_cancelled(job_id):
                    stream_jobs.append(job_id, "status", {"text": "已停止生成"})
                    stream_jobs.complete(job_id)
                    return
                stream_jobs.append(job_id, str(item["event"]), dict(item["data"]))
        stream_jobs.complete(job_id)
    except Exception as exc:  # noqa: BLE001 - expose stable job failure to UI.
        stream_jobs.fail(job_id, str(exc))


def _run_rewrite_stream_job(job_id: str, skill_id: str, request_data: dict[str, object]) -> None:
    """后台执行改写任务:先校验路径 skill_id 与请求体一致,再逐条推送模型事件。

    异常统一转为任务失败状态,避免订阅端无限等待;结果本身通过事件流下发。
    """
    stream_jobs.start(job_id)
    try:
        request = SkillRewriteRequest.model_validate(request_data)
        if request.current_skill.skill_id != skill_id:
            raise ValueError("Path skill_id must match current_skill.skill_id")
        with Session(get_session_engine()) as db:
            ensure_tenant(db, request.tenant_id)
            model_config = _get_request_model(db, request.tenant_id, request.model_config_id)
            stream_jobs.append(job_id, "status", {"text": "正在调用模型分析改写要求"})
            for item in SkillEditor().stream_text(request, model_config):
                if stream_jobs.is_cancelled(job_id):
                    stream_jobs.append(job_id, "status", {"text": "已停止改写"})
                    stream_jobs.complete(job_id)
                    return
                stream_jobs.append(job_id, str(item["event"]), dict(item["data"]))
        stream_jobs.complete(job_id)
    except Exception as exc:  # noqa: BLE001 - expose stable job failure to UI.
        stream_jobs.fail(job_id, str(exc))


def _stream_skill_job(job_id: str, after_seq: int = 0) -> Iterator[str]:
    """生成 SSE 事件流:先补发 job_attached,再增量推送任务事件直到终态。

    以轮询读取任务快照(间隔 0.15s)实现;任务丢失时发送 error 并结束。
    """
    last_seq = max(0, after_seq)
    yield _sse("job_attached", {"job_id": job_id, "after_seq": after_seq})
    while True:
        job, events = stream_jobs.snapshot(job_id, last_seq)
        if not job:
            yield _sse("error", {"message": "Job not found"})
            return
        for event in events:
            last_seq = event.seq
            yield _sse_event(event, job_id)
        if job.status in {"succeeded", "failed"} and not events:
            yield _sse("job_complete", {"job_id": job_id, "status": job.status, "error": job.error})
            return
        sleep(0.15)


def _sse_event(event: SkillStreamEvent, job_id: str) -> str:
    """把单个流事件序列化为 SSE 文本,附带 job_id 与 seq 便于断线续传。"""
    data = {"job_id": job_id, "seq": event.seq, **event.data}
    return _sse(event.event, data)


def get_session_engine():
    """延迟导入并返回全局数据库引擎,避免模块导入期即建立连接。"""
    from app.db import engine

    return engine


def _get_default_model(db: Session, tenant_id: str) -> ModelConfig:
    """取租户内启用且标记为默认的模型配置,不存在时报 400。"""
    model_config = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    if not model_config:
        raise HTTPException(status_code=400, detail="No enabled default model config")
    return _model_runtime_config(db, tenant_id, model_config)


def _model_runtime_config(db: Session, tenant_id: str, row: ModelConfig):
    """把模型配置行解析为运行时可用的配置(含凭据解析等运行时字段)。"""
    return resolve_model_config_for_runtime(db, tenant_id, row.id)


def _get_request_model(
    db: Session, tenant_id: str, model_config_id: str | None = None
) -> ModelConfig:
    """按 model_config_id 取租户内启用的模型配置,缺省时回退到默认模型。"""
    if not model_config_id:
        return _get_default_model(db, tenant_id)
    model_config = db.get(ModelConfig, model_config_id)
    if not model_config or model_config.tenant_id != tenant_id or not model_config.enabled:
        raise HTTPException(status_code=404, detail="Model config not found")
    return _model_runtime_config(db, tenant_id, model_config)


def _sync_skill_tool_bindings(
    db: Session,
    tenant_id: str,
    skill_id: str,
    content: dict[str, object],
) -> None:
    """把 SOP 中 call_tool:<name> 引用的工具补进各自的 allowed_skills_json 白名单。

    只追加缺失的 skill_id,不改动工具其它字段;不提交事务,由调用方决定。
    """
    tool_names: set[str] = set()
    for key in ("nodes", "steps"):
        items = content.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            actions = item.get("allowed_actions")
            if not isinstance(actions, list):
                continue
            for action in actions:
                value = str(action or "").strip()
                if not value.startswith("call_tool:"):
                    continue
                tool_name = value.split(":", 1)[1].strip()
                if tool_name:
                    tool_names.add(tool_name)
    if not tool_names:
        return

    rows = db.exec(
        select(Tool).where(
            Tool.tenant_id == tenant_id,
            Tool.name.in_(sorted(tool_names)),
        )
    ).all()
    for row in rows:
        allowed_skills = [
            str(item)
            for item in (row.allowed_skills_json or [])
            if str(item).strip()
        ]
        if skill_id in allowed_skills:
            continue
        row.allowed_skills_json = [*allowed_skills, skill_id]
        row.updated_at = utc_now()
        db.add(row)


def _ensure_distill_agent_scope(
    db: Session,
    request: SkillDistillRequest,
    current_user: User,
) -> None:
    """请求带 agent_id 时校验当前用户是该 agent 的作用域管理者,否则直接放行。"""
    if request.agent_id:
        ensure_agent_scope_manager(db, request.tenant_id, request.agent_id, current_user)


def _visible_general_skill_rows_for_distill(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
) -> list[GeneralSkill]:
    """筛选蒸馏可用的通用技能:整体范围取开放画廊,私有范围取该 agent 的有效绑定。"""
    agent = get_agent(db, tenant_id, agent_id)
    rows = db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id,
            GeneralSkill.status == "published",
        )
    ).all()
    if agent_id and not agent:
        return []
    if not agent or agent.is_overall:
        return [
            row
            for row in rows
            if is_open_gallery_resource(db, tenant_id, "general_skill", row)
        ]
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.status == "active",
        )
    ).all()
    rows_by_id = {row.id: row for row in rows}
    return [
        row
        for binding in bindings
        if (row := rows_by_id.get(binding.resource_id)) is not None
        and is_bound_resource_visible_for_agent(
            db,
            tenant_id,
            "general_skill",
            row,
            binding,
        )
    ]


def _with_available_context_for_distill(
    db: Session,
    request: SkillDistillRequest,
) -> SkillDistillRequest:
    """把租户内可见的工具、通用技能与知识库合并进蒸馏请求上下文。

    请求自带的候选项与库内数据按 id/name 等键去重,保证模型看到统一的能力目录。
    """
    tools = visible_tool_rows(
        db,
        request.tenant_id,
        request.agent_id,
        include_inactive=False,
    )
    available_tools = _dedupe_capability_catalog(
        [
            *request.available_tools,
            *[
                {
                    "id": tool.id,
                    "name": tool.name,
                    "display_name": tool.display_name,
                    "description": tool.description,
                    "bucket": tool.bucket or "未分桶",
                    "method": tool.method,
                    "url": tool.url,
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                }
                for tool in tools
            ],
        ],
        ("id", "name"),
    )
    general_skills = _visible_general_skill_rows_for_distill(
        db,
        request.tenant_id,
        request.agent_id,
    )
    available_general_skills = _dedupe_capability_catalog(
        [
            *request.available_general_skills,
            *[
                {
                    "id": skill.id,
                    "slug": skill.slug,
                    "name": skill.name,
                    "description": skill.description or "",
                    "capability_scope": skill.capability_scope,
                }
                for skill in general_skills
            ],
        ],
        ("id", "slug"),
    )
    visible_knowledge = visible_knowledge_base_versions(
        db,
        request.tenant_id,
        request.agent_id,
        include_inactive=False,
    )
    available_knowledge_bases = _dedupe_capability_catalog(
        [
            *request.available_knowledge_bases,
            *[
                {
                    "id": knowledge_base_id,
                    "name": version.name,
                    "description": version.description or "",
                    "capability_scope": version.capability_scope,
                }
                for knowledge_base_id, version in visible_knowledge.items()
            ],
        ],
        ("id", "name"),
    )
    return request.model_copy(
        update={
            "available_tools": available_tools,
            "available_general_skills": available_general_skills,
            "available_knowledge_bases": available_knowledge_bases,
        }
    )


def _with_available_tools(db: Session, request: SkillDistillRequest) -> SkillDistillRequest:
    """旧名兼容入口,内部转调 _with_available_context_for_distill。"""

    return _with_available_context_for_distill(db, request)


def _with_available_context_for_rewrite(
    db: Session, request: SkillRewriteRequest
) -> SkillRewriteRequest:
    """为改写请求补齐可用工具与可嵌套 SOP 目录,并标注每个 SOP 不可选的原因。"""
    tools = db.exec(
        select(Tool).where(Tool.tenant_id == request.tenant_id, Tool.enabled == True)  # noqa: E712
    ).all()
    available_tools = _dedupe_capability_catalog(
        [
            *request.available_tools,
            *[
                {
                    "id": tool.id,
                    "name": tool.name,
                    "display_name": tool.display_name,
                    "description": tool.description,
                    "bucket": tool.bucket or "未分桶",
                    "method": tool.method,
                    "url": tool.url,
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                }
                for tool in tools
            ],
        ],
        ("id", "name"),
    )
    rows = visible_skill_rows(
        db,
        request.tenant_id,
        request.agent_id,
        include_inactive=True,
    )
    available_sops = [
        {
            "skill_id": row.skill_id,
            "name": row.name,
            "description": row.description or "",
            "capability_scope": sop_capability_scope(row),
            "status": row.status,
            "nested_sop_ids": nested_sop_ids(row.content_json or {}),
            "selectable": row.skill_id != request.current_skill.skill_id
            and row.status in {"published", "active"},
            "unavailable_reason": (
                "不能调用当前 SOP 自身"
                if row.skill_id == request.current_skill.skill_id
                else "SOP 尚未发布"
                if row.status not in {"published", "active"}
                else None
            ),
            "content": {
                "capability_scope": sop_capability_scope(row),
                "nodes": [
                    {
                        "type": "subflow",
                        "sub_sop_id": node.get("sub_sop_id"),
                    }
                    for node in (row.content_json or {}).get("nodes", [])
                    if isinstance(node, dict) and str(node.get("type") or "") == "subflow"
                ],
            },
        }
        for row in rows
    ]
    return request.model_copy(
        update={
            "available_tools": available_tools,
            "available_sops": available_sops,
        }
    )


def _ensure_rewrite_agent_scope(
    db: Session,
    request: SkillRewriteRequest,
    current_user: User,
) -> None:
    """请求带 agent_id 时校验当前用户是该 agent 的作用域管理者,否则直接放行。"""
    if request.agent_id:
        ensure_agent_scope_manager(db, request.tenant_id, request.agent_id, current_user)


def _dedupe_capability_catalog(
    values: list[dict[str, object]],
    keys: tuple[str, ...],
) -> list[dict[str, object]]:
    """按给定键顺序取首个非空值作为身份标识对能力目录去重,并保持原顺序。"""
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        identity = next((str(item.get(key) or "").strip() for key in keys if item.get(key)), "")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        result.append(item)
    return result


def _sse(event: object, data: object) -> str:
    """把事件名与数据序列化为单条 SSE 帧,JSON 不转义中文。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _extract_uploaded_skill_file(filename: str, data: bytes) -> str:
    """按扩展名分派解析:md/txt 走文本解码,docx 解 XML,doc 做宽松解码。"""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in {"md", "txt"}:
        return _decode_text_bytes(data)
    if suffix == "docx":
        return _extract_docx_text(data)
    if suffix == "doc":
        return _decode_legacy_doc_text(data)
    raise HTTPException(status_code=400, detail="Unsupported file type")


def _decode_text_bytes(data: bytes) -> str:
    """依次尝试 utf-8-sig/utf-8/gb18030 解码,全部失败则忽略非法字节兜底。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _decode_legacy_doc_text(data: bytes) -> str:
    """宽松提取老式 .doc 文本:去控制字符、压缩空白并丢弃空行。"""
    text = _decode_text_bytes(data)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_docx_text(data: bytes) -> str:
    """从 docx 的 word/document.xml 提取段落文本,保留制表符与手动换行。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail="Invalid docx file") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=400, detail="Invalid docx xml") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for element in paragraph.iter():
            if element.tag == f"{namespace}t" and element.text:
                parts.append(element.text)
            elif element.tag == f"{namespace}tab":
                parts.append("\t")
            elif element.tag in {f"{namespace}br", f"{namespace}cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _skill_stats(db: Session, tenant_id: str) -> dict[str, dict[str, float | int]]:
    """按 skill_id 与 skill_id@version 汇总调用次数与正负反馈率。

    调用次数来自 AgentEvent 的 skill_started/skill_resumed 事件;反馈按
    (skill_id, version, session_id, user_id) 聚合,同一会话同一用户只计一票。
    """
    stats: dict[str, dict[str, float | int]] = {}
    events = db.exec(
        select(AgentEvent).where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.event_type.in_(["skill_started", "skill_resumed"]),  # type: ignore[attr-defined]
        )
    ).all()
    for event in events:
        payload = event.payload_json or {}
        skill_id = str(payload.get("to_skill_id") or "")
        if not skill_id:
            continue
        skill_version = (
            str(payload.get("to_skill_version") or payload.get("skill_version") or "") or None
        )
        _increment_call(stats, skill_id, skill_version)

    feedback_rows = db.exec(select(SkillFeedback).where(SkillFeedback.tenant_id == tenant_id)).all()
    flow_feedback: dict[tuple[str, str | None, str, str], set[str]] = {}
    for feedback in feedback_rows:
        skill_version = feedback.skill_version
        flow_key = (feedback.skill_id, skill_version, feedback.session_id, feedback.user_id)
        flow_feedback.setdefault(flow_key, set()).add(feedback.rating)

    for (skill_id, skill_version, _session_id, _user_id), ratings in flow_feedback.items():
        entries = [stats.setdefault(skill_id, _empty_stats())]
        if skill_version:
            entries.append(stats.setdefault(_stats_key(skill_id, skill_version), _empty_stats()))
        for entry in entries:
            if "down" in ratings:
                entry["negative_feedback_count"] = int(entry["negative_feedback_count"]) + 1
            elif "up" in ratings:
                entry["positive_feedback_count"] = int(entry["positive_feedback_count"]) + 1

    for entry in stats.values():
        positive = int(entry["positive_feedback_count"])
        negative = int(entry["negative_feedback_count"])
        calls = int(entry["call_count"])
        entry["positive_rate"] = round(positive / calls, 4) if calls else 0.0
        entry["negative_rate"] = round(negative / calls, 4) if calls else 0.0
    return stats


def _increment_call(
    stats: dict[str, dict[str, float | int]], skill_id: str, version: str | None
) -> None:
    """把一次调用同时累加到 skill_id 汇总与 skill_id@version 明细。"""
    entries = [stats.setdefault(skill_id, _empty_stats())]
    if version:
        entries.append(stats.setdefault(_stats_key(skill_id, version), _empty_stats()))
    for entry in entries:
        entry["call_count"] = int(entry["call_count"]) + 1


def _stats_key(skill_id: str, version: str) -> str:
    """拼接 skill_id@version 形式的版本统计键。"""
    return f"{skill_id}@{version}"


def _stats_for(
    stats: dict[str, dict[str, float | int]], skill_id: str, version: str
) -> dict[str, float | int]:
    """取出指定 skill_id 与版本组合的统计,缺失时返回空字典。"""
    return stats.get(_stats_key(skill_id, version), {})


def _recent_skill_stats(
    db: Session,
    tenant_id: str,
    stats: dict[str, dict[str, float | int]],
) -> dict[str, dict[str, object]]:
    """聚合每个 SOP 最近 3 个版本的调用与反馈统计,供列表页展示近期表现。"""
    recent_versions: dict[str, list[str]] = {}
    version_rows = db.exec(
        select(SkillVersion)
        .where(SkillVersion.tenant_id == tenant_id)
        .order_by(
            SkillVersion.skill_id.asc(), SkillVersion.created_at.desc(), SkillVersion.version.desc()
        )
    ).all()
    for row in version_rows:
        versions = recent_versions.setdefault(row.skill_id, [])
        if len(versions) < 3:
            versions.append(row.version)

    skill_rows = db.exec(select(Skill).where(Skill.tenant_id == tenant_id)).all()
    for row in skill_rows:
        recent_versions.setdefault(row.skill_id, [row.version])

    recent_stats: dict[str, dict[str, object]] = {}
    for skill_id, versions in recent_versions.items():
        entry: dict[str, object] = {
            **_empty_stats(),
            "recent_versions": versions,
        }
        for version in versions:
            version_stats = stats.get(_stats_key(skill_id, version), {})
            entry["call_count"] = int(entry["call_count"]) + int(version_stats.get("call_count", 0))
            entry["positive_feedback_count"] = int(entry["positive_feedback_count"]) + int(
                version_stats.get("positive_feedback_count", 0)
            )
            entry["negative_feedback_count"] = int(entry["negative_feedback_count"]) + int(
                version_stats.get("negative_feedback_count", 0)
            )
        positive = int(entry["positive_feedback_count"])
        negative = int(entry["negative_feedback_count"])
        calls = int(entry["call_count"])
        entry["positive_rate"] = round(positive / calls, 4) if calls else 0.0
        entry["negative_rate"] = round(negative / calls, 4) if calls else 0.0
        recent_stats[skill_id] = entry
    return recent_stats


def _upsert_skill_version(db: Session, row: Skill) -> SkillVersion:
    """按 (tenant_id, skill_id, version) 写入或更新版本快照,并自行提交事务。"""
    existing = db.exec(
        select(SkillVersion).where(
            SkillVersion.tenant_id == row.tenant_id,
            SkillVersion.skill_id == row.skill_id,
            SkillVersion.version == row.version,
        )
    ).first()
    if existing:
        existing.name = row.name
        existing.business_domain = row.business_domain
        existing.description = row.description
        existing.content_json = row.content_json
        existing.status = row.status
        existing.updated_at = utc_now()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    version_row = SkillVersion(
        tenant_id=row.tenant_id,
        skill_id=row.skill_id,
        version=row.version,
        name=row.name,
        business_domain=row.business_domain,
        description=row.description,
        content_json=row.content_json,
        status=row.status,
    )
    db.add(version_row)
    db.commit()
    db.refresh(version_row)
    return version_row


def _empty_stats() -> dict[str, float | int]:
    """返回一份全零统计字典,避免调用方反复处理键缺失。"""
    return {
        "call_count": 0,
        "positive_feedback_count": 0,
        "negative_feedback_count": 0,
        "positive_rate": 0.0,
        "negative_rate": 0.0,
    }


def _get_skill(db: Session, tenant_id: str, skill_id: str) -> Skill:
    """按 tenant_id 与 skill_id 取主表行;租户或 SOP 不存在时抛 HTTPException。"""
    ensure_tenant(db, tenant_id)
    row = db.exec(
        select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == skill_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")
    return row


def _get_visible_skill_for_scope(
    db: Session,
    tenant_id: str,
    skill_id: str,
    agent_id: str | None,
) -> Skill:
    """在当前 agent 作用域可见的 SOP 中按 skill_id 查找,不可见即 404。"""
    row = next(
        (
            item
            for item in visible_skill_rows(db, tenant_id, agent_id, include_inactive=True)
            if item.skill_id == skill_id
        ),
        None,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")
    return row


def _get_skill_version(db: Session, tenant_id: str, skill_id: str, version: str) -> SkillVersion:
    """按 tenant_id、skill_id、version 取版本快照,缺失时报 404。"""
    ensure_tenant(db, tenant_id)
    row = db.exec(
        select(SkillVersion).where(
            SkillVersion.tenant_id == tenant_id,
            SkillVersion.skill_id == skill_id,
            SkillVersion.version == version,
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Skill version not found")
    return row
