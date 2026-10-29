from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import Message, ScheduledTask, ScheduledTaskRun, User, utc_now
from app.scheduled_tasks.schema import (
    ScheduledTaskCreateRequest,
    ScheduledTaskDraftRead,
    ScheduledTaskDraftRequest,
    ScheduledTaskRead,
    ScheduledTaskRunRead,
    ScheduledTaskUpdateRequest,
)
from app.scheduled_tasks.service import (
    create_scheduled_task,
    detect_scheduled_task_draft,
    scheduled_task_read,
    scheduled_task_run_read,
    start_scheduled_task_async,
    update_scheduled_task,
)
from app.security.auth import get_current_user
from app.security.permissions import is_admin_user as _is_admin_user
from app.security.tenant import ensure_tenant


enterprise_router = APIRouter(prefix="/api/enterprise/scheduled-tasks", tags=["enterprise:scheduled-tasks"])
chat_router = APIRouter(prefix="/api/chat/scheduled-tasks", tags=["chat:scheduled-tasks"])
chat_draft_router = APIRouter(prefix="/api/chat/scheduled-task-drafts", tags=["chat:scheduled-task-drafts"])


@enterprise_router.get("", response_model=list[ScheduledTaskRead])
def list_enterprise_scheduled_tasks(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRead]:
    """列出企业自动任务，可按员工与状态过滤；非管理员只看自己创建的任务。"""
    _ensure_request_tenant(tenant_id, current_user)
    rows = _list_tasks(db, tenant_id, current_user, agent_id, status)
    return [scheduled_task_read(row) for row in rows]


@enterprise_router.post("", response_model=ScheduledTaskRead)
def create_enterprise_scheduled_task(
    request: ScheduledTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    """创建企业自动任务，返回含下一次运行时间等调度字段的读取模型。"""
    _ensure_request_tenant(request.tenant_id, current_user)
    row = create_scheduled_task(db, request, current_user)
    return scheduled_task_read(row)


@enterprise_router.get("/runs", response_model=list[ScheduledTaskRunRead])
def list_enterprise_scheduled_task_runs_for_agent(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRunRead]:
    """按租户（可再按员工、状态）列出运行记录，最新在前。

    非管理员只能看到自己发起的运行；limit 上限为 500。
    """
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    conditions = [ScheduledTaskRun.tenant_id == tenant_id]
    if agent_id:
        conditions.append(ScheduledTaskRun.agent_id == agent_id)
    if status:
        conditions.append(ScheduledTaskRun.status == status)
    if not _is_admin_user(current_user):
        conditions.append(ScheduledTaskRun.user_id == current_user.id)
    rows = db.exec(
        select(ScheduledTaskRun, ScheduledTask)
        .join(ScheduledTask, ScheduledTaskRun.scheduled_task_id == ScheduledTask.id)
        .where(*conditions)
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(limit)
    ).all()
    return [scheduled_task_run_read(run, task) for run, task in rows]


@enterprise_router.get("/{task_id}", response_model=ScheduledTaskRead)
def get_enterprise_scheduled_task(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    """读取单个自动任务；不存在、跨租户或无权访问时抛 HTTP 异常。"""
    row = _get_task(db, tenant_id, task_id, current_user)
    return scheduled_task_read(row)


@enterprise_router.put("/{task_id}", response_model=ScheduledTaskRead)
def update_enterprise_scheduled_task(
    task_id: str,
    request: ScheduledTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    """更新自动任务配置，租户与归属校验复用 _get_task。"""
    _ensure_request_tenant(request.tenant_id, current_user)
    row = _get_task(db, request.tenant_id, task_id, current_user)
    row = update_scheduled_task(db, row, request, current_user)
    return scheduled_task_read(row)


@enterprise_router.delete("/{task_id}")
def archive_enterprise_scheduled_task(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    """归档自动任务：状态置为 archived 并清空 next_run_at，后续不再调度。"""
    row = _get_task(db, tenant_id, task_id, current_user)
    row.status = "archived"
    row.next_run_at = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return {"ok": True}


@enterprise_router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunRead])
def list_enterprise_scheduled_task_runs(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRunRead]:
    """列出指定自动任务的全部运行记录，按计划执行时间倒序。"""
    row = _get_task(db, tenant_id, task_id, current_user)
    runs = db.exec(
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.tenant_id == tenant_id, ScheduledTaskRun.scheduled_task_id == row.id)
        .order_by(ScheduledTaskRun.scheduled_for.desc())
    ).all()
    return [scheduled_task_run_read(item, row) for item in runs]


@enterprise_router.post("/{task_id}/run-now", response_model=ScheduledTaskRunRead)
def run_enterprise_scheduled_task_now(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRunRead:
    """立即手动触发一次运行并返回运行记录；已归档任务返回 400。"""
    row = _get_task(db, tenant_id, task_id, current_user)
    if row.status == "archived":
        raise HTTPException(status_code=400, detail="已删除的自动任务不能运行")
    run = start_scheduled_task_async(db, row, scheduled_for=utc_now(), manual=True)
    return scheduled_task_run_read(run, row)


@chat_router.get("", response_model=list[ScheduledTaskRead])
def list_chat_scheduled_tasks(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRead]:
    """聊天侧自动任务列表，语义同企业列表但不支持状态过滤。"""
    _ensure_request_tenant(tenant_id, current_user)
    rows = _list_tasks(db, tenant_id, current_user, agent_id, None)
    return [scheduled_task_read(row) for row in rows]


@chat_router.post("", response_model=ScheduledTaskRead)
def create_chat_scheduled_task(
    request: ScheduledTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    """聊天侧创建自动任务，并把创建结果回写到来源会话的草稿消息元数据。"""
    _ensure_request_tenant(request.tenant_id, current_user)
    row = create_scheduled_task(db, request, current_user)
    read = scheduled_task_read(row)
    _mark_chat_draft_created(db, row, read)
    return read


@chat_draft_router.post("", response_model=ScheduledTaskDraftRead)
def create_chat_scheduled_task_draft(
    request: ScheduledTaskDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskDraftRead:
    """根据用户消息识别自动任务草稿；识别不到时返回 should_create=False 的空草稿。"""
    _ensure_request_tenant(request.tenant_id, current_user)
    draft = detect_scheduled_task_draft(
        db,
        request.tenant_id,
        request.agent_id,
        current_user.id,
        request.message,
        request.session_id,
        request.timezone,
    )
    if not draft:
        return ScheduledTaskDraftRead(
            should_create=False,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            source_session_id=request.session_id,
        )
    return draft


def _mark_chat_draft_created(db: Session, row: ScheduledTask, read: ScheduledTaskRead) -> None:
    """把已创建的任务回写到来源会话最近 20 条助手消息中的草稿元数据。

    只更新第一条带 scheduled_task_draft 元数据的消息；无来源会话时直接返回。
    """
    if not row.source_session_id:
        return
    messages = db.exec(
        select(Message)
        .where(
            Message.tenant_id == row.tenant_id,
            Message.session_id == row.source_session_id,
            Message.role == "assistant",
        )
        .order_by(Message.created_at.desc())
        .limit(20)
    ).all()
    for message in messages:
        metadata = dict(message.metadata_json or {})
        if not isinstance(metadata.get("scheduled_task_draft"), dict):
            continue
        metadata["scheduled_task_created"] = read.model_dump(mode="json")
        message.metadata_json = metadata
        db.add(message)
        db.commit()
        return


def _list_tasks(
    db: Session,
    tenant_id: str,
    current_user: User,
    agent_id: str | None,
    status: str | None,
) -> list[ScheduledTask]:
    """查询自动任务列表，按更新时间倒序；非管理员仅限自己创建的记录。"""
    ensure_tenant(db, tenant_id)
    conditions = [ScheduledTask.tenant_id == tenant_id]
    if agent_id:
        conditions.append(ScheduledTask.agent_id == agent_id)
    if status:
        conditions.append(ScheduledTask.status == status)
    if not _is_admin_user(current_user):
        conditions.append(ScheduledTask.created_by_user_id == current_user.id)
    return db.exec(select(ScheduledTask).where(*conditions).order_by(ScheduledTask.updated_at.desc())).all()


def _get_task(db: Session, tenant_id: str, task_id: str, current_user: User) -> ScheduledTask:
    """取自动任务并校验租户一致与访问权限，失败时抛 404/403。"""
    _ensure_request_tenant(tenant_id, current_user)
    row = db.get(ScheduledTask, task_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="自动任务不存在")
    if not _is_admin_user(current_user) and row.created_by_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问该自动任务")
    return row


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    """校验请求里的 tenant_id 与当前用户所属租户一致，不一致抛 403。"""
    if current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access another tenant")
