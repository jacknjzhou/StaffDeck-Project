"""users.status 双路径迁移:SQLite 手写补列与 Alembic 0003 都必须幂等。"""

from sqlalchemy import create_engine, text

from app.db import database

LEGACY_USERS_DDL = """
CREATE TABLE users (
    id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    username VARCHAR NOT NULL,
    display_name VARCHAR,
    role VARCHAR NOT NULL DEFAULT 'member',
    source VARCHAR NOT NULL DEFAULT 'web',
    password_hash VARCHAR NOT NULL,
    created_at DATETIME,
    updated_at DATETIME
)
"""


def _legacy_engine(tmp_path):
    """建一个没有 status 列的旧版 SQLite 库,模拟升级前的开发库。

    迁移函数读模块级 `database.engine`/`database.database_url`,因此必须落盘并用
    monkeypatch 指向它(内存库在每次 connect 时都是新库)。
    """
    db_path = tmp_path / "users.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(LEGACY_USERS_DDL))
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, password_hash) "
                "VALUES ('user_old', 'tenant_demo', 'olduser', 'hash')"
            )
        )
    return engine, f"sqlite:///{db_path}"


def test_sqlite_migration_adds_status_column_and_backfills_active(monkeypatch, tmp_path):
    """旧库补列后,既有账号必须全部是 active,不能掉进待审核。"""
    engine, url = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "database_url", url)
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()

    with engine.connect() as conn:
        columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(users)")).mappings()}
        assert "status" in columns
        status = conn.execute(text("SELECT status FROM users WHERE id = 'user_old'")).scalar()
        assert status == "active"


def test_sqlite_migration_is_idempotent(monkeypatch, tmp_path):
    """重复执行迁移不能报错(启动时每次都会跑一遍)。"""
    engine, url = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "database_url", url)
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    database._migrate_sqlite_skill_schema()

    with engine.connect() as conn:
        indexes = {row["name"] for row in conn.execute(text("PRAGMA index_list(users)")).mappings()}
        assert "ix_users_status" in indexes
