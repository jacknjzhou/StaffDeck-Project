# backend/tests/test_lease_required.py
"""生产环境必须有 Redis 才能取得角色租约，避免无锁多进程静默并发。"""

import pytest

from app.distributed import lease


def test_production_requires_redis(monkeypatch):
    """生产模式没有 Redis 时不能静默降级为无锁。"""
    monkeypatch.setattr(
        lease,
        "get_settings",
        lambda: type("S", (), {"redis_url": "", "is_production": True})(),
    )
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        lease.acquire_role_lease("api")


def test_development_allows_missing_redis(monkeypatch):
    """开发模式仍允许无 Redis 单进程运行。"""
    monkeypatch.setattr(
        lease,
        "get_settings",
        lambda: type("S", (), {"redis_url": "", "is_production": False})(),
    )
    assert lease.acquire_role_lease("api") is None


def test_production_treats_whitespace_redis_url_as_missing(monkeypatch):
    """只含空白的 REDIS_URL 等同于未配置，不能绕过生产守卫。"""
    monkeypatch.setattr(
        lease,
        "get_settings",
        lambda: type("S", (), {"redis_url": "   ", "is_production": True})(),
    )
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        lease.acquire_role_lease("api")
