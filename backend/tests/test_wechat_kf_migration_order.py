from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.db import database
from app.db.database import _migrate_wechat_kf_accounts


def _legacy_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE channel_bindings (id VARCHAR PRIMARY KEY, tenant_id VARCHAR, "
            "agent_id VARCHAR, channel VARCHAR, config_json TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, config_json) "
            "VALUES ('cb1', 't1', 'a1', 'wechat_kf', '{\"open_kfid\": \"kf1\"}')"
        ))
    return engine


def test_migration_skips_when_team_id_missing():
    """旧库缺少 team_id 列时迁移必须跳过而不是抛 no such column。"""
    engine = _legacy_engine()
    with engine.begin() as conn:
        _migrate_wechat_kf_accounts(conn, {"channel_bindings"})  # 不抛异常即通过
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM wechat_kf_accounts")).scalar()
    assert count == 0


def test_migration_backfills_when_team_id_present():
    """team_id 存在时正常回填 wechat_kf_accounts。"""
    engine = _legacy_engine()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE channel_bindings ADD COLUMN team_id VARCHAR"))
    with engine.begin() as conn:
        _migrate_wechat_kf_accounts(conn, {"channel_bindings"})
        row = conn.execute(text(
            "SELECT binding_id, open_kfid FROM wechat_kf_accounts"
        )).mappings().one()
    assert row["binding_id"] == "cb1" and row["open_kfid"] == "kf1"


def _production_settings() -> SimpleNamespace:
    return SimpleNamespace(is_production=True)


def _development_settings() -> SimpleNamespace:
    return SimpleNamespace(is_production=False)


def _versioned_engine(version: str) -> object:
    """建一张带 alembic_version 的 SQLite 库,并用方言名冒充 PostgreSQL。"""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version})
    return engine


def test_assert_schema_current_is_noop_for_sqlite(monkeypatch):
    """SQLite 开发/测试库没有 alembic_version,即使生产开关打开也不能拒绝启动。"""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "get_settings", _production_settings)
    database.assert_schema_current(engine)  # 不抛异常即通过


def test_assert_schema_current_is_noop_outside_production(monkeypatch):
    """非生产环境不做版本校验,避免本地/测试库被 alembic_version 卡住。"""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(database, "get_settings", _development_settings)
    database.assert_schema_current(engine)  # 不抛异常即通过


def test_assert_schema_current_rejects_unmanaged_production_db(monkeypatch):
    """生产库缺 alembic_version 时必须快速失败并提示先跑迁移。"""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(database, "get_settings", _production_settings)
    with pytest.raises(RuntimeError, match="alembic_version"):
        database.assert_schema_current(engine)


def test_assert_schema_current_rejects_outdated_revision(monkeypatch):
    """生产库版本落后于 alembic head 时必须报 out of date。"""
    engine = _versioned_engine("0001")
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(database, "get_settings", _production_settings)
    with pytest.raises(RuntimeError, match="out of date"):
        database.assert_schema_current(engine)


def test_assert_schema_current_accepts_head_revision(monkeypatch):
    """生产库已升级到全部 head 版本时校验通过。"""
    heads = database._alembic_head_revisions()
    assert heads, "alembic 脚本目录至少要有一个 head 版本"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR PRIMARY KEY)"))
        for head in sorted(heads):
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head}
            )
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(database, "get_settings", _production_settings)
    database.assert_schema_current(engine)  # 不抛异常即通过
