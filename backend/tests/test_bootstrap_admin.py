from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import ensure_bootstrap_admin
from app.security.auth import verify_password


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_bootstrap_admin_uses_random_password(monkeypatch):
    """空库引导必须生成随机密码，不能再是 admin。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    password = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    assert password and password != "admin"
    assert verify_password(password, admin.password_hash)
    assert admin.role == "admin"


def test_bootstrap_admin_is_idempotent(monkeypatch):
    """第二次启动不得重置已存在管理员的密码。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    first = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    before = admin.password_hash
    assert ensure_bootstrap_admin(session) is None
    session.refresh(admin)
    assert admin.password_hash == before
    assert verify_password(first, before)


def test_bootstrap_admin_rotates_legacy_default(monkeypatch):
    """已有 admin/admin 的旧库在启动时被轮换，避免默认口令继续可用。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    from app.security.auth import hash_password
    session.add(User(id="admin", tenant_id="tenant_demo", username="admin",
                     display_name="Administrator", role="admin",
                     password_hash=hash_password("admin")))
    session.commit()
    password = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    assert password and password != "admin"
    assert not verify_password("admin", admin.password_hash)
    assert verify_password(password, admin.password_hash)
