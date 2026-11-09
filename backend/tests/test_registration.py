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
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert detail["code"] == "REGISTRATION_PENDING"


def test_register_rejected_account_reports_rejected_code(monkeypatch) -> None:
    """同名账号已被拒绝时返回 REGISTRATION_REJECTED,而不是笼统的已存在。"""
    existing = User(id="user_r", tenant_id="tenant_demo", username="zhangsan",
                    password_hash=hash_password("secret"), status="rejected")
    client, _ = _client(monkeypatch, users=[existing])
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REGISTRATION_REJECTED"


def test_register_active_account_reports_account_exists(monkeypatch) -> None:
    """同名 active 账号返回 ACCOUNT_EXISTS,与待审核/已拒绝可区分。"""
    existing = User(id="user_ok", tenant_id="tenant_demo", username="zhangsan",
                    password_hash=hash_password("secret"), status="active")
    client, _ = _client(monkeypatch, users=[existing])
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACCOUNT_EXISTS"


def test_register_cannot_shadow_display_name_alias(monkeypatch) -> None:
    """注册不得占用他人 display_name:否则登录别名会被劫持。"""
    existing = User(id="user_li", tenant_id="tenant_demo", username="li.si",
                    display_name="zhangsan", password_hash=hash_password("secret123"),
                    status="active")
    client, _ = _client(monkeypatch, users=[existing])
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACCOUNT_EXISTS"
    # 别名登录必须仍然可用:注册不能把它变成 401/403。
    login = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert login.status_code == 200


def test_register_rejects_unknown_tenant(monkeypatch) -> None:
    """租户不存在时返回 404,并带上租户缺失的具体原因,不建任何账号。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_missing", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 404
    # 单看 404 无法区分「路由缺失」与「租户不存在」,必须断言具体原因。
    assert "Tenant not found" in response.json()["detail"]


def test_register_rejects_short_password(monkeypatch) -> None:
    """口令少于 8 位返回 422,且响应体不得回显提交的口令。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "s3cr3t",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert "s3cr3t" not in response.text


def test_register_rejects_overlong_password_without_echo(monkeypatch) -> None:
    """口令超过 128 位返回 422,且不得在响应体里回显整段口令。"""
    client, _ = _client(monkeypatch)
    secret = "s3cr3t-" + "x" * 200
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": secret,
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PASSWORD"
    assert secret not in response.text


def test_register_is_rate_limited(monkeypatch) -> None:
    """同一来源连续注册超过阈值后返回 429。"""
    client, _ = _client(monkeypatch, limit=2)
    payload = {"tenant_id": "tenant_demo", "username": "a1", "password": "secret123"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a2"}).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a3"}).status_code == 429


def test_register_quota_is_independent_from_login_quota(monkeypatch) -> None:
    """注册窗口与登录窗口互不占用:打满登录配额后注册仍可成功。"""
    user = User(id="user_a", tenant_id="tenant_demo", username="zhangsan",
                password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, limit=2, users=[user])
    bad_login = {
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "wrong-pass",
    }
    assert client.post("/api/auth/login", json=bad_login).status_code == 401
    assert client.post("/api/auth/login", json=bad_login).status_code == 401
    assert client.post("/api/auth/login", json=bad_login).status_code == 429
    register = {"tenant_id": "tenant_demo", "username": "lisi", "password": "secret123"}
    assert client.post("/api/auth/register", json=register).status_code == 201


def test_login_quota_is_independent_from_register_quota(monkeypatch) -> None:
    """反向同样成立:打满注册配额后登录仍可成功。"""
    user = User(id="user_a", tenant_id="tenant_demo", username="zhangsan",
                password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, limit=2, users=[user])
    register = {"tenant_id": "tenant_demo", "username": "lisi", "password": "secret123"}
    assert client.post("/api/auth/register", json=register).status_code == 201
    second = {**register, "username": "wangwu"}
    third = {**register, "username": "zhaoliu"}
    assert client.post("/api/auth/register", json=second).status_code == 201
    assert client.post("/api/auth/register", json=third).status_code == 429
    login = {"tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123"}
    assert client.post("/api/auth/login", json=login).status_code == 200

