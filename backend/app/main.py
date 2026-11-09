from __future__ import annotations

import contextlib
import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
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
from app.core import runtime_state
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.harness_recovery import (
    recover_orphan_harness_runs,
    start_harness_recovery_sweeper,
    stop_harness_recovery_sweeper,
)
from app.core.runtime_state import is_lease_lost
from app.db import engine, init_db
from app.db.database_path import sqlite_database_path
from app.db.seed import ensure_bootstrap_admin, seed_demo_data
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

# 上传体积预检:FastAPI 会在进入端点前解析 multipart,超限请求必须在这里
# 读取 body 之前拒绝。仅覆盖 POST /api/chat/attachments,其余请求原样放行。
# 先注册体积预检、后注册 CORS:Starlette 后注册的中间件在外层,413 也能带 CORS 头。
app.add_middleware(BodySizeLimitMiddleware)

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
    """判断当前进程角色是否启用某一能力组（api/worker/connector）。"""
    return capability in _ROLE_MAP.get(
        get_settings().process_role, {"api", "worker", "connector"}
    )


# 本进程持有的角色租约（PG + redis_url 部署下防同角色双进程）。
# SQLite 桌面/单机模式靠文件锁互斥；生产缺 Redis 时 acquire_role_lease 直接报错。
_held_leases: list[RedisLease] = []
_lease_renew_stop = threading.Event()
_lease_renew_thread: threading.Thread | None = None
LEASE_RENEW_INTERVAL_SECONDS = 20.0
# 连续这么多轮续期失败即认定租约丢失：互斥保护已失效，就绪探针必须转未就绪。
LEASE_RENEW_MAX_FAILURES = 3
_lease_renew_failures = 0


def _lease_label(lease: RedisLease, index: int) -> str:
    """为日志生成可读的租约标识：优先 role 属性，否则类型名 + 序号。

    默认对象 repr 只含内存地址，排障时无法区分配置了哪个角色。
    """
    role = getattr(lease, "role", "")
    return str(role) if role else f"{type(lease).__name__}#{index}"


def _renew_held_leases_once() -> tuple[bool, str]:
    """为全部已持有租约续期一轮，返回 (fencing_lost, 失败描述)。

    fencing_lost 表示至少一个租约的 renew() 明确返回 False：租约已被其他进程
    持有或已过期，本进程不再具备互斥保证，必须立即置为 not-ready。非空描述而
    fencing_lost 为 False 表示本轮的失败都是异常（Redis 暂时不可达等），可重试。
    单个租约的异常不打断其余租约，也不让续期线程退出：租约丢失的处理是
    “标记未就绪”，而不是杀掉进程。
    """
    fencing_lost = False
    failures: list[str] = []
    # 取快照再遍历：shutdown 路径可能并发清空 _held_leases。
    held = list(_held_leases)
    for index, lease in enumerate(held):
        label = _lease_label(lease, index)
        try:
            renewed = lease.renew()
        except Exception as exc:  # 续期失败不应打断进程
            failures.append(f"{label}: {exc!r}")
            continue
        # 只有明确的 False 才算 fencing 丢失；None/异常留给下面的重试计数。
        if renewed is False:
            fencing_lost = True
            failures.append(f"{label}: 租约已被他人持有或已过期")
    return fencing_lost, "; ".join(failures)


def _latch_lease_lost(reason: str) -> None:
    """置位 not-ready，并且只在状态翻转时记录一条警告日志。

    日志不逐轮刷屏：lost 置位后重复调用直接返回，恢复时由
    _record_lease_renew_success() 打一条恢复日志。
    """
    if runtime_state.is_lease_lost():
        return
    runtime_state.mark_lease_lost()
    logger.warning("角色租约丢失，本进程转为 not-ready：%s", reason)


def _record_lease_renew_failure(reason: str) -> None:
    """累计连续异常轮数；达到阈值即置位 not-ready（fencing 未确认时保守处理）。"""
    global _lease_renew_failures
    _lease_renew_failures += 1
    if _lease_renew_failures < LEASE_RENEW_MAX_FAILURES:
        return
    _latch_lease_lost(f"连续 {_lease_renew_failures} 轮续期异常：{reason}")


def _reset_lease_renew_failures() -> None:
    """清零连续失败计数（启动装配与续期成功路径共用）。"""
    global _lease_renew_failures
    _lease_renew_failures = 0


def _record_lease_renew_success() -> None:
    """续期成功后清零失败计数；若此前已置位，则解除 not-ready 并记录恢复。

    只治临时故障：一次 Redis 抖动或重启后，下一轮成功续期就会让探针恢复 200，
    而不是把进程永久钉在 not-ready 上。
    """
    _reset_lease_renew_failures()
    if runtime_state.is_lease_lost():
        runtime_state.clear_lease_lost()
        logger.warning("角色租约续期恢复，本进程转回 ready")


def _start_lease_renewer() -> None:
    """为已持有的角色租约周期续期。

    租约 TTL 很短，崩溃的进程不会永久占用角色，因此存活进程必须续期；否则
    超过 LEASE_TTL_SECONDS 后租约静默过期，互斥保证随之失效。renew() 明确
    返回 False（租约已不属于本进程）时立即置为未就绪；连续
    LEASE_RENEW_MAX_FAILURES 轮异常后同样置为未就绪，但不退出进程。恢复一次
    成功续期即清除未就绪状态。
    """
    global _lease_renew_thread
    if not _held_leases:
        return
    _lease_renew_stop.clear()
    _reset_lease_renew_failures()

    def renew_loop() -> None:
        """后台循环：每次间隔结束后为全部已持有租约续期一次。"""
        while not _lease_renew_stop.wait(LEASE_RENEW_INTERVAL_SECONDS):
            fencing_lost, reason = _renew_held_leases_once()
            if fencing_lost:
                _latch_lease_lost(reason)
            elif reason:
                _record_lease_renew_failure(reason)
            else:
                _record_lease_renew_success()

    _lease_renew_thread = threading.Thread(
        target=renew_loop, name="staffdeck-lease-renewer", daemon=True
    )
    _lease_renew_thread.start()


def _stop_lease_renewer() -> None:
    """停止续期线程并最多等待 5 秒，随后清空线程句柄。"""
    global _lease_renew_thread
    _lease_renew_stop.set()
    thread = _lease_renew_thread
    if thread is not None:
        thread.join(timeout=5.0)
    _lease_renew_thread = None


def _release_held_leases() -> None:
    """停止续期并尽力释放本进程持有的全部角色租约。

    释放异常被忽略：shutdown 阶段 Redis 不可达不应阻塞退出，过期由 TTL 兜底。
    """
    _stop_lease_renewer()
    for lease in _held_leases:
        # 释放尽力而为：shutdown 阶段 Redis 不可达不应阻塞进程退出，
        # 租约本身带 TTL 会自动过期。
        with contextlib.suppress(Exception):
            lease.release()
    _held_leases.clear()


@app.on_event("startup")
def on_startup() -> None:
    """应用启动钩子：获取实例锁与角色租约，再按角色启动后台服务。

    SQLite 部署用进程级文件锁互斥，PG + Redis 部署改由角色租约互斥；任一步
    失败都会释放已持有的租约与实例锁，避免残留占用阻塞后续启动。
    """
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
    """按进程角色初始化数据库，并启动该角色对应的后台线程与 worker。"""
    settings = get_settings()
    init_db()
    if _role_enables("api"):
        with Session(engine) as db:
            if settings.seed_demo_data:
                generated = seed_demo_data(db)
            else:
                generated = ensure_bootstrap_admin(db)
        if generated:
            logger.warning(
                "Bootstrap admin ensured; generated password (if any) was logged at creation."
            )
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
    """应用关闭钩子：按角色停止后台服务，最后释放租约与实例锁。"""
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
    """健康检查端点，返回固定的 ok 状态与应用名，供探活使用。"""
    return {"status": "ok", "app": "StaffDeck"}


_probe_engine: Engine | None = None


def _probe_connect_args(backend: str) -> dict[str, object]:
    """按方言给探针连接加客户端超时，保证单次探测有明确上界。"""
    if backend.startswith("postgresql"):
        # connect_timeout 限制建连；statement_timeout 防止 SELECT 1 排在长事务后面。
        return {"connect_timeout": 3, "options": "-c statement_timeout=3000"}
    if backend == "sqlite":
        return {"timeout": 3}
    return {}


def _build_probe_engine() -> Engine:
    """为就绪探针单独建一个引擎，不与应用共享连接池。

    探针绝不能排在应用连接池后面：池被慢查询占满时复用 engine.connect() 会让
    /api/ready 一起阻塞，编排层只能等到超时才判定 not-ready。NullPool 让每次
    探测新建连接，pre_ping 关闭（探测本身就是一条查询），客户端超时兜底。
    """
    probe_url = engine.url
    return create_engine(
        probe_url,
        poolclass=NullPool,
        pool_pre_ping=False,
        connect_args=_probe_connect_args(probe_url.get_backend_name()),
    )


def _get_probe_engine() -> Engine:
    """懒加载并缓存探针引擎；进程生命周期内数据库 URL 不变，无需失效逻辑。"""
    global _probe_engine
    if _probe_engine is None:
        _probe_engine = _build_probe_engine()
    return _probe_engine


def _check_database() -> bool:
    """探测数据库连通性，连接与查询都有客户端超时上界。

    探针的职责是报告状态而不是制造故障：连接或查询失败统一收敛成 False，
    由调用方转成 503；这里不抛异常，也不重试，编排层靠重试间隔兜底。
    """
    try:
        with _get_probe_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


# is_lease_lost 以模块级名字从 runtime_state 导入：就绪探针与测试都直接引用
# 本模块的这个名字，monkeypatch 才有一个稳定的补丁点。
@app.get("/api/ready", tags=["health"])
def ready() -> JSONResponse:
    """就绪探针：数据库可连通且角色租约仍在握时才返回 200。

    未就绪返回 503，编排层据此摘掉流量；两项状态分开上报，排障时不需要
    再猜是数据库还是角色互斥先失效。端点不依赖 lifespan，可独立于启动钩子调用。
    """
    database_ok = _check_database()
    lease_ok = not is_lease_lost()
    is_ready = database_ok and lease_ok
    payload = {
        "status": "ok" if is_ready else "degraded",
        "database": database_ok,
        "lease": lease_ok,
    }
    return JSONResponse(payload, status_code=200 if is_ready else 503)


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
