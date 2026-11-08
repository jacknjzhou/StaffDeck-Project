from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.scheduled_tasks import worker


@contextmanager
def _null_session() -> Iterator[None]:
    """测试用的空会话上下文，避免 worker 触碰真实数据库。"""
    yield None


def _patch_worker(monkeypatch: pytest.MonkeyPatch, *, seed_enabled: bool) -> list[object]:
    """把 worker 的数据库、种子与调度依赖替换为记录型替身，返回种子调用记录。"""
    seed_calls: list[object] = []
    monkeypatch.setattr(worker, "init_db", lambda: None)
    monkeypatch.setattr(worker, "seed_demo_data", lambda db: seed_calls.append(db))
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(seed_demo_data=seed_enabled),
        raising=False,
    )
    monkeypatch.setattr(worker, "Session", lambda _engine: _null_session())
    monkeypatch.setattr(worker, "due_scheduled_tasks", lambda _db: [])
    monkeypatch.setattr(worker, "_stopped", False)
    return seed_calls


def test_run_worker_skips_demo_seed_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEED_DEMO_DATA=false 时 worker 启动不得重建演示数据与引导管理员。"""
    seed_calls = _patch_worker(monkeypatch, seed_enabled=False)

    worker.run_worker(once=True)

    assert seed_calls == []


def test_run_worker_seeds_demo_data_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEED_DEMO_DATA=true 时 worker 启动仍执行一次种子。"""
    seed_calls = _patch_worker(monkeypatch, seed_enabled=True)

    worker.run_worker(once=True)

    assert len(seed_calls) == 1
