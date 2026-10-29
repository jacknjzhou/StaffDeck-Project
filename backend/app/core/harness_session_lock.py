from __future__ import annotations

from threading import Lock


class HarnessSessionBusy(RuntimeError):
    """同一 Harness 会话已有执行在跑时抛出，供上层转成忙碌提示或冲突响应。"""


_registry_lock = Lock()
_session_locks: dict[str, Lock] = {}


def acquire_harness_session(session_id: str) -> Lock:
    """非阻塞地抢占指定会话的进程内互斥锁，阻止同一会话并发执行 Harness。

    session_id 去空格后为空则抛 ValueError；锁已被占用时抛 HarnessSessionBusy。
    成功后返回锁对象，调用方必须原样交给 release_harness_session 释放。
    """
    normalized = str(session_id or "").strip()
    if not normalized:
        raise ValueError("session_id is required")
    with _registry_lock:
        lock = _session_locks.setdefault(normalized, Lock())
    if not lock.acquire(blocking=False):
        raise HarnessSessionBusy(
            "该会话已有一个 Harness 执行正在进行，请等待其结束后重试。"
        )
    return lock


def release_harness_session(session_id: str, lock: Lock | None) -> None:
    """释放 acquire_harness_session 返回的锁；lock 为 None 时静默返回。

    session_id 只用于调用处的可读性，注册表里保留锁对象以便后续复用同一把锁。
    """
    if lock is None:
        return
    lock.release()
