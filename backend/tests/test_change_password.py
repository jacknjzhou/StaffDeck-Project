"""自助改密接口:旧口令确认、口令重用与长度边界的回归测试。"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.auth import ChangePasswordRequest, change_password, router
from app.db import get_session
from app.db.models import User
from app.security.auth import get_current_user, hash_password, verify_password


def _user() -> tuple[Session, User]:
    """创建只含一个账号的内存库,初始口令为 old-pass-123。"""
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
        password_hash=hash_password("old-pass-123"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user


def test_change_password_requires_current_password() -> None:
    """当前口令错误时拒绝修改,避免会话被盗后直接改密。"""
    session, user = _user()
    with pytest.raises(HTTPException) as exc:
        change_password(
            ChangePasswordRequest(current_password="wrong", new_password="new-pass-123"),
            current_user=user,
            db=session,
        )
    assert exc.value.status_code == 403


def test_change_password_updates_hash() -> None:
    """修改成功后 204,新口令生效、旧口令失效。"""
    session, user = _user()
    response = change_password(
        ChangePasswordRequest(current_password="old-pass-123", new_password="new-pass-123"),
        current_user=user,
        db=session,
    )
    session.refresh(user)
    assert response.status_code == 204
    assert verify_password("new-pass-123", user.password_hash)
    assert not verify_password("old-pass-123", user.password_hash)


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


def test_change_password_rejects_short_new_password() -> None:
    """新口令少于 8 位由请求模型拦截,经 FastAPI 返回 422。"""
    session, user = _user()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    response = TestClient(app).post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-123", "new_password": "short"},
    )
    assert response.status_code == 422
