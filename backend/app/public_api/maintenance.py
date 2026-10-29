from __future__ import annotations

import threading

from app.public_api.jobs import cleanup_public_api_records
from app.public_api.webhooks import enqueue_due_webhook_deliveries


_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    """维护线程主循环：每 30 秒补投到期 webhook，每 120 个 tick 清理一次过期记录。"""
    cleanup_tick = 0
    while not _stop.wait(30):
        enqueue_due_webhook_deliveries()
        cleanup_tick += 1
        if cleanup_tick >= 120:
            cleanup_public_api_records()
            cleanup_tick = 0


def start_public_api_maintenance() -> None:
    """启动公共 API 维护线程；已有存活线程时直接返回，保证进程内只跑一个实例。"""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="public-api-maintenance", daemon=True)
    _thread.start()


def stop_public_api_maintenance() -> None:
    """通知维护线程退出，最多等待 2 秒收尾，并清空线程引用以便再次启动。"""
    global _thread
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
    _thread = None
