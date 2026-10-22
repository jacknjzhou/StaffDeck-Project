from __future__ import annotations

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
from app.db.seed import seed_demo_data
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


@app.on_event("startup")
def on_startup() -> None:
    acquire_runtime_instance_lock()
    try:
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
    except Exception:
        release_runtime_instance_lock()
        raise


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
