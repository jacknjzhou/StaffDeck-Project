from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any

_BUSY_MESSAGE = "该会话已有一个 Harness 执行正在进行，请等待其结束后重试。"
_REDIS_KEY_PREFIX = "staffdeck:harness_session:"
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
# 进程崩溃时跨进程锁靠 TTL 自动失效；单个 Harness 回合通常远短于该值。
DEFAULT_LOCK_TTL_SECONDS = 3600
_BACKENDS = ("redis", "memory")


class HarnessSessionBusy(RuntimeError):
    """同一 Harness 会话已有执行在跑时抛出，供上层转成忙碌提示或冲突响应。"""


def _settings() -> Any:
    """延迟读取设置，避免模块导入时锁定 redis_url（测试可替换 Settings）。"""
    from app.config import get_settings

    return get_settings()


def _redis_key(session_id: str) -> str:
    """把会话 ID 映射成 Redis 锁键。"""
    return f"{_REDIS_KEY_PREFIX}{session_id}"


@dataclass(frozen=True)
class HarnessSessionLockHandle:
    """acquire 返回、release 原样交回的句柄。

    redis 后端用 token 校验持有者，避免过期后误删他人重新抢到的锁；
    memory 后端携带进程内 threading.Lock，释放即 unlock。
    """

    session_id: str
    backend: str
    token: str = ""
    memory_lock: Lock | None = None


class HarnessSessionLock:
    """Harness 会话互斥锁，支持 redis（跨进程）与 memory（单进程）两种后端。

    backend 留空时延迟按 `get_settings().redis_url` 选择：配置了 Redis 就走
    `SET NX PX` 跨进程锁，否则退回进程内字典。生产环境缺 Redis 时
    `acquire_role_lease` 已先行快速失败，因此这里的 memory 兜底只影响开发与
    测试，不会让生产在无锁状态下静默运行。
    """

    def __init__(
        self,
        backend: str = "",
        client: Any | None = None,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> None:
        """记录后端选择、可注入的 Redis 客户端与锁 TTL。"""
        if backend and backend not in _BACKENDS:
            raise ValueError(f"unsupported harness session lock backend: {backend}")
        self._backend = backend
        self._client = client
        self._ttl_ms = ttl_seconds * 1000
        self._registry_lock = Lock()
        self._memory_locks: dict[str, Lock] = {}

    @property
    def backend(self) -> str:
        """返回生效的后端名；未显式指定时按当前 redis_url 延迟解析。"""
        if self._backend:
            return self._backend
        return "redis" if str(_settings().redis_url or "").strip() else "memory"

    def acquire(self, session_id: str) -> HarnessSessionLockHandle:
        """非阻塞抢占指定会话的锁；已被占用时抛 HarnessSessionBusy。

        返回的句柄必须原样交给 release；session_id 去空格后为空则抛 ValueError。
        """
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("session_id is required")
        if self.backend == "redis":
            return self._acquire_redis(normalized)
        return self._acquire_memory(normalized)

    def release(self, handle: HarnessSessionLockHandle | None) -> None:
        """释放 acquire 返回的句柄；handle 为 None 时静默返回。

        redis 后端只在 token 仍匹配时删键，token 不匹配说明锁已过期并被他人
        接管，此时不能删掉对方的锁。
        """
        if handle is None:
            return
        if handle.backend == "redis":
            self._redis_client().eval(
                _RELEASE_LUA, 1, _redis_key(handle.session_id), handle.token
            )
            return
        if handle.memory_lock is not None:
            handle.memory_lock.release()

    def _acquire_memory(self, session_id: str) -> HarnessSessionLockHandle:
        """进程内字典后端：同一会话复用一把 threading.Lock。"""
        with self._registry_lock:
            lock = self._memory_locks.setdefault(session_id, Lock())
        if not lock.acquire(blocking=False):
            raise HarnessSessionBusy(_BUSY_MESSAGE)
        return HarnessSessionLockHandle(
            session_id=session_id, backend="memory", memory_lock=lock
        )

    def _acquire_redis(self, session_id: str) -> HarnessSessionLockHandle:
        """Redis 后端：`SET key token NX PX ttl`，失败即表示其他进程持有。"""
        token = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        acquired = self._redis_client().set(
            _redis_key(session_id), token, nx=True, px=self._ttl_ms
        )
        if not acquired:
            raise HarnessSessionBusy(_BUSY_MESSAGE)
        return HarnessSessionLockHandle(session_id=session_id, backend="redis", token=token)

    def _redis_client(self) -> Any:
        """延迟构造 Redis 客户端；测试可通过 client 参数注入 fakeredis。"""
        if self._client is None:
            from redis import Redis

            self._client = Redis.from_url(str(_settings().redis_url), decode_responses=True)
        return self._client


_default_lock = HarnessSessionLock()


def acquire_harness_session(session_id: str) -> HarnessSessionLockHandle:
    """非阻塞地抢占指定会话的互斥锁，阻止同一会话并发执行 Harness。

    session_id 去空格后为空则抛 ValueError；锁已被占用时抛 HarnessSessionBusy。
    成功后返回锁句柄，调用方必须原样交给 release_harness_session 释放。
    """
    return _default_lock.acquire(session_id)


def release_harness_session(
    session_id: str, lock: HarnessSessionLockHandle | None
) -> None:
    """释放 acquire_harness_session 返回的锁；lock 为 None 时静默返回。

    session_id 只用于调用处的可读性，句柄里已记录后端与持有者 token。
    """
    _default_lock.release(lock)
