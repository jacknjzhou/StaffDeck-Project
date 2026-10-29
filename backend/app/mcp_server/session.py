from __future__ import annotations

import secrets
import time

SESSION_TTL_SECONDS = 3600
ISSUED_SESSIONS: dict[str, float] = {}


def issue_session_id() -> str:
    """签发新的 MCP 会话 id，并按 SESSION_TTL_SECONDS 记下其到期时刻。

    返回值登记进模块级 ISSUED_SESSIONS；签发后顺带清理已过期条目，
    避免长跑进程里这张注册表无界增长。
    """
    session_id = secrets.token_urlsafe(24)
    ISSUED_SESSIONS[session_id] = time.monotonic() + SESSION_TTL_SECONDS
    _evict_expired()
    return session_id


def session_is_live(session_id: str | None) -> bool:
    """判断给定 MCP 会话 id 是否仍在有效期内。

    空 id、未签发过的 id 和已过期的 id 都返回 False；命中的过期条目会
    顺手从 ISSUED_SESSIONS 中删除。
    """
    if not session_id:
        return False
    expiry = ISSUED_SESSIONS.get(session_id)
    if expiry is None:
        return False
    if expiry < time.monotonic():
        ISSUED_SESSIONS.pop(session_id, None)
        return False
    return True


def _evict_expired() -> None:
    """清理 ISSUED_SESSIONS 中已过期的会话 id，控制注册表的内存占用。"""
    now = time.monotonic()
    for key in [key for key, expiry in ISSUED_SESSIONS.items() if expiry < now]:
        ISSUED_SESSIONS.pop(key, None)
