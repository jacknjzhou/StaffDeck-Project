# backend/app/core/runtime_state.py
"""进程级运行状态，作为就绪探针等外部读取方的单一事实来源。"""

from __future__ import annotations

import threading

# 角色租约已确认丢失：本进程不再能保证角色独占，就绪探针应据此返回未就绪。
lease_lost = threading.Event()


def mark_lease_lost() -> None:
    """置位租约丢失状态；幂等，重复调用不会改变结果。"""
    lease_lost.set()


def is_lease_lost() -> bool:
    """返回本进程是否已确认丢失角色租约。"""
    return lease_lost.is_set()
