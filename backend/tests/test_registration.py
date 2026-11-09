"""自助注册与审核:创建 pending 账号、拒绝重名、登录态收口与管理端审核。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.auth as auth_api
from app.config import Settings
from app.db import get_session
from app.db.models import Tenant, User
from app.security.auth import create_access_token, hash_password


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """每个用例前后清空限速窗口,避免跨用例串扰。"""
    auth_api._reset_login_rate_limit_state()
    yield
    auth_api._reset_login_rate_limit_state()


def _client(
    monkeypatch, *, limit: int = 100, users: list[User] | None = None
) -> tuple[TestClient, Engine]:
    """构建只挂载认证路由的测试客户端,并返回 engine 供落库断言。"""
    settings = Settings(
        database_url="sqlite://",
        app_secret="test-secret",
        redis_url="",
        login_rate_limit_per_minute=limit,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        for user in users or []:
            db.add(user)
        db.commit()

    test_app = FastAPI()
    test_app.include_router(auth_api.router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    return TestClient(test_app), engine


def _token(user: User) -> dict[str, str]:
    """生成 Bearer 头,供需要登录态的端点使用。"""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_register_creates_pending_web_account(monkeypatch) -> None:
    """注册成功返回 201,落库账号为 pending、source=web、role=member。"""
    client, engine = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": " zhangsan ",
        "display_name": "张三", "password": "secret123",
    })
    assert response.status_code == 201
    assert response.json() == {"status": "pending", "username": "zhangsan"}
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "zhangsan")).one()
        assert user.status == "pending"
        assert user.source == "web"
        assert user.role == "member"
        assert user.tenant_id == "tenant_demo"
        assert user.password_hash != "secret123"


def test_register_rejects_duplicate_username(monkeypatch) -> None:
    """同名账号已存在时返回 409,并带上可区分的错误码。"""
    existing = User(id="user_a", tenant_id="tenant_demo", username="zhangsan",
                    password_hash=hash_password("secret"), status="pending")
    client, _ = _client(monkeypatch, users=[existing])
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REGISTRATION_PENDING"


def test_register_rejects_unknown_tenant(monkeypatch) -> None:
    """租户不存在时返回 404,不建任何账号。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_missing", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 404


def test_register_rejects_short_password(monkeypatch) -> None:
    """口令少于 8 位返回 422(由 Pydantic 约束兜底)。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "short",
    })
    assert response.status_code == 422


def test_register_is_rate_limited(monkeypatch) -> None:
    """同一来源连续注册超过阈值后返回 429。"""
    client, _ = _client(monkeypatch, limit=2)
    payload = {"tenant_id": "tenant_demo", "username": "a1", "password": "secret123"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a2"}).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a3"}).status_code == 429
