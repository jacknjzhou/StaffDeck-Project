from pathlib import Path

from alembic.config import Config
from sqlmodel import text

from alembic import command
from app.db.database import build_engine
from app.db.migrations import run_migrations


def _user_columns(eng) -> set[str]:
    """读取 PostgreSQL 中 users 表的列名集合。"""
    with eng.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            )
        }


def test_alembic_baseline_upgrades_empty_postgres(pg_url: str):
    """验证空 PostgreSQL 库从零跑通 Alembic 基线迁移并建出核心表。

    先重建 public schema 模拟空库，迁移后应出现 alembic_version、
    agent_profiles、skills 与 sessions 表。
    """
    eng = build_engine(pg_url)
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    run_migrations(pg_url)
    with eng.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
        }
        assert "alembic_version" in tables
        assert "agent_profiles" in tables
        assert "skills" in tables
        assert "sessions" in tables
    eng.dispose()


def test_user_status_revision_round_trips_on_postgres(pg_url: str, monkeypatch):
    """0003 可在 PostgreSQL 上降级再升级,且列已存在时 upgrade 仍可重复执行。"""
    # env.py 的 _database_url() 只认 STAFFDECK_DATABASE_URL,显式指向测试库,
    # 避免环境变量缺失时 Config 悄悄迁移到配置里的开发库。
    monkeypatch.setenv("STAFFDECK_DATABASE_URL", pg_url)

    eng = build_engine(pg_url)
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    run_migrations(pg_url)

    backend_dir = Path(__file__).resolve().parents[1]
    # 真正的 ini 在 backend/alembic.ini;backend/alembic/ 只是脚本目录,
    # 路径写错时 Config() 不会报错,直到 command.* 触发 env.py 的 fileConfig 才炸。
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    command.downgrade(cfg, "0002")
    assert "status" not in _user_columns(eng)

    # 回滚到 0002 后写入一条旧账号,升级时 0003 必须把它的 status 回填为 active,
    # 且不能顺手丢掉 users 的其他列(§7-8 要求「回退不丢其他列」)。
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, display_name, role, source,"
                " password_hash, created_at, updated_at) VALUES"
                " ('u_legacy', 'tenant_legacy', 'legacy', 'Legacy User', 'member', 'web',"
                " 'hash', now(), now())"
            )
        )

    command.upgrade(cfg, "head")
    assert "status" in _user_columns(eng)
    assert {"username", "display_name", "role", "source", "password_hash"} <= _user_columns(eng)
    with eng.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM users WHERE id = 'u_legacy'")
        ).scalar_one() == "active"

    # stamp 回 0002 但保留 status 列(模拟中断或手工改过的库):再次 upgrade 时
    # 0003 的 inspector 守卫必须跳过已存在的列与索引,而不是撞 add_column 报错。
    command.stamp(cfg, "0002")
    command.upgrade(cfg, "head")
    assert "status" in _user_columns(eng)
    eng.dispose()
