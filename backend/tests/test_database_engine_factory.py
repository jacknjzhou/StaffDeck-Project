from sqlalchemy import Engine

from app.db.database import build_engine


def test_build_engine_sqlite_keeps_connect_args():
    eng = build_engine("sqlite:///./t.db")
    assert isinstance(eng, Engine)
    assert eng.url.drivername == "sqlite"


def test_build_engine_postgres_uses_pool_options():
    eng = build_engine("postgresql+psycopg://u:p@localhost:5432/staffdeck")
    assert eng.url.drivername == "postgresql+psycopg"
    # pool_pre_ping 打开后每次取连接先 ping，PG 断连自动重建
    assert eng.pool._pre_ping is True
    eng.dispose()


def test_build_engine_memory_sqlite_unchanged():
    eng = build_engine("sqlite://")
    assert eng.url.database in (None, "", ":memory:")
