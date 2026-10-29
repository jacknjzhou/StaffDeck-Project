from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.db.models import AgentProfile, User
from app.security.auth import ensure_current_user_tenant, get_current_user

ADMIN_ROLE = "admin"
MEMBER_ROLE = "member"
USER_ROLES = {ADMIN_ROLE, MEMBER_ROLE}


def is_admin_user(current_user: User) -> bool:
    """判断当前用户是否为管理员，即 role 等于 ADMIN_ROLE。"""
    return current_user.role == ADMIN_ROLE


def ensure_tenant_admin(tenant_id: str, current_user: User) -> User:
    """确认调用者属于该租户且是管理员，通过后回传用户对象，否则抛 403/租户不匹配错误。"""
    ensure_current_user_tenant(tenant_id, current_user)
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Only administrator can manage tenant settings")
    return current_user


def require_tenant_admin(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
) -> User:
    """FastAPI 依赖：从查询参数取 tenant_id 并要求调用者是该租户管理员。"""
    return ensure_tenant_admin(tenant_id, current_user)


def require_agent_scope_viewer(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> User:
    """FastAPI 依赖：校验租户归属，并在指定 agent_id 时要求该员工可被查看。

    可查看的条件是管理员、总控员工、创建者或已发布到画廊；agent_id 缺省时只校验租户。
    员工不存在或属于别的租户返回 404，无权访问返回 403。
    """
    ensure_current_user_tenant(tenant_id, current_user)
    if not agent_id:
        return current_user
    row = db.get(AgentProfile, agent_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if (
        is_admin_user(current_user)
        or row.is_overall
        or agent_owned_by_user(row, current_user)
        or (row.metadata_json or {}).get("published_to_gallery") is True
    ):
        return current_user
    raise HTTPException(status_code=403, detail="Cannot access this staff")


def ensure_open_gallery_admin(tenant_id: str, current_user: User) -> None:
    """校验调用者是该租户管理员，供开放画廊等仅管理员可用的配置入口复用。"""
    ensure_tenant_admin(tenant_id, current_user)


def ensure_agent_scope_manager(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
    current_user: User,
) -> AgentProfile | None:
    """校验调用者可以管理目标员工并返回该员工行；agent_id 缺省时返回 None。

    管理员可管理任意员工，但总控员工只有管理员能动；其余用户必须是创建者（owner_user_id 命中）。
    员工不存在或跨租户返回 404，越权返回 403。
    """
    ensure_current_user_tenant(tenant_id, current_user)
    if not agent_id:
        return None
    row = db.get(AgentProfile, agent_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if is_admin_user(current_user):
        return row
    if row.is_overall:
        raise HTTPException(status_code=403, detail="Only administrator can manage overall agent")
    if agent_owned_by_user(row, current_user):
        return row
    raise HTTPException(status_code=403, detail="Only the creator or administrator can manage this staff")


def agent_owned_by_user(row: AgentProfile, user: User) -> bool:
    """判断员工是否由该用户创建，依据 metadata_json 里的 owner_user_id。"""
    metadata = row.metadata_json or {}
    return metadata.get("owner_user_id") == user.id
