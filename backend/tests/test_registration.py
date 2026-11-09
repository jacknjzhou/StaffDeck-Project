"""自助注册与审核:创建 pending 账号、拒绝重名、登录态收口与管理端审核。"""

from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.auth as auth_api
from app.config import Settings
from app.db import get_session
from app.db.models import Tenant, User, utc_now
from app.security.auth import create_access_token, get_current_user, hash_password


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
    # expire_on_commit=False:种子用户提交后仍可读取属性,供 _token 直接签发 Bearer 头
    with Session(engine, expire_on_commit=False) as db:
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



def test_login_pending_account_returns_403_code(monkeypatch) -> None:
    """待审核账号用正确口令登录返回 403 与明确错误码,便于前端区分文案。"""
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[pending])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_PENDING_APPROVAL"


def test_login_wrong_password_for_pending_account_returns_401(monkeypatch) -> None:
    """口令错误一律 401:不知道口令的人不能靠状态码枚举账号状态。"""
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[pending])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "wrong-pass",
    })
    assert response.status_code == 401


def test_login_rejected_account_returns_403_code(monkeypatch) -> None:
    """已拒绝账号登录返回 403 与 REGISTRATION_REJECTED 错误码。"""
    rejected = User(id="user_r", tenant_id="tenant_demo", username="lisi",
                    password_hash=hash_password("secret123"), status="rejected")
    client, _ = _client(monkeypatch, users=[rejected])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "lisi", "password": "secret123",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_REGISTRATION_REJECTED"


def test_login_active_account_succeeds(monkeypatch) -> None:
    """active 账号不受状态判断影响,仍能正常拿到 token。"""
    active = User(id="user_a", tenant_id="tenant_demo", username="wangwu",
                  password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[active])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "wangwu", "password": "secret123",
    })
    assert response.status_code == 200
    assert response.json()["user"]["status"] == "active"


def test_get_current_user_rejects_inactive_account() -> None:
    """已签发 token 的账号被停用后,下一次鉴权必须 401(前端据此自动登出)。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_x", tenant_id="tenant_demo", username="zhaoliu",
                    password_hash=hash_password("secret"), status="rejected"))
        db.commit()
        user = db.get(User, "user_x")
        token = create_access_token(user)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            get_current_user(credentials=credentials, db=db)
        assert exc.value.status_code == 401


def test_admin_can_approve_pending_account(monkeypatch) -> None:
    """管理员把 pending 改为 active 后返回 200,且账号可登录。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[admin, pending])
    response = client.put(
        "/api/auth/users/user_p/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(admin),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    login = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert login.status_code == 200


def test_non_admin_cannot_change_status(monkeypatch) -> None:
    """非管理员调用审核端点返回 403。"""
    member = User(id="user_m", tenant_id="tenant_demo", username="zhangsan",
                  password_hash=hash_password("secret123"), status="active")
    target = User(id="user_t", tenant_id="tenant_demo", username="lisi",
                  password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[member, target])
    response = client.put(
        "/api/auth/users/user_t/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(member),
    )
    assert response.status_code == 403


def test_admin_cannot_reject_self(monkeypatch) -> None:
    """管理员不能把自己改为 rejected,避免唯一管理员自锁。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    client, _ = _client(monkeypatch, users=[admin])
    response = client.put(
        "/api/auth/users/user_admin/status",
        json={"tenant_id": "tenant_demo", "status": "rejected"},
        headers=_token(admin),
    )
    assert response.status_code == 400


def test_status_endpoint_rejects_pending_value(monkeypatch) -> None:
    """审核端点不接受把账号设回 pending(422,由 Literal 约束兜底)。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    target = User(id="user_t", tenant_id="tenant_demo", username="lisi",
                  password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[admin, target])
    response = client.put(
        "/api/auth/users/user_t/status",
        json={"tenant_id": "tenant_demo", "status": "pending"},
        headers=_token(admin),
    )
    assert response.status_code == 422


def test_list_users_hides_pending_accounts_from_members(monkeypatch) -> None:
    """非管理员的账号列表只含 active 的 web 账号,待审核账号不进处理人选择器。"""
    member = User(id="user_m", tenant_id="tenant_demo", username="zhangsan",
                  password_hash=hash_password("secret123"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="lisi",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[member, pending])
    response = client.get("/api/auth/users?tenant_id=tenant_demo", headers=_token(member))
    assert response.status_code == 200
    assert [row["username"] for row in response.json()] == ["zhangsan"]


def test_list_users_shows_pending_accounts_to_admin(monkeypatch) -> None:
    """管理员必须能看到待审核账号,否则审核页没有数据可审。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="lisi",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[admin, pending])
    response = client.get("/api/auth/users?tenant_id=tenant_demo", headers=_token(admin))
    assert response.status_code == 200
    assert {row["username"]: row["status"] for row in response.json()} == {
        "admin": "active",
        "lisi": "pending",
    }


def test_admin_can_reactivate_rejected_account(monkeypatch) -> None:
    """已拒绝账号可以重新审核通过:rejected → active。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    rejected = User(id="user_r", tenant_id="tenant_demo", username="lisi",
                    password_hash=hash_password("secret123"), status="rejected")
    client, engine = _client(monkeypatch, users=[admin, rejected])
    response = client.put(
        "/api/auth/users/user_r/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(admin),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    with Session(engine) as db:
        assert db.get(User, "user_r").status == "active"


def test_admin_can_deactivate_another_account(monkeypatch) -> None:
    """管理员可以把另一个 active 账号停用:active → rejected。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    member = User(id="user_m", tenant_id="tenant_demo", username="lisi",
                  password_hash=hash_password("secret123"), status="active")
    client, engine = _client(monkeypatch, users=[admin, member])
    response = client.put(
        "/api/auth/users/user_m/status",
        json={"tenant_id": "tenant_demo", "status": "rejected"},
        headers=_token(admin),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    with Session(engine) as db:
        assert db.get(User, "user_m").status == "rejected"


def test_admin_cannot_manage_other_tenant_account(monkeypatch) -> None:
    """跨租户 user_id 返回 404,不能靠审核端点探测别的租户。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    foreign = User(id="user_other", tenant_id="tenant_other", username="lisi",
                   password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[admin, foreign])
    response = client.put(
        "/api/auth/users/user_other/status",
        json={"tenant_id": "tenant_demo", "status": "rejected"},
        headers=_token(admin),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"


def test_admin_gets_404_for_missing_account(monkeypatch) -> None:
    """不存在的 user_id 返回 404,且 detail 与路由缺失可区分。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    client, _ = _client(monkeypatch, users=[admin])
    response = client.put(
        "/api/auth/users/user_missing/status",
        json={"tenant_id": "tenant_demo", "status": "rejected"},
        headers=_token(admin),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"


def test_non_admin_gets_403_for_missing_account(monkeypatch) -> None:
    """非管理员打不存在的 user_id 必须是 403:管理员校验先于查库,不泄露存在性。"""
    member = User(id="user_m", tenant_id="tenant_demo", username="zhangsan",
                  password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[member])
    response = client.put(
        "/api/auth/users/user_missing/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(member),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Only administrator can manage accounts"


def test_status_update_refreshes_updated_at(monkeypatch) -> None:
    """状态变更必须刷新 updated_at,否则前端展示的处理时间永远不动。"""
    stale = utc_now() - timedelta(days=3)
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="lisi",
                   password_hash=hash_password("secret123"), status="pending",
                   updated_at=stale)
    client, engine = _client(monkeypatch, users=[admin, pending])
    response = client.put(
        "/api/auth/users/user_p/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(admin),
    )
    assert response.status_code == 200
    with Session(engine) as db:
        assert db.get(User, "user_p").updated_at > stale


def test_me_route_rejects_rejected_user_token(monkeypatch) -> None:
    """路由级鉴权:被停用账号带旧 token 访问 /api/auth/me 返回 401 与明确 detail。"""
    rejected = User(id="user_r", tenant_id="tenant_demo", username="lisi",
                    password_hash=hash_password("secret123"), status="rejected")
    client, _ = _client(monkeypatch, users=[rejected])
    response = client.get("/api/auth/me", headers=_token(rejected))
    assert response.status_code == 401
    assert response.json()["detail"] == "Account is not active"
