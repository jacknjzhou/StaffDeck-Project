from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.chat import _build_turn_traces, session_read
from app.session.message_read import message_read
from app.core.harness_session_cleanup import stage_harness_session_execution_reset
from app.db import get_session
from app.db.models import (
    AgentEvent,
    AgentProfile,
    ChatSession,
    HarnessInvocationRecord,
    Message,
    MessageFeedback,
    Skill,
    User,
    utc_now,
)
from app.feedback import feedback_analysis_read
from app.observability.session_timings import enrich_turn_traces_with_timings
from app.security.auth import get_current_user
from app.security.permissions import agent_owned_by_user, is_admin_user
from app.security.tenant import ensure_tenant
from app.session.message_visibility import visible_message_content, visible_message_rows

router = APIRouter(prefix="/api/enterprise/sessions", tags=["enterprise:sessions"])

SESSION_LOG_EXPORT_SCHEMA = "staffdeck.conversation-log.v1"


class SessionLogExportRequest(BaseModel):
    """批量导出会话日志的请求体，session_ids 去重后最多 500 条。"""
    session_ids: list[str] = Field(min_length=1, max_length=500)


@router.get("")
def list_sessions(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    """列出会话：非管理员默认只看自己的，agent 创建者可看该 agent 的全部会话。

    按 updated_at 倒序返回，并在每项上补齐渠道与对话人展示字段。
    """
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    conditions = [ChatSession.tenant_id == tenant_id]
    view_all = False
    if agent_id:
        conditions.append(ChatSession.agent_id == agent_id)
        view_all = _can_view_all_agent_sessions(db, tenant_id, agent_id, current_user)
    if not view_all:
        conditions.append(ChatSession.user_id == current_user.id)
    rows = db.exec(
        select(ChatSession).where(*conditions).order_by(ChatSession.updated_at.desc())
    ).all()
    return _session_payloads(db, rows)


@router.post("/export")
def export_session_logs(
    request: SessionLogExportRequest,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    """按会话 id 批量导出对话日志 JSON，权限逐条复用会话可见性规则。"""
    _ensure_request_tenant(tenant_id, current_user)
    session_ids = list(dict.fromkeys(request.session_ids))
    rows = [
        _get_visible_chat_session(db, tenant_id, session_id, current_user)
        for session_id in session_ids
    ]
    details = _session_details_payload(db, tenant_id, rows)
    exported_at = datetime.now(UTC)
    return _json_download_response(
        {
            "schema_version": SESSION_LOG_EXPORT_SCHEMA,
            "exported_at": exported_at,
            "count": len(details),
            "items": details,
        },
        f"staffdeck-conversation-logs-{exported_at.strftime('%Y%m%d-%H%M%S')}.json",
    )


@router.get("/{session_id}/export")
def export_session_log(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    """导出单个会话的完整日志 JSON，文件名对 session_id 做安全化处理。"""
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_visible_chat_session(db, tenant_id, session_id, current_user)
    return _json_download_response(
        {
            "schema_version": SESSION_LOG_EXPORT_SCHEMA,
            "exported_at": datetime.now(UTC),
            "item": _session_detail_payload(db, tenant_id, row),
        },
        f"staffdeck-conversation-log-{_safe_filename_part(session_id)}.json",
    )


@router.get("/{session_id}")
def get_session_detail(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """返回单会话详情：消息、反馈、轨迹、事件与工具调用。"""
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_visible_chat_session(db, tenant_id, session_id, current_user)
    return _session_detail_payload(db, tenant_id, row)


def _session_detail_payload(db: Session, tenant_id: str, row: ChatSession) -> dict:
    """复用批量构建逻辑，取单个会话的详情载荷。"""
    return _session_details_payload(db, tenant_id, [row])[0]


def _session_details_payload(
    db: Session,
    tenant_id: str,
    rows: list[ChatSession],
) -> list[dict]:
    """批量组装会话详情：先按会话分组消息、事件、反馈与工具调用，再逐个构建。

    空列表直接返回；消息按可见性规则裁剪，技能名取自租户技能表。
    """
    if not rows:
        return []
    session_ids = [row.id for row in rows]
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id.in_(session_ids))
        .order_by(Message.created_at)
    ).all()
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id.in_(session_ids))
        .order_by(AgentEvent.created_at)
    ).all()
    feedback_rows = db.exec(
        select(MessageFeedback)
        .where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.session_id.in_(session_ids),
        )
        .order_by(MessageFeedback.updated_at.desc())
    ).all()
    invocation_rows = db.exec(
        select(HarnessInvocationRecord)
        .where(
            HarnessInvocationRecord.tenant_id == tenant_id,
            HarnessInvocationRecord.session_id.in_(session_ids),
        )
        .order_by(HarnessInvocationRecord.started_at)
    ).all()
    skills = db.exec(select(Skill).where(Skill.tenant_id == tenant_id)).all()
    skill_names = {skill.skill_id: skill.name for skill in skills}
    session_payload_by_id = {str(payload["id"]): payload for payload in _session_payloads(db, rows)}
    messages_by_session = {
        session_id: visible_message_rows(session_messages)
        for session_id, session_messages in _group_by_session_id(messages).items()
    }
    events_by_session = _group_by_session_id(events)
    feedback_by_session = _group_by_session_id(feedback_rows)
    invocations_by_session = _group_by_session_id(invocation_rows)
    return [
        _build_session_detail_payload(
            db,
            session_payload_by_id[row.id],
            messages_by_session.get(row.id, []),
            events_by_session.get(row.id, []),
            feedback_by_session.get(row.id, []),
            invocations_by_session.get(row.id, []),
            skill_names,
        )
        for row in rows
    ]


def _group_by_session_id(rows: list) -> dict[str, list]:
    """按 session_id 归组任意带该属性的行，保持原有顺序。"""
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.session_id, []).append(row)
    return grouped


def _build_session_detail_payload(
    db: Session,
    session_payload: dict,
    messages: list[Message],
    events: list[AgentEvent],
    feedback_rows: list[MessageFeedback],
    invocation_rows: list[HarnessInvocationRecord],
    skill_names: dict[str, str],
) -> dict:
    """拼装单会话详情：会话信息、消息、反馈、轨迹、事件与工具调用。"""
    feedback_by_message = {item.message_id: item for item in feedback_rows}
    traces = enrich_turn_traces_with_timings(
        _build_turn_traces(messages, events, skill_names),
        events,
    )
    return {
        "session": session_payload,
        "messages": [
            _message_payload(message, feedback_by_message.get(message.id), db)
            for message in messages
        ],
        "feedback": [
            {
                "id": item.id,
                "message_id": item.message_id,
                "user_id": item.user_id,
                "rating": item.rating,
                "analysis": feedback_analysis_read(item),
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in feedback_rows
        ],
        "traces": traces,
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload_json,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
        "tool_invocations": [
            {
                "id": item.id,
                "task_id": item.task_id,
                "run_id": item.run_id,
                "call_id": item.call_id,
                "tool_name": item.tool_name,
                "status": item.status,
                "arguments": item.arguments_json,
                "result": item.result_json,
                "replayed_from_invocation_id": item.replayed_from_invocation_id,
                "started_at": item.started_at.isoformat(),
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
            }
            for item in invocation_rows
        ],
    }


def _json_download_response(payload: object, filename: str) -> Response:
    """把载荷序列化为 UTF-8 JSON 附件响应，并写入下载文件名。"""
    content = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_filename_part(value: str) -> str:
    """把任意 id 规整为只含字母数字、中划线或下划线的文件名片段，空结果回退 session。"""
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in value
    )
    return safe.strip("-") or "session"


def _message_payload(
    message: Message,
    feedback: MessageFeedback | None,
    db: Session,
) -> dict:
    """构造消息载荷，按可见性裁剪内容并合并反馈字段。"""
    payload = message_read(
        message,
        feedback.rating if feedback else None,
        db=db,
        content_override=visible_message_content(message),
    ).model_dump()
    if feedback:
        payload["feedback_id"] = feedback.id
        payload["feedback_updated_at"] = feedback.updated_at.isoformat()
        payload["feedback_analysis"] = feedback_analysis_read(feedback)
    return payload


@router.post("/{session_id}/reset")
def reset_session(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """重置会话执行状态：清空技能栈、槽位与上下文，并把状态恢复为 active。"""
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_visible_chat_session(db, tenant_id, session_id, current_user)
    stage_harness_session_execution_reset(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    row.active_skill_id = None
    row.active_step_id = None
    row.slots_json = {}
    row.skill_stack_json = []
    row.pending_tasks_json = []
    row.resume_after_answer_json = None
    row.awaiting_input_json = None
    row.context_state_json = {}
    row.summary = None
    row.last_agent_question = None
    row.status = "active"
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return _session_payloads(db, [row])[0]


def _can_view_all_agent_sessions(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
    current_user: User,
) -> bool:
    """判断用户能否查看指定 agent 的全部会话。

    管理员总是可以；agent 不存在或跨租户时抛 404，is_overall 员工仅管理员可见。
    """
    if is_admin_user(current_user):
        return True
    if not agent_id:
        return False
    agent = db.get(AgentProfile, agent_id)
    if not agent or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.is_overall:
        # is_overall 员工只有 admin 可看全部，创建者永不匹配
        return False
    return agent_owned_by_user(agent, current_user)


def _get_visible_chat_session(
    db: Session,
    tenant_id: str,
    session_id: str,
    current_user: User,
) -> ChatSession:
    """按可见性规则取会话：本人、管理员或该 agent 的创建者可读。

    会话不存在、跨租户或无权限时统一返回 404，避免泄露会话是否存在。
    """
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.user_id == current_user.id or is_admin_user(current_user):
        return row
    agent = db.get(AgentProfile, row.agent_id) if row.agent_id else None
    if (
        agent
        and agent.tenant_id == tenant_id
        and not agent.is_overall
        and agent_owned_by_user(agent, current_user)
    ):
        return row
    raise HTTPException(status_code=404, detail="Session not found")


def _session_payloads(db: Session, rows: list[ChatSession]) -> list[dict]:
    """session_read 共享函数不动，这里 dump 后 augment 渠道与对话人展示字段。"""
    user_ids = {row.user_id for row in rows if row.user_id}
    users = (
        {row.id: row for row in db.exec(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )
    payloads: list[dict] = []
    for row in rows:
        user = users.get(row.user_id)
        payloads.append(
            {
                **session_read(row).model_dump(),
                "channel": row.channel,
                "session_username": user.username if user else None,
                "session_display_name": user.display_name if user else None,
            }
        )
    return payloads


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    """校验请求租户与当前用户租户一致，不一致返回 403。"""
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
