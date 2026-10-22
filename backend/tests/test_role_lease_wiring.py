# backend/tests/test_role_lease_wiring.py
"""Task 6: 租约接线与实例锁条件化的行为验证。

覆盖三层语义：
1. lease 层：同角色租约互斥、redis_url 为空时跳过；
2. main 层：PG 模式走角色租约、SQLite 模式保留文件锁；
3. 异常路径：startup 失败时已持有的租约与文件锁都被释放。
"""

import fakeredis
import pytest

from app.config import Settings
from app.distributed import lease as lease_mod


def test_role_lease_conflict_raises(monkeypatch):
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(lease_mod, "_build_client", lambda url: fake)
    # 本机默认 redis_url 为空会直接跳过，需注入一个已配置 Redis 的 Settings。
    settings = Settings(redis_url="redis://fake:6379/0", database_url="sqlite:///./x.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    first = lease_mod.acquire_role_lease("api")
    assert first is not None
    with pytest.raises(RuntimeError, match="holds the lease"):
        lease_mod.acquire_role_lease("api")
    first.release()


def test_no_redis_url_returns_none(monkeypatch):
    # acquire_role_lease 在函数体内 import get_settings，patch 源头即可生效；
    # kwargs 构造优先级高于环境变量，不受宿主机 STAFFDECK_REDIS_URL 干扰。
    settings = Settings(redis_url="", database_url="postgresql+psycopg://x")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    assert lease_mod.acquire_role_lease("api") is None


class _FakeLease:
    def __init__(self, capability: str) -> None:
        self.capability = capability
        self.released = False

    def release(self) -> None:
        self.released = True


def _patch_settings(monkeypatch, **overrides):
    """让 main 与 lease 两处 get_settings 都指向注入的 Settings 实例。"""
    import app.main as main_mod

    settings = Settings(**overrides)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    return main_mod


def test_pg_startup_uses_role_leases_without_file_lock(monkeypatch):
    main_mod = _patch_settings(
        monkeypatch,
        database_url="postgresql+psycopg://x",
        redis_url="redis://example:6379/0",
        process_role="api",
    )
    calls: list[str] = []
    held: list[_FakeLease] = []
    monkeypatch.setattr(
        main_mod,
        "acquire_runtime_instance_lock",
        lambda: calls.append("file-lock"),
    )

    def fake_acquire(capability):
        calls.append(f"lease:{capability}")
        lease = _FakeLease(capability)
        held.append(lease)
        return lease

    monkeypatch.setattr(main_mod, "acquire_role_lease", fake_acquire)
    monkeypatch.setattr(main_mod, "_startup_by_role", lambda: calls.append("body"))

    main_mod.on_startup()
    # PG 模式：不拿文件锁，只对启用能力拿租约
    assert calls == ["lease:api", "body"]
    assert len(main_mod._held_leases) == 1

    main_mod.on_shutdown()
    # shutdown 统一释放
    assert held[0].released


def test_sqlite_startup_keeps_file_lock(monkeypatch):
    main_mod = _patch_settings(monkeypatch, database_url="sqlite:///./wiring-test.db")
    calls: list[str] = []
    monkeypatch.setattr(
        main_mod, "acquire_runtime_instance_lock", lambda: calls.append("file-lock")
    )
    monkeypatch.setattr(
        main_mod, "release_runtime_instance_lock", lambda: calls.append("file-unlock")
    )
    monkeypatch.setattr(
        main_mod, "acquire_role_lease", lambda capability: calls.append(f"lease:{capability}")
    )
    monkeypatch.setattr(main_mod, "_startup_by_role", lambda: calls.append("body"))

    main_mod.on_startup()
    assert "file-lock" in calls
    # 默认 redis_url 为空：租约调用内部返回 None，不产生持有
    assert calls.count("lease:api") == 1
    assert main_mod._held_leases == []

    main_mod.on_shutdown()
    assert "file-unlock" in calls


def test_startup_failure_releases_leases_and_file_lock(monkeypatch):
    main_mod = _patch_settings(
        monkeypatch,
        database_url="sqlite:///./wiring-test.db",
        redis_url="redis://example:6379/0",
        process_role="api",
    )
    released: list[str] = []
    leases: list[_FakeLease] = []

    def fake_acquire(capability):
        lease = _FakeLease(capability)
        leases.append(lease)
        return lease

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(main_mod, "acquire_runtime_instance_lock", lambda: None)
    monkeypatch.setattr(main_mod, "release_runtime_instance_lock", lambda: released.append("file"))
    monkeypatch.setattr(main_mod, "acquire_role_lease", fake_acquire)
    monkeypatch.setattr(main_mod, "_startup_by_role", boom)

    with pytest.raises(RuntimeError, match="boom"):
        main_mod.on_startup()

    # 异常路径：已持有租约释放、文件锁释放、持有列表清空
    assert leases[0].released
    assert released == ["file"]
    assert main_mod._held_leases == []
