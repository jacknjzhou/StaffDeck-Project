"""OIDC SSO 登录:授权码流程 → 按邮箱找/建 User → 映射到现有 JWT 会话。

沿用仓库既有测试模式(test_user_avatar):新 FastAPI 实例 + get_session 覆盖,
避开 app.main 的 startup 钩子(文件锁/seed)与真实数据库。
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.auth as auth_api
from app.config import Settings
from app.db import get_session
from app.db.models import User

DISCOVERY_FULL = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
}

_TIMESTAMP_FIELDS = ("created_at", "updated_at")


@pytest.fixture(autouse=True)
def _restore_user_default_factories():
    """保存并在测试结束后恢复 User 时间戳 default_factory。

    pydantic v2 的 model_fields 是类属性 dict,_test_engine() 对它的改写若不
    恢复会泄漏到同进程后续所有 import User 的测试(fix round 1 评审发现)。
    fixture yield 即 try/finally 语义:测试体中途失败也会恢复。
    """
    saved = {name: User.model_fields[name].default_factory for name in _TIMESTAMP_FIELDS}
    yield
    for name, factory in saved.items():
        User.model_fields[name].default_factory = factory


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # 存量兼容:SQLModel 0.0.48 的 UTCDateTime 要求 tz-aware,而 models.utc_now
    # 返回 naive(全仓同类失败的一部分,本任务不修)。测试局部把 User 时间戳
    # default_factory 换成 aware 实现,使本用例可真实走通 INSERT。
    # 恢复由上方 autouse fixture 负责(参见 fix round 1)。
    for field_name in _TIMESTAMP_FIELDS:
        User.model_fields[field_name].default_factory = lambda: datetime.now(UTC)
    return engine


def _client_with_oidc(monkeypatch, *, enabled: bool = True):
    settings = Settings(
        oidc_enabled=enabled,
        oidc_issuer="https://idp.example.com",
        oidc_client_id="cid",
        oidc_client_secret="sec",
        oidc_redirect_uri="http://localhost:5173/api/auth/oidc/callback",
        frontend_base_url="http://localhost:5173",
        database_url="sqlite://",
        app_secret="test-secret",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(auth_api.router)
    engine = _test_engine()

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app), engine


def test_login_redirects_to_authorization_endpoint(monkeypatch):
    client, _ = _client_with_oidc(monkeypatch)
    with patch("app.security.oidc._discover", return_value=DISCOVERY_FULL):
        resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/authorize")
    assert "client_id=cid" in resp.headers["location"]
    assert "state=" in resp.headers["location"]


def test_login_returns_404_when_disabled(monkeypatch):
    client, _ = _client_with_oidc(monkeypatch, enabled=False)
    resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 404


def test_callback_creates_user_and_returns_token(monkeypatch):
    client, engine = _client_with_oidc(monkeypatch)
    # 经 new_state() 生成合法 state,真实走 consume_state 校验(一次性)
    from app.security import oidc as oidc_mod

    state = oidc_mod.new_state()
    with patch(
        "app.security.oidc._discover", return_value=DISCOVERY_FULL
    ), patch(
        "app.security.oidc._exchange_code",
        return_value={"access_token": "at"},
    ), patch(
        "app.security.oidc._fetch_userinfo",
        return_value={"sub": "u-9", "email": "dev@example.com", "name": "Dev"},
    ):
        resp = client.get(
            f"/api/auth/oidc/callback?code=c&state={state}", follow_redirects=False
        )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://localhost:5173/login")
    assert "token=" in resp.headers["location"]
    # 用户按邮箱落库:OIDC 来源、tenant 沿用默认租户
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "dev@example.com")).first()
        assert user is not None
        assert user.source == "oidc"
        assert user.tenant_id == "tenant_demo"
    # state 一次性:重放同一 state 拒绝
    resp2 = client.get(
        f"/api/auth/oidc/callback?code=c&state={state}", follow_redirects=False
    )
    assert resp2.status_code == 400


def test_callback_rejects_unknown_state(monkeypatch):
    client, _ = _client_with_oidc(monkeypatch)
    resp = client.get(
        "/api/auth/oidc/callback?code=c&state=forged", follow_redirects=False
    )
    assert resp.status_code == 400
