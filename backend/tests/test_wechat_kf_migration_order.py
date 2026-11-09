import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text

from app.db import database
from app.db.database import _migrate_wechat_kf_accounts

_WECHAT_KF_INDEXES = ("tenant_id", "binding_id", "open_kfid", "agent_id", "team_id", "status")


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


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).scalars()
        return {str(name) for name in rows}


def _index_names(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA index_list({table})")).mappings().all()
        return {str(row["name"]) for row in rows}


def _column_names(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return {str(row["name"]) for row in rows}


def test_migration_skips_when_team_id_missing():
    """旧库缺少 team_id 列时迁移必须跳过而不是抛 no such column。"""
    engine = _legacy_engine()
    with engine.begin() as conn:
        _migrate_wechat_kf_accounts(conn, {"channel_bindings"})  # 不抛异常即通过
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM wechat_kf_accounts")).scalar()
    assert count == 0
    # 跳过回填不等于跳过 DDL:表和 6 个索引都必须照常建出。
    assert "wechat_kf_accounts" in _table_names(engine)
    assert {
        f"ix_wechat_kf_accounts_{column}" for column in _WECHAT_KF_INDEXES
    } <= _index_names(engine, "wechat_kf_accounts")


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
    return SimpleNamespace(is_production=True, process_role="api")


def _development_settings() -> SimpleNamespace:
    return SimpleNamespace(is_production=False, process_role="api")


def _versioned_engine(version: str) -> Engine:
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


@pytest.mark.parametrize("role", ["worker", "connector"])
def test_assert_schema_current_is_noop_for_non_api_roles(monkeypatch, role):
    """worker/connector 不跑迁移又依赖 api 启动顺序,校验只会让它们发版时崩溃重启。"""
    engine = create_engine("sqlite://")  # 故意不带 alembic_version
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: SimpleNamespace(is_production=True, process_role=role),
    )
    database.assert_schema_current(engine)  # 不抛异常即通过


def test_assert_schema_current_enforced_for_monolith_all_role(monkeypatch):
    """单体部署(role=all)自己负责迁移,必须和生产 api 一样校验库版本。"""
    engine = create_engine("sqlite://")  # 故意不带 alembic_version
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: SimpleNamespace(is_production=True, process_role="all"),
    )
    with pytest.raises(RuntimeError, match="alembic_version"):
        database.assert_schema_current(engine)


def test_init_db_checks_schema_before_creating_tables(monkeypatch):
    """未纳管的生产库必须在 create_all 之前失败,不能先建出一批表再报错。"""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    monkeypatch.setattr(database, "get_settings", _production_settings)
    with pytest.raises(RuntimeError, match="alembic_version"):
        database.init_db()
    assert _table_names(engine) == set(), "校验必须早于任何 DDL"


def _revision_0002():
    """按文件路径加载 0002 修订模块,便于直接调用 upgrade/downgrade。"""
    path = (
        Path(database.__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0002_channel_binding_team_id.py"
    )
    spec = importlib.util.spec_from_file_location("revision_0002", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline_engine(with_index: bool = True) -> Engine:
    """模拟 0001 baseline:channel_bindings 已含 team_id 列(及索引)。"""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE channel_bindings (id VARCHAR PRIMARY KEY, team_id VARCHAR)"))
        if with_index:
            conn.execute(
                text("CREATE INDEX ix_channel_bindings_team_id ON channel_bindings (team_id)")
            )
    return engine


def _run_revision(engine: Engine, action: str, monkeypatch) -> None:
    revision = _revision_0002()
    with engine.begin() as conn:
        monkeypatch.setattr(revision, "op", Operations(MigrationContext.configure(conn)))
        getattr(revision, action)()


def test_revision_0002_upgrade_is_noop_on_baseline_database(monkeypatch):
    """0001 已带 team_id 与索引,全新库升级到 0002 不能重复建列/索引。"""
    engine = _baseline_engine()
    _run_revision(engine, "upgrade", monkeypatch)
    assert "team_id" in _column_names(engine, "channel_bindings")
    assert "ix_channel_bindings_team_id" in _index_names(engine, "channel_bindings")


def test_revision_0002_upgrade_creates_missing_index(monkeypatch):
    """列在但索引缺失(手工改过库)时,upgrade 必须把索引补出来。"""
    engine = _baseline_engine(with_index=False)
    _run_revision(engine, "upgrade", monkeypatch)
    assert "ix_channel_bindings_team_id" in _index_names(engine, "channel_bindings")


def test_revision_0002_downgrade_keeps_baseline_column(monkeypatch):
    """0001 baseline 拥有 team_id,回退到 0001 必须保留该列,否则 0001 库结构不完整。"""
    engine = _baseline_engine()
    _run_revision(engine, "downgrade", monkeypatch)
    assert "team_id" in _column_names(engine, "channel_bindings")
    assert "ix_channel_bindings_team_id" in _index_names(engine, "channel_bindings")
