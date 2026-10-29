"""The audit queue must speak the engine's dialect, not SQLite's.

The container stack runs PostgreSQL. The audit worker used to run
``PRAGMA busy_timeout = 2000`` unconditionally, which is a syntax error on
PG, so every audit row was dropped with a warning while the requests
themselves returned 200 — exactly the silent-trail-loss the audit design
exists to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app.db.models import APIAuditLog
from app.public_api.app import (
    _audit_queue,
    _ensure_audit_worker,
    flush_audit_queue,
)


def _make_engine() -> Any:
    """构建共享单连接的 SQLite 引擎，保证审计 worker 与断言看到同一份库。"""
    # StaticPool keeps one shared connection: the audit worker opens its own
    # session, and a plain in-memory sqlite would hand it an empty database.
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=[APIAuditLog.__table__])
    return engine


def _recorded_statements(engine: Any) -> list[str]:
    """注册游标执行前监听器并返回语句列表，用于断言实际下发的 SQL。"""
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, params, context, executemany: statements.append(statement),
    )
    return statements


def _enqueue(engine: Any, request_id: str) -> None:
    """向审计队列投递一条待写入记录，并附带记录所属的引擎。"""
    item: dict[str, object] = {
        "request_id": request_id,
        "method": "GET",
        "path": "/api/v1/digital-employees",
        "status_code": 200,
        "duration_ms": 1.0,
        "_engine": engine,
    }
    _audit_queue.put(item)


def _written_row(engine: Any, request_id: str) -> APIAuditLog:
    """按 request_id 取出已落库的审计行，供断言写入内容。"""
    with Session(engine) as db:
        return db.exec(
            select(APIAuditLog).where(APIAuditLog.request_id == request_id)  # type: ignore[arg-type,return-value]
        ).one()


def test_audit_write_skips_sqlite_pragma_on_other_dialects(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证非 SQLite 方言下不再下发 PRAGMA，审计记录仍能正常写入数据库。"""
    engine = _make_engine()
    statements = _recorded_statements(engine)
    # Only the dialect label changes; the underlying connection stays sqlite so
    # the INSERT itself stays exercisable. Pre-fix, this test fails because the
    # PRAGMA still runs and appears in the recorded statements.
    monkeypatch.setattr(engine.dialect, "name", "postgresql")

    _ensure_audit_worker()
    _enqueue(engine, "req_dialect")
    assert flush_audit_queue(timeout=5.0)

    assert _written_row(engine, "req_dialect").status_code == 200
    assert not any(statement.upper().startswith("PRAGMA") for statement in statements)


def test_audit_write_keeps_sqlite_busy_guard() -> None:
    """验证 SQLite 方言下仍保留 busy_timeout PRAGMA，审计记录正常写入。"""
    engine = _make_engine()
    statements = _recorded_statements(engine)

    _ensure_audit_worker()
    _enqueue(engine, "req_sqlite")
    assert flush_audit_queue(timeout=5.0)

    assert _written_row(engine, "req_sqlite").status_code == 200
    assert any(statement.upper().startswith("PRAGMA") for statement in statements)
