# backend/tests/test_harness_session_lock.py
"""Harness 会话互斥的进程内后端与后端选择行为（模块级函数保持原签名）。"""

import types

import pytest

from app.core import harness_session_lock as lock_mod
from app.core.harness_session_lock import (
    HarnessSessionBusy,
    HarnessSessionLock,
    acquire_harness_session,
    release_harness_session,
)


def _settings_with(redis_url: str) -> types.SimpleNamespace:
    """构造只带 redis_url 的假设置对象。"""
    return types.SimpleNamespace(redis_url=redis_url)


def test_memory_backend_enforces_single_flight():
    """验证 memory 后端同一会话只能被一个调用方持有。"""
    lock = HarnessSessionLock("memory")
    handle = lock.acquire("memory-session")
    with pytest.raises(HarnessSessionBusy):
        lock.acquire("memory-session")
    lock.release(handle)


def test_memory_release_allows_reacquire():
    """验证释放后可重新获取，注册表复用同一把进程内锁。"""
    lock = HarnessSessionLock("memory")
    first = lock.acquire("memory-session")
    lock.release(first)
    second = lock.acquire("memory-session")
    lock.release(second)


def test_default_backend_is_memory_without_redis(monkeypatch):
    """验证未配置 redis_url 时后端延迟解析为 memory。"""
    monkeypatch.setattr(lock_mod, "_settings", lambda: _settings_with(""))
    assert HarnessSessionLock().backend == "memory"


def test_default_backend_is_redis_with_redis(monkeypatch):
    """验证配置了 redis_url 时后端延迟解析为 redis。"""
    monkeypatch.setattr(
        lock_mod, "_settings", lambda: _settings_with("redis://example:6379/0")
    )
    assert HarnessSessionLock().backend == "redis"


def test_unknown_backend_is_rejected():
    """验证未知后端名在构造阶段就报错，避免静默退回错层。"""
    with pytest.raises(ValueError):
        HarnessSessionLock("zookeeper")


def test_public_functions_keep_memory_behavior(monkeypatch):
    """验证模块级 acquire/release 函数签名不变且仍按会话互斥。"""
    monkeypatch.setattr(lock_mod, "_settings", lambda: _settings_with(""))
    handle = acquire_harness_session("public-session")
    try:
        with pytest.raises(HarnessSessionBusy):
            acquire_harness_session("public-session")
    finally:
        release_harness_session("public-session", handle)
    release_harness_session("public-session", acquire_harness_session("public-session"))


def test_blank_session_id_is_rejected(monkeypatch):
    """验证空白 session_id 抛 ValueError，保持原有入参校验。"""
    monkeypatch.setattr(lock_mod, "_settings", lambda: _settings_with(""))
    with pytest.raises(ValueError):
        acquire_harness_session("  ")
    release_harness_session("x", None)


def test_default_lock_resolves_redis_when_configured(monkeypatch):
    """默认锁在配置了 redis_url 时必须解析为 redis，而不是固定 memory。"""
    monkeypatch.setattr(
        lock_mod, "_settings", lambda: _settings_with("redis://example:6379/0")
    )
    assert isinstance(lock_mod._default_lock, HarnessSessionLock)
    assert lock_mod._default_lock.backend == "redis"
    assert lock_mod._build_default_lock().backend == "redis"


def test_public_functions_delegate_to_module_default_lock(monkeypatch):
    """模块级函数必须走 _default_lock 单例，替换默认锁即可观察委托。"""
    calls: list[tuple[str, object]] = []
    sentinel = object()

    class _SpyLock:
        """记录 acquire/release 委托的目标与参数。"""

        def acquire(self, session_id: str):
            """记录抢占请求并返回哨兵句柄。"""
            calls.append(("acquire", session_id))
            return sentinel

        def release(self, handle) -> None:
            """记录释放请求。"""
            calls.append(("release", handle))

    monkeypatch.setattr(lock_mod, "_default_lock", _SpyLock())
    assert acquire_harness_session("session-1") is sentinel
    release_harness_session("session-1", sentinel)
    assert calls == [("acquire", "session-1"), ("release", sentinel)]


def test_memory_double_release_is_best_effort(caplog):
    """重复释放进程内锁会抛错，但释放路径必须吞掉并记一条 warning。"""
    import logging

    caplog.set_level(logging.WARNING, logger="app.core.harness_session_lock")
    lock = HarnessSessionLock("memory")
    handle = lock.acquire("memory-double-release")
    lock.release(handle)
    lock.release(handle)

    warnings = [
        record
        for record in caplog.records
        if record.name == "app.core.harness_session_lock"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "harness session lock release failed" in warnings[0].getMessage()


def test_redis_client_uses_bounded_timeouts(monkeypatch):
    """Redis 客户端必须带 2 秒连接/读写超时，避免锁路径无限挂起。"""
    captured: dict[str, object] = {}

    class _StubClient:
        """仅用于承接 from_url 返回值的空壳客户端。"""

    def fake_from_url(url: str, **kwargs):
        """记录构造参数并返回空壳客户端。"""
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _StubClient()

    monkeypatch.setattr("redis.Redis.from_url", fake_from_url)
    monkeypatch.setattr(
        lock_mod, "_settings", lambda: _settings_with("redis://example:6379/0")
    )
    lock = HarnessSessionLock("redis")
    lock._redis_client()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["decode_responses"] is True
    assert kwargs["socket_connect_timeout"] == 2
    assert kwargs["socket_timeout"] == 2
