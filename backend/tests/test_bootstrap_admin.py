from __future__ import annotations

import logging

from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import Tenant, User
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


def test_bootstrap_admin_creates_tenant_for_login(monkeypatch):
    """空库引导必须同时建租户，否则登录接口在 ensure_tenant 处 404。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    password = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()

    assert session.get(Tenant, "tenant_demo") is not None
    assert password and verify_password(password, admin.password_hash)


def test_bootstrap_admin_logs_generated_password_once(monkeypatch, caplog):
    """随机口令只在生成时记录一次，便于运维从启动日志取到。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    with caplog.at_level(logging.WARNING):
        password = ensure_bootstrap_admin(session)

    assert password
    assert sum(1 for record in caplog.records if password in record.getMessage()) == 1


def test_bootstrap_admin_does_not_log_configured_password(monkeypatch, caplog):
    """运维通过 BOOTSTRAP_ADMIN_PASSWORD 指定的口令不得写入日志。"""
    configured = "Operator-Supplied-Passw0rd"
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": configured, "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    with caplog.at_level(logging.WARNING):
        password = ensure_bootstrap_admin(session)

    assert password == configured
    assert configured not in caplog.text
