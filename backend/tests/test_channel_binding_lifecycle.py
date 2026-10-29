import threading
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels.service_outbox import stage_channel_delivery
from app.channels.service_session import adopt_orphan_channel_sessions
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelBindingAgent,
    ChannelConvState,
    ChannelDelivery,
    ChatSession,
    Message,
    Tenant,
    User,
)


def _test_engine():
    """创建内存 SQLite 引擎并建表，为单个用例提供隔离的数据库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_binding_lifecycle_lock_serializes_same_binding() -> None:
    """验证同一绑定的生命周期锁串行化并发请求，后到者需等先到者退出。"""
    from app.channels import binding_lifecycle_lock

    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    order: list[str] = []

    def first_request() -> None:
        """模拟先到请求：获取锁后记录进入与退出顺序。"""
        with binding_lifecycle_lock("chan_same"):
            order.append("first_enter")
            first_entered.set()
            assert release_first.wait(timeout=5.0)
            order.append("first_exit")

    def second_request() -> None:
        """模拟后到请求：需等先到请求释放锁后才能进入临界区。"""
        assert first_entered.wait(timeout=5.0)
        with binding_lifecycle_lock("chan_same"):
            order.append("second_enter")
            second_entered.set()

    first = threading.Thread(target=first_request)
    second = threading.Thread(target=second_request)
    first.start()
    second.start()
    assert first_entered.wait(timeout=5.0)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert order == ["first_enter", "first_exit", "second_enter"]


def test_connector_process_lock_rejects_second_process(monkeypatch, tmp_path) -> None:
    """验证连接器进程锁被持有时，另一个进程无法再获取同一把锁。"""
    import app.channels as channels
    import app.db as app_db

    db_path = tmp_path / "connector.db"
    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(app_db, "engine", engine)
    channels._release_connector_process_lock()
    assert channels._acquire_connector_process_lock() is True
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["ULTRARAG_DOTENV"] = str(tmp_path / "missing.env")
    code = (
        "from app.channels import _acquire_connector_process_lock; "
        "print(_acquire_connector_process_lock())"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "False"
    finally:
        channels._release_connector_process_lock()


def test_shutdown_keeps_connector_lock_until_consumers_exit(monkeypatch, tmp_path) -> None:
    """验证消费者未退出前仍持有连接器锁，全部退出后才允许新进程接管。"""
    import app.channels as channels
    import app.channels.service_outbox as outbox_module
    import app.db as app_db

    db_path = tmp_path / "shutdown-lock.db"
    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(app_db, "engine", engine)

    class ControlledManager:
        """可手动切换停止结果的渠道管理器替身，用于观察关停时锁的释放时机。"""

        stopped = False

        def stop(self, timeout_seconds=5.0):
            """返回预设的停止结果，模拟消费者是否已经全部退出。"""
            return self.stopped

    manager = ControlledManager()
    monkeypatch.setattr(channels, "_wechat_poll_manager", manager)
    monkeypatch.setattr(channels, "_wecom_stream_manager", None)
    monkeypatch.setattr(channels, "_intake_sweep_thread", None)
    monkeypatch.setattr(outbox_module, "stop_delivery_daemon", lambda timeout_seconds=5.0: True)
    channels._release_connector_process_lock()
    assert channels._acquire_connector_process_lock()
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["ULTRARAG_DOTENV"] = str(tmp_path / "missing.env")
    code = (
        "from app.channels import _acquire_connector_process_lock; "
        "print(_acquire_connector_process_lock())"
    )

    def child_result() -> str:
        """在子进程中尝试获取连接器锁，返回其打印的 True/False。"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()

    try:
        assert channels.stop_channel_services(timeout_seconds=0.01) is False
        assert child_result() == "False"
        manager.stopped = True
        assert channels.stop_channel_services(timeout_seconds=1.0) is True
        assert child_result() == "True"
    finally:
        channels._release_connector_process_lock()


def _make_api_client(engine):
    """构建挂载渠道路由的测试客户端，并用测试引擎替换数据库依赖。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.channels as channels_api
    from app.db import get_session

    app = FastAPI()
    app.include_router(channels_api.router)

    def override_get_session():
        """为每个请求提供绑定到测试引擎的数据库会话。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_users(engine) -> dict[str, User]:
    """创建演示租户、所有者用户与两名智能体，返回可按角色取用的用户表。"""
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
        db.add(owner)
        db.add(
            AgentProfile(
                id="agent_xz",
                tenant_id="tenant_demo",
                name="行政",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.add(
            AgentProfile(
                id="agent_cw",
                tenant_id="tenant_demo",
                name="财务",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
        return {"owner": owner}


def _auth(user: User) -> dict[str, str]:
    """为指定用户签发访问令牌并组装认证请求头。"""
    from app.security.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


# ---------- 级联删除 ----------


def test_delete_binding_cascades_mounts_and_conv_states(monkeypatch) -> None:
    """验证删除渠道绑定后，挂载智能体与会话状态被一并级联清理。"""
    import app.api.channels as channels_api

    engine = _test_engine()
    users = _seed_users(engine)
    with Session(engine) as db:
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="active",
            created_by_user_id="user_owner",
        )
        db.add(binding)
        db.flush()
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo", binding_id=binding.id, agent_id="agent_xz", is_default=True
            )
        )
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo", binding_id=binding.id, agent_id="agent_cw"
            )
        )
        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding.id,
                external_conv_id="wechat_p2p_u1",
                current_agent_id="agent_cw",
            )
        )
        db.commit()
        binding_id = binding.id

    client = _make_api_client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    response = client.delete(
        f"/api/enterprise/channels/{binding_id}?tenant_id=tenant_demo",
        headers=_auth(users["owner"]),
    )
    assert response.status_code == 204

    with Session(engine) as db:
        assert db.get(ChannelBinding, binding_id) is None
        assert db.exec(select(ChannelBindingAgent)).all() == []
        assert db.exec(select(ChannelConvState)).all() == []


# ---------- 重绑自愈(孤儿会话认领) ----------


def test_adopt_orphan_channel_sessions() -> None:
    """验证重绑只认领符合渠道与挂载集条件的孤儿会话，其余保持不变。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        dead_binding_id = "chan_dead"
        alive_other = ChannelBinding(
            id="chan_other_alive",
            tenant_id="tenant_demo",
            agent_id="agent_cw",
            channel="wechat",
            status="active",
            external_account_key="wechat:ilink_bot:other",
        )
        new_binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="pending",
            external_account_key="wechat:ilink_bot:bot_new",
        )
        db.add(alive_other)
        db.add(new_binding)
        db.flush()
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo",
                binding_id=new_binding.id,
                agent_id="agent_xz",
                is_default=True,
            )
        )
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo", binding_id=new_binding.id, agent_id="agent_cw"
            )
        )
        sessions = [
            # 指向已删除绑定 → 认领
            ChatSession(
                id="s_dead",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="wechat",
                external_conv_id="wechat_p2p_u1",
                channel_binding_id=dead_binding_id,
                channel_account_key="wechat:ilink_bot:bot_new",
            ),
            # 空 channel_binding_id 且 agent 在挂载集 → 认领
            ChatSession(
                id="s_null",
                tenant_id="tenant_demo",
                agent_id="agent_cw",
                channel="wechat",
                external_conv_id="wechat_p2p_u2",
                channel_account_key="wechat:ilink_bot:bot_new",
            ),
            # 他渠道 → 不动
            ChatSession(
                id="s_other_channel",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="feishu",
                external_conv_id="feishu_p2p_u1",
                channel_binding_id=dead_binding_id,
                channel_account_key="wechat:ilink_bot:bot_new",
            ),
            # 挂载集外 agent → 不动
            ChatSession(
                id="s_outside_mounts",
                tenant_id="tenant_demo",
                agent_id="agent_other",
                channel="wechat",
                external_conv_id="wechat_p2p_u3",
                channel_binding_id=dead_binding_id,
                channel_account_key="wechat:ilink_bot:bot_new",
            ),
            # 已挂在现存绑定上 → 不动
            ChatSession(
                id="s_alive",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="wechat",
                external_conv_id="wechat_p2p_u4",
                channel_binding_id="chan_other_alive",
                channel_account_key="wechat:ilink_bot:bot_new",
            ),
        ]
        for row in sessions:
            db.add(row)
        db.commit()

        adopted = adopt_orphan_channel_sessions(db, new_binding)
        db.commit()
        assert adopted == 2
        assert db.get(ChatSession, "s_dead").channel_binding_id == new_binding.id
        assert db.get(ChatSession, "s_null").channel_binding_id == new_binding.id
        assert db.get(ChatSession, "s_other_channel").channel_binding_id == dead_binding_id
        assert db.get(ChatSession, "s_outside_mounts").channel_binding_id == dead_binding_id
        assert db.get(ChatSession, "s_alive").channel_binding_id == "chan_other_alive"


def test_adopt_orphan_sessions_legacy_mount_fallback() -> None:
    """验证无挂载行的存量绑定会回退到自身智能体并认领孤儿会话。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        # 无挂载行(存量绑定):挂载集回退为 [binding.agent_id]
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="pending",
            external_account_key="wechat:ilink_bot:bot_legacy",
        )
        db.add(binding)
        db.commit()
        db.add(
            ChatSession(
                id="s_legacy",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="wechat",
                external_conv_id="wechat_p2p_u1",
                channel_binding_id="chan_dead",
                channel_account_key="wechat:ilink_bot:bot_legacy",
            )
        )
        db.commit()

        assert adopt_orphan_channel_sessions(db, binding) == 1
        assert db.get(ChatSession, "s_legacy").channel_binding_id == binding.id


def test_same_wecom_corp_and_bot_is_unique_across_tenants() -> None:
    """验证同一企微企业与机器人组合全局唯一，跨租户重复写入会被拒绝。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(Tenant(id="tenant_b", name="B"))
        db.add(
            ChannelBinding(
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="wecom",
                external_account_key="wecom:corp:5:corpA:bot:10:shared_bot",
            )
        )
        db.commit()
        db.add(
            ChannelBinding(
                tenant_id="tenant_b",
                agent_id="agent_b",
                channel="wecom",
                external_account_key="wecom:corp:5:corpA:bot:10:shared_bot",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_same_wecom_bot_id_in_different_corps_is_allowed() -> None:
    """验证不同企业下出现相同机器人 ID 时允许共存，不会触发唯一约束。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(Tenant(id="tenant_b", name="B"))
        db.add(
            ChannelBinding(
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="wecom",
                external_account_key="wecom:corp:5:corpA:bot:10:shared_bot",
            )
        )
        db.add(
            ChannelBinding(
                tenant_id="tenant_b",
                agent_id="agent_b",
                channel="wecom",
                external_account_key="wecom:corp:5:corpB:bot:10:shared_bot",
            )
        )
        db.commit()
        assert len(db.exec(select(ChannelBinding)).all()) == 2


# ---------- 投递回退挂载感知 ----------


def _stageable_session(**overrides) -> ChatSession:
    """构造可投递的渠道会话，并允许用关键字覆盖默认字段。"""
    values = {
        "id": "session_x",
        "tenant_id": "tenant_demo",
        "agent_id": "agent_cw",
        "channel": "wechat",
        "channel_target_json": {"to_user_id": "u1", "context_token": "ctx"},
    }
    values.update(overrides)
    return ChatSession(**values)


def _message(session_id: str) -> Message:
    """构造该会话下的一条助手回复，作为投递的触发消息。"""
    return Message(
        id=f"msg_{session_id}",
        tenant_id="tenant_demo",
        session_id=session_id,
        role="assistant",
        content="回复",
    )


def test_delivery_does_not_fallback_when_original_binding_is_deleted() -> None:
    """验证会话原绑定被删除时不回退到其他绑定，直接标记投递失败。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        # 会话直挂的绑定已删除;新 active 绑定挂载了 agent_cw
        alive = ChannelBinding(
            tenant_id="tenant_demo", agent_id="agent_xz", channel="wechat", status="active"
        )
        db.add(alive)
        db.flush()
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo", binding_id=alive.id, agent_id="agent_cw"
            )
        )
        db.commit()
        chat_session = _stageable_session(channel_binding_id="chan_dead")
        message = _message(chat_session.id)
        db.add(chat_session)
        db.add(message)
        db.commit()

        stage_channel_delivery(db, chat_session, message)
        db.commit()
        delivery = db.exec(select(ChannelDelivery)).one()
        assert delivery.status == "failed"
        assert delivery.last_error == "binding_missing_or_inactive"


def test_delivery_fallback_default_agent_still_works() -> None:
    """验证无绑定会话按账号键回退到默认智能体的 active 绑定并完成投递。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        # 无挂载行:挂载集回退 binding.agent_id(与旧 (agent_id, channel) 反查等价)
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="active",
            external_account_key="wechat:ilink_bot:legacy",
        )
        db.add(binding)
        db.commit()
        chat_session = _stageable_session(
            agent_id="agent_xz",
            channel_binding_id=None,
            channel_account_key=binding.external_account_key,
        )
        message = _message(chat_session.id)
        db.add(chat_session)
        db.add(message)
        db.commit()

        stage_channel_delivery(db, chat_session, message)
        db.commit()
        deliveries = db.exec(select(ChannelDelivery)).all()
        assert len(deliveries) == 1
        assert deliveries[0].binding_id == binding.id
        assert db.get(ChatSession, chat_session.id).channel_binding_id == binding.id


def test_delivery_gives_up_when_no_active_binding_matches() -> None:
    """验证没有可用 active 绑定时投递放弃，并记录失败原因。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        # 唯一绑定已停用,且无其他 active 绑定
        binding = ChannelBinding(
            tenant_id="tenant_demo", agent_id="agent_xz", channel="wechat", status="disabled"
        )
        db.add(binding)
        db.commit()
        chat_session = _stageable_session(channel_binding_id=binding.id)
        message = _message(chat_session.id)
        db.add(chat_session)
        db.add(message)
        db.commit()

        stage_channel_delivery(db, chat_session, message)
        db.commit()
        delivery = db.exec(select(ChannelDelivery)).one()
        assert delivery.status == "failed"
        assert delivery.last_error == "binding_missing_or_inactive"
