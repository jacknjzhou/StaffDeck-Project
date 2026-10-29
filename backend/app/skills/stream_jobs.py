from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from app.db.models import new_id, utc_now


@dataclass
class SkillStreamEvent:
    """技能流式任务中的单条事件：自增序号、事件名与 JSON 负载，供客户端增量消费。"""

    seq: int
    event: str
    data: dict[str, Any]


@dataclass
class SkillStreamJob:
    """技能流式任务的运行态：归属、状态、事件列表、错误信息与取消标记。"""

    id: str
    name: str
    tenant_id: str
    user_id: str
    status: str = "queued"
    events: list[SkillStreamEvent] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = field(default_factory=lambda: utc_now().isoformat())
    cancel_requested: bool = False


class SkillStreamJobStore:
    """线程安全的内存任务存储，负责建任务、追加事件、标记终态与按上限淘汰。"""

    def __init__(self, max_jobs: int = 200):
        """初始化存储；max_jobs 为保留任务数上限，超出后淘汰最旧的已完结任务。"""
        self._lock = Lock()
        self._jobs: dict[str, SkillStreamJob] = {}
        self._max_jobs = max_jobs

    def create(self, name: str, tenant_id: str, user_id: str) -> SkillStreamJob:
        """创建 queued 状态的新任务写入存储，按上限淘汰后返回该任务对象。"""
        job = SkillStreamJob(id=new_id("skilljob"), name=name, tenant_id=tenant_id, user_id=user_id)
        with self._lock:
            self._jobs[job.id] = job
            self._trim_locked()
        return job

    def start(self, job_id: str) -> None:
        """把任务状态置为 running，表示执行已开始。"""
        self._update(job_id, status="running")

    def append(self, job_id: str, event: str, data: dict[str, Any]) -> None:
        """追加一条事件，seq 取当前事件数加一；任务不存在时静默忽略。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.events.append(SkillStreamEvent(seq=len(job.events) + 1, event=event, data=data))
            job.updated_at = utc_now().isoformat()

    def complete(self, job_id: str) -> None:
        """把任务状态置为 succeeded，表示执行正常结束。"""
        self._update(job_id, status="succeeded")

    def fail(self, job_id: str, error: str) -> None:
        """先追加一条 error 事件，再把状态置为 failed 并记录错误信息。"""
        self.append(job_id, "error", {"message": error})
        self._update(job_id, status="failed", error=error)

    def cancel(self, job_id: str) -> None:
        """只设置取消标记，由执行侧轮询 is_cancelled 自行停止。"""
        self._update(job_id, cancel_requested=True)

    def is_cancelled(self, job_id: str) -> bool:
        """判断任务是否已请求取消；任务不存在时返回 False。"""
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_requested)

    def get(self, job_id: str) -> SkillStreamJob | None:
        """返回任务快照（事件列表浅拷贝），避免调用方改动内部状态；不存在时返回 None。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return SkillStreamJob(
                id=job.id,
                name=job.name,
                tenant_id=job.tenant_id,
                user_id=job.user_id,
                status=job.status,
                events=list(job.events),
                error=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
                cancel_requested=job.cancel_requested,
            )

    def snapshot(self, job_id: str, after: int = 0) -> tuple[SkillStreamJob | None, list[SkillStreamEvent]]:
        """返回任务快照与 seq 大于 after 的事件，用于增量拉流；任务不存在时返回 (None, [])。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None, []
            events = [event for event in job.events if event.seq > after]
            copy = SkillStreamJob(
                id=job.id,
                name=job.name,
                tenant_id=job.tenant_id,
                user_id=job.user_id,
                status=job.status,
                events=[],
                error=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
                cancel_requested=job.cancel_requested,
            )
            return copy, events

    def _update(self, job_id: str, **changes: Any) -> None:
        """在锁内按关键字更新任务字段并刷新 updated_at；任务不存在时静默忽略。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = utc_now().isoformat()

    def _trim_locked(self) -> None:
        """在锁内按 updated_at 淘汰超出 max_jobs 的最旧已完结任务（succeeded/failed）。"""
        overflow = len(self._jobs) - self._max_jobs
        if overflow <= 0:
            return
        removable = sorted(
            (job for job in self._jobs.values() if job.status in {"succeeded", "failed"}),
            key=lambda item: item.updated_at,
        )
        for job in removable[:overflow]:
            self._jobs.pop(job.id, None)


stream_jobs = SkillStreamJobStore()
