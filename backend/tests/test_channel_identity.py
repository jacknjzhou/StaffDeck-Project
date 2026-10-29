from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels.service_identity import (
    channel_label,
    channel_username,
    external_account_key,
    external_identity_for_message,
    resolve_or_provision_user,
)
from app.db.models import ChannelIdentity, Tenant, User


def _test_engine():
    """创建已完成建表的内存 SQLite 引擎，隔离各用例数据。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_tenant(db: Session) -> None:
    """插入演示租户，满足渠道身份的外键约束。"""
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.commit()


def test_channel_labels_use_canonical_names() -> None:
    """验证渠道标签使用统一的中文名称。"""
    assert channel_label("wechat") == "微信"
    assert channel_label("wecom") == "企业微信"
    assert channel_label("feishu") == "飞书"


def test_external_identity_for_p2p_and_group() -> None:
    """验证单聊与群聊分别生成外部身份键和展示名。"""
    external_id, display = external_identity_for_message(
        "wechat", is_group=False, conv_key="", from_user_id="o9cq800kum_ab12cd34@im.wechat"
    )
    assert external_id == "o9cq800kum_ab12cd34@im.wechat"
    assert display == "微信用户 ab12cd34"

    external_id, display = external_identity_for_message(
        "wechat", is_group=True, conv_key="room_123456", from_user_id="sender"
    )
    assert external_id == "group:room_123456"
    assert display == "微信群聊 3456"


def test_wecom_account_key_is_unambiguous_for_delimiter_like_ids() -> None:
    """验证企业微信账号键对含分隔符的 ID 仍可无歧义区分。"""
    first = external_account_key("wecom", {"corp_id": "x:bot:y", "bot_id": "z"})
    second = external_account_key("wecom", {"corp_id": "x", "bot_id": "y:bot:z"})

    assert first == "wecom:corp:7:x:bot:y:bot:1:z"
    assert second == "wecom:corp:1:x:bot:7:y:bot:z"
    assert first != second


def test_provision_creates_member_user_and_identity() -> None:
    """验证首次触达时创建 member 用户并写入渠道身份映射。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        user = resolve_or_provision_user(db, "tenant_demo", "wechat", "wxid_ab12cd34", "微信用户 ab12cd34")
        db.commit()

        assert user.role == "member"
        assert user.username == channel_username("tenant_demo", "wechat", "wxid_ab12cd34")
        assert user.display_name == "微信用户 ab12cd34"
        assert user.password_hash

        identity = db.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == "wechat",
                ChannelIdentity.external_user_id == "wxid_ab12cd34",
            )
        ).one()
        assert identity.staffdeck_user_id == user.id


def test_second_call_hits_existing_mapping() -> None:
    """验证同一外部用户再次触达时复用已有用户，不新增身份行。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        first = resolve_or_provision_user(db, "tenant_demo", "wechat", "wxid_ab12cd34", "微信用户 ab12cd34")
        db.commit()
        second = resolve_or_provision_user(db, "tenant_demo", "wechat", "wxid_ab12cd34", "另一个名字")
        db.commit()

        assert second.id == first.id
        identities = db.exec(
            select(ChannelIdentity).where(ChannelIdentity.external_user_id == "wxid_ab12cd34")
        ).all()
        assert len(identities) == 1


def test_corrupt_cross_tenant_identity_is_replaced_in_current_tenant() -> None:
    """验证跨租户的脏身份映射会被当前租户的新用户替换。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        db.add(Tenant(id="tenant_b", name="B"))
        foreign_user = User(
            id="user_a",
            tenant_id="tenant_a",
            username="foreign_user",
            password_hash="x",
        )
        db.add(foreign_user)
        db.add(
            ChannelIdentity(
                tenant_id="tenant_b",
                channel="wecom",
                external_account_scope="corpB",
                external_user_id="zhangsan",
                staffdeck_user_id=foreign_user.id,
            )
        )
        db.commit()

        current_user = resolve_or_provision_user(
            db,
            "tenant_b",
            "wecom",
            "zhangsan",
            "张三",
            "corpB",
        )
        db.commit()

        assert current_user.tenant_id == "tenant_b"
        identity = db.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.tenant_id == "tenant_b",
                ChannelIdentity.external_account_scope == "corpB",
                ChannelIdentity.external_user_id == "zhangsan",
            )
        ).one()
        assert identity.staffdeck_user_id == current_user.id
        assert identity.staffdeck_user_id != foreign_user.id


def test_group_account_provision() -> None:
    """验证群聊账号按群 ID 生成用户名与展示名。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        user = resolve_or_provision_user(db, "tenant_demo", "wechat", "group_room_123456", "微信群聊 3456")
        db.commit()

        assert user.username == channel_username("tenant_demo", "wechat", "group_room_123456")
        assert user.display_name == "微信群聊 3456"


def test_username_conflict_falls_back_to_existing_user() -> None:
    """验证用户名冲突时回落到已有用户而不是新建。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        existing = User(
            tenant_id="tenant_demo",
            username=channel_username("tenant_demo", "wechat", "wxid_conflict"),
            display_name="老账号",
            role="member",
            password_hash="x",
        )
        db.add(existing)
        db.commit()

        user = resolve_or_provision_user(db, "tenant_demo", "wechat", "wxid_conflict", "微信用户 conflict")
        db.commit()

        assert user.id == existing.id
        identity = db.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == "wechat",
                ChannelIdentity.external_user_id == "wxid_conflict",
            )
        ).one()
        assert identity.staffdeck_user_id == existing.id


def test_username_sanitizes_and_truncates() -> None:
    """验证用户名会清除空格与斜杠等非法字符并限制在 64 字符内。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        long_external = "wx id with space/" + "x" * 100
        user = resolve_or_provision_user(db, "tenant_demo", "wechat", long_external, None)
        db.commit()

        assert " " not in user.username
        assert "/" not in user.username
        assert len(user.username) <= 64


def test_long_external_id_username_uses_stable_hash_suffix() -> None:
    """验证超长且前缀相同的外部 ID 靠稳定哈希后缀区分，且同输入同输出。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        # 两个前缀相同、尾部不同的长 userid:截断时代会撞名,hash 后缀后不再冲突
        shared = "corp_user_" + "x" * 55
        id_a = shared + "aaaa"
        id_b = shared + "bbbb"
        user_a = resolve_or_provision_user(db, "tenant_demo", "wecom", id_a, None, "corpX")
        user_b = resolve_or_provision_user(db, "tenant_demo", "wecom", id_b, None, "corpX")
        db.commit()

        assert user_a.id != user_b.id
        assert user_a.username != user_b.username
        assert len(user_a.username) <= 64
        assert len(user_b.username) <= 64
        assert ".." in user_a.username
        # 稳定:同输入同输出
        from app.channels.service_identity import channel_username

        assert channel_username("tenant_demo", "wecom", id_a, "corpX") == user_a.username
        # 短 id 同样带 hash 后缀(本轮语义)
        assert ".." in channel_username("tenant_demo", "wecom", "zhangsan", "corpX")


def test_username_always_carries_stable_hash() -> None:
    """验证清洗后同形、跨租户与超长的外部 ID 都靠稳定哈希后缀区分。"""
    engine = _test_engine()
    with Session(engine) as db:
        _seed_tenant(db)
        # 清洗后同形的两个 id,hash 后不再同名,各自建不同 User
        user_a = resolve_or_provision_user(db, "tenant_demo", "wechat", "a/b", None)
        user_b = resolve_or_provision_user(db, "tenant_demo", "wechat", "a_b", None)
        db.commit()
        assert user_a.id != user_b.id
        assert user_a.username != user_b.username
        assert ".." in user_a.username and ".." in user_b.username

        # 不同租户同 id:身份键含 tenant,username 不同
        db.add(Tenant(id="tenant_b", name="B"))
        user_c = resolve_or_provision_user(db, "tenant_b", "wechat", "a/b", None)
        db.commit()
        assert user_c.username != user_a.username

        # 超长 id 仍 ≤64 且带 hash 后缀
        long_user = resolve_or_provision_user(db, "tenant_demo", "wecom", "x" * 100, None, "corpX")
        db.commit()
        assert len(long_user.username) <= 64
        assert ".." in long_user.username
