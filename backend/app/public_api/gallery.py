from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlmodel import Session

from app.api import agents as internal_agents
from app.agents.schema import AgentProfileRead
from app.db import get_session
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.idempotency import replay_idempotent_response, store_idempotent_response
from app.public_api.utils import decode_cursor, encode_cursor


router = APIRouter(prefix="/gallery/agents", tags=["gallery"])


def _payload(value: object) -> dict:
    """把员工档案对象或字典转成对外 JSON,去掉 tenant_id 并按元数据补 added 标记。"""
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise TypeError("Unsupported gallery agent payload")
    payload.pop("tenant_id", None)
    metadata = dict(payload.get("metadata") or {})
    payload["added"] = bool(
        metadata.get("used_by_current_user")
        or metadata.get("chat_used_by_current_user")
    )
    return payload


def visible_gallery_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfileRead]:
    """列出该主体可在画廊看到并使用的员工:非 overall、active 且已发布到画廊。"""
    rows = internal_agents.list_agents(
        principal.tenant_id,
        db,
        principal.actor_user,
    )
    return [
        row
        for row in rows
        if not row.is_overall
        and row.status == "active"
        and (row.metadata or {}).get("published_to_gallery") is True
    ]


# Kept as an alias so existing call sites and tests keep working.
_gallery_agents = visible_gallery_agents


@router.get("", response_model=dict)
def list_gallery_agents(
    query: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    principal: PublicPrincipal = Depends(require_scopes("gallery:read")),
    db: Session = Depends(get_session),
) -> dict:
    """分页返回画廊员工,按创建时间倒序,支持关键词过滤与游标翻页。"""
    rows = sorted(
        _gallery_agents(db, principal),
        key=lambda row: (str(row.created_at), row.id),
        reverse=True,
    )
    normalized_query = (query or "").strip().casefold()
    if normalized_query:
        rows = [
            row
            for row in rows
            if normalized_query
            in json.dumps(_payload(row), ensure_ascii=False, default=str).casefold()
        ]

    decoded_cursor = decode_cursor(cursor)
    if cursor and decoded_cursor is None:
        raise PublicAPIError(400, "INVALID_CURSOR", "Gallery cursor is invalid.")
    if decoded_cursor:
        rows = [
            row
            for row in rows
            if (str(row.created_at), row.id) < decoded_cursor
        ]

    page = rows[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].id)
        if has_more and page
        else None
    )
    return {
        "data": [_payload(row) for row in page],
        "next_cursor": next_cursor,
    }


@router.get("/{agent_id}", response_model=dict)
def get_gallery_agent(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("gallery:read")),
    db: Session = Depends(get_session),
) -> dict:
    """按 agent_id 取画廊中可见的单个员工,不可见时抛 404 GALLERY_AGENT_NOT_FOUND。"""
    row = next((item for item in _gallery_agents(db, principal) if item.id == agent_id), None)
    if not row:
        raise PublicAPIError(404, "GALLERY_AGENT_NOT_FOUND", "Gallery agent not found.")
    return _payload(row)


@router.post("/{agent_id}:add", response_model=dict)
def add_gallery_agent(
    agent_id: str,
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("gallery:use")),
    db: Session = Depends(get_session),
) -> dict:
    """把画廊员工加入当前用户的聊天列表,幂等重放命中时直接返回已存响应。"""
    payload = {"agent_id": agent_id}
    replay = replay_idempotent_response(db, principal, request, payload)
    if replay:
        response.status_code = replay[0]
        return replay[1]
    if not any(item.id == agent_id for item in _gallery_agents(db, principal)):
        raise PublicAPIError(404, "GALLERY_AGENT_NOT_FOUND", "Gallery agent not found.")
    selected = internal_agents.use_chat_agent(
        agent_id,
        principal.tenant_id,
        principal.actor_user,
        db,
    )
    result = _payload(selected)
    result["added"] = True
    store_idempotent_response(
        db,
        principal,
        request,
        payload,
        result,
        status_code=200,
        resource_id=agent_id,
    )
    return result
