from sqlmodel import Session, SQLModel, text

import app.db.models  # noqa: F401  确保全部表注册进 metadata
from app.db.database import build_engine


def test_create_all_and_roundtrip_on_postgres(pg_url: str):
    """验证 PostgreSQL 上可重建全部表并完成一次查询往返。"""
    eng = build_engine(pg_url)
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as db:
        db.execute(text("SELECT 1"))
    eng.dispose()
