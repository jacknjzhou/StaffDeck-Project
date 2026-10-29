from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from time import sleep
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.agents.schema import (
    AgentModelsUpdateRequest,
    AgentAPICredentialCreateRequest,
    AgentAPICredentialCreated,
    AgentAPICredentialRead,
    AgentProfileCreateRequest,
    AgentProfileRead,
    AgentProfileUpdateRequest,
    AgentResourceBindingInput,
    AgentResourceImportRequest,
    AgentResourceBindingRead,
    AgentResourcesUpdateRequest,
    AgentScopeRead,
    AgentSkillRollbackRequest,
    AgentWorkRecordEventRead,
    AgentWorkRecordRead,
    AgentWorkRecordReplyStatsRead,
)
from app.agents.branching import (
    agent_private_metadata,
    branch_versions,
    copy_overall_scope_to_agent,
    ensure_agent_skill_branch,
    ensure_knowledge_base_version,
    get_overall_agent,
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
    promote_branch_to_overall,
    promote_knowledge_branch_to_overall,
    rollback_branch,
    sync_branch_from_overall,
    visible_skill_rows,
)
from app.db import get_session
from app.db.models import (
    APIClient,
    APICredential,
    AgentModelBinding,
    AgentKnowledgeBranch,
    AgentProfile,
    AgentResourceBinding,
    AgentSkillBranch,
    AgentSkillBranchVersion,
    AgentUsage,
    ChannelBinding,
    ChannelBindingAgent,
    ChatSession,
    GeneralSkill,
    HumanHandoffRequest,
    KnowledgeBase,
    KnowledgeBucket,
    KnowledgeChunk,
    KnowledgeDocument,
    Message,
    ModelConfig,
    ScheduledTask,
    Skill,
    TeamMember,
    Tool,
    utc_now,
    User,
)
from app.public_api.auth import generate_api_key
from app.public_api.credential_profiles import (
    AGENT_KEY_ALLOWED_SCOPES,
    agent_access_for_scopes,
    scopes_for_agent_access,
)
from app.security.auth import get_current_user
from app.security.permissions import agent_owned_by_user as _agent_owned_by_user
from app.security.permissions import is_admin_user as _is_admin_user
from app.security.tenant import ensure_tenant
from app.session.cleanup import (
    purge_chat_session_records,
    remove_chat_session_workspace,
)

IMPORT_LOCK_RETRY_ATTEMPTS = 2
IMPORT_LOCK_RETRY_DELAY_SECONDS = 0.5

enterprise_router = APIRouter(prefix="/api/enterprise/agents", tags=["enterprise:agents"])
chat_router = APIRouter(prefix="/api/chat/agents", tags=["chat:agents"])
scope_router = APIRouter(prefix="/api/enterprise/agent-scope", tags=["enterprise:agent-scope"])

STAFFDECK_AGENT_API_CLIENT_NAME = "StaffDeck 员工 API 密钥"


@scope_router.get("", response_model=AgentScopeRead)
def get_agent_scope(
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentScopeRead:
    """返回租户的智能体作用域概览，汇总当前用户可见的员工列表。"""
    ensure_tenant(db, tenant_id)
    _ensure_request_tenant(tenant_id, current_user)
    return AgentScopeRead(tenant_id=tenant_id, agents=list_agents(tenant_id, db, current_user))


@enterprise_router.get("", response_model=list[AgentProfileRead])
def list_agents(
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[AgentProfileRead]:
    """列出当前用户在租户内可见的员工，并附上资源绑定与在用标记。

    非管理员额外能看到整体智能体，它作为开放广场的只读复制来源。
    """
    ensure_tenant(db, tenant_id)
    user = current_user
    _ensure_request_tenant(tenant_id, user)
    rows = db.exec(
        select(AgentProfile)
        .where(AgentProfile.tenant_id == tenant_id)
        .order_by(AgentProfile.is_overall.desc(), AgentProfile.updated_at.desc())
    ).all()
    rows = [row for row in rows if not _agent_hidden_from_staffdeck(row)]
    if not _is_admin_user(user):
        # Non-admin users still need the overall agent as a read-only open-gallery
        # source for copy/use flows. Mutations remain guarded by manage/update
        # endpoints, so this only exposes the source scope.
        rows = [row for row in rows if row.is_overall or _agent_visible_to_user(row, user)]
    bindings = _bindings_by_agent(db, tenant_id)
    used_agent_ids = _used_agent_ids_for_user(db, tenant_id, user)
    return [agent_read(row, bindings.get(row.id, []), row.id in used_agent_ids) for row in rows]


@enterprise_router.post("", response_model=AgentProfileRead)
def create_agent(
    request: AgentProfileCreateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentProfileRead:
    """新建员工档案，可按空白、复制指定员工或继承整体智能体三种方式初始化。

    仅管理员能创建整体智能体；名称为空抛 400，租户内重名抛 409。
    """
    ensure_tenant(db, request.tenant_id)
    user = current_user
    _ensure_request_tenant(request.tenant_id, user)
    if request.is_overall and not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Only administrator can create overall agent")
    name = str(request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Agent name cannot be empty")
    existing = db.exec(
        select(AgentProfile).where(
            AgentProfile.tenant_id == request.tenant_id, AgentProfile.name == name
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Agent name already exists")
    row = AgentProfile(
        tenant_id=request.tenant_id,
        name=name,
        description=request.description,
        persona_prompt=request.persona_prompt,
        is_overall=request.is_overall,
        status="active",
        harness_max_actions=request.harness_max_actions,
        metadata_json=_metadata_with_creator(request.metadata or {}, user),
    )
    db.add(row)
    db.flush()
    if not row.is_overall:
        copy_from_agent_id = request.copy_from_agent_id
        if request.source_mode == "blank":
            pass
        elif copy_from_agent_id:
            source_agent = _get_agent(db, request.tenant_id, copy_from_agent_id)
            _ensure_can_copy_from_agent(source_agent, user)
            if not row.persona_prompt:
                row.persona_prompt = source_agent.persona_prompt
            _copy_agent_scope_from_source(db, request.tenant_id, source_agent, row)
        else:
            overall = get_overall_agent(db, request.tenant_id)
            if overall and not row.persona_prompt:
                row.persona_prompt = overall.persona_prompt
            copy_overall_scope_to_agent(db, request.tenant_id, row)
            if overall:
                _copy_agent_models_from_source(db, request.tenant_id, overall, row)
    db.commit()
    db.refresh(row)
    return agent_read(row, _bindings_by_agent(db, request.tenant_id).get(row.id, []))


@enterprise_router.get("/{agent_id}", response_model=AgentProfileRead)
def get_agent(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentProfileRead:
    """读取单个员工档案及其资源绑定，无访问权限时由访问校验抛 403。"""
    row = _get_agent(db, tenant_id, agent_id)
    _ensure_can_access_agent(row, current_user)
    return agent_read(row, _bindings_by_agent(db, tenant_id).get(row.id, []))


@enterprise_router.get(
    "/{agent_id}/api-credentials",
    response_model=list[AgentAPICredentialRead],
)
def list_agent_api_credentials(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[AgentAPICredentialRead]:
    """列出员工名下的 API 密钥，只返回掩码前缀等非敏感字段。"""
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    rows = db.exec(
        select(APICredential)
        .where(
            APICredential.tenant_id == tenant_id,
            APICredential.agent_id == agent_id,
        )
        .order_by(APICredential.created_at.desc())
    ).all()
    return [_agent_api_credential_read(row) for row in rows]


@enterprise_router.post(
    "/{agent_id}/api-credentials",
    response_model=AgentAPICredentialCreated,
)
def create_agent_api_credential(
    agent_id: str,
    request: AgentAPICredentialCreateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentAPICredentialCreated:
    """为员工签发新的 API 密钥，并仅在本次响应中返回一次明文 token。

    整体智能体不能持有员工密钥；scope 由请求的 access 换算得到。
    """
    agent = _get_agent(db, request.tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    if agent.is_overall:
        raise HTTPException(status_code=400, detail="Open gallery cannot own an employee API key")
    client = _ensure_staffdeck_agent_api_client(db, request.tenant_id, current_user)
    token, prefix, digest = generate_api_key()
    row = APICredential(
        tenant_id=request.tenant_id,
        client_id=client.id,
        agent_id=agent_id,
        name=request.name.strip(),
        key_prefix=prefix,
        key_digest=digest,
        scopes_json=scopes_for_agent_access(request.access),
        expires_at=request.expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return AgentAPICredentialCreated(
        **_agent_api_credential_read(row).model_dump(),
        api_key=token,
    )


@enterprise_router.post(
    "/{agent_id}/api-credentials/{credential_id}/rotate",
    response_model=AgentAPICredentialCreated,
)
def rotate_agent_api_credential(
    agent_id: str,
    credential_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentAPICredentialCreated:
    """轮换员工密钥：覆盖摘要与状态并清除吊销时间，旧明文随即失效。

    新明文只在本次响应返回，之后只能看到前缀。
    """
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    row = _get_agent_api_credential(db, tenant_id, agent_id, credential_id)
    token, prefix, digest = generate_api_key()
    row.key_prefix = prefix
    row.key_digest = digest
    row.status = "active"
    row.revoked_at = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return AgentAPICredentialCreated(
        **_agent_api_credential_read(row).model_dump(),
        api_key=token,
    )


@enterprise_router.post(
    "/{agent_id}/api-credentials/{credential_id}/revoke",
    response_model=AgentAPICredentialRead,
)
def revoke_agent_api_credential(
    agent_id: str,
    credential_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentAPICredentialRead:
    """吊销员工密钥并记录吊销时间，保留记录以便审计。"""
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    row = _get_agent_api_credential(db, tenant_id, agent_id, credential_id)
    row.status = "revoked"
    row.revoked_at = utc_now()
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _agent_api_credential_read(row)


@enterprise_router.get("/{agent_id}/work-record", response_model=AgentWorkRecordRead)
def get_agent_work_record(
    agent_id: str,
    tenant_id: str = Query(...),
    timezone: str = Query("Asia/Shanghai"),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentWorkRecordRead:
    """汇总员工工作记录：对话回复统计、资源挂载与定时任务时间线。

    回复统计只覆盖当前用户与该员工的会话；timezone 非法抛 400，时间按本地日期归组。
    """
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_access_agent(agent, current_user)
    try:
        local_timezone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid timezone") from exc

    now = utc_now()
    reply_rows = db.exec(
        select(Message)
        .join(ChatSession, Message.session_id == ChatSession.id)
        .where(
            Message.tenant_id == tenant_id,
            Message.role == "assistant",
            ChatSession.tenant_id == tenant_id,
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == current_user.id,
        )
        .order_by(Message.created_at.asc())
    ).all()
    by_day: dict[str, int] = {}
    events = [
        AgentWorkRecordEventRead(
            id=f"{message.id}:reply",
            kind="chat",
            phase="reply",
            timestamp=_iso_utc(message.created_at),
            label="对话回复",
        )
        for message in reply_rows
    ]
    for message in reply_rows:
        day = _as_utc(message.created_at).astimezone(local_timezone).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1

    events.extend(_agent_resource_timeline_events(db, tenant_id, agent_id))
    events.extend(_agent_scheduled_task_timeline_events(db, tenant_id, agent_id, current_user))
    events.sort(key=lambda item: (item.timestamp, item.id))
    today = _as_utc(now).astimezone(local_timezone).date().isoformat()
    return AgentWorkRecordRead(
        agent_id=agent_id,
        timezone=timezone,
        generated_at=_iso_utc(now),
        reply_stats=AgentWorkRecordReplyStatsRead(
            total=len(reply_rows),
            today=by_day.get(today, 0),
            by_day=dict(sorted(by_day.items())),
        ),
        events=events,
    )


@enterprise_router.put("/{agent_id}", response_model=AgentProfileRead)
def update_agent(
    agent_id: str,
    request: AgentProfileUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentProfileRead:
    """局部更新员工档案，只写入请求中显式提供的字段。

    名称需非空且租户内唯一；metadata 合并时保留原有创建者与所有者信息。
    """
    row = _get_agent(db, request.tenant_id, agent_id)
    user = current_user
    _ensure_can_manage_agent(row, user)
    if request.name is not None:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Agent name cannot be empty")
        conflict = db.exec(
            select(AgentProfile).where(
                AgentProfile.tenant_id == request.tenant_id,
                AgentProfile.name == name,
                AgentProfile.id != row.id,
            )
        ).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Agent name already exists")
        row.name = name
    if request.description is not None:
        row.description = request.description
    if request.persona_prompt is not None:
        row.persona_prompt = request.persona_prompt
    if request.status is not None:
        row.status = request.status
    if request.harness_max_actions is not None:
        row.harness_max_actions = request.harness_max_actions
    if request.metadata is not None:
        row.metadata_json = _metadata_preserving_creator(
            row.metadata_json or {}, request.metadata, user
        )
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return agent_read(row, _bindings_by_agent(db, request.tenant_id).get(row.id, []))


@enterprise_router.post("/{agent_id}/gallery:unpublish", response_model=AgentProfileRead)
def unpublish_agent_from_gallery(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentProfileRead:
    """将员工从开放广场下架，但不删除员工本身。

    仅管理员可操作；整体智能体不可下架，未上架时原样返回当前档案。
    """
    _ensure_admin_user(tenant_id, current_user)
    row = _get_agent(db, tenant_id, agent_id)
    if row.is_overall:
        raise HTTPException(status_code=400, detail="Overall agent cannot be unpublished")

    metadata = dict(row.metadata_json or {})
    if metadata.get("published_to_gallery") is not True:
        return agent_read(row, _bindings_by_agent(db, tenant_id).get(row.id, []))

    now = utc_now()
    metadata["published_to_gallery"] = False
    metadata["gallery_unpublished_at"] = now.isoformat()
    metadata["gallery_unpublished_by"] = current_user.username
    metadata.pop("gallery_published_at", None)
    metadata.pop("gallery_published_by", None)
    row.metadata_json = metadata
    row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return agent_read(row, _bindings_by_agent(db, tenant_id).get(row.id, []))


@enterprise_router.delete("/{agent_id}")
def delete_agent(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除员工并级联清理其绑定、渠道挂载、会话、团队成员与待办数据。

    整体智能体不可删除；默认渠道挂载被删时提升首个剩余挂载为默认，
    定时任务转为暂停、待处理人工转接取消，最后移除会话工作区。
    """
    row = _get_agent(db, tenant_id, agent_id)
    _ensure_can_manage_agent(row, current_user)
    if row.is_overall:
        raise HTTPException(status_code=400, detail="Overall agent cannot be deleted")
    bindings = db.exec(
        select(AgentResourceBinding).where(AgentResourceBinding.agent_id == row.id)
    ).all()
    for binding in bindings:
        db.delete(binding)
    # 渠道挂载同步清理:否则孤儿挂载行会让 /员工 列出已删除员工的裸 ID,
    # 甚至可被 /切换 路由到。默认挂载被删时把首个剩余挂载提升为默认并同步
    # binding.agent_id,保持存量绑定回退路径有效。会话指针无需处理:
    # resolve_current_agent 发现指针不在挂载集会自动重置默认。
    channel_mounts = db.exec(
        select(ChannelBindingAgent).where(ChannelBindingAgent.agent_id == row.id)
    ).all()
    affected_binding_ids = {mount.binding_id for mount in channel_mounts}
    for mount in channel_mounts:
        db.delete(mount)
    for binding_id in affected_binding_ids:
        channel_binding = db.get(ChannelBinding, binding_id)
        if not channel_binding or channel_binding.agent_id != row.id:
            continue
        remaining = db.exec(
            select(ChannelBindingAgent)
            .where(ChannelBindingAgent.binding_id == binding_id)
            .order_by(ChannelBindingAgent.sort_order, ChannelBindingAgent.created_at)
        ).first()
        if remaining:
            remaining.is_default = True
            channel_binding.agent_id = remaining.agent_id
            channel_binding.updated_at = utc_now()
            db.add(remaining)
            db.add(channel_binding)
    # 会话级联清理:员工删除后其会话若保留,新消息会由租户默认 persona 静默接管,
    # 与删除团队一致,直接清空其全部会话(消息/事件/反馈/Harness 记录与工作区)。
    sessions = db.exec(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id, ChatSession.agent_id == row.id
        )
    ).all()
    workspace_keys = [(session.tenant_id, session.id) for session in sessions]
    for session in sessions:
        purge_chat_session_records(db, session)
    # 团队成员关系同步清理,避免花名册/TL 会话悬挂在已删除员工上。
    memberships = db.exec(select(TeamMember).where(TeamMember.agent_id == row.id)).all()
    for membership in memberships:
        db.delete(membership)
    # 定时任务立即暂停,而不是等到下次触发才失败。
    scheduled_tasks = db.exec(
        select(ScheduledTask).where(
            ScheduledTask.tenant_id == tenant_id,
            ScheduledTask.agent_id == row.id,
            ScheduledTask.status == "active",
        )
    ).all()
    for task in scheduled_tasks:
        task.status = "paused"
        task.next_run_at = None
        task.updated_at = utc_now()
        db.add(task)
    # 待处理人工转接直接取消,避免悬挂在已删员工的收件箱里。
    pending_handoffs = db.exec(
        select(HumanHandoffRequest).where(
            HumanHandoffRequest.tenant_id == tenant_id,
            HumanHandoffRequest.agent_id == row.id,
            HumanHandoffRequest.status == "pending",
        )
    ).all()
    for handoff in pending_handoffs:
        handoff.status = "cancelled"
        handoff.updated_at = utc_now()
        db.add(handoff)
    db.delete(row)
    db.commit()
    for session_tenant_id, session_id in workspace_keys:
        remove_chat_session_workspace(tenant_id=session_tenant_id, session_id=session_id, db=db)
    return {"status": "deleted"}


@enterprise_router.get("/{agent_id}/resources", response_model=list[AgentResourceBindingRead])
def get_agent_resources(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[AgentResourceBindingRead]:
    """列出员工当前绑定的资源，按资源类型与创建时间排序。"""
    _ensure_can_access_agent(_get_agent(db, tenant_id, agent_id), current_user)
    rows = db.exec(
        select(AgentResourceBinding)
        .where(
            AgentResourceBinding.tenant_id == tenant_id, AgentResourceBinding.agent_id == agent_id
        )
        .order_by(AgentResourceBinding.resource_type, AgentResourceBinding.created_at)
    ).all()
    return [binding_read(row) for row in rows]


@enterprise_router.put("/{agent_id}/resources", response_model=list[AgentResourceBindingRead])
def update_agent_resources(
    agent_id: str,
    request: AgentResourcesUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[AgentResourceBindingRead]:
    """按请求列表整体替换员工资源绑定：新增缺失、更新已有、删除未列出项。

    整体智能体使用全局资源池，不可单独配置；资源不存在时抛 404。
    """
    agent = _get_agent(db, request.tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    if agent.is_overall:
        raise HTTPException(status_code=400, detail="Overall agent uses the global resource pool")
    existing = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == request.tenant_id,
            AgentResourceBinding.agent_id == agent_id,
        )
    ).all()
    by_key = {(row.resource_type, row.resource_id): row for row in existing}
    desired_keys: set[tuple[str, str]] = set()
    for item in request.resources:
        _ensure_resource_exists(db, request.tenant_id, item)
        key = (item.resource_type, item.resource_id)
        desired_keys.add(key)
        row = by_key.get(key)
        if row:
            row.status = item.status
            row.metadata_json = item.metadata
            row.updated_at = utc_now()
        else:
            row = AgentResourceBinding(
                tenant_id=request.tenant_id,
                agent_id=agent_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                status=item.status,
                metadata_json=item.metadata,
            )
        db.add(row)
    for key, row in by_key.items():
        if key not in desired_keys:
            db.delete(row)
    db.commit()
    return get_agent_resources(agent_id, request.tenant_id, db, current_user)


@enterprise_router.post("/{agent_id}/resources/import")
def import_agent_resources(
    agent_id: str,
    request: AgentResourceImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """把源员工的指定资源导入目标员工，遇到数据库锁冲突时有限次重试。

    非锁错误或重试耗尽直接上抛；仍在忙时返回 503。
    """
    for attempt in range(IMPORT_LOCK_RETRY_ATTEMPTS):
        try:
            return _import_agent_resources_once(agent_id, request, db, current_user)
        except OperationalError as exc:
            db.rollback()
            if not _is_database_locked_error(exc) or attempt >= IMPORT_LOCK_RETRY_ATTEMPTS - 1:
                raise
            sleep(IMPORT_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
    raise HTTPException(status_code=503, detail="Resource import is temporarily busy")


def _import_agent_resources_once(
    agent_id: str,
    request: AgentResourceImportRequest,
    db: Session,
    current_user: User | object,
) -> dict[str, object]:
    """单次执行资源导入：校验权限与来源可见性后逐条复制或提升分支。

    每条资源返回成功明细或 missing 原因（不存在、源不可见、已禁用），末尾统一提交。
    """
    target_agent = _get_agent(db, request.tenant_id, agent_id)
    source_agent = _get_agent(db, request.tenant_id, request.source_agent_id)
    user = current_user
    _ensure_can_import_to_agent(target_agent, user)
    _ensure_can_copy_from_agent(source_agent, user)
    if source_agent.id == target_agent.id:
        raise HTTPException(status_code=400, detail="Source and target agent cannot be the same")
    resource_ids = _dedupe_ids(request.resource_ids)
    if not resource_ids:
        raise HTTPException(status_code=400, detail="No resources selected")
    imported: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    for identifier in resource_ids:
        resolved = _resolve_resource(db, request.tenant_id, request.resource_type, identifier)
        if not resolved:
            missing.append({"resource_id": identifier, "reason": "resource_not_found"})
            continue
        source_binding = _source_resource_binding(
            db, request.tenant_id, source_agent, request.resource_type, resolved.id
        )
        if not source_agent.is_overall and not source_binding:
            missing.append({"resource_id": identifier, "reason": "not_visible_in_source_agent"})
            continue
        block_reason = _blocked_learning_reason(
            db,
            request.tenant_id,
            source_agent,
            request.resource_type,
            resolved,
            source_binding,
        )
        if block_reason:
            missing.append({"resource_id": identifier, "reason": block_reason})
            continue
        if target_agent.is_overall:
            _import_resource_to_overall(
                db, request.tenant_id, source_agent, request.resource_type, resolved
            )
        else:
            resolved = _upsert_imported_resource_binding(
                db,
                request.tenant_id,
                source_agent,
                target_agent,
                request.resource_type,
                resolved,
                source_binding,
            )
        imported.append(
            {
                "resource_type": request.resource_type,
                "resource_id": resolved.id,
                "display_id": _resource_display_id(request.resource_type, resolved),
                "name": getattr(resolved, "name", getattr(resolved, "slug", resolved.id)),
            }
        )
    db.commit()
    return {
        "status": "imported",
        "target_agent_id": target_agent.id,
        "source_agent_id": source_agent.id,
        "imported": imported,
        "missing": missing,
    }


@enterprise_router.get("/{agent_id}/skills")
def get_agent_skills(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """返回员工可见的技能分支视图，包含已停用分支以便前端展示状态。"""
    _ensure_can_access_agent(_get_agent(db, tenant_id, agent_id), current_user)
    return [
        _skill_branch_read(skill)
        for skill in visible_skill_rows(db, tenant_id, agent_id, include_inactive=True)
    ]


@enterprise_router.post("/{agent_id}/skills/{skill_id}/sync-from-overall")
def sync_agent_skill_from_overall(
    agent_id: str,
    skill_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """把开放广场的技能主干同步到员工分支，产生新的 head 版本。

    整体智能体本身就是主干，不允许该操作；未发布的技能不可学习。
    """
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    if agent.is_overall:
        raise HTTPException(status_code=400, detail="Overall agent is already the trunk")
    skill = _get_global_skill(db, tenant_id, skill_id)
    if skill.status != "published":
        raise HTTPException(
            status_code=400, detail="Disabled SOP cannot be learned from the open gallery"
        )
    branch = sync_branch_from_overall(db, tenant_id, agent_id, skill)
    db.commit()
    return {"status": "synced", "skill_id": skill_id, "head_version": branch.head_version}


@enterprise_router.post("/{agent_id}/skills/{skill_id}/promote-to-overall")
def promote_agent_skill_to_overall(
    agent_id: str,
    skill_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """把员工技能分支提升为开放广场主干版本，仅管理员可执行。

    整体智能体没有可提升的分支；分支不存在时抛 404。
    """
    _ensure_admin_user(tenant_id, current_user)
    agent = _get_agent(db, tenant_id, agent_id)
    if agent.is_overall:
        raise HTTPException(
            status_code=400, detail="Overall agent does not have a branch to promote"
        )
    branch = db.exec(
        select(AgentSkillBranch).where(
            AgentSkillBranch.tenant_id == tenant_id,
            AgentSkillBranch.agent_id == agent_id,
            AgentSkillBranch.skill_id == skill_id,
        )
    ).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    skill = promote_branch_to_overall(db, tenant_id, branch)
    db.commit()
    return {"status": "promoted", "skill_id": skill_id, "version": skill.version}


@enterprise_router.post("/{agent_id}/skills/{skill_id}/rollback")
def rollback_agent_skill(
    agent_id: str,
    skill_id: str,
    request: AgentSkillRollbackRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """把员工技能分支回滚到指定历史版本，并返回新的 head 版本。

    整体智能体需改用全局技能回滚接口。
    """
    agent = _get_agent(db, request.tenant_id, agent_id)
    _ensure_can_manage_agent(agent, current_user)
    if agent.is_overall:
        raise HTTPException(
            status_code=400, detail="Use the global skill rollback endpoint for overall agent"
        )
    branch = rollback_branch(db, request.tenant_id, agent_id, skill_id, request.version)
    db.commit()
    return {"status": "rolled_back", "skill_id": skill_id, "head_version": branch.head_version}


@enterprise_router.get("/{agent_id}/skills/{skill_id}/versions")
def list_agent_skill_versions(
    agent_id: str,
    skill_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """列出员工技能分支的历史版本，含内容、同步状态与变更摘要。"""
    _ensure_can_access_agent(_get_agent(db, tenant_id, agent_id), current_user)
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "agent_id": row.agent_id,
            "skill_id": row.skill_id,
            "version": row.version,
            "base_version": row.base_version,
            "sync_state": row.sync_state,
            "status": row.status,
            "content": row.content_json,
            "change_summary": row.change_summary,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in branch_versions(db, tenant_id, agent_id, skill_id)
    ]


@enterprise_router.put("/{agent_id}/models")
def update_agent_models(
    agent_id: str,
    request: AgentModelsUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """兼容旧客户端的模型重置接口：清空员工模型绑定，统一继承租户默认模型。

    员工级模型选择已下线，保留该入口只为避免旧客户端重建遗留绑定。
    """
    _ensure_can_manage_agent(_get_agent(db, request.tenant_id, agent_id), current_user)
    # Employee-specific model selection has been retired. Keep this endpoint as a backwards-
    # compatible reset operation so older clients cannot recreate legacy bindings.
    _delete_agent_model_bindings(db, request.tenant_id, agent_id)
    db.commit()
    return {"status": "updated", "agent_id": agent_id}


@enterprise_router.get("/{agent_id}/models", response_model=list[dict[str, object]])
def get_agent_models(
    agent_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """返回员工实际生效的模型配置，当前实现只回显租户默认模型。

    没有默认模型时返回空列表。
    """
    agent = _get_agent(db, tenant_id, agent_id)
    _ensure_can_access_agent(agent, current_user)
    default = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    if default is None:
        return []
    return [{"role": "default", "model_config_id": default.id, "effective": False}]


@chat_router.get("", response_model=list[AgentProfileRead])
def list_chat_agents(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AgentProfileRead]:
    """列出当前用户可在聊天中选择的员工，排除整体智能体与非活跃项。

    非管理员只能看到自己创建的、或已用过且已上架开放广场的员工。
    """
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    ensure_tenant(db, tenant_id)
    rows = db.exec(
        select(AgentProfile)
        .where(
            AgentProfile.tenant_id == tenant_id,
            AgentProfile.status == "active",
            AgentProfile.is_overall == False,  # noqa: E712
        )
        .order_by(AgentProfile.updated_at.desc())
    ).all()
    rows = [row for row in rows if not _agent_hidden_from_staffdeck(row)]
    used_agent_ids = _used_agent_ids_for_user(db, tenant_id, current_user)
    rows = [
        row for row in rows if _chat_agent_selectable_to_user(row, current_user, used_agent_ids)
    ]
    bindings = _bindings_by_agent(db, tenant_id)
    return [agent_read(row, bindings.get(row.id, []), row.id in used_agent_ids) for row in rows]


@chat_router.post("/{agent_id}/use", response_model=AgentProfileRead)
def use_chat_agent(
    agent_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AgentProfileRead:
    """记录当前用户使用该员工，用于解锁聊天列表中的上架员工。

    租户不匹配、员工非活跃或不可见时抛 403。
    """
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    ensure_tenant(db, tenant_id)
    row = _get_agent(db, tenant_id, agent_id)
    if (
        row.is_overall
        or row.status != "active"
        or not _chat_agent_visible_to_user(row, current_user)
    ):
        raise HTTPException(status_code=403, detail="Cannot access this agent")
    _mark_agent_used(db, tenant_id, current_user, row.id)
    bindings = _bindings_by_agent(db, tenant_id)
    return agent_read(row, bindings.get(row.id, []), True)


def _agent_resource_timeline_events(
    db: Session,
    tenant_id: str,
    agent_id: str,
) -> list[AgentWorkRecordEventRead]:
    """根据员工的有效资源绑定生成“已挂载”时间线事件。

    只保留当前可用的资源（已发布技能、启用工具等），绑定或分支失效则跳过。
    """
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.status == "active",
        )
    ).all()
    ids_by_type = {
        resource_type: {
            binding.resource_id
            for binding in bindings
            if binding.resource_type == resource_type
        }
        for resource_type in ("skill", "general_skill", "knowledge_base", "tool")
    }
    skills = {
        row.id: row
        for row in db.exec(
            select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.id.in_(ids_by_type["skill"]),
            )
        ).all()
    } if ids_by_type["skill"] else {}
    general_skills = {
        row.id: row
        for row in db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == tenant_id,
                GeneralSkill.id.in_(ids_by_type["general_skill"]),
            )
        ).all()
    } if ids_by_type["general_skill"] else {}
    knowledge_bases = {
        row.id: row
        for row in db.exec(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.id.in_(ids_by_type["knowledge_base"]),
            )
        ).all()
    } if ids_by_type["knowledge_base"] else {}
    tools = {
        row.id: row
        for row in db.exec(
            select(Tool).where(
                Tool.tenant_id == tenant_id,
                Tool.id.in_(ids_by_type["tool"]),
            )
        ).all()
    } if ids_by_type["tool"] else {}
    skill_branches = {
        row.skill_id: row
        for row in db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == agent_id,
            )
        ).all()
    }
    knowledge_branches = {
        row.knowledge_base_id: row
        for row in db.exec(
            select(AgentKnowledgeBranch).where(
                AgentKnowledgeBranch.tenant_id == tenant_id,
                AgentKnowledgeBranch.agent_id == agent_id,
            )
        ).all()
    }

    events: list[AgentWorkRecordEventRead] = []
    for binding in bindings:
        kind: str
        label: str
        if binding.resource_type == "skill":
            resource = skills.get(binding.resource_id)
            branch = skill_branches.get(resource.skill_id) if resource else None
            if not resource or resource.status != "published" or (branch and branch.status != "active"):
                continue
            kind, label = "sop", resource.name
        elif binding.resource_type == "general_skill":
            resource = general_skills.get(binding.resource_id)
            if not resource or resource.status != "published":
                continue
            kind, label = "skill", resource.name
        elif binding.resource_type == "knowledge_base":
            resource = knowledge_bases.get(binding.resource_id)
            branch = knowledge_branches.get(binding.resource_id)
            if not resource or resource.status != "active" or (branch and branch.status != "active"):
                continue
            kind, label = "knowledge", resource.name
        elif binding.resource_type == "tool":
            resource = tools.get(binding.resource_id)
            if not resource or not resource.enabled:
                continue
            kind, label = "tool", resource.display_name or resource.name
        else:
            continue
        events.append(
            AgentWorkRecordEventRead(
                id=f"{binding.id}:assigned",
                kind=kind,  # type: ignore[arg-type]
                phase="assigned",
                timestamp=_iso_utc(binding.created_at),
                label=label,
            )
        )
    return events


def _agent_scheduled_task_timeline_events(
    db: Session,
    tenant_id: str,
    agent_id: str,
    current_user: User,
) -> list[AgentWorkRecordEventRead]:
    """生成员工定时任务的最近运行与下次运行时间线事件。

    非管理员只能看到自己创建的任务；已归档任务不参与统计。
    """
    conditions = [
        ScheduledTask.tenant_id == tenant_id,
        ScheduledTask.agent_id == agent_id,
        ScheduledTask.status != "archived",
    ]
    if not _is_admin_user(current_user):
        conditions.append(ScheduledTask.created_by_user_id == current_user.id)
    tasks = db.exec(select(ScheduledTask).where(*conditions)).all()
    events: list[AgentWorkRecordEventRead] = []
    for task in tasks:
        for phase, timestamp in (("last_run", task.last_run_at), ("next_run", task.next_run_at)):
            if not timestamp:
                continue
            events.append(
                AgentWorkRecordEventRead(
                    id=f"{task.id}:{phase}",
                    kind="task",
                    phase=phase,  # type: ignore[arg-type]
                    timestamp=_iso_utc(timestamp),
                    label=task.title,
                )
            )
    return events


def _as_utc(value: datetime) -> datetime:
    """把无时区的时间视为 UTC，有时区的时间统一换算到 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    """格式化为以 Z 结尾的 ISO 8601 字符串，便于前端直接解析。"""
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def agent_read(
    row: AgentProfile,
    bindings: list[AgentResourceBinding],
    used_by_current_user: bool | None = None,
) -> AgentProfileRead:
    """把员工数据行转换为读模型，附带资源绑定与当前用户的在用标记。

    harness_max_actions 会被裁剪到 1-100；传入在用标记时覆盖 metadata 中的对应字段。
    """
    metadata = dict(row.metadata_json or {})
    if used_by_current_user is not None:
        metadata["used_by_current_user"] = used_by_current_user
        metadata["chat_used_by_current_user"] = used_by_current_user
    return AgentProfileRead(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description,
        persona_prompt=row.persona_prompt,
        is_overall=row.is_overall,
        status=row.status,
        harness_max_actions=max(1, min(int(row.harness_max_actions or 32), 100)),
        metadata=metadata,
        resources=[binding_read(binding) for binding in bindings],
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _is_database_locked_error(exc: OperationalError) -> bool:
    """判断 OperationalError 是否为 SQLite 的 database is locked 冲突。"""
    return "database is locked" in str(exc).lower()


def _ensure_request_tenant(tenant_id: str, user: User) -> None:
    """校验请求租户与当前用户所属租户一致，不一致时抛 403。"""
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def _ensure_staffdeck_agent_api_client(
    db: Session,
    tenant_id: str,
    current_user: User,
) -> APIClient:
    """获取或创建承载员工 API 密钥的内部 APIClient，并补齐所需 scope。

    已有客户端若被停用或缺少 scope 会就地修复，新建时记录创建人。
    """
    row = db.exec(
        select(APIClient).where(
            APIClient.tenant_id == tenant_id,
            APIClient.name == STAFFDECK_AGENT_API_CLIENT_NAME,
        )
    ).first()
    required_scopes = sorted({"credentials:write", *AGENT_KEY_ALLOWED_SCOPES})
    if row:
        if row.status != "active" or not set(required_scopes).issubset(row.scopes_json or []):
            row.status = "active"
            row.scopes_json = sorted(set(row.scopes_json or []) | set(required_scopes))
            row.updated_at = utc_now()
            db.add(row)
            db.flush()
        return row
    row = APIClient(
        tenant_id=tenant_id,
        name=STAFFDECK_AGENT_API_CLIENT_NAME,
        description="由数字员工设置页管理的单员工运行密钥。",
        scopes_json=required_scopes,
        created_by_user_id=current_user.id,
        metadata_json={"managed_by": "agent_settings"},
    )
    db.add(row)
    db.flush()
    return row


def _get_agent_api_credential(
    db: Session,
    tenant_id: str,
    agent_id: str,
    credential_id: str,
) -> APICredential:
    """按租户与员工归属读取 API 密钥，越权或不存在统一按 404 处理。"""
    row = db.get(APICredential, credential_id)
    if not row or row.tenant_id != tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Employee API credential not found")
    return row


def _agent_api_credential_read(row: APICredential) -> AgentAPICredentialRead:
    """把 API 密钥行转换为读模型，密钥只暴露前缀加省略号。"""
    return AgentAPICredentialRead(
        id=row.id,
        agent_id=str(row.agent_id or ""),
        name=row.name,
        access=agent_access_for_scopes(list(row.scopes_json or [])),
        key_prefix=f"{row.key_prefix}…",
        scopes=list(row.scopes_json or []),
        status=row.status,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _agent_visible_to_user(row: AgentProfile, user: User) -> bool:
    """判断员工对用户是否可见：管理员可见全部，普通用户可见整体、自建与已上架员工。

    标记为 hidden_from_staffdeck 的员工对所有人隐藏。
    """
    if _agent_hidden_from_staffdeck(row):
        return False
    if _is_admin_user(user):
        return True
    if row.is_overall:
        return True
    metadata = row.metadata_json or {}
    return _agent_owned_by_user(row, user) or metadata.get("published_to_gallery") is True


def _agent_hidden_from_staffdeck(row: AgentProfile) -> bool:
    """判断员工是否被标记为对 StaffDeck 控制台隐藏。"""
    return (row.metadata_json or {}).get("hidden_from_staffdeck") is True


def _agent_published_to_gallery(row: AgentProfile) -> bool:
    """判断员工是否已发布到开放广场。"""
    return (row.metadata_json or {}).get("published_to_gallery") is True


def _used_agent_ids_for_user(db: Session, tenant_id: str, user: User) -> set[str]:
    """汇总用户使用过的员工 ID，来源包括使用记录与会话归属。"""
    usage_rows = db.exec(
        select(AgentUsage.agent_id).where(
            AgentUsage.tenant_id == tenant_id,
            AgentUsage.user_id == user.id,
            AgentUsage.agent_id != None,  # noqa: E711
        )
    ).all()
    session_rows = db.exec(
        select(ChatSession.agent_id).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.user_id == user.id,
            ChatSession.agent_id != None,  # noqa: E711
        )
    ).all()
    return {str(agent_id) for agent_id in [*usage_rows, *session_rows] if agent_id}


def _mark_agent_used(db: Session, tenant_id: str, user: User, agent_id: str) -> AgentUsage:
    """记录用户使用某员工的行为，已存在则只刷新时间。

    并发插入触发唯一约束时回滚并复用已有记录，两者都没有才上抛。
    """
    row = db.exec(
        select(AgentUsage).where(
            AgentUsage.tenant_id == tenant_id,
            AgentUsage.user_id == user.id,
            AgentUsage.agent_id == agent_id,
        )
    ).first()
    if row:
        row.updated_at = utc_now()
    else:
        row = AgentUsage(tenant_id=tenant_id, user_id=user.id, agent_id=agent_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.exec(
            select(AgentUsage).where(
                AgentUsage.tenant_id == tenant_id,
                AgentUsage.user_id == user.id,
                AgentUsage.agent_id == agent_id,
            )
        ).first()
        if not row:
            raise
    db.refresh(row)
    return row


def _chat_agent_selectable_to_user(row: AgentProfile, user: User, used_agent_ids: set[str]) -> bool:
    """判断员工能否出现在当前用户的聊天选择列表。

    整体智能体不入选；自建员工直接可选，上架员工需已用过，其余仅管理员可选。
    """
    if row.is_overall:
        return False
    if _agent_owned_by_user(row, user):
        return True
    if _agent_published_to_gallery(row):
        return row.id in used_agent_ids
    return _is_admin_user(user)


def _ensure_can_access_agent(row: AgentProfile, user: User) -> None:
    """校验当前用户可访问该员工，租户不一致或不可见时抛 403。"""
    _ensure_request_tenant(row.tenant_id, user)
    if not _agent_visible_to_user(row, user):
        raise HTTPException(status_code=403, detail="Cannot access this agent")


def _ensure_can_copy_from_agent(row: AgentProfile, user: User) -> None:
    """校验可从该员工复制资源：整体智能体或对用户可见的员工才放行。"""
    _ensure_request_tenant(row.tenant_id, user)
    if row.is_overall or _agent_visible_to_user(row, user):
        return
    raise HTTPException(status_code=403, detail="Cannot copy resources from this agent")


def _ensure_can_manage_agent(row: AgentProfile, user: User) -> None:
    """校验管理权限：管理员放行，整体智能体仅管理员可管，普通员工需为创建者。"""
    _ensure_request_tenant(row.tenant_id, user)
    if _is_admin_user(user):
        return
    if row.is_overall:
        raise HTTPException(status_code=403, detail="Only administrator can manage overall agent")
    if _agent_owned_by_user(row, user):
        return
    raise HTTPException(
        status_code=403, detail="Only the creator or administrator can manage this staff"
    )


def _ensure_can_import_to_agent(row: AgentProfile, user: User) -> None:
    """校验可向目标员工导入资源：整体智能体要求管理员，其余复用管理权限校验。"""
    if row.is_overall:
        _ensure_admin_user(row.tenant_id, user)
        return
    _ensure_can_manage_agent(row, user)


def _ensure_admin_user(tenant_id: str, user: User) -> None:
    """校验用户是租户管理员，否则抛 403。"""
    _ensure_request_tenant(tenant_id, user)
    if not _is_admin_user(user):
        raise HTTPException(
            status_code=403, detail="Only administrator can update the open gallery"
        )


def _metadata_with_creator(metadata: dict[str, object], user: User) -> dict[str, object]:
    """写入创建者与所有者元信息，供后续权限判断与展示使用。"""
    normalized = dict(metadata or {})
    display_name = user.display_name or user.username
    normalized["owner_user_id"] = user.id
    normalized["owner_username"] = user.username
    normalized["owner_display_name"] = display_name
    normalized["created_by_user_id"] = user.id
    normalized["created_by_username"] = user.username
    normalized["created_by"] = user.username
    normalized["created_by_display_name"] = display_name
    normalized["creator_name"] = user.username
    return normalized


def _metadata_preserving_creator(
    existing_metadata: dict[str, object],
    next_metadata: dict[str, object],
    user: User,
) -> dict[str, object]:
    """合并新的 metadata，但保留已有的创建者与所有者字段，避免更新时丢失归属。

    只有原值为非空字符串时才保留，空值允许被新 metadata 覆盖。
    """
    normalized = dict(next_metadata or {})
    for key in (
        "owner_user_id",
        "owner_username",
        "owner_display_name",
        "created_by_user_id",
        "created_by_username",
        "created_by",
        "created_by_display_name",
        "creator_name",
    ):
        existing_value = existing_metadata.get(key)
        if isinstance(existing_value, str) and existing_value.strip():
            normalized[key] = existing_value
    return normalized


def _chat_agent_visible_to_user(row: AgentProfile, user: User) -> bool:
    """判断员工在聊天场景下对用户是否可见，规则与员工列表保持一致。"""
    return _agent_visible_to_user(row, user)


def binding_read(row: AgentResourceBinding) -> AgentResourceBindingRead:
    """把资源绑定行转换为读模型，metadata 缺省为空字典。"""
    return AgentResourceBindingRead(
        id=row.id,
        tenant_id=row.tenant_id,
        agent_id=row.agent_id,
        resource_type=row.resource_type,  # type: ignore[arg-type]
        resource_id=row.resource_id,
        status=row.status,
        metadata=dict(row.metadata_json or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _copy_agent_scope_from_source(
    db: Session, tenant_id: str, source: AgentProfile, target: AgentProfile
) -> None:
    """把源员工的资源作用域复制到目标员工，并重置其模型绑定以继承租户默认模型。

    整体智能体走全局作用域复制，普通员工逐条复制资源绑定。
    """
    if source.is_overall:
        copy_overall_scope_to_agent(db, tenant_id, target)
    else:
        bindings = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == source.id,
            )
        ).all()
        for binding in bindings:
            _copy_resource_binding(db, tenant_id, source.id, target.id, binding)
    _copy_agent_models_from_source(db, tenant_id, source, target)


def _copy_resource_binding(
    db: Session,
    tenant_id: str,
    source_agent_id: str,
    target_agent_id: str,
    binding: AgentResourceBinding,
) -> None:
    """复制单条资源绑定到目标员工，跳过非活跃绑定。

    私有通用技能会另存副本；技能与知识库同时复制对应分支。
    """
    if binding.status != "active":
        return
    resource_id = binding.resource_id
    metadata = dict(binding.metadata_json or {})
    if binding.resource_type == "general_skill":
        general_skill = db.get(GeneralSkill, binding.resource_id)
        if (
            general_skill is not None
            and general_skill.tenant_id == tenant_id
            and not is_open_gallery_resource(db, tenant_id, "general_skill", general_skill)
        ):
            copied_skill = _copy_private_general_skill(
                db,
                tenant_id,
                general_skill,
                target_agent_id,
            )
            resource_id = copied_skill.id
            metadata = agent_private_metadata(target_agent_id, metadata)
    copied_binding = AgentResourceBinding(
        tenant_id=tenant_id,
        agent_id=target_agent_id,
        resource_type=binding.resource_type,
        resource_id=resource_id,
        status=binding.status,
        metadata_json=metadata,
    )
    db.add(copied_binding)
    if binding.resource_type == "skill":
        skill = db.get(Skill, binding.resource_id)
        if skill and skill.tenant_id == tenant_id:
            _copy_skill_branch(db, tenant_id, source_agent_id, target_agent_id, skill)
    elif binding.resource_type == "knowledge_base":
        kb = db.get(KnowledgeBase, binding.resource_id)
        if kb and kb.tenant_id == tenant_id:
            _copy_knowledge_branch(db, tenant_id, source_agent_id, target_agent_id, kb)


def _dedupe_ids(resource_ids: list[str]) -> list[str]:
    """对资源 ID 做去空白与去重，保持首次出现的顺序。"""
    seen: set[str] = set()
    deduped: list[str] = []
    for raw_id in resource_ids:
        resource_id = str(raw_id or "").strip()
        if not resource_id or resource_id in seen:
            continue
        seen.add(resource_id)
        deduped.append(resource_id)
    return deduped


def _source_resource_binding(
    db: Session,
    tenant_id: str,
    source_agent: AgentProfile,
    resource_type: str,
    resource_id: str,
) -> AgentResourceBinding | None:
    """查找源员工对指定资源的绑定；整体智能体没有私有绑定，返回 None。"""
    if source_agent.is_overall:
        return None
    return db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == source_agent.id,
            AgentResourceBinding.resource_type == resource_type,
            AgentResourceBinding.resource_id == resource_id,
        )
    ).first()


AgentResource = Skill | GeneralSkill | KnowledgeBase | Tool


def _blocked_learning_reason(
    db: Session,
    tenant_id: str,
    source_agent: AgentProfile,
    resource_type: str,
    resolved: AgentResource,
    source_binding: AgentResourceBinding | None,
) -> str | None:
    """判断资源能否从源员工学习，返回阻止原因代码，可学习时返回 None。

    原因包括开放广场侧已禁用，以及源员工的绑定或技能/知识库分支非活跃。
    """
    if source_agent.is_overall:
        return (
            None
            if _open_gallery_resource_enabled(db, tenant_id, resource_type, resolved)
            else "disabled_in_open_gallery"
        )
    if not source_binding or source_binding.status != "active":
        return "inactive_in_source_agent"
    if resource_type == "skill" and isinstance(resolved, Skill):
        branch = db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == source_agent.id,
                AgentSkillBranch.skill_id == resolved.skill_id,
            )
        ).first()
        if branch and branch.status != "active":
            return "inactive_in_source_agent"
    if resource_type == "knowledge_base" and isinstance(resolved, KnowledgeBase):
        branch = db.exec(
            select(AgentKnowledgeBranch).where(
                AgentKnowledgeBranch.tenant_id == tenant_id,
                AgentKnowledgeBranch.agent_id == source_agent.id,
                AgentKnowledgeBranch.knowledge_base_id == resolved.id,
            )
        ).first()
        if branch and branch.status != "active":
            return "inactive_in_source_agent"
    return None


def _open_gallery_resource_enabled(
    db: Session,
    tenant_id: str,
    resource_type: str,
    resolved: AgentResource,
) -> bool:
    """判断资源是否属于开放广场且处于可学习状态（已发布或已启用）。"""
    if not is_open_gallery_resource(db, tenant_id, resource_type, resolved):
        return False
    if resource_type == "skill" and isinstance(resolved, Skill):
        return resolved.status == "published"
    if resource_type == "general_skill" and isinstance(resolved, GeneralSkill):
        return resolved.status == "published"
    if resource_type == "knowledge_base" and isinstance(resolved, KnowledgeBase):
        return resolved.status == "active"
    if resource_type == "tool" and isinstance(resolved, Tool):
        return resolved.enabled
    return False


def _import_resource_to_overall(
    db: Session,
    tenant_id: str,
    source_agent: AgentProfile,
    resource_type: str,
    resolved: AgentResource,
) -> None:
    """把源员工的技能或知识库分支提升到整体智能体。

    源本身就是整体智能体时直接返回；其他资源类型无需提升。
    """
    if source_agent.is_overall:
        return
    if resource_type == "skill" and isinstance(resolved, Skill):
        branch = db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == source_agent.id,
                AgentSkillBranch.skill_id == resolved.skill_id,
            )
        ).first()
        if branch:
            promote_branch_to_overall(db, tenant_id, branch)
        return
    if resource_type == "knowledge_base" and isinstance(resolved, KnowledgeBase):
        promote_knowledge_branch_to_overall(db, tenant_id, source_agent.id, resolved.id)


def _upsert_imported_resource_binding(
    db: Session,
    tenant_id: str,
    source_agent: AgentProfile,
    target_agent: AgentProfile,
    resource_type: str,
    resolved: AgentResource,
    source_binding: AgentResourceBinding | None,
) -> AgentResource:
    """在目标员工上新增或更新资源绑定，并同步对应技能/知识库分支。

    私有通用技能先复制为员工私有副本，再以副本 ID 建立绑定；返回最终资源。
    """
    if (
        resource_type == "general_skill"
        and isinstance(resolved, GeneralSkill)
        and not is_open_gallery_resource(db, tenant_id, resource_type, resolved)
    ):
        resolved = _copy_private_general_skill(db, tenant_id, resolved, target_agent.id)
    status = source_binding.status if source_binding else "active"
    metadata = agent_private_metadata(
        target_agent.id,
        dict(source_binding.metadata_json or {}) if source_binding else {},
    )
    existing = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == target_agent.id,
            AgentResourceBinding.resource_type == resource_type,
            AgentResourceBinding.resource_id == resolved.id,
        )
    ).first()
    if existing:
        existing.status = status
        existing.metadata_json = metadata
        existing.updated_at = utc_now()
        db.add(existing)
    else:
        db.add(
            AgentResourceBinding(
                tenant_id=tenant_id,
                agent_id=target_agent.id,
                resource_type=resource_type,
                resource_id=resolved.id,
                status=status,
                metadata_json=metadata,
            )
        )
    if resource_type == "skill" and isinstance(resolved, Skill):
        _copy_or_update_skill_branch(db, tenant_id, source_agent.id, target_agent.id, resolved)
    elif resource_type == "knowledge_base" and isinstance(resolved, KnowledgeBase):
        _copy_or_update_knowledge_branch(db, tenant_id, source_agent.id, target_agent.id, resolved)
    return resolved


def _copy_private_general_skill(
    db: Session,
    tenant_id: str,
    source: GeneralSkill,
    target_agent_id: str,
) -> GeneralSkill:
    """为员工复制一份私有通用技能，已复制过则直接复用旧副本。

    副本 slug 自动去重，metadata 记录来源技能 ID 与 slug。
    """
    existing_bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == target_agent_id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.status != "deleted",
        )
    ).all()
    for existing_binding in existing_bindings:
        existing = db.get(GeneralSkill, existing_binding.resource_id)
        metadata = (
            existing.metadata_json
            if existing is not None and isinstance(existing.metadata_json, dict)
            else {}
        )
        if metadata.get("copied_from_general_skill_id") == source.id:
            return existing

    metadata = agent_private_metadata(target_agent_id, deepcopy(source.metadata_json or {}))
    metadata["copied_from_general_skill_id"] = source.id
    metadata["copied_from_general_skill_slug"] = source.slug
    copied = GeneralSkill(
        tenant_id=tenant_id,
        slug=_unique_general_skill_slug(db, tenant_id, f"{source.slug}-copy"),
        name=source.name,
        description=source.description,
        homepage=source.homepage,
        skill_markdown=source.skill_markdown,
        skill_files_json=deepcopy(source.skill_files_json or []),
        metadata_json=metadata,
        status=source.status,
        capability_scope=source.capability_scope,
        permissions_json=deepcopy(source.permissions_json or {}),
        runtime_config_json=deepcopy(source.runtime_config_json or {}),
    )
    db.add(copied)
    db.flush()
    return copied


def _unique_general_skill_slug(db: Session, tenant_id: str, base_slug: str) -> str:
    """在租户内生成唯一 slug，冲突时依次追加 -2、-3 等后缀。"""
    candidate = base_slug
    suffix = 2
    while db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id,
            GeneralSkill.slug == candidate,
        )
    ).first():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _copy_or_update_skill_branch(
    db: Session, tenant_id: str, source_agent_id: str, target_agent_id: str, skill: Skill
) -> None:
    """把源员工的技能分支状态复制或更新到目标员工分支。

    源员工没有分支时退化为从整体智能体同步，复制后确保 head 版本记录存在。
    """
    with db.no_autoflush:
        source_branch = db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == source_agent_id,
                AgentSkillBranch.skill_id == skill.skill_id,
            )
        ).first()
    if not source_branch:
        branch = sync_branch_from_overall(db, tenant_id, target_agent_id, skill)
        _ensure_copied_skill_branch_version(db, branch, "导入自整体智能体")
        return
    with db.no_autoflush:
        target_branch = db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == target_agent_id,
                AgentSkillBranch.skill_id == skill.skill_id,
            )
        ).first()
    if not target_branch:
        target_branch = AgentSkillBranch(
            tenant_id=tenant_id,
            agent_id=target_agent_id,
            skill_id=source_branch.skill_id,
            source_skill_id=source_branch.source_skill_id,
        )
    target_branch.base_version = source_branch.base_version
    target_branch.head_version = source_branch.head_version
    target_branch.content_json = dict(source_branch.content_json or {})
    target_branch.status = source_branch.status
    target_branch.sync_state = source_branch.sync_state
    target_branch.metadata_json = dict(source_branch.metadata_json or {})
    target_branch.updated_at = utc_now()
    db.add(target_branch)
    db.flush()
    _ensure_copied_skill_branch_version(db, target_branch, f"导入自 {source_agent_id}")


def _ensure_copied_skill_branch_version(
    db: Session, branch: AgentSkillBranch, change_summary: str
) -> None:
    """确保分支的 head 版本存在对应版本记录，已存在则刷新内容，否则新建。"""
    existing = db.exec(
        select(AgentSkillBranchVersion).where(
            AgentSkillBranchVersion.tenant_id == branch.tenant_id,
            AgentSkillBranchVersion.agent_id == branch.agent_id,
            AgentSkillBranchVersion.skill_id == branch.skill_id,
            AgentSkillBranchVersion.version == branch.head_version,
        )
    ).first()
    if existing:
        existing.content_json = dict(branch.content_json or {})
        existing.status = branch.status
        existing.sync_state = branch.sync_state
        existing.updated_at = utc_now()
        db.add(existing)
        return
    db.add(
        AgentSkillBranchVersion(
            tenant_id=branch.tenant_id,
            agent_id=branch.agent_id,
            skill_id=branch.skill_id,
            source_skill_id=branch.source_skill_id,
            version=branch.head_version,
            base_version=branch.base_version,
            content_json=dict(branch.content_json or {}),
            status=branch.status,
            sync_state=branch.sync_state,
            change_summary=change_summary,
        )
    )


def _copy_or_update_knowledge_branch(
    db: Session,
    tenant_id: str,
    source_agent_id: str,
    target_agent_id: str,
    kb: KnowledgeBase,
) -> None:
    """把源员工的知识库分支版本状态复制或更新到目标员工。

    源分支不存在时取知识库当前版本，并把分支标记为 active 与已同步。
    """
    with db.no_autoflush:
        source_branch = db.exec(
            select(AgentKnowledgeBranch).where(
                AgentKnowledgeBranch.tenant_id == tenant_id,
                AgentKnowledgeBranch.agent_id == source_agent_id,
                AgentKnowledgeBranch.knowledge_base_id == kb.id,
            )
        ).first()
        target_branch = db.exec(
            select(AgentKnowledgeBranch).where(
                AgentKnowledgeBranch.tenant_id == tenant_id,
                AgentKnowledgeBranch.agent_id == target_agent_id,
                AgentKnowledgeBranch.knowledge_base_id == kb.id,
            )
        ).first()
    if source_branch:
        base_version = source_branch.base_version
        head_version = source_branch.head_version
        status = source_branch.status
        sync_state = source_branch.sync_state
        metadata = dict(source_branch.metadata_json or {})
    else:
        version = ensure_knowledge_base_version(db, kb).version
        base_version = version
        head_version = version
        status = "active"
        sync_state = "synced"
        metadata = {}
    if not target_branch:
        target_branch = AgentKnowledgeBranch(
            tenant_id=tenant_id,
            agent_id=target_agent_id,
            knowledge_base_id=kb.id,
        )
    target_branch.base_version = base_version
    target_branch.head_version = head_version
    target_branch.status = status
    target_branch.sync_state = sync_state
    target_branch.metadata_json = metadata
    target_branch.updated_at = utc_now()
    db.add(target_branch)


def _resource_display_id(resource_type: str, resolved: AgentResource) -> str:
    """返回资源在界面展示的标识：技能用 skill_id、通用技能用 slug、工具用 name。"""
    if resource_type == "skill" and isinstance(resolved, Skill):
        return resolved.skill_id
    if resource_type == "general_skill" and isinstance(resolved, GeneralSkill):
        return resolved.slug
    if resource_type == "tool" and isinstance(resolved, Tool):
        return resolved.name
    return resolved.id


def _copy_agent_models_from_source(
    db: Session, tenant_id: str, source: AgentProfile, target: AgentProfile
) -> None:
    """员工模型绑定不再复制，直接清除目标员工的绑定以继承租户默认模型。"""
    # Model bindings are intentionally not copied. Every employee inherits the tenant default.
    _ = source
    _delete_agent_model_bindings(db, tenant_id, target.id)


def _delete_agent_model_bindings(db: Session, tenant_id: str, agent_id: str) -> None:
    """删除员工遗留的模型绑定，删除由调用方统一提交。"""
    bindings = db.exec(
        select(AgentModelBinding).where(
            AgentModelBinding.tenant_id == tenant_id,
            AgentModelBinding.agent_id == agent_id,
        )
    ).all()
    for binding in bindings:
        db.delete(binding)


def _copy_skill_branch(
    db: Session, tenant_id: str, source_agent_id: str, target_agent_id: str, skill: Skill
) -> None:
    """为复制出的员工资源新建技能分支及其 head 版本记录。

    源员工没有分支时退化为从整体智能体创建分支。
    """
    source_branch = db.exec(
        select(AgentSkillBranch).where(
            AgentSkillBranch.tenant_id == tenant_id,
            AgentSkillBranch.agent_id == source_agent_id,
            AgentSkillBranch.skill_id == skill.skill_id,
        )
    ).first()
    if not source_branch:
        ensure_agent_skill_branch(db, tenant_id, target_agent_id, skill)
        return
    target_branch = AgentSkillBranch(
        tenant_id=tenant_id,
        agent_id=target_agent_id,
        skill_id=source_branch.skill_id,
        source_skill_id=source_branch.source_skill_id,
        base_version=source_branch.base_version,
        head_version=source_branch.head_version,
        content_json=dict(source_branch.content_json or {}),
        status=source_branch.status,
        sync_state=source_branch.sync_state,
        metadata_json=dict(source_branch.metadata_json or {}),
    )
    db.add(target_branch)
    db.flush()
    db.add(
        AgentSkillBranchVersion(
            tenant_id=tenant_id,
            agent_id=target_agent_id,
            skill_id=target_branch.skill_id,
            source_skill_id=target_branch.source_skill_id,
            version=target_branch.head_version,
            base_version=target_branch.base_version,
            content_json=dict(target_branch.content_json or {}),
            status=target_branch.status,
            sync_state=target_branch.sync_state,
            change_summary=f"复制自 {source_agent_id}",
        )
    )


def _copy_knowledge_branch(
    db: Session, tenant_id: str, source_agent_id: str, target_agent_id: str, kb: KnowledgeBase
) -> None:
    """复制源员工的知识库分支到目标员工；源分支不存在时不做任何事。"""
    source_branch = db.exec(
        select(AgentKnowledgeBranch).where(
            AgentKnowledgeBranch.tenant_id == tenant_id,
            AgentKnowledgeBranch.agent_id == source_agent_id,
            AgentKnowledgeBranch.knowledge_base_id == kb.id,
        )
    ).first()
    if not source_branch:
        return
    db.add(
        AgentKnowledgeBranch(
            tenant_id=tenant_id,
            agent_id=target_agent_id,
            knowledge_base_id=source_branch.knowledge_base_id,
            base_version=source_branch.base_version,
            head_version=source_branch.head_version,
            status=source_branch.status,
            sync_state=source_branch.sync_state,
            metadata_json=dict(source_branch.metadata_json or {}),
        )
    )


def _resolve_resource(
    db: Session, tenant_id: str, resource_type: str, identifier: str
) -> AgentResource | None:
    """按资源类型把 ID、skill_id、slug 或名称解析为具体资源，未命中返回 None。"""
    if resource_type == "skill":
        return (
            db.get(Skill, identifier)
            or db.exec(
                select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == identifier)
            ).first()
        )
    if resource_type == "general_skill":
        return (
            db.get(GeneralSkill, identifier)
            or db.exec(
                select(GeneralSkill).where(
                    GeneralSkill.tenant_id == tenant_id, GeneralSkill.slug == identifier
                )
            ).first()
        )
    if resource_type == "knowledge_base":
        return (
            db.get(KnowledgeBase, identifier)
            or db.exec(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.name == identifier
                )
            ).first()
        )
    if resource_type == "tool":
        return (
            db.get(Tool, identifier)
            or db.exec(
                select(Tool).where(Tool.tenant_id == tenant_id, Tool.name == identifier)
            ).first()
        )
    return None


def _get_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    """确保租户存在后按 ID 读取员工，跨租户或不存在统一抛 404。"""
    ensure_tenant(db, tenant_id)
    row = db.get(AgentProfile, agent_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return row


def _bindings_by_agent(db: Session, tenant_id: str) -> dict[str, list[AgentResourceBinding]]:
    """按员工分组加载可见的资源绑定，供列表接口一次渲染。

    已删除或对员工摘要不可见的绑定会被过滤。
    """
    rows = db.exec(
        select(AgentResourceBinding)
        .where(AgentResourceBinding.tenant_id == tenant_id)
        .order_by(AgentResourceBinding.created_at.asc())
    ).all()
    agent_ids = {row.agent_id for row in rows}
    agents_by_id = {
        row.id: row
        for row in db.exec(
            select(AgentProfile).where(
                AgentProfile.tenant_id == tenant_id,
                AgentProfile.id.in_(agent_ids) if agent_ids else AgentProfile.id == "__none__",
            )
        ).all()
    }
    grouped: dict[str, list[AgentResourceBinding]] = {}
    for row in rows:
        if not _resource_binding_visible_in_agent_summary(
            db, tenant_id, agents_by_id.get(row.agent_id), row
        ):
            continue
        grouped.setdefault(row.agent_id, []).append(row)
    return grouped


def _resource_binding_visible_in_agent_summary(
    db: Session,
    tenant_id: str,
    agent: AgentProfile | None,
    binding: AgentResourceBinding,
) -> bool:
    """判断资源绑定是否应出现在员工摘要中。

    综合绑定状态、资源状态、分支状态与开放广场可见性过滤失效项，
    同时隐藏空的默认知识库。
    """
    if not agent or binding.status == "deleted":
        return False

    model_by_type = {
        "skill": Skill,
        "general_skill": GeneralSkill,
        "knowledge_base": KnowledgeBase,
        "tool": Tool,
    }
    model = model_by_type.get(binding.resource_type)
    if model is None:
        return False
    resource = db.get(model, binding.resource_id)
    if not resource or resource.tenant_id != tenant_id:
        return False
    if isinstance(resource, KnowledgeBase) and _is_empty_default_knowledge_base(
        db, tenant_id, resource
    ):
        return False

    if agent.is_overall:
        if not is_open_gallery_resource(db, tenant_id, binding.resource_type, resource):
            return False
    elif not is_bound_resource_visible_for_agent(
        db, tenant_id, binding.resource_type, resource, binding
    ):
        return False

    if isinstance(resource, Skill) and not agent.is_overall:
        branch = db.exec(
            select(AgentSkillBranch).where(
                AgentSkillBranch.tenant_id == tenant_id,
                AgentSkillBranch.agent_id == agent.id,
                AgentSkillBranch.skill_id == resource.skill_id,
            )
        ).first()
        if branch and branch.status == "deleted":
            return False
        skill_status = branch.status if branch else resource.status
        if binding.status == "active" and skill_status not in {"active", "published"}:
            return False

    if isinstance(resource, KnowledgeBase) and not agent.is_overall:
        branch = db.exec(
            select(AgentKnowledgeBranch).where(
                AgentKnowledgeBranch.tenant_id == tenant_id,
                AgentKnowledgeBranch.agent_id == agent.id,
                AgentKnowledgeBranch.knowledge_base_id == resource.id,
                AgentKnowledgeBranch.status != "deleted",
            )
        ).first()
        if not branch:
            return False
        if binding.status == "active" and branch.status != "active":
            return False

    if binding.status != "active":
        return True
    if isinstance(resource, Skill):
        return agent.is_overall is False or resource.status == "published"
    if isinstance(resource, GeneralSkill):
        return resource.status == "published"
    if isinstance(resource, KnowledgeBase):
        return resource.status == "active"
    if isinstance(resource, Tool):
        return resource.enabled
    return False


def _is_empty_default_knowledge_base(db: Session, tenant_id: str, kb: KnowledgeBase) -> bool:
    """判断知识库是否为没有文档、桶或分块的空默认知识库，用于摘要中隐藏。"""
    metadata = kb.metadata_json or {}
    has_runtime_rows = any(
        db.exec(
            select(model.id).where(
                model.tenant_id == tenant_id,
                model.knowledge_base_id == kb.id,
            )
        ).first()
        for model in (KnowledgeDocument, KnowledgeBucket, KnowledgeChunk)
    )
    if has_runtime_rows:
        return False
    if metadata.get("created_from_document_upload") and not metadata.get("source_document_id"):
        return True
    return kb.name == "默认知识库"


def _ensure_resource_exists(db: Session, tenant_id: str, item: AgentResourceBindingInput) -> None:
    """校验资源在租户内存在，缺失时抛 404 并带上资源类型与 ID。"""
    model = {
        "skill": Skill,
        "general_skill": GeneralSkill,
        "knowledge_base": KnowledgeBase,
        "tool": Tool,
    }[item.resource_type]
    row = db.get(model, item.resource_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(
            status_code=404, detail=f"Resource not found: {item.resource_type}:{item.resource_id}"
        )


def _get_global_skill(db: Session, tenant_id: str, skill_id: str) -> Skill:
    """按 skill_id 在租户内查找全局技能，不存在时抛 404。"""
    row = db.exec(
        select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == skill_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Skill not found")
    return row


def _skill_branch_read(skill: Skill) -> dict[str, object]:
    """把技能及其分支元信息组装为接口返回的字典结构。

    分支信息优先取自 agent_branch_meta，缺失时回退到 content_json 内的同名节点。
    """
    metadata = getattr(skill, "agent_branch_meta", {}) or {}
    content = skill.content_json or {}
    if not metadata and isinstance(content.get("metadata"), dict):
        metadata = content.get("metadata", {}).get("agent_branch", {}) or {}
    return {
        "id": skill.id,
        "tenant_id": skill.tenant_id,
        "skill_id": skill.skill_id,
        "version": skill.version,
        "name": skill.name,
        "business_domain": skill.business_domain,
        "description": skill.description,
        "content": skill.content_json,
        "status": skill.status,
        "agent_id": metadata.get("agent_id"),
        "branch_status": metadata.get("status"),
        "branch_sync_state": metadata.get("sync_state"),
        "branch_base_version": metadata.get("base_version"),
        "branch_head_version": metadata.get("head_version"),
        "metadata": dict(metadata.get("metadata") or {}),
        "created_at": skill.created_at.isoformat(),
        "updated_at": skill.updated_at.isoformat(),
    }
