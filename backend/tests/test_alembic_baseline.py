from sqlmodel import text

from app.db.database import build_engine
from app.db.migrations import run_migrations


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
