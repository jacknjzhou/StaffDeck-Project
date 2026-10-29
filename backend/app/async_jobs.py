from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from app.db.models import new_id, utc_now


AsyncJobStatus = str


@dataclass
class AsyncJob:
    """进程内后台任务的状态快照，记录名称、生命周期时间点与失败原因。"""

    id: str
    name: str
    status: AsyncJobStatus = "queued"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class AsyncJobQueue:
    """基于线程池的进程内任务队列，用锁保护状态变更并限制历史记录规模。"""

    def __init__(self, max_workers: int = 4, max_history: int = 500):
        """创建工作线程池与空的任务表，并按 max_history 约束历史条数。"""
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ultrarag-job")
        self._lock = Lock()
        self._jobs: dict[str, AsyncJob] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._max_history = max_history
        self._accepting = True

    @property
    def accepting(self) -> bool:
        """队列是否仍接收新任务；shutdown 之后变为 False。"""
        with self._lock:
            return self._accepting

    def enqueue(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncJob:
        """提交一个后台任务并返回可查询的 AsyncJob 句柄。

        队列已关闭时抛 RuntimeError；线程池拒绝提交时先撤销任务登记再继续上抛。
        """
        job = AsyncJob(id=new_id("job"), name=name, metadata=metadata or {})
        with self._lock:
            if not self._accepting:
                raise RuntimeError("AsyncJobQueue is shutting down and no longer accepts jobs")
            self._jobs[job.id] = job
            self._trim_history_locked()
        try:
            future = self._executor.submit(self._run_job, job.id, func, args, kwargs)
        except Exception:
            # A rejected submission was never accepted. Do not expose a
            # phantom queued handle or let it consume history capacity.
            with self._lock:
                self._jobs.pop(job.id, None)
            raise
        with self._lock:
            self._futures[job.id] = future
        future.add_done_callback(lambda completed, job_id=job.id: self._finish_future(job_id, completed))
        return job

    def get(self, job_id: str) -> AsyncJob | None:
        """按 id 查询任务，不存在时返回 None。"""
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 100) -> list[AsyncJob]:
        """按创建时间倒序返回最近的任务，最多 limit 条。"""
        with self._lock:
            rows = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return rows[:limit]

    def shutdown(self) -> None:
        """停止接收新任务并等待已接收任务进入终态，排队中的任务标记为 cancelled。"""
        with self._lock:
            self._accepting = False
        # An accepted in-memory job must reach a terminal state before shutdown
        # returns. Pending work is cancelled; already-running work is drained.
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            now = utc_now()
            for job in self._jobs.values():
                if job.status == "queued":
                    job.status = "cancelled"
                    job.finished_at = now
                    job.error = "Job cancelled because the service is shutting down"
            self._futures.clear()
            self._trim_history_locked()

    def _finish_future(self, job_id: str, future: Future[Any]) -> None:
        """任务句柄结束时的清理回调：记录被取消的排队任务并移除句柄引用。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and future.cancelled() and job.status == "queued":
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.error = "Job cancelled because the service is shutting down"
            self._futures.pop(job_id, None)

    def _run_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """在线程池中执行任务体，把状态从 running 推进到 succeeded 或 failed。"""
        self._update(job_id, status="running", started_at=utc_now())
        try:
            func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - background jobs must never crash the request path.
            self._update(job_id, status="failed", finished_at=utc_now(), error=str(exc))
            return
        self._update(job_id, status="succeeded", finished_at=utc_now(), error=None)

    def _update(self, job_id: str, **changes: Any) -> None:
        """在锁内批量更新任务字段；任务已被历史裁剪时静默忽略。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def _trim_history_locked(self) -> None:
        """按创建时间淘汰最旧的终态任务，把任务数压回 max_history 以内。

        调用方必须已持有 self._lock；运行中的任务不会被淘汰。
        """
        overflow = len(self._jobs) - self._max_history
        if overflow <= 0:
            return
        removable = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in {"succeeded", "failed", "cancelled"}
            ),
            key=lambda item: item.created_at,
        )
        for job in removable[:overflow]:
            self._jobs.pop(job.id, None)


_default_queue_lock = Lock()
_default_queue = AsyncJobQueue()


def start_async_jobs() -> AsyncJobQueue:
    """确保当前应用生命周期持有一个未关闭的进程内队列，否则新建一个。"""
    global _default_queue
    with _default_queue_lock:
        if not _default_queue.accepting:
            _default_queue = AsyncJobQueue()
        return _default_queue


def enqueue_async_job(
    name: str,
    func: Callable[..., Any],
    *args: Any,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AsyncJob:
    """向全局队列提交任务，name 标识任务类型，其余参数原样透传给任务体。"""
    with _default_queue_lock:
        queue = _default_queue
    return queue.enqueue(name, func, *args, metadata=metadata, **kwargs)


def get_async_job_queue() -> AsyncJobQueue:
    """返回全局队列实例，供查询任务状态或直接复用提交入口。"""
    with _default_queue_lock:
        return _default_queue


def shutdown_async_jobs() -> None:
    """关闭全局队列并等待已接收任务收敛，用于应用退出时的清理。"""
    with _default_queue_lock:
        queue = _default_queue
    queue.shutdown()
