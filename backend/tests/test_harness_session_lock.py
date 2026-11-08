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
