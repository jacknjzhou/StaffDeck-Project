from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.channels as channels_api
from app.channels.crypto import decrypt_channel_secret
from app.db import get_session
from app.db.models import AgentProfile, ChannelBinding, Tenant, User
from app.security.auth import create_access_token


def _engine():
    """创建建好全部表的 SQLite 内存引擎,让渠道接口测试互不干扰。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine) -> TestClient:
    """挂载渠道路由并用测试引擎覆盖 get_session,返回可发请求的客户端。"""
    app = FastAPI()
    app.include_router(channels_api.router)

    def override_session():
        """按请求提供绑定到测试引擎的会话,避免使用生产数据库。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _seed(engine) -> User:
    """写入租户、所有者与员工基础数据,返回已脱离会话的属主用户用于签发令牌。"""
    with Session(engine) as db:
        db.add(Tenant(id="tenant_a", name="A"))
        owner = User(
            id="user_owner",
            tenant_id="tenant_a",
            username="owner",
            password_hash="x",
        )
        db.add(owner)
        db.add(
            AgentProfile(
                id="agent_a",
                tenant_id="tenant_a",
                name="Agent A",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
        return owner


def _auth(user: User) -> dict[str, str]:
    """为该用户签发访问令牌并组装成 Authorization 请求头。"""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_feishu_binding_credentials_activate_without_exposing_secret(monkeypatch) -> None:
    """验证提交飞书凭据后绑定被激活且仅落密文,响应中不回显 app_secret。"""
    engine = _engine()
    owner = _seed(engine)
    client = _client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    monkeypatch.setattr(
        channels_api,
        "validate_feishu_credentials",
        lambda app_id, secret: {"bot_open_id": "ou_bot", "bot_name": "StaffDeck Bot"},
    )

    created = client.post(
        "/api/enterprise/channels",
        json={"tenant_id": "tenant_a", "agent_id": "agent_a", "channel": "feishu"},
        headers=_auth(owner),
    )
    assert created.status_code == 200
    binding_id = created.json()["id"]
    response = client.post(
        f"/api/enterprise/channels/{binding_id}/feishu/credentials",
        json={"tenant_id": "tenant_a", "app_id": "cli_app", "app_secret": "secret-value"},
        headers=_auth(owner),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "active"
    assert payload["app_id"] == "cli_app"
    assert payload["bot_open_id"] == "ou_bot"
    assert payload["bot_name"] == "StaffDeck Bot"
    assert payload["provider_tenant_key"] is None
    assert "secret-value" not in response.text
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert decrypt_channel_secret(binding.credentials_enc) == "secret-value"
        assert binding.external_account_key == "feishu:app:7:cli_app"
        assert binding.config_revision == 1


def test_feishu_app_id_is_immutable_after_activation(monkeypatch) -> None:
    """验证绑定激活后更换 app_id 会被拒绝,且不会触发凭据校验调用。"""
    engine = _engine()
    owner = _seed(engine)
    with Session(engine) as db:
        binding = ChannelBinding(
            id="chan_feishu",
            tenant_id="tenant_a",
            agent_id="agent_a",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_old"},
            created_by_user_id=owner.id,
        )
        db.add(binding)
        db.commit()
    client = _client(engine)
    called = False

    def validate(_app_id, _secret):
        """记录校验函数是否被调用,用于断言早退拒绝发生在校验之前。"""
        nonlocal called
        called = True
        return {"bot_open_id": "ou_bot", "bot_name": "Bot"}

    monkeypatch.setattr(channels_api, "validate_feishu_credentials", validate)
    response = client.post(
        "/api/enterprise/channels/chan_feishu/feishu/credentials",
        json={"tenant_id": "tenant_a", "app_id": "cli_new", "app_secret": "secret"},
        headers=_auth(owner),
    )
    assert response.status_code == 400
    assert called is False


def test_channel_meta_exposes_feishu_secret_field() -> None:
    """验证渠道元数据把 app_secret 标记为敏感字段,app_id 保持明文展示。"""
    engine = _engine()
    owner = _seed(engine)
    response = _client(engine).get(
        "/api/enterprise/channels/meta?tenant_id=tenant_a",
        headers=_auth(owner),
    )
    assert response.status_code == 200
    feishu = next(row for row in response.json() if row["channel"] == "feishu")
    fields = {field["key"]: field for field in feishu["credential_fields"]}
    assert fields["app_id"]["secret"] is False
    assert fields["app_secret"]["secret"] is True
