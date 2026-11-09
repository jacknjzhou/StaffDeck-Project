# backend/tests/test_role_lease_wiring.py
"""Task 6: 租约接线与实例锁条件化的行为验证。

覆盖三层语义：
1. lease 层：同角色租约互斥、redis_url 为空时跳过；
2. main 层：PG 模式走角色租约、SQLite 模式保留文件锁；
3. 异常路径：startup 失败时已持有的租约与文件锁都被释放。
"""

import logging

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
    """按预设序列返回续期结果的替身租约，用于验证失败计数与 fencing 语义。

    序列元素既可以是 bool（正常返回），也可以是异常实例（模拟 Redis 不可达）；
    序列用尽后重复最后一项。
    """

    def __init__(self, results: list[object]) -> None:
        """记录续期结果序列，并初始化计数与释放标记。"""
        self.results = results
        self.renewed = 0
        self.released = False

    def renew(self) -> bool:
        """按下标返回结果或抛出预设异常，序列用尽后重复最后一项。"""
        index = min(self.renewed, len(self.results) - 1)
        self.renewed += 1
        result = self.results[index]
        if isinstance(result, BaseException):
            raise result
        return bool(result)

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


def _renewer_test_setup(monkeypatch, results: list[object]):
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


def _main_records(caplog, level: int) -> list[logging.LogRecord]:
    """只取 main 记录器指定级别的日志，排除其他库的噪音。"""
    return [
        record
        for record in caplog.records
        if record.name == "staffdeck.main" and record.levelno == level
    ]


class _ExitHook:
    """记录 ``_terminate_process`` 的调用次数，防止测试进程真的退出。

    fatal 分支调用本钩子后置位续期停止事件：真实的 ``os._exit`` 不会返回，测试里
    用一个空实现模拟，需要主动让续期循环停下来，否则会按 20ms 间隔反复触发。
    """

    def __init__(self, main_mod) -> None:
        """保存被测模块句柄，便于置位其停止事件。"""
        self.main_mod = main_mod
        self.calls = 0

    def __call__(self) -> None:
        """记录一次终止调用并停止续期循环。"""
        self.calls += 1
        self.main_mod._lease_renew_stop.set()


def _install_exit_hook(monkeypatch, main_mod) -> _ExitHook:
    """把真正的 os._exit 替换成可观测的钩子。"""
    hook = _ExitHook(main_mod)
    monkeypatch.setattr(main_mod, "_terminate_process", hook)
    return hook


def test_renew_false_terminates_process(monkeypatch, caplog):
    """renew() 明确返回 False 代表 fencing 已丢失：置位 not-ready 并终止进程。"""
    from app.core import runtime_state

    main_mod, lease = _renewer_test_setup(monkeypatch, [False])
    exit_hook = _install_exit_hook(monkeypatch, main_mod)
    caplog.set_level(logging.ERROR, logger="staffdeck.main")
    main_mod._start_lease_renewer()
    try:
        _wait_until(lambda: exit_hook.calls >= 1)
        assert runtime_state.is_lease_lost()
        assert lease.renewed >= 1
        _wait_until(lambda: len(_main_records(caplog, logging.ERROR)) == 1)
        message = _main_records(caplog, logging.ERROR)[0].getMessage()
        # 日志里要能区分是哪个租约，不能退化成只有内存地址的 repr。
        assert "ScriptedLease#0" in message
        assert "租约已被他人持有或已过期" in message
        assert "object at 0x" not in message
    finally:
        _renewer_test_teardown(main_mod)


def test_three_consecutive_renew_exceptions_do_not_terminate(monkeypatch, caplog):
    """异常可能只是 Redis 抖动：连败 3 轮只置位 not-ready，不终止进程。"""
    from app.core import runtime_state

    main_mod, lease = _renewer_test_setup(monkeypatch, [RuntimeError("redis unavailable")])
    exit_hook = _install_exit_hook(monkeypatch, main_mod)
    caplog.set_level(logging.ERROR, logger="staffdeck.main")
    main_mod._start_lease_renewer()
    try:
        _wait_until(runtime_state.is_lease_lost)
        assert lease.renewed >= 3
        _wait_until(lambda: len(_main_records(caplog, logging.ERROR)) == 1)
        message = _main_records(caplog, logging.ERROR)[0].getMessage()
        assert "ScriptedLease#0" in message
        assert "连续 3 轮续期异常" in message
        assert "object at 0x" not in message
        assert exit_hook.calls == 0

        settled = lease.renewed
        _wait_until(lambda: lease.renewed >= settled + 2)
        assert len(_main_records(caplog, logging.ERROR)) == 1, "lost lease must not log every round"
        assert exit_hook.calls == 0, "transport errors alone must not restart the process"
    finally:
        _renewer_test_teardown(main_mod)


def test_successful_renewal_resets_failure_streak(monkeypatch, caplog):
    """两次异常后成功一次即清零，因此永不到达 3 连败、也不会置位 lost。"""
    from app.core import runtime_state

    main_mod, lease = _renewer_test_setup(
        monkeypatch, [RuntimeError("blip"), RuntimeError("blip"), True]
    )
    exit_hook = _install_exit_hook(monkeypatch, main_mod)
    caplog.set_level(logging.WARNING, logger="staffdeck.main")
    main_mod._start_lease_renewer()
    try:
        _wait_until(lambda: lease.renewed >= 6)
        assert not runtime_state.is_lease_lost()
        assert _main_records(caplog, logging.WARNING) == []
        assert _main_records(caplog, logging.ERROR) == []
        assert exit_hook.calls == 0
    finally:
        _renewer_test_teardown(main_mod)


def test_successful_renewal_clears_lost_state_and_logs_recovery(monkeypatch, caplog):
    """异常抖动被置位后，下一次成功续期必须恢复就绪，而不是永久 503。"""
    from app.core import runtime_state

    main_mod, _lease = _renewer_test_setup(monkeypatch, [False, True])
    exit_hook = _install_exit_hook(monkeypatch, main_mod)
    caplog.set_level(logging.WARNING, logger="staffdeck.main")

    # 直接驱动一轮 renew，避免与后台线程的 20ms 间隔产生竞态。
    fencing_lost, reason = main_mod._renew_held_leases_once()
    assert fencing_lost and reason
    assert not runtime_state.is_lease_lost()
    # 这里模拟异常路径的置位（非 fatal），恢复语义与 fatal 路径共用 clear。
    main_mod._latch_lease_lost(reason)
    assert runtime_state.is_lease_lost()
    assert exit_hook.calls == 0

    fencing_lost, reason = main_mod._renew_held_leases_once()
    assert not fencing_lost and reason == ""
    main_mod._record_lease_renew_success()

    assert not runtime_state.is_lease_lost()
    lost_messages = [r.getMessage() for r in _main_records(caplog, logging.ERROR)]
    recovery_messages = [r.getMessage() for r in _main_records(caplog, logging.WARNING)]
    assert any("not-ready" in message for message in lost_messages)
    assert any("转回 ready" in message for message in recovery_messages)
