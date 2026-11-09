"""自助改密接口:旧口令确认、口令重用、长度/空白边界、OIDC 与限速回归测试。"""

from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import auth as auth_module
from app.api.auth import (
    ChangePasswordRequest,
    _reset_login_rate_limit_state,
    change_password,
    router,
)
from app.db import get_session
from app.db.models import User, utc_now
from app.security.auth import get_current_user, hash_password, verify_password


@pytest.fixture(autouse=True)
def _reset_rate_limit_windows():
    """每个用例前后清空进程内限速窗口,避免跨用例串扰。"""
    _reset_login_rate_limit_state()
    yield
    _reset_login_rate_limit_state()


def _user(*, oidc: bool = False) -> tuple[Session, User]:
    """创建只含一个账号的内存库,本地账号初始口令为 old-pass-123。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    user = User(
        id="u1",
        tenant_id="t1",
        username="u",
        display_name="U",
        password_hash="oidc" if oidc else hash_password("old-pass-123"),
        source="oidc" if oidc else "local",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user


def _client(session: Session, user: User | None = None) -> TestClient:
    """构造只挂载认证路由的测试应用,可选伪造登录态。"""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_change_password_requires_current_password() -> None:
    """当前口令错误时拒绝修改,且不得改动已存哈希。"""
    session, user = _user()
    before = user.password_hash
    with pytest.raises(HTTPException) as exc:
        change_password(
            ChangePasswordRequest(current_password="wrong", new_password="new-pass-123"),
            current_user=user,
            db=session,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "current password is incorrect"
    session.refresh(user)
    assert user.password_hash == before


def test_change_password_updates_hash_and_bumps_updated_at() -> None:
    """修改成功后 204,新口令生效、旧口令失效,updated_at 前进。"""
    session, user = _user()
    old_updated_at = utc_now() - timedelta(days=1)
    user.updated_at = old_updated_at
    session.add(user)
    session.commit()
    session.refresh(user)
    response = change_password(
        ChangePasswordRequest(current_password="old-pass-123", new_password="new-pass-123"),
        current_user=user,
        db=session,
    )
    session.refresh(user)
    assert response.status_code == 204
    assert verify_password("new-pass-123", user.password_hash)
    assert not verify_password("old-pass-123", user.password_hash)
    assert user.updated_at > old_updated_at


def test_change_password_rejects_reusing_current_password() -> None:
    """新口令与当前口令相同时返回 400,提示用户换一个。"""
    session, user = _user()
    with pytest.raises(HTTPException) as exc:
        change_password(
            ChangePasswordRequest(
                current_password="old-pass-123",
                new_password="old-pass-123",
            ),
            current_user=user,
            db=session,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "new password must differ from the current password"


def test_change_password_rejects_short_new_password() -> None:
    """新口令少于 8 位由请求模型拦截,经 FastAPI 返回 422。"""
    session, user = _user()
    response = _client(session, user).post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "short"},
    )
    assert response.status_code == 422


def test_change_password_rejects_overlong_new_password() -> None:
    """新口令超过 128 位由请求模型拦截,返回 422。"""
    session, user = _user()
    response = _client(session, user).post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "a" * 129},
    )
    assert response.status_code == 422


def test_change_password_rejects_whitespace_only_new_password() -> None:
    """纯空白新口令 strip 后不足 8 位,返回 422。"""
    session, user = _user()
    response = _client(session, user).post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "        "},
    )
    assert response.status_code == 422


def test_change_password_strips_new_password_before_hashing() -> None:
    """新口令首尾空白被去除,落库的是 strip 后的口令。"""
    session, user = _user()
    response = change_password(
        ChangePasswordRequest(
            current_password="old-pass-123",
            new_password="  new-pass-123  ",
        ),
        current_user=user,
        db=session,
    )
    session.refresh(user)
    assert response.status_code == 204
    assert verify_password("new-pass-123", user.password_hash)
    assert not verify_password("  new-pass-123  ", user.password_hash)


def test_change_password_rejects_unauthenticated_request() -> None:
    """没有 Bearer 令牌时由依赖层返回 401,不会进入改密逻辑。"""
    session, _ = _user()
    response = _client(session).post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "new-pass-123"},
    )
    assert response.status_code == 401


def test_change_password_tells_oidc_account_it_has_no_local_password() -> None:
    """OIDC 账号没有本地口令,返回区别于“当前口令错误”的明确提示。"""
    session, user = _user(oidc=True)
    with pytest.raises(HTTPException) as exc:
        change_password(
            ChangePasswordRequest(current_password="anything", new_password="new-pass-123"),
            current_user=user,
            db=session,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "account has no local password"


def test_change_password_is_rate_limited_per_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一账号连续尝试会触发 429,响应带 Retry-After,且不占用登录窗口。"""

    class _Settings:
        login_rate_limit_per_minute = 1
        redis_url = ""

    monkeypatch.setattr(auth_module, "_settings", lambda: _Settings())
    session, user = _user()
    payload = ChangePasswordRequest(
        current_password="wrong", new_password="new-pass-123"
    )
    with pytest.raises(HTTPException) as first:
        change_password(payload, current_user=user, db=session)
    assert first.value.status_code == 403
    with pytest.raises(HTTPException) as second:
        change_password(payload, current_user=user, db=session)
    assert second.value.status_code == 429
    assert second.value.headers == {"Retry-After": "60"}
    # 改密窗口独立:登录窗口没有被消耗,首次登录尝试不会被误伤。
    assert auth_module._hit_memory_window("login:test:u") == 1
