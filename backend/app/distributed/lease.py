# backend/app/distributed/lease.py
from __future__ import annotations

import os
import socket
import uuid
from typing import Any

from redis import Redis

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class RedisLease:
    """SET NX PX lease with owner-checked release/renew (single-key fence)."""

    def __init__(self, client: Any, key: str, ttl_seconds: int = 60, holder: str = "") -> None:
        self._client = client
        self._key = key
        self._ttl_ms = ttl_seconds * 1000
        self._holder = holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def acquire(self) -> bool:
        return bool(self._client.set(self._key, self._holder, nx=True, px=self._ttl_ms))

    def renew(self) -> bool:
        return bool(
            self._client.eval(_RENEW_LUA, 1, self._key, self._holder, self._ttl_ms)
        )

    def release(self) -> None:
        self._client.eval(_RELEASE_LUA, 1, self._key, self._holder)


def acquire_role_lease(role: str, ttl_seconds: int = 60) -> RedisLease | None:
    """Acquire the process-role lease; None means 'no redis configured, skip'."""
    from app.config import get_settings

    url = get_settings().redis_url
    if not url:
        return None
    client = Redis.from_url(url, decode_responses=True)
    lease = RedisLease(client, f"staffdeck:lease:{role}", ttl_seconds=ttl_seconds)
    if not lease.acquire():
        raise RuntimeError(f"another {role} process holds the lease")
    return lease
