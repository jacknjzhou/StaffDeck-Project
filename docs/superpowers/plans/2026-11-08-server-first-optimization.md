# StaffDeck 服务端优先优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 StaffDeck 收敛为“服务端部署 + 浏览器访问”的单一交付形态，并关闭代码审查中会阻断生产上线的 P0 缺陷，随后补齐并发、认证与运营基线。

**Architecture:** 沿用现有 FastAPI 单体 + `PROCESS_ROLE` 拆分（api/worker/connector）+ PostgreSQL/Redis；不重写业务模块。改动集中在启动与配置边界（`config.py`、`main.py`、`seed.py`、迁移入口）、外部执行边界（MCP stdio）、流式边界（SSE）与部署资产（Compose/CI）。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel + SQLAlchemy 2 + Alembic + PostgreSQL 16 + Redis 7 + Docker Compose + Nginx + React 18 + TypeScript + Vite + TanStack Query

**Spec:** `docs/superpowers/specs/2026-11-08-server-first-platform.md`

## Global Constraints

- 遵循 `AGENTS.md`：Conventional Commits（`feat(scope): ...` / `fix: ...`）；Python 4 空格、类型注解、100 字符行宽；前端 2 空格、单引号、分号；每个任务独立 commit。
- 服务端是唯一生产形态；不得新增桌面/单机专属分支。SQLite 只允许出现在开发与测试路径，所有 SQLite 专属逻辑必须按 `database_url.startswith("sqlite")` 条件化。
- 不引入重量级框架：认证继续手写 JWT；限速与租约用 Redis 原语或进程内实现；前端只新增 `@tanstack/react-query` 一个运行时依赖。
- 不改数据库表结构而不写迁移：任何模型变更都必须带 Alembic revision，并在 PostgreSQL 上验证 `upgrade`/`downgrade`。
- 所有新端点必须有租户校验与回归测试；错误信息不得回显密钥、SQL 或堆栈。
- 保持现有测试可通过：当前基线为 2,296 passed / 11 个真实失败（迁移顺序）/ 2 个环境失败。任务 4 修完后基线必须全绿。

## Phase A：上线阻断（P0）

### Task 1: 生产配置门禁与 `.env.example`

**Files:**
- Modify: `backend/app/config.py:7-20`（Settings 字段与校验）
- Create: `backend/.env.example`
- Modify: `packaging/docker/.env.example`
- Modify: `packaging/docker/docker-compose.yml`（api 环境变量）
- Test: `backend/tests/test_config_production_guard.py`（新建）

**Interfaces:**
- Produces: `Settings.environment: str = "development"`、`Settings.is_production: bool`（property）
- Produces: `Settings.bootstrap_admin_password: str = ""`、`Settings.seed_demo_data: bool = True`
- Produces: 生产模式下 `APP_SECRET` 非法时构造 `Settings` 抛 `ValueError`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_production_guard.py
import pytest

from app.config import Settings

DEFAULT_SECRET = "change-me-in-development"


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_production_rejects_default_secret():
    """生产环境使用内置默认密钥时必须拒绝启动。"""
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(environment="production", app_secret=DEFAULT_SECRET)


def test_production_rejects_short_secret():
    """生产环境的 APP_SECRET 少于 32 字符时拒绝启动。"""
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(environment="production", app_secret="short-secret")


def test_production_accepts_strong_secret():
    """生产环境使用足够长的随机密钥时可以构造配置。"""
    settings = _settings(environment="production", app_secret="x" * 48)
    assert settings.is_production is True


def test_development_allows_default_secret():
    """开发环境保留默认密钥，避免本地启动被阻断。"""
    settings = _settings(environment="development")
    assert settings.is_production is False
    assert settings.app_secret == DEFAULT_SECRET
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_config_production_guard.py -v`
Expected: FAIL `TypeError: Settings.__init__() got an unexpected keyword argument 'environment'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/config.py` 在 `app_secret` 之后加入字段，并在类末尾加入校验：

```python
from pydantic import model_validator

    environment: str = "development"
    bootstrap_admin_password: str = ""
    seed_demo_data: bool = True
    mcp_stdio_enabled: bool = False
    mcp_stdio_allowed_commands: str = "npx,uvx,node,python3,uv"
    login_rate_limit_per_minute: int = 10
    max_upload_bytes: int = 50 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if not self.is_production:
            return self
        if self.app_secret == "change-me-in-development" or len(self.app_secret) < 32:
            raise ValueError(
                "APP_SECRET must be set to a random value of at least 32 characters "
                "in production; generate one with `python -c \"import secrets;"
                "print(secrets.token_urlsafe(48))\"`"
            )
        return self
```

新建 `backend/.env.example`（README 与 AGENTS.md 一直引用但缺失）：

```dotenv
# 本地开发配置。复制为 backend/.env 后按需修改；切勿提交真实密钥。
APP_SECRET="dev-only-change-me"
ENVIRONMENT="development"
DATABASE_URL="sqlite:///./skill_agent_loop.db"
DEMO_MODEL_BASE_URL="http://localhost:52010/v1"
DEMO_MODEL_NAME="qwen3.6-27b"
DEMO_MODEL_API_KEY=""
REDIS_URL=""
```

`packaging/docker/.env.example` 增加：

```dotenv
STAFFDECK_ENVIRONMENT=production
# 生成：python -c "import secrets; print(secrets.token_urlsafe(48))"
STAFFDECK_BOOTSTRAP_ADMIN_PASSWORD=
```

`docker-compose.yml` 的 `api` 环境加入 `ENVIRONMENT: ${STAFFDECK_ENVIRONMENT:-production}` 与 `BOOTSTRAP_ADMIN_PASSWORD: ${STAFFDECK_BOOTSTRAP_ADMIN_PASSWORD:-}`。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_config_production_guard.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/.env.example packaging/docker/.env.example packaging/docker/docker-compose.yml backend/tests/test_config_production_guard.py
git commit -m "feat(config): fail fast on default APP_SECRET in production"
```

---

### Task 2: 引导管理员与演示数据开关

**Files:**
- Modify: `backend/app/db/seed.py:917-965`
- Modify: `backend/app/main.py:188-193`
- Test: `backend/tests/test_bootstrap_admin.py`（新建）
- Modify: `backend/tests/test_staffdeck_seed.py`（断言默认口令消失）

**Interfaces:**
- Produces: `ensure_bootstrap_admin(session: Session) -> str | None`（返回本次生成的明文密码，已存在则返回 `None`）
- Produces: `seed_demo_data(session: Session) -> str | None`（透传引导密码）
- Consumes: `Settings.bootstrap_admin_password`、`Settings.seed_demo_data`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_bootstrap_admin.py
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import ensure_bootstrap_admin
from app.security.auth import verify_password


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_bootstrap_admin_uses_random_password(monkeypatch):
    """空库引导必须生成随机密码，不能再是 admin。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    password = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    assert password and password != "admin"
    assert verify_password(password, admin.password_hash)
    assert admin.role == "admin"


def test_bootstrap_admin_is_idempotent(monkeypatch):
    """第二次启动不得重置已存在管理员的密码。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    first = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    before = admin.password_hash
    assert ensure_bootstrap_admin(session) is None
    session.refresh(admin)
    assert admin.password_hash == before
    assert verify_password(first, before)


def test_bootstrap_admin_rotates_legacy_default(monkeypatch):
    """已有 admin/admin 的旧库在启动时被轮换，避免默认口令继续可用。"""
    monkeypatch.setattr("app.db.seed.get_settings", lambda: type("S", (), {
        "bootstrap_admin_password": "", "seed_demo_data": False, "is_production": True,
    })())
    session = _session()
    from app.security.auth import hash_password
    session.add(User(id="admin", tenant_id="tenant_demo", username="admin",
                     display_name="Administrator", role="admin",
                     password_hash=hash_password("admin")))
    session.commit()
    password = ensure_bootstrap_admin(session)
    admin = session.exec(select(User).where(User.username == "admin")).one()
    assert password and password != "admin"
    assert not verify_password("admin", admin.password_hash)
    assert verify_password(password, admin.password_hash)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_bootstrap_admin.py -v`
Expected: FAIL `ImportError: cannot import name 'ensure_bootstrap_admin'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/db/seed.py` 新增（放在 `seed_demo_data` 之前）：

```python
import secrets


def ensure_bootstrap_admin(session: Session) -> str | None:
    """确保存在租户管理员；空库生成随机口令，旧库默认口令就地轮换。"""
    settings = get_settings()
    admin = session.exec(
        select(User).where(User.tenant_id == "tenant_demo", User.username == "admin")
    ).first()
    if admin is None:
        password = settings.bootstrap_admin_password or secrets.token_urlsafe(18)
        session.add(User(id="admin", tenant_id="tenant_demo", username="admin",
                         display_name="Administrator", role="admin",
                         password_hash=hash_password(password)))
        session.commit()
        return password
    if admin.role != "admin":
        admin.role = "admin"
        admin.updated_at = utc_now()
        session.add(admin)
        session.commit()
    if settings.is_production and verify_password("admin", admin.password_hash):
        password = settings.bootstrap_admin_password or secrets.token_urlsafe(18)
        admin.password_hash = hash_password(password)
        admin.updated_at = utc_now()
        session.add(admin)
        session.commit()
        return password
    return None
```

从 `seed_demo_data` 的 admin 分支改为 `generated = ensure_bootstrap_admin(session)`，函数末尾 `return generated`（签名改为 `-> str | None`）。

`backend/app/main.py` 的 `_startup_by_role`：

```python
    if _role_enables("api"):
        with Session(engine) as db:
            if settings.seed_demo_data:
                generated = seed_demo_data(db)
            else:
                generated = ensure_bootstrap_admin(db)
        if generated:
            logger.warning(
                "bootstrap admin password generated; change it after first login: %s",
                generated,
            )
        recover_codex_a2a_tasks()
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_bootstrap_admin.py backend/tests/test_staffdeck_seed.py -v`
Expected: 全部 passed；原 `admin/admin` 断言改为“存在 admin 且口令非 admin”。

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/seed.py backend/app/main.py backend/tests/test_bootstrap_admin.py backend/tests/test_staffdeck_seed.py
git commit -m "feat(auth): bootstrap admin with random password and gate demo seed"
```

---

### Task 3: MCP stdio 执行策略

**Files:**
- Modify: `backend/app/config.py`（`mcp_stdio_enabled` / 白名单，Task 1 已加字段）
- Modify: `backend/app/tools/tool_schema.py:174-200`
- Modify: `backend/app/tools/mcp_client.py:470-495`
- Modify: `backend/app/api/tools.py:961-1005`
- Test: `backend/tests/test_mcp_stdio_policy.py`（新建）

**Interfaces:**
- Produces: `MCPServerConnection.allowed_stdio_command() -> bool`
- Produces: `build_stdio_env(config_env: dict[str, str]) -> dict[str, str]`（`app.tools.mcp_client` 导出）
- Consumes: `Settings.mcp_stdio_enabled`、`Settings.mcp_stdio_allowed_commands`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mcp_stdio_policy.py
import pytest

from app.tools.mcp_client import build_stdio_env
from app.tools.tool_schema import MCPServerConnection


def _enabled_settings(monkeypatch):
    """测试里绕过全局配置缓存，显式开启 stdio 与白名单。"""
    monkeypatch.setattr("app.tools.tool_schema.get_settings", lambda: type("S", (), {
        "mcp_stdio_enabled": True,
        "mcp_stdio_allowed_commands": "npx,uvx,node,python3,uv",
    })())


def test_stdio_rejects_non_allowlisted_command(monkeypatch):
    """未在白名单中的可执行文件必须被拒绝，避免任意命令执行。"""
    _enabled_settings(monkeypatch)
    with pytest.raises(ValueError, match="allowlist"):
        MCPServerConnection(transport="stdio", command="/bin/sh", args=["-c", "id"])


def test_stdio_accepts_npx(monkeypatch):
    """白名单内启动器可以通过校验。"""
    _enabled_settings(monkeypatch)
    conn = MCPServerConnection(transport="stdio", command="npx", args=["-y", "pkg"])
    assert conn.command == "npx"


def test_stdio_env_is_allowlisted(monkeypatch):
    """子进程环境变量只保留基础键与显式配置，不能继承云凭证。"""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_stdio_env({"MCP_TOKEN": "ok"})
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["MCP_TOKEN"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mcp_stdio_policy.py -v`
Expected: FAIL —— 当前 `/bin/sh` 被接受；`build_stdio_env` 不存在。

- [ ] **Step 3: Write minimal implementation**

`backend/app/tools/tool_schema.py` 在 `MCPServerConnection` 上加校验：

```python
from pydantic import model_validator

from app.config import get_settings

    @model_validator(mode="after")
    def _validate_stdio_command(self) -> "MCPServerConnection":
        if self.transport != "stdio":
            return self
        settings = get_settings()
        if not settings.mcp_stdio_enabled:
            raise ValueError("stdio MCP transport is disabled on this deployment")
        allowed = {c.strip() for c in settings.mcp_stdio_allowed_commands.split(",") if c.strip()}
        if Path(self.command or "").name not in allowed:
            raise ValueError(f"stdio command not in allowlist: {self.command!r}")
        return self
```

`backend/app/tools/mcp_client.py`：

```python
_SAFE_ENV_KEYS = (
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
    "SYSTEMROOT", "USERPROFILE", "COMSPEC",
)


def build_stdio_env(config_env: dict[str, str] | None = None) -> dict[str, str]:
    """构造 stdio 子进程环境：只继承基础变量，其余由显式配置提供。"""
    env = {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}
    env.update(config_env or {})
    return env
```

把 `env = os.environ.copy()` 改为 `env = build_stdio_env(self.config.env)`。

`backend/app/api/tools.py` 的 `create_mcp_server` 开头追加：

```python
    if request.connection.transport == "stdio" and not get_settings().mcp_stdio_enabled:
        raise HTTPException(status_code=403, detail="stdio MCP 未在本部署启用")
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mcp_stdio_policy.py backend/tests/test_mcp_servers_api.py backend/tests/test_mcp_client_stdio.py -v`
Expected: passed；原有用例需补 `mcp_stdio_enabled=True` 的 fixture。

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/tool_schema.py backend/app/tools/mcp_client.py backend/app/api/tools.py backend/tests/test_mcp_stdio_policy.py
git commit -m "feat(tools): restrict stdio MCP commands and sanitize child env"
```

---

### Task 4: 迁移顺序修复与 PostgreSQL 增量迁移

**Files:**
- Modify: `backend/app/db/database.py:1270-1290`（`_migrate_wechat_kf_accounts`）
- Create: `backend/alembic/versions/0002_channel_binding_team_id.py`
- Modify: `backend/app/db/database.py`（启动时 PG schema 校验）
- Test: `backend/tests/test_wechat_kf_migration_order.py`（新建）

**Interfaces:**
- Produces: `_table_columns(conn, table: str) -> set[str]`（`app.db.database` 内部工具）
- Produces: `assert_schema_current(engine) -> None`（生产模式下 `alembic_version` 缺失直接抛错）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_wechat_kf_migration_order.py
from sqlalchemy import create_engine, text

from app.db.database import _migrate_wechat_kf_accounts


def _legacy_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE channel_bindings (id VARCHAR PRIMARY KEY, tenant_id VARCHAR, "
            "agent_id VARCHAR, channel VARCHAR, config_json TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO channel_bindings (id, tenant_id, agent_id, channel, config_json) "
            "VALUES ('cb1', 't1', 'a1', 'wechat_kf', '{\"open_kfid\": \"kf1\"}')"
        ))
    return engine


def test_migration_skips_when_team_id_missing():
    """旧库缺少 team_id 列时迁移必须跳过而不是抛 no such column。"""
    engine = _legacy_engine()
    with engine.begin() as conn:
        _migrate_wechat_kf_accounts(conn, {"channel_bindings"})  # 不抛异常即通过
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM wechat_kf_accounts")).scalar()
    assert count == 0


def test_migration_backfills_when_team_id_present():
    """team_id 存在时正常回填 wechat_kf_accounts。"""
    engine = _legacy_engine()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE channel_bindings ADD COLUMN team_id VARCHAR"))
    with engine.begin() as conn:
        _migrate_wechat_kf_accounts(conn, {"channel_bindings"})
        row = conn.execute(text(
            "SELECT binding_id, open_kfid FROM wechat_kf_accounts"
        )).mappings().one()
    assert row["binding_id"] == "cb1" and row["open_kfid"] == "kf1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_wechat_kf_migration_order.py -v`
Expected: FAIL `OperationalError: no such column: team_id`

- [ ] **Step 3: Write minimal implementation**

`backend/app/db/database.py` 新增工具并在 `_migrate_wechat_kf_accounts` 顶部加守卫：

```python
def _table_columns(conn: Connection, table: str) -> set[str]:
    """读取当前连接可见的列名，避免 inspector 缓存导致迁移顺序误判。"""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
    return {row["name"] for row in rows}


def _migrate_wechat_kf_accounts(conn: Connection, tables: set[str]) -> None:
    ...
    if "channel_bindings" in tables:
        columns = _table_columns(conn, "channel_bindings")
        if "team_id" not in columns:
            return
        rows = conn.execute(...)
```

新建 `backend/alembic/versions/0002_channel_binding_team_id.py`：

```python
"""add team_id to channel_bindings

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 baseline 已包含 team_id，全新库上必须跳过，否则 add_column 重复报错。
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("channel_bindings")}
    if "team_id" not in columns:
        op.add_column("channel_bindings", sa.Column("team_id", sa.String(), nullable=True))
        op.create_index("ix_channel_bindings_team_id", "channel_bindings", ["team_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("channel_bindings")}
    if "team_id" in columns:
        op.drop_index("ix_channel_bindings_team_id", table_name="channel_bindings")
        op.drop_column("channel_bindings", "team_id")
```

> 生成方式应为 `cd backend && alembic revision --autogenerate -m "channel_binding_team_id"`，再人工校对：只保留 `channel_bindings.team_id`，删除 autogenerate 顺带产生的无关差异。

`init_db()` 在非 SQLite 且生产模式时校验版本：

```python
def assert_schema_current(engine: Engine) -> None:
    """生产库必须由 Alembic 管理；缺失版本表说明部署方式错误。"""
    with engine.connect() as conn:
        has_table = inspect(conn).has_table("alembic_version")
    if not has_table:
        raise RuntimeError("database is not managed by Alembic; run migrations first")
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_wechat_kf_migration_order.py backend/tests/test_channel_scope.py backend/tests/test_feishu_schema_migration.py backend/tests/test_feishu_durable_inbox.py backend/tests/test_channel_team_binding.py -v`
Expected: 全绿（这是当前 11 个失败用例的收口点）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/database.py backend/alembic/versions/0002_channel_binding_team_id.py backend/tests/test_wechat_kf_migration_order.py
git commit -m "fix(db): guard wechat_kf migration on missing team_id and add 0002 revision"
```

## Phase B：并发与可靠（P1）

### Task 5: SSE 中继异步化

**Files:**
- Modify: `backend/app/api/chat.py:100`（常量）、`:1151`（路由）、`:1480-1580`（`stream_events`）
- Test: `backend/tests/test_chat_stream_async.py`（新建）

**Interfaces:**
- Produces: 模块级 `async def stream_events(...) -> AsyncIterator[str]`（从 `chat_stream` 内嵌闭包提取）
- Produces: 同步辅助 `_read_relay_batch(...) -> list[dict]`（在线程池中执行事件表读取）
- 保持 `chat_stream(request, current_user, db) -> StreamingResponse` 为同步 `def`：`StreamingResponse` 接受异步生成器，阻塞点已移出路由。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chat_stream_async.py
import inspect

from app.api import chat


def test_stream_events_is_module_level_async_generator():
    """中继必须是模块级异步生成器，不能在事件循环里 time.sleep。"""
    assert hasattr(chat, "stream_events")
    assert inspect.isasyncgenfunction(chat.stream_events)


def test_relay_source_has_no_blocking_sleep():
    """chat.py 中继路径不得再出现 time.sleep(0.08) 式的阻塞轮询。"""
    import pathlib

    source = pathlib.Path(chat.__file__).read_text(encoding="utf-8")
    assert "time.sleep(STREAM_RELAY_POLL_SECONDS)" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_chat_stream_async.py -v`
Expected: FAIL —— 两者当前都是同步函数。

- [ ] **Step 3: Write minimal implementation**

`backend/app/api/chat.py`：把 `chat_stream` 内部 `def stream_events()`（约 :1485）整体提到模块级：

```python
import asyncio
from starlette.concurrency import run_in_threadpool

async def stream_events(
    *,
    run_id: str,
    tenant_id: str,
    session_id_ref: dict[str, str],
    initial_cursor: int | None,
    relay_ready: threading.Event,
    worker_done: threading.Event,
    worker_terminal: dict[str, bool],
) -> AsyncIterator[str]:
    """按游标轮询事件表，把新增事件转成 SSE 文本输出。"""
    if not await asyncio.to_thread(relay_ready.wait, 15):
        return
    cursor = initial_cursor
    while True:
        # 同步 SQLAlchemy 读取放线程池，事件循环只负责等待与写流。
        batch, cursor = await run_in_threadpool(_read_relay_batch, run_id, tenant_id, cursor)
        for frame in batch:
            yield frame
        if batch:
            continue
        if _idle_expired(...):
            return
        await asyncio.sleep(STREAM_RELAY_POLL_SECONDS)
```

把原闭包里的同步读取块抽成 `_read_relay_batch(run_id, tenant_id, cursor) -> tuple[list[str], int | None]`（内部 `with Session(engine)`）；原 `nonlocal initial_cursor` 改为局部变量；`worker_done` / `worker_terminal` / `session_id_ref` 作为参数传入，避免闭包跨线程读可变状态时语义变化。

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_chat_stream_async.py -v && backend/.venv/bin/python -m pytest backend/tests -k "stream or chat" -q`
Expected: passed；连续 3 次运行无 flake。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chat.py backend/tests/test_chat_stream_async.py
git commit -m "perf(chat): relay SSE without blocking the event loop"
```

---

### Task 6: 租约与跨进程互斥硬化

**Files:**
- Modify: `backend/app/distributed/lease.py:55-90`
- Modify: `backend/app/main.py:110-135`（续期失败处理）
- Modify: `backend/app/core/harness_session_lock.py:1-40`
- Test: `backend/tests/test_lease_required.py`（新建）、`backend/tests/test_harness_session_lock_redis.py`（新建）

**Interfaces:**
- Produces: `acquire_role_lease(role, ttl_seconds=60)` 在生产且无 Redis 时抛 `RuntimeError`
- Produces: `LeaseHeartbeat` 暴露 `lost: threading.Event`
- Produces: `HarnessSessionLock(backend: str)` 支持 `redis` / `memory`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lease_required.py
import pytest

from app.distributed import lease


def test_production_requires_redis(monkeypatch):
    """生产模式没有 Redis 时不能静默降级为无锁。"""
    monkeypatch.setattr(lease, "get_settings", lambda: type("S", (), {
        "redis_url": "", "is_production": True,
    })())
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        lease.acquire_role_lease("api")


def test_development_allows_missing_redis(monkeypatch):
    """开发模式仍允许无 Redis 单进程运行。"""
    monkeypatch.setattr(lease, "get_settings", lambda: type("S", (), {
        "redis_url": "", "is_production": False,
    })())
    assert lease.acquire_role_lease("api") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_lease_required.py -v`
Expected: FAIL —— 当前返回 `None`。

- [ ] **Step 3: Write minimal implementation**

`backend/app/distributed/lease.py`：

```python
def acquire_role_lease(role: str, ttl_seconds: int = 60) -> RedisLease | None:
    settings = get_settings()
    url = settings.redis_url
    if not url:
        if settings.is_production:
            raise RuntimeError(
                "REDIS_URL is required in production: role leases prevent duplicate "
                "api/worker/connector processes"
            )
        return None
```

`backend/app/main.py` 续期线程：连续 3 次续期失败时 `lease_lost.set()`，并把进程标记为 not-ready（`/api/ready` 读取同一事件），不再只 `logger.error`。

`backend/app/core/harness_session_lock.py`：把模块级 `dict` 换成 `HarnessSessionLock` 类，`redis_url` 存在时用 `SET key token NX PX ttl` + Lua 比较删除，否则退回进程内字典；保持现有函数签名 `try_acquire(session_id)` / `release(session_id)` 不变，避免调用点改动。

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_lease_required.py backend/tests/test_distributed_lease.py backend/tests/test_role_lease_wiring.py backend/tests/test_harness_session_lock_redis.py -v`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/distributed/lease.py backend/app/main.py backend/app/core/harness_session_lock.py backend/tests/test_lease_required.py backend/tests/test_harness_session_lock_redis.py
git commit -m "fix(distributed): require redis leases in production and share session locks"
```

---

### Task 7: 认证与输入边界加固

**Files:**
- Modify: `backend/app/api/auth.py:132-160`（登录限速）、`:679-715`（OIDC 租户绑定）
- Modify: `backend/app/api/chat.py:1035-1075`（上传预检）
- Test: `backend/tests/test_auth_hardening.py`（新建）

**Interfaces:**
- Produces: `_rate_limit_login(identity: str) -> None`（超限抛 429）
- Produces: OIDC 用户匹配条件为 `(tenant_id, username)`，租户来自 `Settings.oidc_tenant_id`
- Produces: 上传在 `file.read()` 前校验 `Content-Length`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_auth_hardening.py
from app.api.auth import _login_identity_key
from app.api.chat import _reject_oversized_upload


def test_login_identity_key_is_per_ip_and_user():
    """限速键同时包含来源与用户名，避免只按 IP 被 NAT 放大或只按用户被绕过。"""
    key = _login_identity_key("10.0.0.7", "admin")
    assert key == "login:10.0.0.7:admin"


def test_oversized_content_length_is_rejected_before_read():
    """声明长度超限时直接 413，不读取 body。"""
    import pytest
    with pytest.raises(Exception) as exc:
        _reject_oversized_upload(10, limit=5)
    assert getattr(exc.value, "status_code", None) == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_auth_hardening.py -v`
Expected: FAIL `ImportError`

- [ ] **Step 3: Write minimal implementation**

`backend/app/api/auth.py` 登录入口：

```python
def _login_identity_key(host: str, username: str) -> str:
    return f"login:{host}:{username}"


def _rate_limit_login(identity: str) -> None:
    """滑动窗口限速；Redis 可用时跨进程共享，否则退化为进程内计数。"""
    ...
```

在 `login()` 首次校验密码前调用 `_rate_limit_login(_login_identity_key(request.client.host, request.username))`，超限 `HTTPException(429, "too many login attempts")`。

OIDC 回调：

```python
    tenant_id = settings.oidc_tenant_id or "tenant_demo"
    user = db.exec(
        select(User).where(User.tenant_id == tenant_id, User.username == email)
    ).first()
```

新建用户时使用 `tenant_id=tenant_id`；若同一邮箱在多个租户存在，返回 409 并提示管理员先绑定，禁止静默选一个。

`backend/app/api/chat.py`：

```python
def _reject_oversized_upload(content_length: int | None, limit: int) -> None:
    if content_length is not None and content_length > limit:
        raise HTTPException(status_code=413, detail="请求体超过上传大小限制")
```

路由签名加 `request: Request`，在循环前 `_reject_oversized_upload(request.headers.get("content-length") and int(...), limit=MAX_CHAT_ATTACHMENT_BYTES * MAX_CHAT_ATTACHMENTS + 1024 * 1024)`。

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_auth_hardening.py backend/tests/test_oidc_login.py backend/tests/test_chat_attachments.py -v`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/app/api/chat.py backend/tests/test_auth_hardening.py
git commit -m "fix(auth): tenant-bound OIDC, login rate limit, and upload precheck"
```

---

### Task 8: LLM 客户端生命周期

**Files:**
- Modify: `backend/app/llm/client.py`（`close()` / `__enter__` / `__exit__`）
- Modify: 调用点（`grep -rn "LLMClient(" backend/app`）
- Test: `backend/tests/test_llm_client_lifecycle.py`（新建）

**Interfaces:**
- Produces: `LLMClient.close() -> None`；支持 `with LLMClient(...) as client:`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_llm_client_lifecycle.py
from app.llm.client import LLMClient


def test_client_closes_transport():
    """close() 必须释放底层 SDK 客户端，避免长跑进程句柄泄漏。"""
    class Stub:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = object.__new__(LLMClient)
    client.client = Stub()
    client.close()
    assert client.client.closed is True


def test_client_is_context_manager():
    """LLMClient 支持 with，确保异常路径也会关闭连接。"""
    class Stub:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = object.__new__(LLMClient)
    client.client = Stub()
    with client as entered:
        assert entered is client
    assert client.client.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_llm_client_lifecycle.py -v`
Expected: FAIL `TypeError: 'LLMClient' object does not support the context manager protocol`

- [ ] **Step 3: Write minimal implementation**

```python
    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
```

把所有 `LLMClient(...)` 调用点改为 `with LLMClient(...) as client:`（流式生成器需在 `finally` 中关闭，不能跨 `yield` 依赖 `with` 退出时机）。

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_llm_client_lifecycle.py backend/tests -k "llm or model" -q`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/client.py backend/tests/test_llm_client_lifecycle.py
git commit -m "fix(llm): close HTTP clients deterministically"
```

## Phase C：运营与工程基线

### Task 9: CI 门禁与测试隔离

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/ci_local.sh`
- Modify: `backend/pyproject.toml`（ruff 配置、pytest-timeout）
- Modify: `backend/tests/conftest.py`（隔离 `HOME`）

- [ ] **Step 1: Write the failing check**

`scripts/ci_local.sh` 先写成会失败的版本：

```bash
#!/usr/bin/env bash
set -euo pipefail
backend/.venv/bin/ruff check backend
backend/.venv/bin/python -m pytest backend/tests -q --timeout=120
npm --prefix frontend-enterprise run build
npm --prefix frontend-enterprise test -- --run
```

Run: `bash scripts/ci_local.sh`
Expected: FAIL（pytest 不认识 `--timeout`；ruff 报 2,062 条）。

- [ ] **Step 2: 固定 lint 规则并安装超时插件**

`backend/pyproject.toml`：

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "BLE", "RUF"]
ignore = ["B008", "BLE001", "UP045", "E501"]

[tool.pytest.ini_options]
timeout = 120
```

`[project.optional-dependencies].dev` 增加 `pytest-timeout`，执行 `backend/.venv/bin/python -m pip install -e "backend[dev]"`。

- [ ] **Step 3: 隔离测试环境**

`backend/tests/conftest.py` 增加 session 级 fixture，把 `HOME`、`XDG_*` 指到 `tmp_path_factory` 目录，避免测试写 `~/Library/Application Support`：

```python
@pytest.fixture(scope="session", autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    yield home
```

- [ ] **Step 4: 写 CI 工作流并本地验证**

`.github/workflows/ci.yml`：两个 job（backend / frontend），Python 3.11 + Node 20，缓存 pip 与 npm，执行 `ruff check`、`pytest -q --timeout=120`、`npm ci`、`npm run build`、`npm test -- --run`。PostgreSQL 集成用例通过 service 容器提供 `STAFFDECK_TEST_PG_URL`。

Run: `bash scripts/ci_local.sh`
Expected: exit 0；若仍有历史 ruff 违规，逐条修复或加入带注释的 `per-file-ignores`，不得整体 `ignore`。

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml scripts/ci_local.sh backend/pyproject.toml backend/tests/conftest.py
git commit -m "ci: add backend and frontend gates with pinned ruff rules"
```

---

### Task 10: 运维基线（就绪探针、非 root、备份）

**Files:**
- Modify: `backend/app/main.py:230-240`（`/api/ready`）
- Modify: `packaging/docker/Dockerfile.server`（`USER`）
- Modify: `packaging/docker/docker-compose.yml`（healthcheck 指向 `/api/ready`）
- Create: `scripts/backup_db.sh`、`docs/backup-restore.md`
- Test: `backend/tests/test_ready_endpoint.py`（新建）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ready_endpoint.py
from fastapi.testclient import TestClient


def test_ready_reports_database_failure(monkeypatch):
    """数据库不可用时 readiness 必须 503，而不是恒返回 ok。"""
    from app import main
    monkeypatch.setattr(main, "_check_database", lambda: False)
    client = TestClient(main.app)
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["database"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_ready_endpoint.py -v`
Expected: FAIL 404

- [ ] **Step 3: Write minimal implementation**

```python
def _check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/api/ready")
def ready() -> JSONResponse:
    db_ok = _check_database()
    payload = {"status": "ok" if db_ok else "degraded", "database": db_ok}
    return JSONResponse(payload, status_code=200 if db_ok else 503)
```

`Dockerfile.server`：创建 `app` 用户并 `USER app`，确保 `/app` 与数据目录可写（`chown -R app:app /app`）。
`docker-compose.yml` 的 healthcheck 改为 `curl -fsS http://localhost:80/api/ready`。
`scripts/backup_db.sh`：`docker compose exec -T postgres pg_dump -U staffdeck staffdeck | gzip > backups/staffdeck-$(date +%F-%H%M).sql.gz`，保留 14 天；`docs/backup-restore.md` 写清恢复演练步骤。

- [ ] **Step 4: Run tests + 冒烟**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_ready_endpoint.py -v && bash scripts/compose_smoke.sh --rebuild`
Expected: passed；`docker compose ps` 中 api 显示 healthy，容器内 `id -u` 非 0。

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py packaging/docker/Dockerfile.server packaging/docker/docker-compose.yml scripts/backup_db.sh docs/backup-restore.md backend/tests/test_ready_endpoint.py
git commit -m "feat(ops): add readiness probe, non-root image, and backup script"
```

---

### Task 11: 前端数据层与路由拆包

**Files:**
- Modify: `frontend-enterprise/package.json`
- Modify: `frontend-enterprise/src/main.tsx`（`QueryClientProvider`）
- Modify: `frontend-enterprise/src/App.tsx:316,495`（改用 `useQuery`）
- Modify: `frontend-enterprise/src/App.tsx`（路由 `React.lazy`）
- Test: `frontend-enterprise/src/api/__tests__/useAgents.test.tsx`（新建）

- [ ] **Step 1: Write the failing test**

```tsx
// frontend-enterprise/src/api/__tests__/useAgents.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useAgents } from '../useAgents';

describe('useAgents', () => {
  it('dedupes requests for the same tenant', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'a1' }],
    });
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => [useAgents('t1'), useAgents('t1')], { wrapper });
    await waitFor(() => expect(result.current[0].data).toHaveLength(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend-enterprise test -- --run src/api/__tests__/useAgents.test.tsx`
Expected: FAIL —— `useAgents` 与 `@tanstack/react-query` 均不存在。

- [ ] **Step 3: 安装依赖并实现查询层**

```bash
npm --prefix frontend-enterprise install @tanstack/react-query
```

新建 `frontend-enterprise/src/api/useAgents.ts`：

```ts
import { useQuery } from '@tanstack/react-query';

import { apiGet } from './client';

export interface AgentSummary { id: string; name?: string }

export function useAgents(tenantId: string) {
  return useQuery({
    queryKey: ['agents', tenantId],
    queryFn: () => apiGet<AgentSummary[]>(`/api/enterprise/agents?tenant_id=${tenantId}`),
    staleTime: 60_000,
  });
}
```

把 `App.tsx` 两处 `fetch('/api/enterprise/agents...')` 替换为 `useAgents(tenantId)`；`main.tsx` 包一层 `QueryClientProvider`（`retry: 1`、`refetchOnWindowFocus: false`）。

路由拆包：把页面组件改为 `const DistillPage = React.lazy(() => import('./pages/DistillPage'))`，`<Suspense fallback={<PageSkeleton />}>` 包裹路由出口。

- [ ] **Step 4: 运行测试与体积验收**

```bash
npm --prefix frontend-enterprise test -- --run
npm --prefix frontend-enterprise run build
```

Expected: 全部通过；构建输出主 chunk gzip < 300KB（当前 677KB），`DistillPage` 等出现在独立 chunk。

- [ ] **Step 5: Commit**

```bash
git add frontend-enterprise/package.json frontend-enterprise/package-lock.json frontend-enterprise/src/main.tsx frontend-enterprise/src/App.tsx frontend-enterprise/src/api/useAgents.ts frontend-enterprise/src/api/__tests__/useAgents.test.tsx
git commit -m "perf(web): add query cache and route-level code splitting"
```

---

### Task 12: 自助改密

**Files:**
- Modify: `backend/app/api/auth.py`（`POST /api/auth/change-password`）
- Modify: `frontend-enterprise/src/pages/AccountsPage.tsx`（账号管理页加“修改密码”）
- Test: `backend/tests/test_change_password.py`（新建）

**Interfaces:**
- Produces: `POST /api/auth/change-password` body `{current_password, new_password}`；成功 204
- Produces: `ChangePasswordRequest`（Pydantic，`new_password` 最少 8 位）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_change_password.py
import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.api.auth import ChangePasswordRequest, change_password
from app.db.models import User
from app.security.auth import hash_password, verify_password


def _user() -> tuple[Session, User]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    user = User(id="u1", tenant_id="t1", username="u", display_name="U",
                password_hash=hash_password("old-pass-123"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user


def test_change_password_requires_current_password():
    """当前口令错误时拒绝修改，避免会话被盗后直接改密。"""
    session, user = _user()
    with pytest.raises(HTTPException) as exc:
        change_password(
            ChangePasswordRequest(current_password="wrong", new_password="new-pass-123"),
            current_user=user,
            db=session,
        )
    assert exc.value.status_code == 403


def test_change_password_updates_hash():
    """修改成功后新口令生效、旧口令失效。"""
    session, user = _user()
    change_password(
        ChangePasswordRequest(current_password="old-pass-123", new_password="new-pass-123"),
        current_user=user,
        db=session,
    )
    session.refresh(user)
    assert verify_password("new-pass-123", user.password_hash)
    assert not verify_password("old-pass-123", user.password_hash)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_change_password.py -v`
Expected: FAIL `ImportError: cannot import name 'ChangePasswordRequest'`

- [ ] **Step 3: Write minimal implementation**

```python
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    current_user.password_hash = hash_password(payload.new_password)
    current_user.updated_at = utc_now()
    db.add(current_user)
    db.commit()
    return Response(status_code=204)
```

前端在账号设置页加“修改密码”表单，成功后提示重新登录（当前 JWT 无吊销，改密不失效旧 token，属已知限制，写入 `docs/server-deployment.md`）。

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_change_password.py backend/tests/test_auth.py -v`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/tests/test_change_password.py frontend-enterprise/src/pages/AccountsPage.tsx
git commit -m "feat(auth): allow users to change their own password"
```

## Review Focus

- **安全**：Task 1–3、7 的边界是否真的不可绕过（默认密钥、stdio 白名单、OIDC 租户、限速键）。
- **数据**：Task 4 的断言是否覆盖“旧库缺列”与“新库有列”两条路径；Alembic 0002 的 `downgrade` 是否可执行。
- **并发**：Task 5 删除 `time.sleep` 后，SSE 的心跳与空闲超时是否仍按 660 秒语义工作；Task 6 的租约丢失是否会阻止新请求。
- **兼容**：Task 11 拆包后，深链接刷新是否仍由 nginx fallback 到 `index.html`。
- **文档一致性**：`docs/server-deployment.md` 的检查清单与代码实际行为逐条对照。

## 后续独立计划（不在本计划范围）

- `chat.py` / `database.py` 拆分与 service 层收口（当前 4,397 / 3,139 行）。
- i18n 运行时 DOM 翻译改为编译期字典；去掉 `MutationObserver`。
- OpenTelemetry metrics/tracing、日志聚合与告警。
- 飞书 connector 迁移到服务端 inbox 表。
- MCP 子进程沙箱（容器/命名空间隔离）与出站网络策略。
- 对象存储与附件生命周期治理。

## 执行方式

计划完成后请选择：

1. **Subagent-Driven（推荐）**：每个任务派一个子代理，任务间做代码评审，适合 12 个任务的并行推进。
2. **Inline Execution**：在当前会话按任务顺序执行，适合你想逐步确认每一处改动。

两种方式都应先做 Task 1–4（上线阻断），再做 Task 9（CI），其余按依赖顺序推进。
