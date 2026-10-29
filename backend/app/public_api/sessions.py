from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import AgentProfile, ChatSession, ExternalSessionBinding, utc_now
from app.public_api.auth import PublicPrincipal, enforce_agent_access, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.idempotency import replay_idempotent_response, store_idempotent_response
from app.public_api.schemas import PublicSessionCreate, PublicSessionUpdate
from app.public_api.utils import etag_for


router = APIRouter(prefix="/agents/{agent_id}/sessions", tags=["sessions"])


def _session_payload(row: ChatSession, binding: ExternalSessionBinding | None = None) -> dict:
    """把会话行与可选的外部会话绑定序列化成公开 API 响应体，时间戳统一带 Z 后缀。"""
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "external_session_id": binding.external_session_id if binding else None,
        "external_user_id": binding.external_user_id if binding else None,
        "title": row.title,
        "status": row.status,
        "active_sop_id": row.active_skill_id,
        "active_step_id": row.active_step_id,
        "pending_tasks": list(row.pending_tasks_json or []),
        "awaiting_input": dict(row.awaiting_input_json or {}) or None,
        "summary": row.summary,
        "metadata": dict(binding.metadata_json or {}) if binding else {},
        "created_at": row.created_at.isoformat() + "Z",
        "updated_at": row.updated_at.isoformat() + "Z",
    }


def ensure_public_agent(db: Session, principal: PublicPrincipal, agent_id: str) -> None:
    """确认员工存在、属于本租户且未归档；不可见一律按 404 AGENT_NOT_FOUND 处理。"""
    enforce_agent_access(principal, agent_id)
    row = db.get(AgentProfile, agent_id)
    if row and row.tenant_id == principal.tenant_id and row.status != "archived":
        return
    raise PublicAPIError(404, "AGENT_NOT_FOUND", "Agent not found.")


def owned_public_session(
    db: Session,
    principal: PublicPrincipal,
    agent_id: str,
    session_id: str,
) -> tuple[ChatSession, ExternalSessionBinding | None]:
    """取主体名下的会话：校验员工访问权与租户/员工/用户归属，再按凭据查外部会话绑定。

    归属不满足统一抛 404 SESSION_NOT_FOUND，避免泄露他人会话是否存在；
    无 credential_id（用户 token 调用）时不返回外部绑定。
    """
    ensure_public_agent(db, principal, agent_id)
    row = db.get(ChatSession, session_id)
    if (
        not row
        or row.tenant_id != principal.tenant_id
        or row.agent_id != agent_id
        or row.user_id != principal.actor_user.id
    ):
        raise PublicAPIError(404, "SESSION_NOT_FOUND", "Session not found.")
    binding = None
    if principal.credential_id:
        binding = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.tenant_id == principal.tenant_id,
                ExternalSessionBinding.credential_id == principal.credential_id,
                ExternalSessionBinding.session_id == row.id,
            )
        ).first()
    return row, binding


def create_public_session_row(
    db: Session,
    principal: PublicPrincipal,
    agent_id: str,
    body: PublicSessionCreate,
) -> tuple[ChatSession, ExternalSessionBinding | None]:
    """创建 channel 为 public_api 的会话，并按需写入 external_session_id 幂等绑定。

    同一凭据下该 external_session_id 已绑定同员工时直接复用原会话，绑定到其他员工抛
    409 EXTERNAL_SESSION_CONFLICT；主体没有 credential_id 时抛 403 API_KEY_REQUIRED。
    """
    ensure_public_agent(db, principal, agent_id)
    if not principal.credential_id:
        raise PublicAPIError(403, "API_KEY_REQUIRED", "Public sessions require an API key.")
    if body.external_session_id:
        existing = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.credential_id == principal.credential_id,
                ExternalSessionBinding.external_session_id == body.external_session_id,
            )
        ).first()
        if existing:
            if existing.agent_id != agent_id:
                raise PublicAPIError(
                    409,
                    "EXTERNAL_SESSION_CONFLICT",
                    "The external session is already bound to another agent.",
                )
            row = db.get(ChatSession, existing.session_id)
            if row:
                return row, existing
    from app.db.models import new_id

    row = ChatSession(
        id=new_id("session"),
        tenant_id=principal.tenant_id,
        user_id=principal.actor_user.id,
        agent_id=agent_id,
        title=(body.title or "新会话").strip()[:200],
        channel="public_api",
    )
    db.add(row)
    db.flush()
    binding = None
    if body.external_session_id:
        binding = ExternalSessionBinding(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            agent_id=agent_id,
            external_session_id=body.external_session_id,
            external_user_id=body.external_user_id,
            session_id=row.id,
            metadata_json=body.metadata,
        )
        db.add(binding)
    db.commit()
    db.refresh(row)
    if binding:
        db.refresh(binding)
    return row, binding


@router.post("", response_model=dict, status_code=201)
def create_session(
    agent_id: str,
    body: PublicSessionCreate,
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("sessions:write")),
    db: Session = Depends(get_session),
) -> dict:
    """POST 创建会话：命中 Idempotency-Key 时回放旧响应，否则建行、回 ETag 并登记响应。"""
    replay = replay_idempotent_response(db, principal, request, body.model_dump(mode="json"))
    if replay:
        response.status_code = replay[0]
        return replay[1]
    row, binding = create_public_session_row(db, principal, agent_id, body)
    payload = _session_payload(row, binding)
    response.headers["ETag"] = etag_for(payload)
    store_idempotent_response(
        db,
        principal,
        request,
        body.model_dump(mode="json"),
        payload,
        status_code=201,
        resource_id=row.id,
    )
    return payload


@router.get("", response_model=dict)
def list_sessions(
    agent_id: str,
    limit: int = 50,
    principal: PublicPrincipal = Depends(require_scopes("sessions:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出当前主体在该员工下的 public_api 会话，按更新时间倒序，limit 收敛到 1-100。

    同一凭据的外部会话绑定会一并回填；next_cursor 恒为 None（暂不支持分页游标）。
    """
    ensure_public_agent(db, principal, agent_id)
    rows = db.exec(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == principal.tenant_id,
            ChatSession.agent_id == agent_id,
            ChatSession.user_id == principal.actor_user.id,
            ChatSession.channel == "public_api",
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    bindings = {
        binding.session_id: binding
        for binding in db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.tenant_id == principal.tenant_id,
                ExternalSessionBinding.credential_id == principal.credential_id,
                ExternalSessionBinding.session_id.in_([row.id for row in rows]),
            )
        ).all()
    } if rows and principal.credential_id else {}
    return {"data": [_session_payload(row, bindings.get(row.id)) for row in rows], "next_cursor": None}


@router.get("/{session_id}", response_model=dict)
def get_session(
    agent_id: str,
    session_id: str,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("sessions:read")),
    db: Session = Depends(get_session),
) -> dict:
    """读取单个会话详情并回写 ETag，供后续 PATCH 用 If-Match 做乐观锁。"""
    row, binding = owned_public_session(db, principal, agent_id, session_id)
    payload = _session_payload(row, binding)
    response.headers["ETag"] = etag_for(payload)
    return payload


@router.patch("/{session_id}", response_model=dict)
def update_session(
    agent_id: str,
    session_id: str,
    body: PublicSessionUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    principal: PublicPrincipal = Depends(require_scopes("sessions:write")),
    db: Session = Depends(get_session),
) -> dict:
    """PATCH 更新会话标题、状态与绑定元数据：必须带 If-Match 且与当前 ETag 一致。

    缺 If-Match 抛 428 IF_MATCH_REQUIRED，ETag 不匹配抛 412 ETAG_MISMATCH；
    只改请求体里显式给出的字段，成功后返回新响应体与新 ETag。
    """
    row, binding = owned_public_session(db, principal, agent_id, session_id)
    current = _session_payload(row, binding)
    if not if_match:
        raise PublicAPIError(428, "IF_MATCH_REQUIRED", "If-Match is required for this update.")
    if if_match != etag_for(current):
        raise PublicAPIError(412, "ETAG_MISMATCH", "The session changed since it was read.")
    changes = body.model_dump(exclude_unset=True)
    if "title" in changes:
        row.title = (changes["title"] or "新会话").strip()[:200]
    if "status" in changes:
        row.status = changes["status"]
    if binding and "metadata" in changes:
        binding.metadata_json = changes["metadata"] or {}
        binding.updated_at = utc_now()
        db.add(binding)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    payload = _session_payload(row, binding)
    response.headers["ETag"] = etag_for(payload)
    return payload
