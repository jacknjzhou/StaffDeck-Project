# backend/app/distributed/lease.py
from __future__ import annotations

import os
import socket
import uuid
from typing import Any

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class RedisLease:
    """基于 `SET NX PX` 的 Redis 租约，释放与续期都校验持有者（单键栅栏）。"""

    def __init__(self, client: Any, key: str, ttl_seconds: int = 60, holder: str = "") -> None:
        """记录 Redis 客户端、租约键与 TTL，并生成缺省的持有者标识。"""
        self._client = client
        self._key = key
        self._ttl_ms = ttl_seconds * 1000
        self._holder = holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def acquire(self) -> bool:
        """以 `SET NX PX` 抢占租约，返回是否抢到。"""
        return bool(self._client.set(self._key, self._holder, nx=True, px=self._ttl_ms))

    def renew(self) -> bool:
        """用 Lua 脚本在持有者匹配时重置 TTL，返回是否续期成功。"""
        return bool(
            self._client.eval(_RENEW_LUA, 1, self._key, self._holder, self._ttl_ms)
        )

    def release(self) -> None:
        """仅在持有者匹配时删除租约键，避免释放掉别人的锁。"""
        self._client.eval(_RELEASE_LUA, 1, self._key, self._holder)


def _build_client(url: str) -> Any:
    """按 URL 构造租约用的 Redis 客户端。

    放在模块级是为了让测试可以用 fakeredis 之类的假客户端 monkeypatch，
    而不必连接真实 Redis。
    """
    from redis import Redis

    return Redis.from_url(url, decode_responses=True)


def acquire_role_lease(role: str, ttl_seconds: int = 60) -> RedisLease | None:
    """获取某个进程角色的租约；未配置 Redis 时返回 `None` 表示跳过。

    已有其他进程持有该角色的租约时抛 `RuntimeError`，调用方据此拒绝启动。
    """
    from app.config import get_settings

    url = get_settings().redis_url
    if not url:
        return None
    client = _build_client(url)
    lease = RedisLease(client, f"staffdeck:lease:{role}", ttl_seconds=ttl_seconds)
    if not lease.acquire():
        raise RuntimeError(f"another {role} process holds the lease")
    return lease
