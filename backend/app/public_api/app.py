from __future__ import annotations

import logging
import queue
import threading
import time
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.db.models import APIAuditLog
from app.public_api import (
    agents,
    credentials,
    digital_employees,
    examples,
    external_sessions,
    gallery,
    jobs,
    knowledge_sync,
    mcp,
    operations,
    resources,
    runs,
    sessions,
    sops,
    webhooks,
)
from app.public_api.errors import (
    PublicAPIError,
    public_api_error_handler,
    public_http_error_handler,
    public_validation_error_handler,
)

logger = logging.getLogger(__name__)

# One serial worker owns audit writes. A thread per request would spawn unbounded
# threads under load and interleave SQLite writes with SSE streams that are still
# running. The engine is captured per item rather than read from the module at
# write time, so a caller that swaps `app.public_api.app.engine` (tests do this)
# keeps its audit rows in the database it actually used.
_audit_queue: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=1000)
_audit_worker: threading.Thread | None = None
_audit_worker_lock = threading.Lock()


def _audit_loop() -> None:
    while True:
        item = _audit_queue.get()
        if item is None:
            _audit_queue.task_done()
            return
        target_engine = item.pop("_engine", engine)
        try:
            with Session(target_engine) as db:  # type: ignore[arg-type]
                if target_engine.dialect.name == "sqlite":
                    # SQLite-only busy guard. On PostgreSQL this statement is a
                    # syntax error and used to drop every audit row with a
                    # warning; pick it by dialect, not by habit.
                    db.connection().exec_driver_sql("PRAGMA busy_timeout = 2000")
                db.add(APIAuditLog(**item))  # type: ignore[arg-type]
                db.commit()
        except Exception as error:  # noqa: BLE001
            # Never silently drop the failure: a missing audit trail is an
            # incident, not a detail, and the request itself already succeeded.
            logger.warning(
                "public API audit write failed request_id=%s path=%s status=%s error=%s",
                item.get("request_id"),
                item.get("path"),
                item.get("status_code"),
                error,
            )
        finally:
            _audit_queue.task_done()


def _ensure_audit_worker() -> None:
    global _audit_worker
    if _audit_worker is not None and _audit_worker.is_alive():
        return
    with _audit_worker_lock:
        if _audit_worker is not None and _audit_worker.is_alive():
            return
        _audit_worker = threading.Thread(
            target=_audit_loop, name="public-api-audit", daemon=True
        )
        _audit_worker.start()


def flush_audit_queue(timeout: float = 5.0) -> bool:
    """Block until queued audit rows are written. Returns False on timeout.

    Exposed so tests and shutdown paths can observe audit rows deterministically
    instead of sleeping.
    """
    deadline = time.monotonic() + timeout
    while _audit_queue.unfinished_tasks:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _write_audit_log(
    request: Request,
    principal: object | None,
    *,
    status_code: int,
    duration_ms: float,
) -> None:
    """Queue the audit row instead of blocking the response on a write lock.

    Writing it inline made the request wait out SQLite's busy timeout whenever
    another connection held the lock, which turned a 0.3s request into 33s and
    then dropped the row. Queueing keeps the response fast and lets one worker
    own the write, so latency never depends on audit contention.
    """
    _ensure_audit_worker()
    try:
        _audit_queue.put_nowait(
            {
                "_engine": engine,
                "tenant_id": getattr(principal, "tenant_id", None),
                "credential_id": getattr(principal, "credential_id", None),
                "request_id": str(getattr(request.state, "request_id", "")),
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            }
        )
    except queue.Full:
        logger.warning(
            "public API audit queue is full, dropping request_id=%s path=%s",
            getattr(request.state, "request_id", ""),
            request.url.path,
        )


def create_public_api_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} Open API",
        description="数字员工、Harness v2、SOP、知识与自动任务开放 API。",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_exception_handler(PublicAPIError, public_api_error_handler)
    app.add_exception_handler(HTTPException, public_http_error_handler)
    app.add_exception_handler(RequestValidationError, public_validation_error_handler)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            # The audit write must not share a connection with the handler. On
            # SQLite a handler read transaction stays open until its dependency
            # is torn down, and a second connection writing the audit row then
            # blocks on the database write lock: the request paid the full
            # busy-timeout (30s+) and the row was silently dropped. The write is
            # therefore moved off the request's connection pool after the
            # response, and failures are logged instead of swallowed.
            _write_audit_log(
                request,
                getattr(request.state, "public_principal", None),
                status_code=status_code,
                duration_ms=(perf_counter() - started) * 1000,
            )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "v1", "engine": "harness_v2"}

    app.include_router(credentials.router)
    app.include_router(gallery.router)
    app.include_router(agents.router)
    # knowledge_sync must register before digital_employees: FastAPI matches in
    # registration order, and the latter's /{agent_id} would swallow the nested
    # knowledge-package routes.
    app.include_router(knowledge_sync.router)
    app.include_router(mcp.router)
    app.include_router(digital_employees.router)
    app.include_router(sessions.router)
    app.include_router(external_sessions.router)
    app.include_router(runs.router)
    app.include_router(jobs.router)
    app.include_router(sops.router)
    app.include_router(resources.router)
    app.include_router(operations.router)
    app.include_router(webhooks.router)
    app.include_router(examples.router)
    return app
