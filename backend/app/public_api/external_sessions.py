from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import ChatSession, ExternalSessionBinding, Message, new_id, utc_now
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.schemas import (
    ExternalEventsBatch,
    ExternalEventsBatchResult,
    ExternalSessionCreate,
)

router = APIRouter(prefix="/external-sessions", tags=["external-sessions"])

# Marks the event a row came from. Stored inside metadata so a resend of the
# same batch can be told apart from a genuinely repeated message.
EVENT_KEY = "external_event_id"


def _binding_for(
    db: Session, principal: PublicPrincipal, external_session_id: str
) -> ExternalSessionBinding | None:
    if not principal.credential_id:
        raise PublicAPIError(
            403, "API_KEY_REQUIRED", "External sessions are addressed by credential."
        )
    return db.exec(
        select(ExternalSessionBinding).where(
            ExternalSessionBinding.credential_id == principal.credential_id,
            ExternalSessionBinding.external_session_id == external_session_id,
        )
    ).first()


@router.post("", response_model=dict)
def upsert_external_session(
    body: ExternalSessionCreate,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("sessions:write")),
    db: Session = Depends(get_session),
) -> dict:
    """Announce a locally-created session, creating the server side on first sight.

    Reuses `create_public_session_row` instead of its own insert so the
    "same external id → same server session" rule is written once: the run
    endpoint and this one must never disagree about it, or a conversation gets
    two rows and its messages land in whichever one the caller saw last.
    """
    from app.public_api.schemas import PublicSessionCreate
    from app.public_api.sessions import create_public_session_row

    existing = _binding_for(db, principal, body.external_session_id)
    if existing:
        if existing.agent_id != body.agent_id:
            raise PublicAPIError(
                409,
                "EXTERNAL_SESSION_CONFLICT",
                "The external session is already bound to another agent.",
            )
        if not db.get(ChatSession, existing.session_id):
            raise PublicAPIError(404, "SESSION_NOT_FOUND", "Session not found.")
        response.status_code = 200
        return {
            "session_id": existing.session_id,
            "external_session_id": existing.external_session_id,
            "agent_id": existing.agent_id,
            "created": False,
        }

    row, _ = create_public_session_row(
        db,
        principal,
        body.agent_id,
        PublicSessionCreate(
            external_session_id=body.external_session_id,
            external_user_id=body.external_user_id,
            title=body.title,
            metadata=body.metadata,
        ),
    )
    response.status_code = 201
    return {
        "session_id": row.id,
        "external_session_id": body.external_session_id,
        "agent_id": body.agent_id,
        "created": True,
    }


@router.post("/{session_id}/events:batch", response_model=ExternalEventsBatchResult)
def append_events(
    session_id: str,
    body: ExternalEventsBatch,
    principal: PublicPrincipal = Depends(require_scopes("sessions:write")),
    db: Session = Depends(get_session),
) -> ExternalEventsBatchResult:
    """Append a batch of local events to a session.

    Idempotency is per event: a retry resends the whole batch, so a stored event
    must be skipped rather than rejecting the batch that carries it.
    """
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != principal.tenant_id:
        # Deliberately 404 rather than 403: the id belongs to another tenant, and
        # confirming it exists would leak that.
        raise PublicAPIError(404, "SESSION_NOT_FOUND", "Session not found.")

    # The credential must own this session through a binding. Without this a key
    # could append to any same-tenant session it can name.
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
    rows = db.exec(
        select(Message).where(
            Message.tenant_id == principal.tenant_id,
            Message.session_id == session_id,
        )
    ).all()
    return {
        str(entry[EVENT_KEY])
        for row in rows
        if isinstance(row.metadata_json, dict) and EVENT_KEY in row.metadata_json
        for entry in [row.metadata_json]
    }


def _to_message(tenant_id: str, session_id: str, event) -> Message:
    metadata: dict = {EVENT_KEY: event.event_id}
    if event.type == "message":
        role = event.role
        content = event.content
        metadata.update(event.metadata)
    elif event.type == "tool_call":
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
