from __future__ import annotations

import contextlib
import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.a2a import recover_codex_a2a_tasks, stop_codex_a2a_tasks
from app.a2a import router as a2a_router
from app.api import (
    agents,
    app_updates,
    auth,
    channels,
    chat,
    evolution,
    external_business_tasks,
    feedback,
    general_skills,
    knowledge,
    knowledge_bases,
    memories,
    mock,
    model_configs,
    persona,
    scheduled_tasks,
    sessions,
    skills,
    teams,
    tools,
    traces,
    ui_config,
    wechat_kf,
)
from app.async_jobs import shutdown_async_jobs, start_async_jobs
from app.channels import start_channel_services, stop_channel_services
from app.config import get_settings
from app.core.harness_recovery import (
    recover_orphan_harness_runs,
    start_harness_recovery_sweeper,
    stop_harness_recovery_sweeper,
)
from app.db import engine, init_db
from app.db.database_path import sqlite_database_path
from app.db.seed import seed_demo_data
from app.distributed.lease import RedisLease, acquire_role_lease
from app.public_api import create_public_api_app
from app.public_api.jobs import cleanup_public_api_records, recover_public_jobs
from app.public_api.maintenance import start_public_api_maintenance, stop_public_api_maintenance
from app.public_api.webhooks import enqueue_due_webhook_deliveries
from app.runtime_lock import acquire_runtime_instance_lock, release_runtime_instance_lock
from app.scheduled_tasks.worker import start_background_worker, stop_background_worker
from app.teams.sweeper import start_timeout_sweeper, stop_timeout_sweeper
from app.tools.a2a_recovery import recover_a2a_client_tasks
from app.tools.external_task_worker import start_external_task_worker, stop_external_task_worker
from app.version import app_version

settings = get_settings()

logger = logging.getLogger("staffdeck.main")

app = FastAPI(
    title=settings.app_name,
    version=app_version(),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 进程角色 -> 启用的能力组。/api/chat、/api/enterprise、/api/v1 路由全部进程
# 都挂载（无状态 HTTP 处理成本低），后台线程/worker 只在 worker 角色启动。
_ROLE_MAP = {
    "all": {"api", "worker", "connector"},
    "api": {"api"},
    "worker": {"worker"},
    "connector": {"connector"},
}


def _role_enables(capability: str) -> bool:
    return capability in _ROLE_MAP.get(
        get_settings().process_role, {"api", "worker", "connector"}
    )


# 本进程持有的角色租约（PG + redis_url 部署下防同角色双进程）。
# SQLite 桌面/单机模式靠文件锁互斥，redis_url 为空时租约获取内部直接跳过。
_held_leases: list[RedisLease] = []
_lease_renew_stop = threading.Event()
_lease_renew_thread: threading.Thread | None = None
LEASE_RENEW_INTERVAL_SECONDS = 20.0


def _start_lease_renewer() -> None:
    """Keep held role leases alive.

    Leases carry a short TTL so a crashed process cannot block its role forever,
    which means a healthy long-running process must renew them. Without this the
    lease silently expires after LEASE_TTL_SECONDS and the exclusivity guarantee
    disappears for every process that outlives it.
    """
    global _lease_renew_thread
    if not _held_leases:
        return
    _lease_renew_stop.clear()

    def renew_loop() -> None:
        while not _lease_renew_stop.wait(LEASE_RENEW_INTERVAL_SECONDS):
            for lease in list(_held_leases):
                try:
                    if not lease.renew():
                        logger.error(
                            "角色租约续期失败（租约已被他人持有或已过期）：%s", lease
                        )
                except Exception:  # noqa: BLE001 - 续期失败不应打断进程
                    logger.exception("角色租约续期异常")

    _lease_renew_thread = threading.Thread(
        target=renew_loop, name="staffdeck-lease-renewer", daemon=True
    )
    _lease_renew_thread.start()


def _stop_lease_renewer() -> None:
    global _lease_renew_thread
    _lease_renew_stop.set()
    thread = _lease_renew_thread
    if thread is not None:
        thread.join(timeout=5.0)
    _lease_renew_thread = None


def _release_held_leases() -> None:
    _stop_lease_renewer()
    for lease in _held_leases:
        # 释放尽力而为：shutdown 阶段 Redis 不可达不应阻塞进程退出，
        # 租约本身带 TTL 会自动过期。
        with contextlib.suppress(Exception):
            lease.release()
    _held_leases.clear()


@app.on_event("startup")
def on_startup() -> None:
    # 实例文件锁只用于 SQLite 数据库的单机互斥；PG 部署允许多进程/多机，
    # 由下方角色租约负责互斥。
    if sqlite_database_path(get_settings().database_url) is not None:
        acquire_runtime_instance_lock()
    _held_leases.clear()
    try:
        for capability in ("api", "worker", "connector"):
            if _role_enables(capability):
                lease = acquire_role_lease(capability)
                if lease is not None:
                    _held_leases.append(lease)
        _start_lease_renewer()
        _startup_by_role()
    except Exception:
        _release_held_leases()
        release_runtime_instance_lock()
        raise


def _startup_by_role() -> None:
    init_db()
    if _role_enables("api"):
        with Session(engine) as db:
            seed_demo_data(db)
        recover_codex_a2a_tasks()
    if _role_enables("worker"):
        start_async_jobs()
        with Session(engine) as db:
            recover_orphan_harness_runs(db, startup=True)
        recover_a2a_client_tasks()
        start_external_task_worker()
        start_background_worker()
        start_timeout_sweeper()
        start_harness_recovery_sweeper()
        # Internal durable jobs (for example feedback analysis) use the same
        # recovery table even when the externally exposed API is disabled.
        recover_public_jobs()
        if settings.public_api_enabled:
            cleanup_public_api_records()
            enqueue_due_webhook_deliveries()
            start_public_api_maintenance()
    if _role_enables("connector"):
        start_channel_services()


@app.on_event("shutdown")
def on_shutdown() -> None:
    try:
        if _role_enables("worker"):
            stop_codex_a2a_tasks()
            stop_external_task_worker()
            stop_public_api_maintenance()
            stop_background_worker()
            stop_timeout_sweeper()
            stop_harness_recovery_sweeper()
            shutdown_async_jobs()
        if _role_enables("connector"):
            stop_channel_services()
    finally:
        _release_held_leases()
        release_runtime_instance_lock()


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": "StaffDeck"}


app.include_router(app_updates.router)
app.include_router(chat.router)
app.include_router(agents.chat_router)
app.include_router(ui_config.chat_router)
app.include_router(auth.router)
app.include_router(agents.scope_router)
app.include_router(agents.enterprise_router)
app.include_router(general_skills.router)
app.include_router(knowledge_bases.router)
app.include_router(knowledge.router)
app.include_router(skills.router)
app.include_router(model_configs.router)
app.include_router(memories.router)
app.include_router(evolution.router)
app.include_router(feedback.router)
app.include_router(persona.router)
app.include_router(scheduled_tasks.enterprise_router)
app.include_router(scheduled_tasks.chat_router)
app.include_router(scheduled_tasks.chat_draft_router)
app.include_router(ui_config.enterprise_router)
app.include_router(channels.router)
app.include_router(wechat_kf.router)
app.include_router(teams.router)
app.include_router(teams.threads_router)
app.include_router(tools.router)
app.include_router(tools.mcp_router)
app.include_router(external_business_tasks.enterprise_router)
app.include_router(external_business_tasks.callback_router)
app.include_router(sessions.router)
app.include_router(traces.router)
app.include_router(mock.router)
app.include_router(a2a_router)

if settings.public_api_enabled:
    app.mount("/api/v1", create_public_api_app())
