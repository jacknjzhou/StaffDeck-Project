from sqlalchemy import Engine

from app.db.database import build_engine


def test_build_engine_sqlite_keeps_connect_args():
    """验证 SQLite URL 构造出标准 Engine 实例，驱动名为 sqlite。"""
    eng = build_engine("sqlite:///./t.db")
    assert isinstance(eng, Engine)
    assert eng.url.drivername == "sqlite"


def test_build_engine_postgres_uses_pool_options():
    """验证 PostgreSQL 引擎启用 pool_pre_ping，断连后取连接可自动重建。"""
    eng = build_engine("postgresql+psycopg://u:p@localhost:5432/staffdeck")
    assert eng.url.drivername == "postgresql+psycopg"
    # pool_pre_ping 打开后每次取连接先 ping，PG 断连自动重建
    assert eng.pool._pre_ping is True
    eng.dispose()


def test_build_engine_memory_sqlite_unchanged():
    """验证内存 SQLite URL 构造的引擎指向内存库、不落盘。"""
    eng = build_engine("sqlite://")
    assert eng.url.database in (None, "", ":memory:")
