from __future__ import annotations

import secrets
import time

SESSION_TTL_SECONDS = 3600
ISSUED_SESSIONS: dict[str, float] = {}


def issue_session_id() -> str:
    session_id = secrets.token_urlsafe(24)
    ISSUED_SESSIONS[session_id] = time.monotonic() + SESSION_TTL_SECONDS
    _evict_expired()
    return session_id


def session_is_live(session_id: str | None) -> bool:
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
    now = time.monotonic()
    for key in [key for key, expiry in ISSUED_SESSIONS.items() if expiry < now]:
        ISSUED_SESSIONS.pop(key, None)
