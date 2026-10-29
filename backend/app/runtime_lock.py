from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from app.config import get_settings
from app.db.database_path import sqlite_database_path


class RuntimeInstanceLockError(RuntimeError):
    """当另一个 StaffDeck 进程已占用同一 SQLite 运行时锁时抛出。"""


_lock_handle: IO[str] | None = None


def _try_lock(handle: IO[str]) -> None:
    """对锁文件句柄做非阻塞排他加锁，锁被其他进程占用时抛出 OSError。

    Windows 上先确保文件至少有一个字节，msvcrt.locking 才能锁住第 1 个字节。
    """
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write("0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[str]) -> None:
    """释放 _try_lock 加上的文件锁，按平台调用对应的解锁 API。"""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_runtime_instance_lock() -> Path | None:
    """为每个 SQLite 数据库保证只有一个应用进程在运行。

    Uvicorn 会先执行 FastAPI startup 钩子再绑定监听端口，因此指向同一端口的第二个
    服务可能在 bind 失败前就跑完恢复任务等启动副作用；持有以数据库为范围的进程锁
    可以阻止这个注定失败的实例进入这些钩子。锁已被本进程持有时直接返回锁文件路径，
    数据库不是 SQLite 时返回 None。
    """

    global _lock_handle
    if _lock_handle is not None:
        return Path(_lock_handle.name)

    database_path = sqlite_database_path(get_settings().database_url)
    if database_path is None:
        return None

    lock_path = database_path.with_name(f"{database_path.name}.runtime.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        _try_lock(handle)
    except OSError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "unknown"
        handle.close()
        raise RuntimeInstanceLockError(
            f"Another StaffDeck process already owns {database_path} (pid={owner})."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _lock_handle = handle
    return lock_path


def release_runtime_instance_lock() -> None:
    """释放本进程持有的运行时锁并关闭锁文件句柄；未持有时为空操作。"""
    global _lock_handle
    handle = _lock_handle
    if handle is None:
        return
    _lock_handle = None
    try:
        _unlock(handle)
    finally:
        handle.close()
