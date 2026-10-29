from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import AgentProfile, ChatSession, MemoryRecord, User
from app.memory.service import memory_agent_id, memory_matches_agent, memory_read, memory_rows_for_read
from app.security.auth import get_current_user, require_current_tenant
from app.security.permissions import agent_owned_by_user, is_admin_user
from app.security.tenant import ensure_tenant


router = APIRouter(prefix="/api/enterprise/memories", tags=["enterprise:memories"])


@router.get("")
def list_memories(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    username: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(require_current_tenant),
    db: Session = Depends(get_session),
) -> list[dict]:
    """按可见范围列出记忆，普通用户只能看到自己的记录。

    管理员或 agent 拥有者可跨用户查询；按 agent 过滤时先用会话归属补齐缺失的
    agent_id，所以会多取若干行再截断；conversation 类记忆不在列表内。
    """
    ensure_tenant(db, tenant_id)
    can_view_all = _can_view_all_memories(db, tenant_id, agent_id, current_user)
    if not can_view_all and user_id and user_id != current_user.id:
        return []
    if not can_view_all and username and username != current_user.username:
        return []
    statement = select(MemoryRecord).where(
        MemoryRecord.tenant_id == tenant_id,
        MemoryRecord.kind != "conversation",
    )
    if can_view_all and user_id:
        statement = statement.where(MemoryRecord.user_id == user_id)
    elif not can_view_all:
        statement = statement.where(MemoryRecord.user_id == current_user.id)
    if can_view_all and username:
        statement = statement.where(MemoryRecord.username == username)
    fetch_limit = limit * 5 if agent_id else limit
    rows = list(db.exec(statement.order_by(MemoryRecord.updated_at.desc()).limit(fetch_limit)).all())
    session_agents = _session_agent_map(db, rows) if agent_id else {}
    if agent_id:
        rows = [row for row in rows if _memory_matches_agent(row, agent_id, session_agents)]
    rows = memory_rows_for_read(rows[:limit])
    if q:
        needle = q.strip().lower()
        rows = [row for row in rows if needle in row.content.lower() or needle in (row.username or "").lower()]
    return [_memory_read_with_inferred_agent(row, session_agents) for row in rows]


@router.delete("/me")
def clear_my_memories(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """清空当前用户在指定租户下的记忆，可用 agent_id 只删该员工的记录。

    只删除 kind 非 conversation 的行，返回实际删除条数；租户不匹配时直接 403。
    """
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    ensure_tenant(db, tenant_id)
    rows = list(
        db.exec(
            select(MemoryRecord)
            .where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == current_user.id,
                MemoryRecord.kind != "conversation",
            )
            .order_by(MemoryRecord.updated_at.desc())
        ).all()
    )
    session_agents = _session_agent_map(db, rows) if agent_id else {}
    if agent_id:
        rows = [row for row in rows if _memory_matches_agent(row, agent_id, session_agents)]
    deleted = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": deleted}


def _session_agent_map(db: Session, rows: list[MemoryRecord]) -> dict[str, str | None]:
    """为缺少 agent_id 的记忆批量查出所属会话的员工，未命中的会话不入表。"""
    session_ids = sorted({row.session_id for row in rows if row.session_id and not memory_agent_id(row)})
    if not session_ids:
        return {}
    sessions = db.exec(select(ChatSession).where(ChatSession.id.in_(session_ids))).all()
    return {session.id: session.agent_id for session in sessions}


def _can_view_all_memories(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
    current_user: User,
) -> bool:
    """判断当前用户能否查看该租户下的全部记忆。

    管理员直接放行；指定 agent_id 时要求该 agent 属于当前用户，跨租户或不存在则 404。
    """
    if is_admin_user(current_user):
        return True
    if not agent_id:
        return False
    agent = db.get(AgentProfile, agent_id)
    if not agent or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_owned_by_user(agent, current_user)


def _memory_matches_agent(row: MemoryRecord, agent_id: str, session_agents: dict[str, str | None]) -> bool:
    """判断记忆是否属于指定 agent；没有显式 agent_id 时回退到会话归属。"""
    if memory_matches_agent(row, agent_id):
        return True
    if memory_agent_id(row):
        return False
    return bool(row.session_id) and session_agents.get(row.session_id) == agent_id


def _memory_read_with_inferred_agent(row: MemoryRecord, session_agents: dict[str, str | None]) -> dict:
    """把记忆转成读取结构，并为缺少 agent_id 的记录补上会话推导值及来源标记。"""
    payload = memory_read(row)
    if memory_agent_id(row) or not row.session_id:
        return payload
    inferred_agent_id = session_agents.get(row.session_id)
    if inferred_agent_id:
        metadata = dict(payload.get("metadata") or {})
        metadata["agent_id"] = inferred_agent_id
        metadata["agent_id_source"] = "session"
        payload["metadata"] = metadata
    return payload
