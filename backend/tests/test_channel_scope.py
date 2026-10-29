import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.channels.service_intake as intake_module
import app.core.agent_loop as agent_loop_module
from app.channels.adapters.wecom import normalize_wecom_frame
from app.channels.service_identity import (
    channel_username,
    external_account_key,
    external_account_scope,
    resolve_or_provision_user,
)
from app.channels.service_intake import process_inbound
from app.channels.service_session import adopt_orphan_channel_sessions
from app.db import database
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelIdentity,
    ChannelInboundEvent,
    ChatSession,
    MemoryRecord,
    Tenant,
    User,
)


def _test_engine():
    """建一个进程内共享连接的 SQLite 内存库,供各用例互不干扰地复用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_wecom_binding(
    engine,
    *,
    tenant_id: str = "tenant_demo",
    agent_id: str = "agent_1",
    corp_id: str | None = None,
    bot_id: str = "aib_bot1",
) -> str:
    """插入租户、员工与企微绑定行,返回绑定 ID 供用例引用。"""
    config = {"bot_id": bot_id}
    if corp_id:
        config["corp_id"] = corp_id
    with Session(engine) as db:
        if not db.get(Tenant, tenant_id):
            db.add(Tenant(id=tenant_id, name=tenant_id))
        if not db.get(AgentProfile, agent_id):
            db.add(AgentProfile(id=agent_id, tenant_id=tenant_id, name=agent_id, metadata_json={}))
        binding = ChannelBinding(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel="wecom",
            status="active",
            config_json=config,
            external_account_key=external_account_key("wecom", config),
            identity_scope_key=corp_id or bot_id,
            created_by_user_id="user_owner",
        )
        db.add(binding)
        db.commit()
        return binding.id


def _load_binding(engine, binding_id: str) -> ChannelBinding:
    """重新读取绑定并脱离会话,避免离开会话后对象失效。"""
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        db.expunge(binding)
        return binding


def _wecom_inbound(event_id: str, text: str, *, userid: str = "zhangsan", group: bool = False):
    """构造企微文本回调帧并归一化成入站事件,可切换单聊与群聊。"""
    body = {
        "msgid": event_id,
        "aibotid": "aib_bot1",
        "chattype": "group" if group else "single",
        "from": {"userid": userid},
        "msgtype": "text",
        "text": {"content": text},
    }
    if group:
        body["chatid"] = "wr_room1"
    return normalize_wecom_frame(
        {"cmd": "aibot_msg_callback", "headers": {"req_id": f"req_{event_id}"}, "body": body}
    )


class RecordingAgentLoop:
    """记录每轮 turn 请求的假 AgentLoop,替代真实模型调用。"""
    calls: list = []

    def __init__(self, db, *, event_sink=None):
        """保存测试会话,供 handle_turn 提交事务时使用。"""
        self.db = db

    def handle_turn(self, request):
        """记录本次 turn 请求并提交事务,模拟真实循环的最小行为。"""
        type(self).calls.append(request)
        self.db.commit()


@pytest.fixture(autouse=True)
def _fake_agent_loop(monkeypatch):
    """用例级自动替换真实 AgentLoop 与企微打字调用,并清空调用记录。"""
    RecordingAgentLoop.calls = []
    monkeypatch.setattr(agent_loop_module, "AgentLoop", RecordingAgentLoop)
    monkeypatch.setattr(intake_module, "_send_wechat_typing", lambda *args, **kwargs: None)
    yield


def _users_of(engine, tenant_id: str, channel: str, external_id: str, scope: str) -> User:
    """按通道身份直接解析或创建用户,便于断言身份归属。"""
    with Session(engine) as db:
        return resolve_or_provision_user(db, tenant_id, channel, external_id, None, scope)


# ---------- scope helper ----------


def test_external_account_scope_resolution() -> None:
    """验证 scope 解析优先级:企业 corp_id 优先,其次 bot_id,最后回退绑定 ID。"""
    engine = _test_engine()
    with Session(engine) as db:
        wechat = ChannelBinding(tenant_id="t", agent_id="a", channel="wechat", config_json={})
        corp = ChannelBinding(
            tenant_id="t",
            agent_id="a2",
            channel="wecom",
            config_json={"bot_id": "b1", "corp_id": "corpX"},
        )
        bot_only = ChannelBinding(
            tenant_id="t", agent_id="a3", channel="wecom", config_json={"bot_id": "b2"}
        )
        empty = ChannelBinding(
            id="chan_fallback", tenant_id="t", agent_id="a4", channel="wecom", config_json={}
        )
        assert external_account_scope(db, wechat) == ""
        assert external_account_scope(db, corp) == "corpX"
        assert external_account_scope(db, bot_only) == "b2"
        assert external_account_scope(db, empty) == "chan_fallback"


# ---------- 维护者场景 ----------


def test_same_userid_different_tenants_get_different_users() -> None:
    """验证不同租户下相同 userid 被拆成两个独立账号。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, tenant_id="tenant_a", corp_id="corpA")
    binding_b = _seed_wecom_binding(
        engine,
        tenant_id="tenant_b",
        corp_id="corpA",
        bot_id="aib_bot2",
        agent_id="agent_2",
    )

    binding = _load_binding(engine, binding_a)
    assert process_inbound(binding, _wecom_inbound("m1", "你好"), db_engine=engine) is True
    user_a = RecordingAgentLoop.calls[-1].user_id

    binding = _load_binding(engine, binding_b)
    assert process_inbound(binding, _wecom_inbound("m1", "你好"), db_engine=engine) is True
    user_b = RecordingAgentLoop.calls[-1].user_id

    assert user_a != user_b


def test_same_tenant_different_corp_same_userid_get_different_users() -> None:
    """验证同租户不同企业的同名 userid 得到不同用户,username 与身份行都不冲突。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, corp_id="corpA", bot_id="bot_a")
    binding_b = _seed_wecom_binding(engine, corp_id="corpB", bot_id="bot_b", agent_id="agent_2")

    binding = _load_binding(engine, binding_a)
    assert process_inbound(binding, _wecom_inbound("m1", "你好"), db_engine=engine) is True
    binding = _load_binding(engine, binding_b)
    assert process_inbound(binding, _wecom_inbound("m2", "你好"), db_engine=engine) is True

    user_a, user_b = RecordingAgentLoop.calls[0].user_id, RecordingAgentLoop.calls[1].user_id
    assert user_a != user_b
    with Session(engine) as db:
        usernames = {row.username for row in db.exec(select(User)).all()}
        # 同租户两企业同名不撞 username
        assert channel_username("tenant_demo", "wecom", "zhangsan", "corpA") in usernames
        assert channel_username("tenant_demo", "wecom", "zhangsan", "corpB") in usernames
        identities = db.exec(select(ChannelIdentity)).all()
        assert len(identities) == 2
        assert {i.external_account_scope for i in identities} == {"corpA", "corpB"}


def test_same_corp_different_bindings_share_identity() -> None:
    """验证同企业不同绑定的同一 userid 复用同一用户与同一条身份记录。"""
    engine = _test_engine()
    binding_1 = _seed_wecom_binding(engine, corp_id="corpX", bot_id="bot_1")
    binding_2 = _seed_wecom_binding(engine, corp_id="corpX", bot_id="bot_2", agent_id="agent_2")

    binding = _load_binding(engine, binding_1)
    assert process_inbound(binding, _wecom_inbound("m1", "你好"), db_engine=engine) is True
    binding = _load_binding(engine, binding_2)
    assert process_inbound(binding, _wecom_inbound("m2", "你好"), db_engine=engine) is True

    assert RecordingAgentLoop.calls[0].user_id == RecordingAgentLoop.calls[1].user_id
    with Session(engine) as db:
        assert len(db.exec(select(ChannelIdentity)).all()) == 1


def test_cross_corp_same_msgid_events_both_processed() -> None:
    """验证两个企业的同一 msgid 各自入库并处理,幂等按绑定维度判定。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, corp_id="corpA", bot_id="bot_a")
    binding_b = _seed_wecom_binding(engine, corp_id="corpB", bot_id="bot_b", agent_id="agent_2")

    binding = _load_binding(engine, binding_a)
    assert process_inbound(binding, _wecom_inbound("same_msgid", "甲企业消息"), db_engine=engine) is True
    binding = _load_binding(engine, binding_b)
    # 同一 msgid 在另一企业(另一绑定)不再被幂等误杀
    assert process_inbound(binding, _wecom_inbound("same_msgid", "乙企业消息"), db_engine=engine) is True

    assert len(RecordingAgentLoop.calls) == 2
    with Session(engine) as db:
        events = db.exec(select(ChannelInboundEvent)).all()
        assert len(events) == 2
        assert {event.binding_id for event in events} == {binding_a, binding_b}


def test_bind_unbind_never_migrates_other_corp_data() -> None:
    """验证绑定与解绑只迁移当前企业 corpA 的会话与记忆,corpB 数据全程不动。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, corp_id="corpA", bot_id="bot_a")
    _seed_wecom_binding(engine, corp_id="corpB", bot_id="bot_b", agent_id="agent_2")
    with Session(engine) as db:
        db.add(
            User(id="user_web", tenant_id="tenant_demo", username="webadmin", display_name="管理员", password_hash="x")
        )
        lazy_a = User(
            id="lazy_a",
            tenant_id="tenant_demo",
            username=channel_username("tenant_demo", "wecom", "zhangsan", "corpA"),
            display_name="企微用户 zhangsan",
            source="wecom",
            password_hash="x",
        )
        lazy_b = User(
            id="lazy_b",
            tenant_id="tenant_demo",
            username=channel_username("tenant_demo", "wecom", "zhangsan", "corpB"),
            display_name="企微用户 zhangsan",
            source="wecom",
            password_hash="x",
        )
        db.add(lazy_a)
        db.add(lazy_b)
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="corpA",
                external_user_id="zhangsan",
                staffdeck_user_id=lazy_a.id,
            )
        )
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="corpB",
                external_user_id="zhangsan",
                staffdeck_user_id=lazy_b.id,
            )
        )
        db.add(
            ChatSession(
                id="s_a",
                tenant_id="tenant_demo",
                user_id="lazy_a",
                agent_id="agent_1",
                channel="wecom",
                external_conv_id="wecom_corpA_p2p_zhangsan",
            )
        )
        db.add(
            ChatSession(
                id="s_b",
                tenant_id="tenant_demo",
                user_id="lazy_b",
                agent_id="agent_1",
                channel="wecom",
                external_conv_id="wecom_corpB_p2p_zhangsan",
            )
        )
        db.add(
            MemoryRecord(
                id="mem_a",
                tenant_id="tenant_demo",
                user_id="lazy_a",
                username=lazy_a.username,
                session_id="s_a",
                content="A 企业偏好",
            )
        )
        db.add(
            MemoryRecord(
                id="mem_b",
                tenant_id="tenant_demo",
                user_id="lazy_b",
                username=lazy_b.username,
                session_id="s_b",
                content="B 企业偏好",
            )
        )
        from datetime import timedelta

        from app.db.models import ChannelBindCode, utc_now

        db.add(
            ChannelBindCode(
                tenant_id="tenant_demo",
                user_id="user_web",
                code="123456",
                expires_at=utc_now() + timedelta(minutes=10),
            )
        )
        db.commit()

    binding = _load_binding(engine, binding_a)
    # 绑定 corpA 身份到 web 账号:只迁 corpA 的会话与记忆
    assert process_inbound(binding, _wecom_inbound("m_bind", "/绑定 123456"), db_engine=engine) is False
    with Session(engine) as db:
        assert db.get(ChatSession, "s_a").user_id == "user_web"
        assert db.get(MemoryRecord, "mem_a").user_id == "user_web"
        # 他企业(corpB)的会话与记忆不动
        assert db.get(ChatSession, "s_b").user_id == "lazy_b"
        assert db.get(MemoryRecord, "mem_b").user_id == "lazy_b"

    # 解绑:corpA 迁回懒建账号,corpB 仍然不动
    assert process_inbound(binding, _wecom_inbound("m_unbind", "/解绑"), db_engine=engine) is False
    with Session(engine) as db:
        assert db.get(ChatSession, "s_a").user_id == "lazy_a"
        assert db.get(MemoryRecord, "mem_a").user_id == "lazy_a"
        assert db.get(ChatSession, "s_b").user_id == "lazy_b"
        assert db.get(MemoryRecord, "mem_b").user_id == "lazy_b"


# ---------- 认领不认领他 scope 会话 ----------


def test_adopt_skips_other_scope_sessions() -> None:
    """验证孤儿会话认领只按账号键匹配本绑定,不认领其它企业或历史键的会话。"""
    engine = _test_engine()
    binding_id = _seed_wecom_binding(engine, corp_id="corpA", bot_id="bot_a")
    with Session(engine) as db:
        for session_id, conv in (
            ("s_same", "wecom_corpA_p2p_zhangsan"),
            ("s_other", "wecom_corpB_p2p_zhangsan"),
            ("s_legacy", "wecom_legacy_p2p_lisi"),
        ):
            db.add(
                ChatSession(
                    id=session_id,
                    tenant_id="tenant_demo",
                    agent_id="agent_1",
                    channel="wecom",
                    external_conv_id=conv,
                    channel_binding_id="chan_dead",
                )
            )
        db.commit()
        binding = db.get(ChannelBinding, binding_id)
        binding.external_account_key = "wecom:bot:bot_a"
        db.add(binding)
        for session_id in ("s_same", "s_other", "s_legacy"):
            session = db.get(ChatSession, session_id)
            session.channel_account_key = (
                "wecom:bot:bot_a" if session_id == "s_same" else "wecom:bot:other"
            )
        assert adopt_orphan_channel_sessions(db, binding) == 1
        assert db.get(ChatSession, "s_same").channel_binding_id == binding_id
        assert db.get(ChatSession, "s_other").channel_binding_id == "chan_dead"
        assert db.get(ChatSession, "s_legacy").channel_binding_id == "chan_dead"


# ---------- 迁移 ----------


def _build_legacy_scope_schema(engine) -> None:
    """手工搭出 scope 重构前的老表结构。"""
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                """
                CREATE TABLE channel_identities (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    channel VARCHAR,
                    external_user_id VARCHAR NOT NULL,
                    staffdeck_user_id VARCHAR,
                    display_name VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME,
                    CONSTRAINT uq_channel_identity_external UNIQUE (channel, external_user_id)
                )
                """
            )
        )
        conn.execute(
            sa_text(
                """
                CREATE TABLE channel_inbound_events (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    binding_id VARCHAR,
                    channel VARCHAR,
                    event_id VARCHAR NOT NULL,
                    payload_json JSON,
                    status VARCHAR,
                    error VARCHAR,
                    processed_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME,
                    CONSTRAINT uq_channel_inbound_event UNIQUE (channel, event_id)
                )
                """
            )
        )
        conn.execute(
            sa_text(
                """
                CREATE TABLE channel_bindings (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    status VARCHAR,
                    credentials_enc VARCHAR,
                    config_json JSON,
                    connected BOOLEAN,
                    created_by_user_id VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            sa_text(
                """
                CREATE TABLE sessions (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    user_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    external_conv_id VARCHAR,
                    channel_binding_id VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, status, config_json) "
                "VALUES ('chan_corpA', 'tenant_demo', 'agent_1', 'wecom', 'active', '{\"bot_id\": \"aib_bot1\", \"corp_id\": \"corpA\"}')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_identities (id, tenant_id, channel, external_user_id, staffdeck_user_id) VALUES "
                "('ci_1', 'tenant_demo', 'wecom', 'zhangsan', 'lazy_1'), "
                "('ci_2', 'tenant_demo', 'wechat', 'wxid_1', 'lazy_2'), "
                "('ci_3', 'tenant_b', 'wecom', 'lisi', 'lazy_3')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_inbound_events (id, tenant_id, binding_id, channel, event_id, status) VALUES "
                "('ev_1', 'tenant_demo', 'chan_corpA', 'wecom', 'm1', 'done')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO sessions (id, tenant_id, channel, external_conv_id, channel_binding_id) VALUES "
                "('s_1', 'tenant_demo', 'wecom', 'wecom_p2p_zhangsan', 'chan_corpA'), "
                "('s_2', 'tenant_demo', 'wecom', 'wecom_p2p_lisi', 'chan_dead'), "
                "('s_3', 'tenant_demo', 'wecom', 'wecom_group_wr_room1', 'chan_corpA')"
            )
        )


def test_channel_scope_rebuild_migration(monkeypatch, tmp_path) -> None:
    """验证 scope 重建迁移回填身份与 session 前缀、改用按绑定的幂等唯一约束,并可重复执行。"""
    db_path = tmp_path / "scope.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _build_legacy_scope_schema(engine)
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        identities = conn.execute(
            sa_text(
                "SELECT id, external_account_scope FROM channel_identities ORDER BY id"
            )
        ).all()
        assert dict(identities) == {"ci_1": "corpA", "ci_2": "", "ci_3": "legacy"}
        convs = dict(
            conn.execute(sa_text("SELECT id, external_conv_id FROM sessions")).all()
        )
        assert convs == {
            "s_1": "wecom_corpA_p2p_zhangsan",
            "s_2": "wecom_legacy_p2p_lisi",
            "s_3": "wecom_corpA_group_wr_room1",
        }
        # 新唯一约束生效:同 binding 同 event 冲突、跨 binding 同 event 放行
        conn.execute(
            sa_text(
                "INSERT INTO channel_inbound_events (id, tenant_id, binding_id, channel, event_id, status) "
                "VALUES ('ev_2', 'tenant_demo', 'chan_other', 'wecom', 'm1', 'done')"
            )
        )
        with pytest.raises(Exception):
            conn.execute(
                sa_text(
                    "INSERT INTO channel_inbound_events (id, tenant_id, binding_id, channel, event_id, status) "
                    "VALUES ('ev_3', 'tenant_demo', 'chan_corpA', 'wecom', 'm1', 'done')"
                )
            )

    # 幂等:重跑不炸、数据不变
    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        assert conn.execute(sa_text("SELECT COUNT(*) FROM channel_identities")).scalar_one() == 3
        applied = conn.execute(
            sa_text("SELECT id FROM app_data_migrations WHERE id = :id"),
            {"id": database._CHANNEL_SCOPE_REBUILD_MIGRATION_ID},
        ).first()
        assert applied is not None


def test_scope_rebuild_uses_session_binding_in_multi_corp_tenant(
    monkeypatch, tmp_path
) -> None:
    """验证多企业租户下按会话所在绑定回填 scope,歧义身份归 legacy 并隔离其会话。"""
    db_path = tmp_path / "scope-multi-corp.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _build_legacy_scope_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                "CREATE TABLE users ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, username VARCHAR, source VARCHAR)"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO users (id, tenant_id, username, source) VALUES "
                "('foreign_user', 'tenant_other', 'foreign', 'wechat'), "
                "('old_user', 'tenant_demo', 'old', 'wecom'), "
                "('unused_user', 'tenant_demo', 'unused', 'wecom'), "
                "('group_user', 'tenant_demo', 'group', 'wecom'), "
                "('shared_user', 'tenant_demo', 'shared', 'wecom')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings "
                "(id, tenant_id, agent_id, channel, status, config_json) VALUES "
                "('chan_corpB', 'tenant_demo', 'agent_2', 'wecom', 'active', "
                "'{\"bot_id\":\"bot_b\",\"corp_id\":\"corpB\"}')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_identities "
                "(id, tenant_id, channel, external_user_id, staffdeck_user_id) VALUES "
                "('ci_alice', 'tenant_demo', 'wecom', 'alice', 'old_user'), "
                "('ci_ambiguous', 'tenant_demo', 'wecom', 'nobody', 'unused_user'), "
                "('ci_group', 'tenant_demo', 'wecom', 'group_roomB', 'group_user'), "
                "('ci_shared', 'tenant_demo', 'wecom', 'shared', 'shared_user'), "
                "('ci_polluted', 'tenant_demo', 'wecom', 'foreign', 'foreign_user')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO sessions "
                "(id, tenant_id, user_id, agent_id, channel, external_conv_id, "
                "channel_binding_id) VALUES "
                "('s_corpB', 'tenant_demo', 'old_user', 'agent_2', 'wecom', "
                "'wecom_p2p_alice', 'chan_corpB'), "
                "('s_groupB', 'tenant_demo', 'group_user', 'agent_2', 'wecom', "
                "'wecom_group_roomB', 'chan_corpB'), "
                "('s_sharedA', 'tenant_demo', 'shared_user', 'agent_1', 'wecom', "
                "'wecom_p2p_shared', 'chan_corpA'), "
                "('s_sharedB', 'tenant_demo', 'shared_user', 'agent_2', 'wecom', "
                "'wecom_p2p_shared', 'chan_corpB'), "
                "('s_polluted', 'tenant_demo', 'foreign_user', 'agent_2', 'wecom', "
                "'wecom_p2p_foreign', 'chan_corpB')"
            )
        )
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()

    with engine.begin() as conn:
        scopes = dict(
            conn.execute(
                sa_text(
                    "SELECT id, external_account_scope FROM channel_identities "
                    "WHERE id IN ('ci_alice', 'ci_ambiguous', 'ci_group', 'ci_shared', "
                    "'ci_polluted') "
                    "ORDER BY id"
                )
            ).all()
        )
        assert scopes == {
            "ci_alice": "corpB",
            # ②无会话引用但 tenant 下 scope 不唯一(corpA/corpB):不猜归属,留 legacy
            "ci_ambiguous": "legacy",
            "ci_group": "corpB",
            "ci_polluted": "legacy_cross_tenant",
            # 多 scope 会话引用歧义:留 legacy 并隔离会话(安全例外)
            "ci_shared": "legacy",
        }
        group_identity = conn.execute(
            sa_text(
                "SELECT external_user_id, staffdeck_user_id FROM channel_identities "
                "WHERE id = 'ci_group'"
            )
        ).one()
        assert group_identity == ("group:roomB", "group_user")
        assert conn.execute(
            sa_text("SELECT external_conv_id FROM sessions WHERE id = 's_corpB'")
        ).scalar_one() == "wecom_corpB_p2p_alice"
        assert conn.execute(
            sa_text("SELECT external_conv_id FROM sessions WHERE id = 's_groupB'")
        ).scalar_one() == "wecom_corpB_group_roomB"
        shared_convs = conn.execute(
            sa_text(
                "SELECT external_conv_id FROM sessions "
                "WHERE id IN ('s_sharedA', 's_sharedB') ORDER BY id"
            )
        ).scalars().all()
        assert all(conv.startswith("legacy_ambiguous_identity:") for conv in shared_convs)
        polluted = conn.execute(
            sa_text(
                "SELECT user_id, external_conv_id FROM sessions WHERE id = 's_polluted'"
            )
        ).one()
        assert polluted[0] is None
        assert polluted[1].startswith("legacy_cross_tenant:s_polluted:")


# ---------- API:corp_id 与 meta ----------


def _make_api_client(engine):
    """搭一个只挂载渠道路由的 TestClient,并用内存库会话覆盖依赖。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.channels as channels_api
    from app.db import get_session

    app = FastAPI()
    app.include_router(channels_api.router)

    def override_get_session():
        """为每个请求提供独立测试会话,请求结束时关闭。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_wecom_credentials_accepts_corp_id_and_meta_lists_it(monkeypatch) -> None:
    """验证企微凭据接口接收 corp_id 并落库,meta 将其标为必填且非敏感字段。"""
    import app.api.channels as channels_api

    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
        db.add(owner)
        db.add(
            AgentProfile(
                id="agent_1",
                tenant_id="tenant_demo",
                name="客服",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
    binding_id = _seed_wecom_binding(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        binding.status = "pending"
        binding.credentials_enc = None
        db.add(binding)
        db.commit()

    from app.security.auth import create_access_token

    client = _make_api_client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    headers = {"Authorization": f"Bearer {create_access_token(owner)}"}

    response = client.post(
        f"/api/enterprise/channels/{binding_id}/wecom/credentials",
        json={
            "tenant_id": "tenant_demo",
            "bot_id": "aib_bot1",
            "secret": "bot_secret",
            "corp_id": "corpA",
        },
        headers=headers,
    )
    assert response.status_code == 200
    with Session(engine) as db:
        config = db.get(ChannelBinding, binding_id).config_json
        assert config["corp_id"] == "corpA"

    meta = client.get("/api/enterprise/channels/meta?tenant_id=tenant_demo", headers=headers)
    wecom = next(item for item in meta.json() if item["channel"] == "wecom")
    fields = {field["key"]: field for field in wecom["credential_fields"]}
    assert fields["corp_id"]["optional"] is False
    assert fields["corp_id"]["secret"] is False


# ---------- scope 变化连续性迁移 ----------


def _seed_owner(engine):
    """插入租户、员工与所有者账号,返回所有者及其鉴权头。"""
    from app.security.auth import create_access_token

    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
        db.add(owner)
        db.add(
            AgentProfile(
                id="agent_1",
                tenant_id="tenant_demo",
                name="客服",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
    return owner, {"Authorization": f"Bearer {create_access_token(owner)}"}


def _seed_scope_history(engine, binding_id: str, scope: str) -> None:
    """bot_id scope 时期的存量:身份/会话/指针。"""
    from app.db.models import ChannelConvState

    with Session(engine) as db:
        lazy = User(
            id="lazy_scope",
            tenant_id="tenant_demo",
            username=f"wecom_{scope}_zhangsan",
            display_name="企微用户 zhangsan",
            source="wecom",
            password_hash="x",
        )
        db.add(lazy)
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope=scope,
                external_user_id="zhangsan",
                staffdeck_user_id=lazy.id,
            )
        )
        db.add(
            ChatSession(
                id="s_scope",
                tenant_id="tenant_demo",
                user_id=lazy.id,
                agent_id="agent_1",
                channel="wecom",
                external_conv_id=f"wecom_{scope}_p2p_zhangsan",
                channel_binding_id=binding_id,
            )
        )
        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding_id,
                external_conv_id=f"wecom_{scope}_p2p_zhangsan",
                current_agent_id="agent_1",
            )
        )
        db.commit()


def test_credentials_corp_id_fill_migrates_identity_sessions_and_pointer(monkeypatch) -> None:
    """验证补填 corp_id 后身份、会话与出站指针同步迁移,后续消息仍路由到同一用户。"""
    import app.api.channels as channels_api
    from app.db.models import ChannelConvState

    engine = _test_engine()
    owner, headers = _seed_owner(engine)
    _ = owner
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    _seed_scope_history(engine, binding_id, "aib_bot1")
    client = _make_api_client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)

    response = client.post(
        f"/api/enterprise/channels/{binding_id}/wecom/credentials",
        json={
            "tenant_id": "tenant_demo",
            "bot_id": "aib_bot1",
            "secret": "bot_secret",
            "corp_id": "corpA",
        },
        headers=headers,
    )
    assert response.status_code == 200

    with Session(engine) as db:
        identity = db.exec(select(ChannelIdentity)).one()
        # 身份连续:scope 更新为 corpA,仍指向同一 User
        assert identity.external_account_scope == "corpA"
        assert identity.staffdeck_user_id == "lazy_scope"
        assert db.get(ChatSession, "s_scope").external_conv_id == "wecom_corpA_p2p_zhangsan"
        state = db.exec(select(ChannelConvState)).one()
        assert state.external_conv_id == "wecom_corpA_p2p_zhangsan"

    # 新消息按新 scope 解析到同一 User,正常路由
    binding = _load_binding(engine, binding_id)
    assert process_inbound(binding, _wecom_inbound("m_after", "你好"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].user_id == "lazy_scope"


def test_scope_migration_includes_identities_without_sessions() -> None:
    """验证 scope 迁移覆盖无会话引用的身份行,且只改写当前绑定的会话。"""
    engine = _test_engine()
    binding_id = _seed_wecom_binding(engine, corp_id="corpA", bot_id="aib_bot1")
    with Session(engine) as db:
        db.add(User(id="l1", tenant_id="tenant_demo", username="wecom_aib_bot1_zhangsan", source="wecom", password_hash="x"))
        db.add(User(id="l2", tenant_id="tenant_demo", username="wecom_aib_bot1_lisi", source="wecom", password_hash="x"))
        # l1 被该 binding 的会话引用;l2 的旧 scope 身份无任何该 binding 会话引用
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="aib_bot1",
                external_user_id="zhangsan",
                staffdeck_user_id="l1",
            )
        )
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="aib_bot1",
                external_user_id="lisi",
                staffdeck_user_id="l2",
            )
        )
        binding = db.get(ChannelBinding, binding_id)
        config = dict(binding.config_json or {})
        config["corp_id"] = "corpA"
        binding.config_json = config
        db.add(binding)
        db.add(
            ChatSession(
                id="s_ref",
                tenant_id="tenant_demo",
                user_id="l1",
                agent_id="agent_1",
                channel="wecom",
                external_conv_id="wecom_aib_bot1_p2p_zhangsan",
                channel_binding_id=binding_id,
            )
        )
        db.add(
            ChatSession(
                id="s_unref",
                tenant_id="tenant_demo",
                user_id="l2",
                agent_id="agent_1",
                channel="wecom",
                external_conv_id="wecom_aib_bot1_p2p_lisi",
                channel_binding_id="chan_other",
            )
        )
        db.commit()

        from app.channels.service_identity import migrate_scope_for_binding

        stats = migrate_scope_for_binding(db, binding, "aib_bot1", "corpA")
        db.commit()

        assert stats["identities"] == 2
        assert stats["sessions"] == 1
        identities = {
            row.external_user_id: row.external_account_scope
            for row in db.exec(select(ChannelIdentity)).all()
        }
        # 被当前 binding 会话引用的身份迁移;未被引用的旧 scope 行不动
        assert identities == {"zhangsan": "corpA", "lisi": "corpA"}
        assert db.get(ChatSession, "s_ref").external_conv_id == "wecom_corpA_p2p_zhangsan"
        assert db.get(ChatSession, "s_unref").external_conv_id == "wecom_aib_bot1_p2p_lisi"


def test_scope_migration_normalizes_legacy_group_identity_once() -> None:
    """验证旧群身份在迁移时被规范化为 group:<room> 形式,且只转换一次。"""
    engine = _test_engine()
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    with Session(engine) as db:
        db.add(
            User(
                id="legacy_group_user",
                tenant_id="tenant_demo",
                username="legacy_group",
                source="wecom",
                password_hash="x",
            )
        )
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="aib_bot1",
                external_user_id="group_aib_bot1_room1",
                staffdeck_user_id="legacy_group_user",
            )
        )
        binding = db.get(ChannelBinding, binding_id)
        config = dict(binding.config_json or {})
        config["corp_id"] = "corpA"
        binding.config_json = config
        db.add(binding)
        db.commit()

        from app.channels.service_identity import migrate_scope_for_binding

        stats = migrate_scope_for_binding(db, binding, "aib_bot1", "corpA")
        db.commit()

        identity = db.exec(select(ChannelIdentity)).one()
        assert stats["identities"] == 1
        assert identity.external_account_scope == "corpA"
        assert identity.external_user_id == "group:room1"


def test_scope_migration_roundtrip_and_noop(monkeypatch) -> None:
    """验证补填 corp_id 触发迁移、跨企业变更被 400 拦截,且 scope 未变时不重复迁移。"""
    import app.api.channels as channels_api
    from app.channels import service_identity as identity_module

    engine = _test_engine()
    owner, headers = _seed_owner(engine)
    _ = owner
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    _seed_scope_history(engine, binding_id, "aib_bot1")
    client = _make_api_client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)

    url = f"/api/enterprise/channels/{binding_id}/wecom/credentials"
    # ① 补填 corp_id:迁移到 corpA
    client.post(url, json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s1", "corp_id": "corpA"}, headers=headers)
    with Session(engine) as db:
        assert db.exec(select(ChannelIdentity)).one().external_account_scope == "corpA"
        assert db.get(ChatSession, "s_scope").external_conv_id == "wecom_corpA_p2p_zhangsan"
        assert db.get(ChatSession, "s_scope").channel_account_key == (
            "wecom:corp:5:corpA:bot:8:aib_bot1"
        )
        assert db.get(ChannelBinding, binding_id).external_account_key == (
            "wecom:corp:5:corpA:bot:8:aib_bot1"
        )

    # ② 显式空串去掉 corp_id:跨企业变更,400 拦截且数据零变化
    cleared = client.post(url, json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s2", "corp_id": ""}, headers=headers)
    assert cleared.status_code == 400
    assert "corp_id" in cleared.json()["detail"]
    with Session(engine) as db:
        assert db.exec(select(ChannelIdentity)).one().external_account_scope == "corpA"
        assert db.get(ChatSession, "s_scope").external_conv_id == "wecom_corpA_p2p_zhangsan"

    # ②b corpA→corpB:同样 400 拦截且零变化
    changed = client.post(url, json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s2", "corp_id": "corpB"}, headers=headers)
    assert changed.status_code == 400
    with Session(engine) as db:
        assert db.exec(select(ChannelIdentity)).one().external_account_scope == "corpA"

    # ③ 再保存一次(scope 无变化):不触发迁移
    calls: list = []
    original = identity_module.migrate_scope_for_binding
    monkeypatch.setattr(
        channels_api,
        "migrate_scope_for_binding",
        lambda db, binding, old, new: calls.append((old, new)) or original(db, binding, old, new),
    )
    client.post(url, json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s3", "corp_id": "corpA"}, headers=headers)
    assert calls == []
    with Session(engine) as db:
        assert len(db.exec(select(ChannelIdentity)).all()) == 1
        assert db.get(ChatSession, "s_scope").external_conv_id == "wecom_corpA_p2p_zhangsan"


def test_migrate_scope_noop_when_unchanged() -> None:
    """验证新旧 scope 相同时迁移返回全零统计,不触碰任何数据。"""
    engine = _test_engine()
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    with Session(engine) as db:
        from app.channels.service_identity import migrate_scope_for_binding

        binding = db.get(ChannelBinding, binding_id)
        stats = migrate_scope_for_binding(db, binding, "aib_bot1", "aib_bot1")
        assert stats == {"identities": 0, "identities_conflicted": 0, "sessions": 0, "conv_states": 0}


# ---------- 同企业多 Bot:会话与出站按 binding 隔离 ----------


def test_same_corp_two_bots_get_isolated_sessions_and_outbound() -> None:
    """验证同企业两个 Bot 的会话与出站投递按 binding 隔离,互不串台。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, corp_id="corpX", bot_id="bot_a")
    # uq(agent_id, channel) 全局唯一:同 tenant 两绑定用不同默认员工
    binding_b = _seed_wecom_binding(engine, corp_id="corpX", bot_id="bot_b", agent_id="agent_2")

    binding = _load_binding(engine, binding_a)
    assert process_inbound(binding, _wecom_inbound("m_a", "你好 A"), db_engine=engine) is True
    binding = _load_binding(engine, binding_b)
    assert process_inbound(binding, _wecom_inbound("m_b", "你好 B"), db_engine=engine) is True

    session_a = RecordingAgentLoop.calls[0].session_id
    session_b = RecordingAgentLoop.calls[1].session_id
    assert session_a != session_b
    with Session(engine) as db:
        assert db.get(ChatSession, session_a).channel_binding_id == binding_a
        assert db.get(ChatSession, session_b).channel_binding_id == binding_b

        # 出站各自按会话直挂 binding 投递(second_incoming_binding=bot_b → outbound=bot_b)
        from app.channels.service_outbox import stage_channel_delivery
        from app.db.models import ChannelDelivery, Message

        for session_id, message_id in ((session_a, "msg_out_a"), (session_b, "msg_out_b")):
            chat_session = db.get(ChatSession, session_id)
            message = Message(
                id=message_id,
                tenant_id="tenant_demo",
                session_id=session_id,
                role="assistant",
                content="回复",
            )
            db.add(message)
            stage_channel_delivery(db, chat_session, message)
        db.commit()
        deliveries = {
            row.message_id: row.binding_id for row in db.exec(select(ChannelDelivery)).all()
        }
        assert deliveries == {"msg_out_a": binding_a, "msg_out_b": binding_b}


# ---------- scope 迁移限定当前 binding + 冲突合并一致 ----------


def test_scope_migration_narrowed_to_current_binding() -> None:
    """验证 scope 迁移只影响当前绑定的身份、会话与指针,其它绑定保持原样。"""
    engine = _test_engine()
    binding_a = _seed_wecom_binding(engine, bot_id="bot_a")
    binding_b = _seed_wecom_binding(engine, bot_id="bot_b", agent_id="agent_2")
    with Session(engine) as db:
        for lazy_id, conv_suffix, bid, agent, scope in (
            ("lazy_a", "zhangsan", binding_a, "agent_1", "bot_a"),
            ("lazy_b", "lisi", binding_b, "agent_2", "bot_b"),
        ):
            db.add(
                User(
                    id=lazy_id,
                    tenant_id="tenant_demo",
                    username=channel_username("tenant_demo", "wecom", conv_suffix, scope),
                    source="wecom",
                    password_hash="x",
                )
            )
            db.add(
                ChannelIdentity(
                    tenant_id="tenant_demo",
                    channel="wecom",
                    external_account_scope=scope,
                    external_user_id=conv_suffix,
                    staffdeck_user_id=lazy_id,
                )
            )
            db.add(
                ChatSession(
                    id=f"s_{conv_suffix}",
                    tenant_id="tenant_demo",
                    user_id=lazy_id,
                    agent_id=agent,
                    channel="wecom",
                    external_conv_id=f"wecom_{scope}_p2p_{conv_suffix}",
                    channel_binding_id=bid,
                )
            )
        from app.db.models import ChannelConvState

        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding_a,
                external_conv_id="wecom_bot_a_p2p_zhangsan",
                current_agent_id="agent_1",
            )
        )
        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding_b,
                external_conv_id="wecom_bot_b_p2p_lisi",
                current_agent_id="agent_2",
            )
        )
        db.commit()

        from app.channels.service_identity import migrate_scope_for_binding

        # A 首次补填企业信息(bot_a → corpY):只有 A 的身份/会话/指针迁移
        binding_a_row = db.get(ChannelBinding, binding_a)
        config = dict(binding_a_row.config_json or {})
        config["corp_id"] = "corpY"
        binding_a_row.config_json = config
        db.add(binding_a_row)
        db.commit()
        stats = migrate_scope_for_binding(db, db.get(ChannelBinding, binding_a), "bot_a", "corpY")
        db.commit()
        assert stats["identities"] == 1
        assert stats["sessions"] == 1
        assert stats["conv_states"] == 1

        identity_a = db.exec(
            select(ChannelIdentity).where(ChannelIdentity.external_user_id == "zhangsan")
        ).one()
        assert identity_a.external_account_scope == "corpY"
        assert db.get(ChatSession, "s_zhangsan").external_conv_id == "wecom_corpY_p2p_zhangsan"
        state_a = db.exec(
            select(ChannelConvState).where(ChannelConvState.binding_id == binding_a)
        ).one()
        assert state_a.external_conv_id == "wecom_corpY_p2p_zhangsan"

        # B 的身份/会话/指针零影响
        identity_b = db.exec(
            select(ChannelIdentity).where(ChannelIdentity.external_user_id == "lisi")
        ).one()
        assert identity_b.external_account_scope == "bot_b"
        assert db.get(ChatSession, "s_lisi").external_conv_id == "wecom_bot_b_p2p_lisi"
        state_b = db.exec(
            select(ChannelConvState).where(ChannelConvState.binding_id == binding_b)
        ).one()
        assert state_b.external_conv_id == "wecom_bot_b_p2p_lisi"


def test_scope_migration_conflict_aborts_without_merging_users() -> None:
    """验证目标 scope 已有冲突身份时抛 IdentityScopeConflict 并回滚,不合并用户数据。"""
    engine = _test_engine()
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    with Session(engine) as db:
        db.add(User(id="lazy_old", tenant_id="tenant_demo", username=channel_username("tenant_demo", "wecom", "zhangsan", "aib_bot1"), source="wecom", password_hash="x"))
        db.add(User(id="user_web", tenant_id="tenant_demo", username="webadmin", password_hash="x"))
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="aib_bot1",
                external_user_id="zhangsan",
                staffdeck_user_id="lazy_old",
            )
        )
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="corpY",
                external_user_id="zhangsan",
                staffdeck_user_id="user_web",
            )
        )
        db.add(
            ChatSession(
                id="s_conflict",
                tenant_id="tenant_demo",
                user_id="lazy_old",
                agent_id="agent_1",
                channel="wecom",
                external_conv_id="wecom_aib_bot1_p2p_zhangsan",
                channel_binding_id=binding_id,
            )
        )
        db.add(
            MemoryRecord(
                id="mem_conflict",
                tenant_id="tenant_demo",
                user_id="lazy_old",
                username=channel_username("tenant_demo", "wecom", "zhangsan", "aib_bot1"),
                session_id="s_conflict",
                content="偏好",
            )
        )
        db.commit()

        from app.channels.service_identity import migrate_scope_for_binding

        # 首次补填企业信息(aib_bot1 → corpY):冲突合并
        binding = db.get(ChannelBinding, binding_id)
        config = dict(binding.config_json or {})
        config["corp_id"] = "corpY"
        binding.config_json = config
        db.add(binding)
        db.commit()
        from app.channels.service_identity import IdentityScopeConflict

        with pytest.raises(IdentityScopeConflict):
            migrate_scope_for_binding(
                db, db.get(ChannelBinding, binding_id), "aib_bot1", "corpY"
            )
        db.rollback()

        # 合并后身份与会话一致:同一 User、同一 conv 前缀
        rows = db.exec(select(ChannelIdentity)).all()
        assert len(rows) == 2
        assert {(row.external_account_scope, row.staffdeck_user_id) for row in rows} == {
            ("aib_bot1", "lazy_old"),
            ("corpY", "user_web"),
        }
        session = db.get(ChatSession, "s_conflict")
        assert session.user_id == "lazy_old"
        assert session.external_conv_id == "wecom_aib_bot1_p2p_zhangsan"
        memory = db.get(MemoryRecord, "mem_conflict")
        assert memory.user_id == "lazy_old"


# ---------- corp_id 可读取 + 不传不清空 ----------


def test_corp_id_read_and_reconfig_keeps_unless_explicit(monkeypatch) -> None:
    """验证 corp_id 可读回、重配未传时保留原值、显式置空按跨企业变更 400 拦截。"""
    import app.api.channels as channels_api

    engine = _test_engine()
    owner, headers = _seed_owner(engine)
    binding_id = _seed_wecom_binding(engine, bot_id="aib_bot1")
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        binding.status = "pending"
        binding.credentials_enc = None
        db.add(binding)
        db.commit()
    client = _make_api_client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)

    url = f"/api/enterprise/channels/{binding_id}/wecom/credentials"
    client.post(
        url,
        json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s1", "corp_id": "corpA"},
        headers=headers,
    )
    listed = client.get(
        "/api/enterprise/channels?tenant_id=tenant_demo&agent_id=agent_1", headers=headers
    )
    assert listed.json()[0]["corp_id"] == "corpA"
    assert "s1" not in listed.text

    # 不传 corp_id 字段重新配置:不被静默清空
    client.post(
        url,
        json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s2"},
        headers=headers,
    )
    with Session(engine) as db:
        assert db.get(ChannelBinding, binding_id).config_json["corp_id"] == "corpA"

    # 显式传空串想清除:属于跨企业变更,400 拦截,corpA 保留(与本轮禁止迁移语义一致)
    cleared = client.post(
        url,
        json={"tenant_id": "tenant_demo", "bot_id": "aib_bot1", "secret": "s3", "corp_id": ""},
        headers=headers,
    )
    assert cleared.status_code == 400
    with Session(engine) as db:
        assert db.get(ChannelBinding, binding_id).config_json["corp_id"] == "corpA"


# ---------- 同 Agent 多 binding 迁移(去 uq 重建) ----------


def test_channel_bindings_multi_migration_removes_uq(monkeypatch, tmp_path) -> None:
    """验证迁移重建绑定表去掉 (agent_id, channel) 唯一约束,并支持幂等重跑。"""
    db_path = tmp_path / "multi.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                """
                CREATE TABLE channel_bindings (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    status VARCHAR,
                    credentials_enc VARCHAR,
                    config_json JSON,
                    connected BOOLEAN,
                    created_by_user_id VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME,
                    CONSTRAINT uq_channel_binding_agent_channel UNIQUE (agent_id, channel)
                )
                """
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, status) "
                "VALUES ('chan_1', 'tenant_demo', 'agent_1', 'wecom', 'active')"
            )
        )
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        # 重建后同 (agent_id, channel) 可重复
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, status) "
                "VALUES ('chan_2', 'tenant_demo', 'agent_1', 'wecom', 'pending')"
            )
        )
        count = conn.execute(sa_text("SELECT COUNT(*) FROM channel_bindings")).scalar_one()
        assert count == 2

    # 幂等重跑不炸
    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        assert conn.execute(sa_text("SELECT COUNT(*) FROM channel_bindings")).scalar_one() == 2
        applied = conn.execute(
            sa_text("SELECT id FROM app_data_migrations WHERE id = :id"),
            {"id": database._CHANNEL_BINDINGS_MULTI_MIGRATION_ID},
        ).first()
        assert applied is not None


def test_channel_account_key_migration_uses_corp_and_backfills_sessions(
    monkeypatch, tmp_path
) -> None:
    """验证账号键按企业加机器人生成,并回填已关联会话的账号键。"""
    db_path = tmp_path / "account-key.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            ChannelBinding(
                id="chan_a",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="wecom",
                config_json={"corp_id": "corpA", "bot_id": "shared_bot"},
            )
        )
        db.add(
            ChannelBinding(
                id="chan_b",
                tenant_id="tenant_b",
                agent_id="agent_b",
                channel="wecom",
                config_json={"corp_id": "corpB", "bot_id": "shared_bot"},
            )
        )
        db.add(
            ChatSession(
                id="session_a",
                tenant_id="tenant_a",
                agent_id="agent_a",
                channel="wecom",
                channel_binding_id="chan_a",
                channel_account_key="wecom:bot:shared_bot",
            )
        )
        db.commit()
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    database._migrate_sqlite_skill_schema()

    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_a").external_account_key == (
            "wecom:corp:5:corpA:bot:10:shared_bot"
        )
        assert db.get(ChannelBinding, "chan_b").external_account_key == (
            "wecom:corp:5:corpB:bot:10:shared_bot"
        )
        assert db.get(ChatSession, "session_a").channel_account_key == (
            "wecom:corp:5:corpA:bot:10:shared_bot"
        )


def test_channel_account_key_migration_rolls_back_duplicate_corp_bot(
    monkeypatch, tmp_path
) -> None:
    """验证同一企业同一机器人被多绑定占用时迁移报错回滚,账号键保持为空。"""
    db_path = tmp_path / "account-key-duplicate.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for binding_id, tenant_id in (("chan_a", "tenant_a"), ("chan_b", "tenant_b")):
            db.add(
                ChannelBinding(
                    id=binding_id,
                    tenant_id=tenant_id,
                    agent_id=f"agent_{binding_id}",
                    channel="wecom",
                    config_json={"corp_id": "corpA", "bot_id": "shared_bot"},
                )
            )
        db.commit()
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    with pytest.raises(RuntimeError, match="同一外部 Bot"):
        database._migrate_sqlite_skill_schema()

    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_a").external_account_key is None
        assert db.get(ChannelBinding, "chan_b").external_account_key is None


def test_channel_account_key_failure_rolls_back_real_legacy_schema(
    monkeypatch, tmp_path
) -> None:
    """验证账号键迁移在真实老库上失败时完整回滚(无新列、无迁移标记),修正后可继续迁移。"""
    db_path = tmp_path / "legacy-account-key-failure.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                """
                CREATE TABLE channel_bindings (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    status VARCHAR,
                    credentials_enc VARCHAR,
                    config_json JSON,
                    connected BOOLEAN,
                    created_by_user_id VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            sa_text(
                """
                CREATE TABLE sessions (
                    id VARCHAR PRIMARY KEY,
                    tenant_id VARCHAR,
                    agent_id VARCHAR,
                    channel VARCHAR,
                    external_conv_id VARCHAR,
                    channel_binding_id VARCHAR
                )
                """
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings "
                "(id, tenant_id, agent_id, channel, status, config_json) VALUES "
                "('chan_a', 'tenant_a', 'agent_a', 'wecom', 'active', "
                "'{\"corp_id\":\"corpA\",\"bot_id\":\"same\"}'), "
                "('chan_b', 'tenant_b', 'agent_b', 'wecom', 'active', "
                "'{\"corp_id\":\"corpA\",\"bot_id\":\"same\"}')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO sessions "
                "(id, tenant_id, agent_id, channel, external_conv_id, channel_binding_id) "
                "VALUES ('session_a', 'tenant_a', 'agent_a', 'wecom', "
                "'wecom_corpA_p2p_u1', 'chan_a')"
            )
        )
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    with pytest.raises(RuntimeError, match="同一外部 Bot"):
        database._migrate_sqlite_skill_schema()

    with engine.begin() as conn:
        columns = {
            row[1] for row in conn.execute(sa_text("PRAGMA table_info(channel_bindings)"))
        }
        assert "external_account_key" not in columns
        assert "identity_scope_key" not in columns
        assert "config_revision" not in columns
        assert conn.execute(sa_text("SELECT COUNT(*) FROM channel_bindings")).scalar_one() == 2
        assert conn.execute(sa_text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
        marker_table = conn.execute(
            sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='app_data_migrations'"
            )
        ).first()
        assert marker_table is None
        conn.execute(
            sa_text(
                "UPDATE channel_bindings SET config_json = "
                "'{\"corp_id\":\"corpB\",\"bot_id\":\"same\"}' "
                "WHERE id = 'chan_b'"
            )
        )

    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        keys = dict(
            conn.execute(
                sa_text("SELECT id, external_account_key FROM channel_bindings ORDER BY id")
            ).all()
        )
        assert keys == {
            "chan_a": "wecom:corp:5:corpA:bot:4:same",
            "chan_b": "wecom:corp:5:corpB:bot:4:same",
        }
        session_key = conn.execute(
            sa_text("SELECT channel_account_key FROM sessions WHERE id='session_a'")
        ).scalar_one()
        assert session_key == keys["chan_a"]


# ---------- scope 回填三优先级 ----------


def test_scope_backfill_priority_rules(monkeypatch, tmp_path) -> None:
    """验证 scope 回填按会话引用、租户唯一性、无法推断三级优先级的取值规则。"""
    db_path = tmp_path / "scope-priority.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _build_legacy_scope_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                "CREATE TABLE users ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, username VARCHAR, source VARCHAR)"
            )
        )
        # 两个 active wecom binding:chan_corpA 与 chan_corpB(tenant 下 scope 不唯一)
        conn.execute(
            sa_text(
                "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, status, config_json) "
                "VALUES ('chan_corpB', 'tenant_demo', 'agent_2', 'wecom', 'active', "
                "'{\"bot_id\":\"bot_b\",\"corp_id\":\"corpB\"}')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO users (id, tenant_id, username, source) VALUES "
                "('u_alice', 'tenant_demo', 'alice', 'wecom'), "
                "('u_nobody', 'tenant_demo', 'nobody', 'wecom'), "
                "('u_ghost', 'tenant_b', 'ghost', 'wecom')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO channel_identities (id, tenant_id, channel, external_user_id, staffdeck_user_id) VALUES "
                "('id_alice', 'tenant_demo', 'wecom', 'alice', 'u_alice'), "
                "('id_nobody', 'tenant_demo', 'wecom', 'nobody', 'u_nobody'), "
                "('id_ghost', 'tenant_b', 'wecom', 'ghost', 'u_ghost')"
            )
        )
        conn.execute(
            sa_text(
                "INSERT INTO sessions (id, tenant_id, user_id, agent_id, channel, external_conv_id, channel_binding_id) VALUES "
                "('s_alice', 'tenant_demo', 'u_alice', 'agent_2', 'wecom', 'wecom_p2p_alice', 'chan_corpB')"
            )
        )
    monkeypatch.setattr(database, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        scopes = dict(
            conn.execute(
                sa_text(
                    "SELECT id, external_account_scope FROM channel_identities "
                    "WHERE id IN ('id_alice', 'id_nobody', 'id_ghost')"
                )
            ).all()
        )
        assert scopes == {
            # ①被会话引用:按其会话所在 binding(chan_corpB)的 scope
            "id_alice": "corpB",
            # ②无会话引用且 tenant 下 scope 不唯一(corpA/corpB):不猜归属,归 legacy
            "id_nobody": "legacy",
            # ③取不到(tenant_b 无任何 binding):归 legacy
            "id_ghost": "legacy",
        }

    # 幂等:重跑不炸、数据不变
    database._migrate_sqlite_skill_schema()
    with engine.begin() as conn:
        assert dict(
            conn.execute(
                sa_text(
                    "SELECT id, external_account_scope FROM channel_identities "
                    "WHERE id IN ('id_alice', 'id_nobody', 'id_ghost')"
                )
            ).all()
        ) == scopes
