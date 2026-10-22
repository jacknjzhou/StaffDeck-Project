# StaffDeck M1 服务端化基座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 StaffDeck 从桌面单进程（SQLite+实例锁）改造为生产级服务端：PostgreSQL + Alembic 基线、api/worker/connector 三类进程、Redis 租约、Docker Compose、OIDC 登录、租户推导收紧。

**Architecture:** 保持 FastAPI 单体代码库不变，按"进程角色"拆分启动钩子（同一 `app.main:app`，不同角色只激活自己的后台服务）；SQLite 专属逻辑全部按 URL 前缀条件化（现状已基本如此）；新增轻量 Redis 租约模块承担多实例互斥；部署侧用 Docker Compose 编排四服务。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel + SQLAlchemy 2 + Alembic + PostgreSQL 16 + Redis 7（redis-py 5）+ Docker Compose + Nginx

**Spec:** `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（§5 服务端化改造）

## Global Constraints

- 遵循 AGENTS.md：Conventional Commits；Python ruff 100 字符行宽；后端测试目录 `backend/tests`；每个任务单独 commit，不 `--no-verify`
- SQLite 兼容期：CI 保持 SQLite 矩阵不被破坏；所有 SQLite 专属逻辑按 `database_url.startswith("sqlite")` 条件化（与 `app/db/database.py:105-115` 现有惯例一致）
- 禁止引入重量级认证框架（authlib 等）；OIDC 用 httpx 手工实现 code flow（与 `app/security/auth.py` 手写 JWT 的既有风格一致）
- 桌面打包路径（PyInstaller + single_port_app.py）不删除，仅降级为非重点；本计划新增 `backend/server_app.py` 与之并存
- 租户策略：JWT 为唯一权威（payload 含 tenant_id，见 `app/security/auth.py:40-49`）；请求参数 tenant_id 降级为可选
- 新增依赖最小集：`alembic`、`redis`（运行时）；`fakeredis`、`pytest`（dev，fakeredis 仅 dev）

---

### Task 1: PostgreSQL 连接支持与 engine 工厂

**Files:**
- Modify: `backend/app/db/database.py:20-24`（engine 创建处）
- Modify: `backend/app/config.py`（新增连接池配置项）
- Test: `backend/tests/test_database_engine_factory.py`（新建）

**Interfaces:**
- Produces: `build_engine(database_url: str) -> Engine`（`app.db.database` 导出，后续 Alembic env.py 与测试复用）
- Produces: `Settings.redis_url: str = ""`、`Settings.db_pool_size: int = 10`、`Settings.db_max_overflow: int = 20`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_database_engine_factory.py
from sqlalchemy import Engine
from sqlmodel import SQLModel, create_engine

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_database_engine_factory.py -v`
Expected: FAIL `ImportError: cannot import name 'build_engine'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/config.py` 在 `public_api_enabled` 附近追加：

```python
    redis_url: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 20
```

`backend/app/db/database.py` 将第 20-24 行改为工厂函数 + 保留模块级 engine：

```python
def build_engine(url: str) -> Engine:
    """Create an engine with dialect-appropriate options."""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
        return create_engine(url, echo=False, connect_args=connect_args)
    return create_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_size=get_settings().db_pool_size,
        max_overflow=get_settings().db_max_overflow,
    )


settings = get_settings()

database_url = _normalize_database_url(settings.database_url)
engine: Engine = build_engine(database_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_database_engine_factory.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 全量回归 + Commit**

Run: `backend/.venv/bin/python -m pytest backend/tests -x -q`
Expected: 全部 PASS（engine 行为对 SQLite 路径无变化）

```bash
git add backend/app/db/database.py backend/app/config.py backend/tests/test_database_engine_factory.py
git commit -m "feat(db): extract dialect-aware build_engine factory for PostgreSQL support"
```

---

### Task 2: psycopg 依赖与 PG 冒烟测试夹具

**Files:**
- Modify: `backend/pyproject.toml`（dependencies 增加 psycopg；dev 增加 fakeredis）
- Create: `backend/tests/conftest_pg.py`（PG 可用性探测 fixture，供 PG 集成测试复用）
- Test: `backend/tests/test_postgres_smoke.py`（新建，PG 未配置时 skip）

**Interfaces:**
- Produces: `pg_engine` pytest fixture（`postgresql+psycopg://` 真实连接；未设置 `STAFFDECK_TEST_PG_URL` 时 skip）——Task 3/6 的集成测试消费

- [ ] **Step 1: 添加依赖**

`backend/pyproject.toml` 的 `[project] dependencies` 数组追加：

```toml
  "psycopg[binary]>=3.1.0",
```

dev 依赖组（`[project.optional-dependencies] dev`）追加：

```toml
  "fakeredis>=2.21.0",
```

安装：`backend/.venv/bin/python -m pip install -e "backend[dev]"`

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/conftest_pg.py
import os

import pytest


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Real PostgreSQL URL for integration tests; skip when not provided."""
    url = os.environ.get("STAFFDECK_TEST_PG_URL", "")
    if not url:
        pytest.skip("STAFFDECK_TEST_PG_URL not set; PostgreSQL integration skipped")
    return url
```

```python
# backend/tests/test_postgres_smoke.py
from sqlmodel import Session, SQLModel, text

import app.db.models  # noqa: F401  确保全部表注册进 metadata
from app.db.database import build_engine


def test_create_all_and_roundtrip_on_postgres(pg_url: str):
    eng = build_engine(pg_url)
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as db:
        db.execute(text("SELECT 1"))
    eng.dispose()
```

- [ ] **Step 3: Run test to verify behavior**

```bash
docker run -d --name staffdeck-pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=staffdeck -p 5433:5432 postgres:16
STAFFDECK_TEST_PG_URL="postgresql+psycopg://postgres:test@localhost:5433/staffdeck" \
  backend/.venv/bin/python -m pytest backend/tests/test_postgres_smoke.py -v
```

Expected: PASS（未设环境变量时 SKIP 也算通过）

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/tests/conftest_pg.py backend/tests/test_postgres_smoke.py
git commit -m "feat(db): add psycopg dependency and PostgreSQL smoke fixture"
```

---

### Task 3: Alembic 基线迁移

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`、`backend/alembic/script.py.mako`、`backend/alembic/versions/0001_baseline.py`
- Modify: `backend/pyproject.toml`（dependencies 增加 `alembic>=1.13.0`）
- Test: `backend/tests/test_alembic_baseline.py`（新建）

**Interfaces:**
- Produces: `alembic upgrade head` 可从空 PG 库建出全部表（env.py 读 `STAFFDECK_DATABASE_URL` 环境变量）
- Produces: `app.db.migrations.run_migrations(url: str) -> None`（server_app 启动时调用）

- [ ] **Step 1: 安装并初始化 Alembic**

```bash
backend/.venv/bin/python -m pip install "alembic>=1.13.0"
cd backend && .venv/bin/python -m alembic init alembic
```

- [ ] **Step 2: 配置 env.py**

`backend/alembic/env.py` 替换为（关键段）：

```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401  注册全部 SQLModel 表
from app.db.database import build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = app.db.models.SQLModel.metadata


def _database_url() -> str:
    url = os.environ.get("STAFFDECK_DATABASE_URL", "")
    if url:
        return url
    from app.config import get_settings

    return get_settings().database_url


def run_migrations_online() -> None:
    context.configure(
        connection=build_engine(_database_url()).connect(),
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=False,
    )
    with context.configure.context._connection:  # noqa: SLF001 — 保持标准写法见下
        pass
```

注意：上面最后一行不是有效写法，`run_migrations_online` 的正确完整实现为：

```python
def run_migrations_online() -> None:
    connectable = build_engine(_database_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: 生成基线迁移**

```bash
cd backend
STAFFDECK_DATABASE_URL="postgresql+psycopg://postgres:test@localhost:5433/staffdeck" \
  .venv/bin/python -m alembic revision --autogenerate -m "baseline schema"
# 生成后人工审读 versions/0001_baseline.py：确认无 sqlite 方言残留（如 sqlite_autoincrement）
.venv/bin/python -m ruff check alembic/
```

把生成文件重命名为 `versions/0001_baseline.py`，并确认 `revision = "0001"`、`down_revision = None`（手工修改 revision 标识为固定值，避免随机 id）。

- [ ] **Step 4: Write the failing test**

```python
# backend/tests/test_alembic_baseline.py
from sqlmodel import text

from app.db.database import build_engine
from app.db.migrations import run_migrations


def test_alembic_baseline_upgrades_empty_postgres(pg_url: str):
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
```

```python
# backend/app/db/migrations.py（新建）
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_migrations(database_url: str) -> None:
    """Run `alembic upgrade head` against the given URL."""
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    os.environ["STAFFDECK_DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")
```

- [ ] **Step 5: Run test to verify it passes**

```bash
STAFFDECK_TEST_PG_URL="postgresql+psycopg://postgres:test@localhost:5433/staffdeck" \
STAFFDECK_DATABASE_URL="postgresql+psycopg://postgres:test@localhost:5433/staffdeck" \
  backend/.venv/bin/python -m pytest backend/tests/test_alembic_baseline.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/alembic.ini backend/alembic backend/app/db/migrations.py backend/pyproject.toml backend/tests/test_alembic_baseline.py
git commit -m "feat(db): add Alembic baseline migration generated from SQLModel metadata"
```

---

### Task 4: 进程角色拆分与 server_app.py 入口

**Files:**
- Modify: `backend/app/config.py`（新增 `process_role`）
- Modify: `backend/app/main.py:74-114`（startup/shutdown 按角色分派）
- Create: `backend/app/static_serving.py`（从 single_port_app.py 抽取 SPA 静态挂载）
- Create: `backend/server_app.py`
- Test: `backend/tests/test_process_role_hooks.py`（新建）

**Interfaces:**
- Produces: `Settings.process_role: str = "all"`（取值 `all|api|worker|connector`）
- Produces: `app.static_serving.mount_frontend(app) -> None`（server_app.py 与未来拆分复用）
- Produces: `uvicorn backend.server_app:app` 可启动纯服务端进程（托管管理端/用户端前端 + /api/* + /api/v1，无 PilotDeck/site-chat/LLM relay）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_process_role_hooks.py
from app.config import get_settings


def test_process_role_default_all():
    assert get_settings().process_role == "all"


def test_role_gate_helpers():
    from app.main import _role_enables

    assert _role_enables("api") is True          # all 角色启用一切
    settings = get_settings()
    settings.process_role = "worker"
    assert _role_enables("api") is False
    assert _role_enables("worker") is True
    settings.process_role = "all"  # 还原，避免污染 lru_cache 单例
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_process_role_hooks.py -v`
Expected: FAIL `process_role` 不存在 / `_role_enables` 不存在

- [ ] **Step 3: Implement role gating**

`backend/app/config.py` 追加：

```python
    process_role: str = "all"  # all | api | worker | connector
```

`backend/app/main.py` 第 74-114 行重写为按角色分派（完整替换 startup/shutdown）：

```python
_ROLE_MAP = {
    "all": {"api", "worker", "connector"},
    "api": {"api"},
    "worker": {"worker"},
    "connector": {"connector"},
}


def _role_enables(capability: str) -> bool:
    return capability in _ROLE_MAP.get(get_settings().process_role, {"api", "worker", "connector"})


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
```

说明：`/api/chat`、`/api/enterprise`、`/api/v1` 路由全部进程都挂载（无状态 HTTP 处理成本低），后台线程/worker 只在 worker 角色启动；`recover_orphan_harness_runs` 属恢复类移入 worker。

- [ ] **Step 4: 抽取静态挂载 + 新建 server_app.py**

新建 `backend/app/static_serving.py`（把 `single_port_app.py:444-503` 的静态挂载与 SPA fallback 原样搬入，导出函数）：

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app import paths

SPA_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

MOUNTS = (
    ("/assets", "assets"),
    ("/enterprise/assets", "enterprise/assets"),
    ("/chat/assets", "chat/assets"),
    ("/workspace/assets", "workspace/assets"),
)

SPA_ROUTES = ("/", "/login", "/chat", "/workspace", "/enterprise")


def _enterprise_dist():
    root = paths.resource_dir()
    return (
        root / "frontend-enterprise" / "dist"
        if paths.is_frozen()
        else root.parent / "frontend-enterprise" / "dist"
    )


def mount_frontend(app: FastAPI) -> None:
    dist = _enterprise_dist()
    if not dist.exists():
        return
    for mount_path, rel in MOUNTS:
        target = dist / rel
        if target.exists():
            app.mount(mount_path, StaticFiles(directory=str(target)), name=mount_path.strip("/"))
    index = dist / "index.html"

    def spa_fallback(path: str):  # noqa: ANN001
        return FileResponse(str(index), headers=SPA_INDEX_HEADERS)

    for route in SPA_ROUTES:
        app.get(route, include_in_schema=False)(spa_fallback)
```

（实现时以 `single_port_app.py` 现有段为准逐行搬移，保留 `/chat/*`、`/workspace/*`、`/enterprise/*` 子路径 fallback。）

新建 `backend/server_app.py`：

```python
"""Server-mode entry: API + /api/v1 + admin/user frontend hosting.

No PilotDeck proxy, no site-chat proxy, no LLM relay — those stay in the
desktop-oriented single_port_app. Run with:
    uvicorn backend.server_app:app --host 0.0.0.0 --port 5173
"""
from fastapi import FastAPI

from app.config import get_settings
from app.main import app as core_app  # noqa: F401  复用同一 ASGI 应用与启动钩子
from app.static_serving import mount_frontend

settings = get_settings()

app = core_app

mount_frontend(app)
```

- [ ] **Step 5: Run tests and full suite**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_process_role_hooks.py -v
backend/.venv/bin/python -m pytest backend/tests -x -q
```

Expected: PASS；全量无回归

- [ ] **Step 6: 手工冒烟（dev SQLite all 角色）**

```bash
backend/.venv/bin/python -m uvicorn backend.server_app:app --port 5199 &
curl http://127.0.0.1:5199/api/health   # {"status":"ok",...}
curl -s http://127.0.0.1:5199/openapi.json | head -c 100  # 404（主 app docs 仍关闭）
curl -s http://127.0.0.1:5199/api/v1/openapi.json | head -c 100  # 有输出
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py backend/app/main.py backend/app/static_serving.py backend/server_app.py backend/tests/test_process_role_hooks.py
git commit -m "feat(server): split startup hooks by process role and add server_app entry"
```

---

### Task 5: Redis 租约模块

**Files:**
- Create: `backend/app/distributed/__init__.py`
- Create: `backend/app/distributed/lease.py`
- Test: `backend/tests/test_distributed_lease.py`（新建，用 fakeredis）

**Interfaces:**
- Produces: `RedisLease(client, key: str, ttl_seconds: int = 60, holder: str = "")`，方法 `acquire() -> bool`、`renew() -> bool`、`release() -> None`
- Produces: `app.distributed.lease.acquire_role_lease(role: str) -> RedisLease | None`（redis_url 未配置返回 None=跳过）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_distributed_lease.py
import fakeredis

from app.distributed.lease import RedisLease


def test_acquire_is_exclusive():
    client = fakeredis.FakeStrictRedis()
    a = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-a")
    b = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-b")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_release_only_by_owner():
    client = fakeredis.FakeStrictRedis()
    a = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-a")
    a.acquire()
    b = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-b")
    b.release()  # 不应删掉 a 的锁
    assert client.get("staffdeck:lease:api") is not None
    a.release()
    assert client.get("staffdeck:lease:api") is None


def test_renew_extends_ttl():
    import time

    client = fakeredis.FakeStrictRedis()
    lease = RedisLease(client, "staffdeck:lease:api", ttl_seconds=1, holder="p")
    assert lease.acquire() is True
    time.sleep(0.05)
    assert lease.renew() is True
    assert client.ttl("staffdeck:lease:api") > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_distributed_lease.py -v`
Expected: FAIL `ModuleNotFoundError: app.distributed`

- [ ] **Step 3: Implement lease**

```python
# backend/app/distributed/lease.py
from __future__ import annotations

import os
import socket
import uuid
from typing import Any

from redis import Redis

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class RedisLease:
    """SET NX PX lease with owner-checked release/renew (single-key fence)."""

    def __init__(self, client: Any, key: str, ttl_seconds: int = 60, holder: str = "") -> None:
        self._client = client
        self._key = key
        self._ttl_ms = ttl_seconds * 1000
        self._holder = holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def acquire(self) -> bool:
        return bool(self._client.set(self._key, self._holder, nx=True, px=self._ttl_ms))

    def renew(self) -> bool:
        return bool(
            self._client.eval(_RENEW_LUA, 1, self._key, self._holder, self._ttl_ms)
        )

    def release(self) -> None:
        self._client.eval(_RELEASE_LUA, 1, self._key, self._holder)


def acquire_role_lease(role: str, ttl_seconds: int = 60) -> RedisLease | None:
    """Acquire the process-role lease; None means 'no redis configured, skip'."""
    from app.config import get_settings

    url = get_settings().redis_url
    if not url:
        return None
    client = Redis.from_url(url, decode_responses=True)
    lease = RedisLease(client, f"staffdeck:lease:{role}", ttl_seconds=ttl_seconds)
    if not lease.acquire():
        raise RuntimeError(f"another {role} process holds the lease")
    return lease
```

（`backend/app/distributed/__init__.py` 为空文件。）

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_distributed_lease.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/distributed backend/tests/test_distributed_lease.py
git commit -m "feat(server): add Redis-based role lease with owner-checked release"
```

---

### Task 6: 租约接线与实例锁条件化

**Files:**
- Modify: `backend/app/main.py`（startup 中：sqlite 才走文件锁；PG 模式下 api/worker/connector 各自 acquire_role_lease）
- Test: `backend/tests/test_role_lease_wiring.py`（新建）

**Interfaces:**
- Consumes: Task 5 `acquire_role_lease(role)`、Task 4 `_role_enables`
- Produces: PG + redis_url 配置下，同角色第二进程 startup 抛 `RuntimeError`；无 redis 时行为与现状一致（不阻塞）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_role_lease_wiring.py
import pytest


def test_role_lease_conflict_raises(monkeypatch):
    import fakeredis

    from app.distributed import lease as lease_mod

    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(lease_mod, "_build_client", lambda url: fake)

    first = lease_mod.acquire_role_lease("api")
    assert first is not None
    with pytest.raises(RuntimeError, match="holds the lease"):
        lease_mod.acquire_role_lease("api")
    first.release()


def test_no_redis_url_returns_none(monkeypatch):
    from app.distributed import lease as lease_mod

    monkeypatch.setattr(type(lease_mod), "acquire_role_lease", lease_mod.acquire_role_lease)
    monkeypatch.setenv("STAFFDECK_REDIS_URL", "")
    # get_settings 带 lru_cache：测试里用 get_settings.cache_clear() 或注入 Settings 实例
    from app.config import Settings

    settings = Settings(redis_url="", database_url="postgresql+psycopg://x")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    assert lease_mod.acquire_role_lease("api") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_role_lease_wiring.py -v`
Expected: FAIL（`_build_client` 不存在）

- [ ] **Step 3: Implement**

`backend/app/distributed/lease.py` 抽出可 monkeypatch 的客户端构造并接入 main.py：

```python
def _build_client(url: str):
    from redis import Redis

    return Redis.from_url(url, decode_responses=True)
```

（`acquire_role_lease` 内改用 `_build_client(url)`。）

`backend/app/main.py` startup 开头改为：

```python
@app.on_event("startup")
def on_startup() -> None:
    from app.db.database_path import sqlite_database_path

    if sqlite_database_path(database_url) is not None:
        acquire_runtime_instance_lock()  # SQLite 桌面/单机模式维持现状
    role = get_settings().process_role
    try:
        for capability in ("api", "worker", "connector"):
            if _role_enables(capability):
                acquire_role_lease(capability)  # redis_url 为空时内部返回 None
        _startup_by_role()
    except Exception:
        ...
```

（`_startup_by_role` 即 Task 4 中按角色分派的主体，抽函数以便异常处理统一释放租约；shutdown 对应 `release()`。每个 `RedisLease` 实例存入模块级 `_held_leases: list` 供 shutdown 释放。）

- [ ] **Step 4: Run tests**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_role_lease_wiring.py backend/tests/test_process_role_hooks.py -v
backend/.venv/bin/python -m pytest backend/tests -x -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/distributed/lease.py backend/tests/test_role_lease_wiring.py
git commit -m "feat(server): gate process roles with Redis leases, keep file lock for SQLite"
```

---

### Task 7: 租户推导收紧

**Files:**
- Modify: `backend/app/security/auth.py:72-77`（`require_current_tenant` 参数改为可选）
- Test: `backend/tests/test_tenant_derivation.py`（新建）

**Interfaces:**
- Produces: `require_current_tenant(tenant_id: str | None = Query(None), current_user = Depends(get_current_user)) -> User`——`tenant_id` 缺省时以 `current_user.tenant_id` 为准；显式传入且不匹配仍 403

- [ ] **Step 1: 全仓确认调用面**

Run: `grep -rn "require_current_tenant\|ensure_current_user_tenant" backend/app --include="*.py" | wc -l`
记录调用点数量——本任务不改调用方，只改签名默认行为（Query(... ) → Query(None)），调用方无感知。

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_tenant_derivation.py
import pytest
from fastapi import HTTPException

from app.security.auth import ensure_current_user_tenant
from tests_helpers import make_user  # 项目既有测试工厂；若无则内联构造 User(...)


def test_missing_tenant_param_falls_back_to_token():
    user = make_user(tenant_id="t1")
    # 新语义：tenant_id=None 表示"未传"，直接采信 token
    assert ensure_current_user_tenant(None, user) is None


def test_mismatched_tenant_still_403():
    user = make_user(tenant_id="t1")
    with pytest.raises(HTTPException) as exc:
        ensure_current_user_tenant("t2", user)
    assert exc.value.status_code == 403
```

（`make_user` 若仓库无既有工厂，则在测试内直接 `User(id="u1", tenant_id="t1", username="u", hashed_password="x")` 构造。）

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_tenant_derivation.py -v`
Expected: FAIL（`ensure_current_user_tenant(None, user)` 现抛 403，因为 None != "t1"）

- [ ] **Step 4: Implement**

```python
def ensure_current_user_tenant(tenant_id: str | None, current_user: User) -> None:
    if not isinstance(current_user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if tenant_id is not None and tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def require_current_tenant(
    tenant_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> User:
    ensure_current_user_tenant(tenant_id, current_user)
    return current_user
```

- [ ] **Step 5: Run tests and full suite**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_tenant_derivation.py -v
backend/.venv/bin/python -m pytest backend/tests -x -q
```

Expected: PASS；全量无回归（显式传 tenant_id 的既有前端行为不受影响）

- [ ] **Step 6: Commit**

```bash
git add backend/app/security/auth.py backend/tests/test_tenant_derivation.py
git commit -m "feat(auth): derive tenant from JWT, make tenant_id query param optional"
```

---

### Task 8: OIDC SSO 登录

**Files:**
- Modify: `backend/app/config.py`（新增 oidc_* 配置）
- Create: `backend/app/security/oidc.py`
- Modify: `backend/app/api/auth.py`（新增 2 个路由）
- Test: `backend/tests/test_oidc_login.py`（新建）

**Interfaces:**
- Produces: `GET /api/auth/oidc/login` → 302 到 IdP（state 存短时内存字典）
- Produces: `GET /api/auth/oidc/callback?code&state` → 换 token → userinfo → 按邮箱找/建 User → 302 `{frontend_base}/login?token=<JWT>`（前端复用现有 localStorage 登录态）
- Produces: `Settings.oidc_enabled/oidc_issuer/oidc_client_id/oidc_client_secret/oidc_redirect_uri/frontend_base_url`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_oidc_login.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def _client_with_oidc(monkeypatch):
    settings = Settings(
        oidc_enabled=True,
        oidc_issuer="https://idp.example.com",
        oidc_client_id="cid",
        oidc_client_secret="sec",
        oidc_redirect_uri="http://localhost:5173/api/auth/oidc/callback",
        frontend_base_url="http://localhost:5173",
        database_url="sqlite://",
        app_secret="test-secret",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return TestClient(app)


def test_login_redirects_to_authorization_endpoint(monkeypatch):
    client = _client_with_oidc(monkeypatch)
    discovery = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
    }
    with patch("app.security.oidc._discover", return_value=discovery):
        resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/authorize")
    assert "client_id=cid" in resp.headers["location"]
    assert "state=" in resp.headers["location"]


def test_callback_creates_user_and_returns_token(monkeypatch):
    client = _client_with_oidc(monkeypatch)
    discovery = {
        "token_endpoint": "https://idp.example.com/token",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
    }
    with patch("app.security.oidc._discover", return_value=discovery), patch(
        "app.security.oidc._exchange_code",
        return_value={"access_token": "at"},
    ), patch(
        "app.security.oidc._fetch_userinfo",
        return_value={"sub": "u-9", "email": "dev@example.com", "name": "Dev"},
    ):
        resp = client.get(
            "/api/auth/oidc/callback?code=c&state=s", follow_redirects=False
        )
    assert resp.status_code == 302
    assert "token=" in resp.headers["location"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_oidc_login.py -v`
Expected: FAIL（路由 404）

- [ ] **Step 3: Implement**

`backend/app/config.py` 追加：

```python
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    frontend_base_url: str = "http://localhost:5173"
```

```python
# backend/app/security/oidc.py
from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from app.config import get_settings

# state -> exp（内存态；服务端单组 api 进程内有效，M1 可接受）
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _discover(issuer: str) -> dict:
    resp = httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10)
    resp.raise_for_status()
    return resp.json()


def new_state() -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = time.time() + _STATE_TTL_SECONDS
    return state


def consume_state(state: str) -> bool:
    exp = _pending_states.pop(state, 0)
    return bool(exp) and time.time() < exp


def authorization_url(discovery: dict, state: str) -> str:
    s = get_settings()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": s.oidc_redirect_uri,
            "scope": "openid profile email",
            "state": state,
        }
    )
    return f"{discovery['authorization_endpoint']}?{query}"


def _exchange_code(discovery: dict, code: str) -> dict:
    s = get_settings()
    resp = httpx.post(
        discovery["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": s.oidc_redirect_uri,
            "client_id": s.oidc_client_id,
            "client_secret": s.oidc_client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_userinfo(discovery: dict, access_token: str) -> dict:
    resp = httpx.get(
        discovery["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

`backend/app/api/auth.py` 末尾追加（复用文件内既有 import；`User` 已从 models 导入）：

```python
@auth_router.get("/oidc/login")
def oidc_login():
    from app.security import oidc as oidc_mod

    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC disabled")
    discovery = oidc_mod._discover(settings.oidc_issuer)
    state = oidc_mod.new_state()
    return RedirectResponse(oidc_mod.authorization_url(discovery, state))


@auth_router.get("/oidc/callback")
def oidc_callback(code: str, state: str, db: Session = Depends(get_session)):
    from app.security import oidc as oidc_mod

    settings = get_settings()
    if not settings.oidc_enabled or not oidc_mod.consume_state(state):
        raise HTTPException(status_code=400, detail="Invalid OIDC state")
    discovery = oidc_mod._discover(settings.oidc_issuer)
    tokens = oidc_mod._exchange_code(discovery, code)
    userinfo = oidc_mod._fetch_userinfo(discovery, tokens["access_token"])
    email = (userinfo.get("email") or "").strip()
    username = userinfo.get("preferred_username") or userinfo.get("name") or email
    if not email:
        raise HTTPException(status_code=400, detail="OIDC account missing email")
    user = db.exec(select(User).where(User.username == email)).first()
    if user is None:
        user = User(tenant_id="tenant_demo", username=email, hashed_password="oidc", source="oidc")
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(user)
    return RedirectResponse(f"{settings.frontend_base_url}/login?token={token}")
```

（import 段补 `from fastapi.responses import RedirectResponse` 与 `from sqlmodel import select`；`tenant_id` 沿用前端既有 `VITE_TENANT_ID` 默认 `tenant_demo`，M2 起再演进为 IdP claim 映射。若 `User` 表无 `source="oidc"` 值约束，先用现有 source 字段写入 `"oidc"`——models.py `User.source` 为自由字符串。）请以实际 `User` 构造参数为准核对字段名（`app/db/models.py:28-44`）。

- [ ] **Step 4: Run tests**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_oidc_login.py -v
backend/.venv/bin/python -m ruff check backend/app/security/oidc.py backend/app/api/auth.py
backend/.venv/bin/python -m pytest backend/tests -x -q
```

Expected: PASS；ruff 无告警；全量无回归

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/security/oidc.py backend/app/api/auth.py backend/tests/test_oidc_login.py
git commit -m "feat(auth): add OIDC authorization-code login mapping to existing JWT sessions"
```

---

### Task 9: Dockerfile 与 Docker Compose 编排

**Files:**
- Create: `packaging/docker/Dockerfile.server`
- Create: `packaging/docker/docker-compose.yml`
- Create: `packaging/docker/.env.example`
- Create: `packaging/docker/nginx.conf`

**Interfaces:**
- Produces: `docker compose up` 一键起 postgres/redis/api/worker/connector/nginx，交付标志 = `/api/health` + `/api/v1/docs` + 管理端页面可用

- [ ] **Step 1: Dockerfile（多阶段：前端构建 → 运行镜像）**

```dockerfile
# packaging/docker/Dockerfile.server
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend-enterprise/package.json frontend-enterprise/package-lock.json ./
RUN npm ci
COPY frontend-enterprise/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY backend/pyproject.toml backend/README.md* ./backend/
COPY backend/app ./backend/app
COPY backend/scripts ./backend/scripts
RUN pip install -e ./backend
COPY --from=frontend /build/dist ./frontend-enterprise/dist
# alembic 迁移目录
COPY backend/alembic.ini backend/alembic ./backend/
EXPOSE 5173
ENV STAFFDECK_SERVER_MODE=1
CMD ["python", "-m", "uvicorn", "backend.server_app:app", "--host", "0.0.0.0", "--port", "5173"]
```

（以实际 `pyproject.toml` 的包发现配置为准调整 COPY 路径；若 backend 以 flat 布局打包需带 `alembic/`。）

- [ ] **Step 2: Compose 文件**

```yaml
# packaging/docker/docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: staffdeck
      POSTGRES_USER: staffdeck
      POSTGRES_PASSWORD: ${STAFFDECK_PG_PASSWORD:?}
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U staffdeck"]
      interval: 5s
      retries: 12

  redis:
    image: redis:7
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 12

  api:
    build: { context: ../.., dockerfile: packaging/docker/Dockerfile.server }
    environment: &app_env
      STAFFDECK_DATABASE_URL: postgresql+psycopg://staffdeck:${STAFFDECK_PG_PASSWORD}@postgres:5432/staffdeck
      STAFFDECK_REDIS_URL: redis://redis:6379/0
      STAFFDECK_APP_SECRET: ${STAFFDECK_APP_SECRET:?}
      STAFFDECK_PROCESS_ROLE: api
      STAFFDECK_RUN_MIGRATIONS: "1"
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    # api 角色额外执行 alembic upgrade head（entrypoint 判断 STAFFDECK_RUN_MIGRATIONS）

  worker:
    build: { context: ../.., dockerfile: packaging/docker/Dockerfile.server }
    environment:
      <<: *app_env
      STAFFDECK_PROCESS_ROLE: worker
    depends_on:
      api: { condition: service_started }

  connector:
    build: { context: ../.., dockerfile: packaging/docker/Dockerfile.server }
    environment:
      <<: *app_env
      STAFFDECK_PROCESS_ROLE: connector
    depends_on:
      api: { condition: service_started }

  nginx:
    image: nginx:1.27
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    ports: ["5173:80"]
    depends_on: [api]

volumes:
  pgdata:
```

- [ ] **Step 3: Nginx 与入口脚本**

```nginx
# packaging/docker/nginx.conf
server {
  listen 80;
  location / {
    proxy_pass http://api:5173;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_read_timeout 3600s;   # SSE 长连接
    proxy_buffering off;        # SSE 必须关闭缓冲
  }
}
```

`packaging/docker/entrypoint.sh`（api 容器迁移执行）：

```bash
#!/bin/sh
set -e
if [ "${STAFFDECK_RUN_MIGRATIONS}" = "1" ]; then
  python -m app.db.migrations_cli upgrade   # Task 3 的 run_migrations 的 CLI 包装
fi
exec python -m uvicorn backend.server_app:app --host 0.0.0.0 --port 5173
```

配套在 `backend/app/db/migrations_cli.py` 新增：

```python
import os

from app.db.migrations import run_migrations

if __name__ == "__main__":
    url = os.environ["STAFFDECK_DATABASE_URL"]
    run_migrations(url)
    print("migrations applied")
```

Dockerfile CMD 改为 `CMD ["bash", "packaging/docker/entrypoint.sh"]`（worker/connector 镜像同一 CMD，但 `STAFFDECK_RUN_MIGRATIONS` 不设即跳过迁移；entrypoint 里 `exec` 前仅 api 角色跑迁移）。

```bash
chmod +x packaging/docker/entrypoint.sh
```

- [ ] **Step 4: env 模板与本地验证**

```
# packaging/docker/.env.example
STAFFDECK_PG_PASSWORD=change-me-strong
STAFFDECK_APP_SECRET=change-me-long-random
```

```bash
cd packaging/docker && cp .env.example .env && docker compose up -d --build
curl http://127.0.0.1:5173/api/health          # {"status":"ok",...}
curl -s http://127.0.0.1:5173/api/v1/openapi.json | head -c 120
docker compose ps                              # api/worker/connector/postgres/redis/nginx 全 healthy/running
docker compose down
```

Expected: 上述断言全部成立（前端 dist 未构建时 nginx 仍代理到 api，`/workspace` 返回 SPA fallback 提示——以 `static_serving.mount_frontend` 的 dist 存在性分支为准）

- [ ] **Step 5: Commit**

```bash
git add packaging/docker
git commit -m "build(server): add Dockerfile, compose stack (api/worker/connector/pg/redis/nginx)"
```

---

### Task 10: 端到端冒烟与部署文档

**Files:**
- Modify: `README.zh.md`、`README.md`（部署章节：Docker Compose 服务端模式）
- Create: `docs/server-deployment.md`（完整部署手册）
- Create: `scripts/compose_smoke.sh`（冒烟脚本）

**Interfaces:**
- Produces: M1 交付标志的可重复验证命令 `scripts/compose_smoke.sh`（exit 0 = 通过）

- [ ] **Step 1: 冒烟脚本**

```bash
#!/usr/bin/env bash
# scripts/compose_smoke.sh — M1 交付标志验证
set -euo pipefail
cd "$(dirname "$0")/../packaging/docker"
docker compose up -d --build --wait
trap "docker compose down" EXIT

curl -fsS http://127.0.0.1:5173/api/health | grep -q '"status":"ok"'
curl -fsS http://127.0.0.1:5173/api/v1/openapi.json | grep -q 'openapi'
# 管理端 SPA 可达（nginx → api → static fallback）
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5173/workspace)
[ "$code" = "200" ] || { echo "workspace returned $code"; exit 1; }
echo "M1 smoke passed"
```

```bash
chmod +x scripts/compose_smoke.sh
```

- [ ] **Step 2: 部署文档 `docs/server-deployment.md`**

内容骨架（正文章节，非占位）：
1. 前置要求（Docker ≥ 24、2C4G 起）
2. 快速开始（cp .env.example .env → 改两个密钥 → docker compose up -d --build）
3. 配置说明表：`STAFFDECK_APP_SECRET`（JWT/渠道凭证根密钥，必改强随机）、`STAFFDECK_PG_PASSWORD`、`STAFFDECK_REDIS_URL`、`STAFFDECK_PUBLIC_API_KEY_PEPPER`（public API 密钥 pepper，必配）、OIDC 五项（可选启用 SSO）
4. 进程角色说明（api/worker/connector 各自职责与日志排查：`docker compose logs <role>`）
5. OIDC 启用步骤（IdP 注册 redirect_uri = `https://<host>/api/auth/oidc/callback`）
6. 从桌面 SQLite 迁移数据（`sqlite3 .dump` → PG 导入的受限路径说明：仅核心业务表，渠道凭证表需重新绑定；标注为"尽力而为"，正式迁移工具属后续里程碑）
7. 升级流程（pull 镜像 → `docker compose up -d`；api 容器自动跑 Alembic 迁移）
8. 安全清单（HTTPS 反代、APP_SECRET 强随机、公网不开 5432/6379）

- [ ] **Step 3: README 部署章节**

`README.zh.md` 在"客户端下载"之后新增二级标题 `## 服务端部署（Docker Compose）`，内容：四行命令 + 指向 `docs/server-deployment.md`；`README.md` 同步英文版。

- [ ] **Step 4: 全量验证与提交**

```bash
bash scripts/compose_smoke.sh
backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/ruff check backend
git add README.md README.zh.md docs/server-deployment.md scripts/compose_smoke.sh
git commit -m "docs(server): add compose deployment guide and M1 smoke script"
```

Expected: 冒烟 exit 0；pytest 全绿；ruff 干净

---

## Self-Review 记录

- **Spec 覆盖**：spec §5 表格逐项落位——PG+Alembic（Task 1/2/3）、实例锁→Redis 租约（Task 5/6）、三类进程（Task 4/6）、server_app.py+Compose（Task 4/9）、租户推导收紧（Task 7）、OIDC（Task 8）、OpenAPI 单一来源（维持现状：新增端点仍进 public_api 子应用，本计划不触碰）。§12 中"渠道 connector 进程化拆分"由 Task 4/6 的角色编排承接，durable inbox/outbox 内核不改动。
- **占位符扫描**：无 TBD/TODO；Task 8/9 各有一处"以实际文件为准"的核对指令，属于对既有代码的字段核对而非实现缺口。
- **类型一致性**：`build_engine`（Task 1 定义，Task 3 env.py 消费）、`_role_enables`（Task 4 定义，Task 6 消费）、`acquire_role_lease`（Task 5 定义，Task 6 消费）、`run_migrations`（Task 3 定义，Task 9 migrations_cli 消费）签名一致。
