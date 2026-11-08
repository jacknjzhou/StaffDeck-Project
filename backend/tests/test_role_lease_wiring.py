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
    """验证同角色重复获取租约时抛出冲突错误，释放后可再次获取。"""
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
    """验证未配置 redis_url 时获取角色租约直接返回 None。"""
    settings = Settings(redis_url="", database_url="postgresql+psycopg://x")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    assert lease_mod.acquire_role_lease("api") is None


class _FakeLease:
    """租约替身对象，记录续期次数与释放状态。"""
    def __init__(self, capability: str) -> None:
        """记录能力名并初始化释放标记与续期计数。"""
        self.capability = capability
        self.released = False
        self.renewed = 0

    def renew(self) -> bool:
        """累加续期次数并返回成功，用于断言续期线程确实工作。"""
        self.renewed += 1
        return True

    def release(self) -> None:
        """标记租约已释放，供 shutdown 断言。"""
        self.released = True


def _patch_settings(monkeypatch, **overrides):
    """让 main 与 lease 两处 get_settings 都指向注入的 Settings 实例。"""
    import app.main as main_mod

    settings = Settings(**overrides)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    return main_mod


def test_pg_startup_uses_role_leases_without_file_lock(monkeypatch):
    """验证 PG 模式启动只获取角色租约、不拿文件锁，关闭时统一释放。"""
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
        """记录租约获取顺序并返回可断言的替身租约。"""
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
    """验证 SQLite 模式启动仍用文件锁，未配置 Redis 时不持有租约。"""
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
    """验证启动主体抛错时已持有的租约与文件锁都被释放。"""
    main_mod = _patch_settings(
        monkeypatch,
        database_url="sqlite:///./wiring-test.db",
        redis_url="redis://example:6379/0",
        process_role="api",
    )
    released: list[str] = []
    leases: list[_FakeLease] = []

    def fake_acquire(capability):
        """返回替身租约并记录，便于断言异常路径已释放。"""
        lease = _FakeLease(capability)
        leases.append(lease)
        return lease

    def boom():
        """模拟启动主体失败以触发异常清理路径。"""
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


def test_held_leases_are_renewed_while_process_runs(monkeypatch):
    """租约带短 TTL，进程活着就必须续期，否则互斥保护在 60s 后静默失效。

    这里把续期间隔压到极短，验证 renewer 线程确实调用了 lease.renew()，
    并在 shutdown 时停止。
    """
    import time as _time

    main_mod = _patch_settings(
        monkeypatch,
        database_url="postgresql+psycopg://x",
        redis_url="redis://example:6379/0",
        process_role="api",
    )
    monkeypatch.setattr(main_mod, "LEASE_RENEW_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(main_mod, "acquire_runtime_instance_lock", lambda: None)
    monkeypatch.setattr(main_mod, "release_runtime_instance_lock", lambda: None)

    held: list[_FakeLease] = []

    def fake_acquire(capability):
        """返回替身租约并记录，便于统计续期次数。"""
        lease = _FakeLease(capability)
        held.append(lease)
        return lease

    monkeypatch.setattr(main_mod, "acquire_role_lease", fake_acquire)
    monkeypatch.setattr(main_mod, "_startup_by_role", lambda: None)

    main_mod.on_startup()
    try:
        assert main_mod._lease_renew_thread is not None
        assert main_mod._lease_renew_thread.is_alive()
        deadline = _time.monotonic() + 3.0
        while held[0].renewed < 2 and _time.monotonic() < deadline:
            _time.sleep(0.02)
        assert held[0].renewed >= 2, "held lease was never renewed"
    finally:
        main_mod.on_shutdown()

    assert not main_mod._lease_renew_thread
    assert held[0].released


class _ScriptedLease:
    """按预设序列返回续期结果的替身租约，用于验证“连续失败”计数语义。"""

    def __init__(self, results: list[bool]) -> None:
        """记录续期结果序列，并初始化计数与释放标记。"""
        self.results = results
        self.renewed = 0
        self.released = False

    def renew(self) -> bool:
        """按下标返回结果，序列用尽后重复最后一项。"""
        index = min(self.renewed, len(self.results) - 1)
        self.renewed += 1
        return self.results[index]

    def release(self) -> None:
        """标记租约已释放，供清理路径断言。"""
        self.released = True


def _wait_until(predicate, timeout: float = 3.0) -> None:
    """轮询等待条件成立，避免对后台线程用固定 sleep 造成偶发失败。"""
    import time as _time

    deadline = _time.monotonic() + timeout
    while not predicate() and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert predicate()


def _renewer_test_setup(monkeypatch, results: list[bool]):
    """装配一个只持有替身租约的 main 模块，返回（模块, 租约）。"""
    from app.core import runtime_state

    main_mod = _patch_settings(monkeypatch, redis_url="redis://example:6379/0")
    monkeypatch.setattr(main_mod, "LEASE_RENEW_INTERVAL_SECONDS", 0.02)
    lease = _ScriptedLease(results)
    main_mod._stop_lease_renewer()
    main_mod._held_leases.clear()
    main_mod._held_leases.append(lease)
    runtime_state.lease_lost.clear()
    main_mod._lease_renew_failures = 0
    return main_mod, lease


def _renewer_test_teardown(main_mod) -> None:
    """停止续期线程并清理托管状态，避免测试间相互污染。"""
    from app.core import runtime_state

    main_mod._stop_lease_renewer()
    main_mod._held_leases.clear()
    main_mod._lease_renew_failures = 0
    runtime_state.lease_lost.clear()


def test_three_consecutive_renew_failures_mark_lease_lost_once(monkeypatch, caplog):
    """连续 3 次续期失败后置位 lease_lost，且只记一条错误日志（不逐次刷屏）。"""
    import logging

    from app.core import runtime_state

    main_mod, lease = _renewer_test_setup(monkeypatch, [False])
    caplog.set_level(logging.ERROR, logger="staffdeck.main")

    def _error_records() -> list[logging.LogRecord]:
        """只取 main 记录器发出的 ERROR，排除其他库的噪音。"""
        return [
            record
            for record in caplog.records
            if record.name == "staffdeck.main" and record.levelno == logging.ERROR
        ]

    main_mod._start_lease_renewer()
    try:
        _wait_until(runtime_state.is_lease_lost)
        assert lease.renewed >= 3
        # mark_lease_lost 与 logger.error 紧邻，事件置位后日志可能尚未落盘。
        _wait_until(lambda: len(_error_records()) == 1)
        assert len(_error_records()) == 1

        settled = lease.renewed
        _wait_until(lambda: lease.renewed >= settled + 3)
        assert len(_error_records()) == 1, "lost lease must not log on every failed renewal"
        assert main_mod._lease_renew_thread.is_alive()
    finally:
        _renewer_test_teardown(main_mod)


def test_successful_renewal_resets_failure_streak(monkeypatch, caplog):
    """两次失败后成功一次即清零，因此永不到达 3 连败、也不会置位 lost。"""
    import logging

    from app.core import runtime_state

    main_mod, lease = _renewer_test_setup(monkeypatch, [False, False, True])
    caplog.set_level(logging.ERROR, logger="staffdeck.main")
    main_mod._start_lease_renewer()
    try:
        _wait_until(lambda: lease.renewed >= 6)
        assert not runtime_state.is_lease_lost()
        assert [r for r in caplog.records if r.levelno == logging.ERROR] == []
    finally:
        _renewer_test_teardown(main_mod)
