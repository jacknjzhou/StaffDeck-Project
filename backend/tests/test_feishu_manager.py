from __future__ import annotations

import time

from sqlmodel import Session, SQLModel, create_engine

from app.channels.feishu_manager import FeishuProcessManager
from app.channels.feishu_process import ConnectorState
from app.db.models import ChannelBinding, Tenant


class FakeSupervisor:
    """内存版连接器监督器，记录启动/停止调用便于断言管理器行为。"""
    def __init__(self, **kwargs):
        """初始化记录容器，并保存传入的数据库路径。"""
        self.database_path = kwargs["database_path"]
        self.started = []
        self.stopped = []
        self.states = {}
        self.connections = {}
        self.closed = False
        self.allow_stop = True

    def start_binding(self, binding_id, revision):
        """记录启动请求并把绑定标记为运行中。"""
        self.started.append((binding_id, revision))
        was_running = self.states.get(binding_id) is ConnectorState.RUNNING
        self.states[binding_id] = ConnectorState.RUNNING
        if not was_running:
            self.connections[binding_id] = True

    def stop_binding(self, binding_id, timeout=5.0):
        """记录停止请求；allow_stop 为假时模拟停止超时并返回失败。"""
        self.stopped.append((binding_id, timeout))
        if not self.allow_stop:
            return False
        self.states[binding_id] = ConnectorState.ABSENT
        self.connections[binding_id] = False
        return True

    def state(self, binding_id):
        """返回绑定的连接器状态，未记录时视为不存在。"""
        return self.states.get(binding_id, ConnectorState.ABSENT)

    def connected(self, binding_id):
        """返回绑定的连接标记，未记录时为未连接。"""
        return self.connections.get(binding_id, False)

    def stop(self, timeout=5.0):
        """模拟整体关闭监督器并记录已关闭标记。"""
        self.closed = True
        return True

    def reset_backoff(self, binding_id):
        """清除绑定状态，模拟退避计数被重置。"""
        self.states.pop(binding_id, None)


def test_manager_reconciles_pause_resume_revision_and_shutdown(tmp_path) -> None:
    """验证管理器能按暂停、恢复、版本变更与关闭事件正确启停绑定。"""
    db_path = tmp_path / "manager.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(
            ChannelBinding(
                id="chan_feishu",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="feishu",
                status="active",
                config_revision=4,
            )
        )
        db.commit()
    created = []

    def factory(**kwargs):
        """创建并登记 FakeSupervisor，便于测试取回同一实例做断言。"""
        supervisor = FakeSupervisor(**kwargs)
        created.append(supervisor)
        return supervisor

    manager = FeishuProcessManager(
        db_engine=engine,
        supervisor_factory=factory,
        reconcile_seconds=60,
    )
    manager.reconcile_once()
    supervisor = created[0]
    assert supervisor.started == [("chan_feishu", 4)]

    manager.pause_binding("chan_feishu")
    assert manager.wait_binding_stopped("chan_feishu", timeout_seconds=1.0)
    assert supervisor.stopped[-1][0] == "chan_feishu"
    manager.resume_binding("chan_feishu")
    assert supervisor.started[-1] == ("chan_feishu", 4)

    with Session(engine) as db:
        binding = db.get(ChannelBinding, "chan_feishu")
        binding.config_revision = 5
        db.add(binding)
        db.commit()
    manager.reconcile_once()
    assert supervisor.stopped[-1][0] == "chan_feishu"
    assert supervisor.started[-1] == ("chan_feishu", 5)
    assert manager.stop(timeout_seconds=1.0) is True
    assert supervisor.closed is True


def test_manager_keeps_tracking_binding_when_stop_times_out(tmp_path) -> None:
    """验证停止超时后绑定仍被跟踪，后续 reconcile 会再次尝试停止。"""
    db_path = tmp_path / "manager-stop-timeout.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(
            ChannelBinding(
                id="chan_feishu",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="feishu",
                status="active",
                config_revision=1,
            )
        )
        db.commit()
    created = []

    def factory(**kwargs):
        """创建并登记 FakeSupervisor，便于测试取回同一实例做断言。"""
        supervisor = FakeSupervisor(**kwargs)
        created.append(supervisor)
        return supervisor

    manager = FeishuProcessManager(db_engine=engine, supervisor_factory=factory)
    manager.reconcile_once()
    supervisor = created[0]
    supervisor.allow_stop = False
    with Session(engine) as db:
        binding = db.get(ChannelBinding, "chan_feishu")
        binding.status = "disabled"
        db.add(binding)
        db.commit()

    manager.reconcile_once()
    manager.reconcile_once()
    assert len(supervisor.stopped) == 2
    supervisor.allow_stop = True
    assert manager.stop(timeout_seconds=1.0) is True


def test_manager_writes_disconnected_truth_and_accepts_backoff_as_stopped(tmp_path) -> None:
    """验证管理器的连接状态写回真实值，且退避状态视为已停止。"""
    db_path = tmp_path / "manager-disconnect.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(
            ChannelBinding(
                id="chan_feishu",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="feishu",
                status="active",
                config_revision=1,
            )
        )
        db.commit()
    created = []

    def factory(**kwargs):
        """创建并登记 FakeSupervisor，便于测试取回同一实例做断言。"""
        supervisor = FakeSupervisor(**kwargs)
        created.append(supervisor)
        return supervisor

    manager = FeishuProcessManager(db_engine=engine, supervisor_factory=factory)
    manager.reconcile_once()
    supervisor = created[0]
    manager.reconcile_once()
    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_feishu").connected is True
    supervisor.connections["chan_feishu"] = False
    manager.reconcile_once()
    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_feishu").connected is False
    supervisor.states["chan_feishu"] = ConnectorState.CRASH_BACKOFF
    assert manager.wait_binding_stopped("chan_feishu", timeout_seconds=0.01) is True
    assert manager.stop(timeout_seconds=1.0) is True


def test_pause_starts_stop_without_consuming_wait_deadline(tmp_path) -> None:
    """验证暂停会异步发起停止，不阻塞调用方且不提前消耗等待超时。"""
    db_path = tmp_path / "manager-async-stop.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(
            ChannelBinding(
                id="chan_feishu",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="feishu",
                status="active",
                config_revision=1,
            )
        )
        db.commit()

    class SlowSupervisor(FakeSupervisor):
        """停止耗时较长的监督器，用于验证暂停不会同步等待停止完成。"""
        def stop_binding(self, binding_id, timeout=5.0):
            """先延迟再执行真实停止，模拟慢速关闭。"""
            time.sleep(0.15)
            return super().stop_binding(binding_id, timeout)

    manager = FeishuProcessManager(
        db_engine=engine,
        supervisor_factory=lambda **kwargs: SlowSupervisor(**kwargs),
    )
    manager.reconcile_once()
    started = time.monotonic()
    manager.pause_binding("chan_feishu")
    assert time.monotonic() - started < 0.05
    assert manager.wait_binding_stopped("chan_feishu", timeout_seconds=1.0)
    assert manager.stop(timeout_seconds=1.0)


def test_connected_writeback_is_revision_fenced_in_single_update(tmp_path) -> None:
    """验证连接状态回写带 revision 栅栏，版本竞态下不会覆盖新配置。"""
    db_path = tmp_path / "manager-connected-cas.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(
            ChannelBinding(
                id="chan_feishu",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="feishu",
                status="active",
                connected=False,
                config_revision=1,
            )
        )
        db.commit()

    class RevisionRacingSupervisor(FakeSupervisor):
        """首次查询连接状态时提升配置版本，模拟并发修改。"""
        raced = False

        def connected(self, binding_id):
            """返回已连接，并在首次调用时抢先写入新 revision。"""
            if not self.raced:
                self.raced = True
                with Session(engine) as db:
                    binding = db.get(ChannelBinding, binding_id)
                    binding.config_revision = 2
                    db.add(binding)
                    db.commit()
            return True

    manager = FeishuProcessManager(
        db_engine=engine,
        supervisor_factory=lambda **kwargs: RevisionRacingSupervisor(**kwargs),
    )
    manager.reconcile_once()
    with Session(engine) as db:
        binding = db.get(ChannelBinding, "chan_feishu")
        assert binding.config_revision == 2
        assert binding.connected is False
    assert manager.stop(timeout_seconds=1.0)


def test_channel_service_lifecycle_starts_and_stops_feishu_manager(monkeypatch) -> None:
    """验证渠道服务启停会同时启动和停止飞书进程管理器。"""
    import app.channels as channels
    import app.channels.service_intake as intake
    import app.channels.service_outbox as outbox

    class Manager:
        """记录 start/stop 调用次数的管理器替身。"""
        def __init__(self):
            """初始化启动与停止计数。"""
            self.started = 0
            self.stopped = 0

        def start(self):
            """记录一次启动。"""
            self.started += 1

        def stop(self, timeout_seconds=5.0):
            """记录一次停止并返回成功。"""
            self.stopped += 1
            return True

    wechat = Manager()
    wecom = Manager()
    feishu = Manager()
    monkeypatch.setattr(channels, "_wechat_poll_manager", wechat)
    monkeypatch.setattr(channels, "_wecom_stream_manager", wecom)
    monkeypatch.setattr(channels, "_feishu_process_manager", feishu)
    monkeypatch.setattr(channels, "_acquire_connector_process_lock", lambda: True)
    monkeypatch.setattr(channels, "_release_connector_process_lock", lambda: None)
    monkeypatch.setattr(channels, "_ensure_adapters_registered", lambda: None)
    monkeypatch.setattr(intake, "start_staged_inbound_daemon", lambda: None)
    monkeypatch.setattr(intake, "stop_staged_inbound_daemon", lambda timeout_seconds=5.0: True)
    monkeypatch.setattr(intake, "sweep_stale_inbound_events", lambda: 0)
    monkeypatch.setattr(outbox, "start_delivery_daemon", lambda: None)
    monkeypatch.setattr(outbox, "stop_delivery_daemon", lambda timeout_seconds=5.0: True)

    channels.start_channel_services()
    assert (wechat.started, wecom.started, feishu.started) == (1, 1, 1)
    assert channels.stop_channel_services(timeout_seconds=1.0) is True
    assert (wechat.stopped, wecom.stopped, feishu.stopped) == (1, 1, 1)


def test_feishu_adapter_is_registered() -> None:
    """验证 feishu 渠道适配器已被注册且可被查询到。"""
    import app.channels as channels
    from app.channels.adapters.base import get_channel_adapter

    channels._ensure_adapters_registered()
    assert get_channel_adapter("feishu").__class__.__name__ == "FeishuAdapter"
