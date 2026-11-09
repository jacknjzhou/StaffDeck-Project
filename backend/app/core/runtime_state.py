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


def clear_lease_lost() -> None:
    """清除租约丢失状态，用于续期恢复后让探针重新返回就绪。

    只由续期线程在成功续期后调用：它证明本进程重新握住了租约，此前的
    fencing 丢失只是一次可恢复的抖动。
    """
    lease_lost.clear()
