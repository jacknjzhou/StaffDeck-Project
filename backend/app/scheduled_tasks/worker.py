from __future__ import annotations

import argparse
import signal
import threading
from time import sleep

from sqlmodel import Session

from app.db import engine, init_db
from app.db.seed import seed_demo_data
from app.scheduled_tasks.service import WORKER_SLEEP_SECONDS, due_scheduled_tasks, execute_scheduled_task


_stopped = False
_background_thread: threading.Thread | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    """置位全局停止标志，供 SIGTERM/SIGINT 信号处理调用。"""
    global _stopped
    _stopped = True


def run_worker(*, once: bool = False, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    """初始化数据库并轮询执行到期的定时任务，直到收到停止标志。

    `once=True` 时只扫描执行一轮便返回；相邻两轮之间至少间隔 1 秒。
    """
    init_db()
    with Session(engine) as db:
        seed_demo_data(db)
    while not _stopped:
        with Session(engine) as db:
            due = due_scheduled_tasks(db)
            for task in due:
                execute_scheduled_task(db, task)
        if once:
            return
        sleep(max(1.0, poll_seconds))


def start_background_worker(*, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    """在守护线程中启动后台定时任务 worker；已有存活线程时直接返回。"""
    global _background_thread, _stopped
    if _background_thread and _background_thread.is_alive():
        return
    _stopped = False
    _background_thread = threading.Thread(
        target=run_worker,
        kwargs={"once": False, "poll_seconds": poll_seconds},
        name="ultrarag-scheduled-task-worker",
        daemon=True,
    )
    _background_thread.start()


def stop_background_worker() -> None:
    """置位停止标志，让后台 worker 在当前轮询结束后退出。"""
    global _stopped
    _stopped = True


def main() -> None:
    """解析命令行参数并注册停止信号，然后启动定时任务 worker。"""
    parser = argparse.ArgumentParser(description="Run StaffDeck scheduled task worker")
    parser.add_argument("--once", action="store_true", help="scan and execute due tasks once, then exit")
    parser.add_argument("--poll-seconds", type=float, default=WORKER_SLEEP_SECONDS)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_worker(once=args.once, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
