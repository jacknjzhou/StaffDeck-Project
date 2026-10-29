from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import ChatSession, ExternalSessionBinding, Message, new_id, utc_now
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.schemas import ExternalEventsBatch, ExternalEventsBatchResult
from app.public_api.sessions import ensure_public_agent

router = APIRouter(prefix="/agents/{agent_id}/sessions", tags=["sessions"])

# Marks the client-side event a row came from. Kept in metadata so a resent
# batch can be told apart from a genuinely repeated message.
EVENT_KEY = "external_event_id"


@router.post("/{session_id}/events:batch", response_model=ExternalEventsBatchResult)
def append_events(
    agent_id: str,
    session_id: str,
    body: ExternalEventsBatch,
    principal: PublicPrincipal = Depends(require_scopes("sessions:write")),
    db: Session = Depends(get_session),
) -> ExternalEventsBatchResult:
    """追加客户端回放的事件到既有会话，逐事件去重后写入消息表。

    刻意不提供创建外部会话的接口：``POST /agents/{agent_id}/sessions`` 已接受
    ``external_session_id`` 且对其幂等，再加一条创建路径会让同一规则有两份实现。
    幂等粒度是单个事件而非整批，否则重投时随批的新事件会被一起丢弃。
    """
    ensure_public_agent(db, principal, agent_id)

    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != principal.tenant_id or row.agent_id != agent_id:
        # 404 rather than 403: the id may belong to another tenant, and
        # confirming it exists would leak that.
        raise PublicAPIError(404, "SESSION_NOT_FOUND", "Session not found.")

    # Tenancy is not enough. Without the binding check any key in the tenant
    # could append to any conversation it can name, turning a read scope into a
    # write capability.
    owned = db.exec(
        select(ExternalSessionBinding).where(
            ExternalSessionBinding.credential_id == principal.credential_id,
            ExternalSessionBinding.session_id == session_id,
        )
    ).first()
    if not owned:
        raise PublicAPIError(404, "SESSION_NOT_FOUND", "Session not found.")

    seen = _stored_event_ids(db, principal, session_id)
    accepted = 0
    duplicates = 0
    for event in body.events:
        if event.event_id in seen:
            duplicates += 1
            continue
        seen.add(event.event_id)
        db.add(_to_message(principal.tenant_id, session_id, event))
        accepted += 1

    if accepted:
        db.commit()
    return ExternalEventsBatchResult(accepted=accepted, duplicates=duplicates)


def _stored_event_ids(db: Session, principal: PublicPrincipal, session_id: str) -> set[str]:
    """汇总该会话已落库的事件 id，用于整批重投时的逐事件去重。"""
    rows = db.exec(
        select(Message).where(
            Message.tenant_id == principal.tenant_id,
            Message.session_id == session_id,
        )
    ).all()
    return {
        str(row.metadata_json[EVENT_KEY])
        for row in rows
        if isinstance(row.metadata_json, dict) and EVENT_KEY in row.metadata_json
    }


def _to_message(tenant_id: str, session_id: str, event) -> Message:
    """把回放事件映射成 Message 行，并把事件 id 写入 metadata。

    message 事件保留原 role 与内容；tool_call 事件落成 ``tool`` 行，
    usage 事件落成无正文的 ``system`` 行，均可被既有会话记录查询到。
    """
    metadata: dict = {EVENT_KEY: event.event_id}
    if event.type == "message":
        role = event.role
        content = event.content
        metadata.update(event.metadata)
    elif event.type == "tool_call":
        # Stored as a `tool` role row so the existing transcript view shows it
        # in place, next to the turn that produced it.
        role = "tool"
        content = event.name
        metadata.update(
            {
                "tool_name": event.name,
                "status": event.status,
                "duration_ms": event.duration_ms,
                "error_code": event.error_code,
            }
        )
    else:
        # A usage record has no user-visible text; keeping it as a `system` row
        # makes it queryable without inventing a table.
        role = "system"
        content = ""
        metadata.update(
            {
                "prompt_tokens": event.prompt_tokens,
                "completion_tokens": event.completion_tokens,
                "total_tokens": event.total_tokens,
            }
        )
    return Message(
        id=new_id("msg"),
        tenant_id=tenant_id,
        session_id=session_id,
        role=role,
        content=content,
        metadata_json=metadata,
        created_at=event.created_at or utc_now(),
    )
