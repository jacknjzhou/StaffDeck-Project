# StaffDeck 员工自助注册与管理员审核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让内部员工在登录页自助提交注册申请，管理员在「账号管理」页审核通过后才能登录；未通过审核的账号无法登录、不可被指派，被停用的已登录用户立即失效。

**Architecture:** 在 `users` 表增加 `status` 列（`active`/`pending`/`rejected`），默认 `active` 以让引导管理员、演示数据、OIDC、渠道懒建四条既有建号路径零改动。新增 `POST /api/auth/register` 与 `PUT /api/auth/users/{user_id}/status` 两个端点；登录与 `get_current_user` 两处做状态收口。前端在登录页加注册入口、账号页加状态列与审核操作。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel + SQLAlchemy 2 + Alembic + PostgreSQL 16 + SQLite（开发/测试）+ React 18 + TypeScript + Vite + Vitest

**Spec:** `docs/superpowers/specs/2026-11-09-employee-self-registration.md`

## Global Constraints

- 遵循 `AGENTS.md`：Conventional Commits（`feat(scope): ...`）；Python 4 空格、类型注解、100 字符行宽；前端 2 空格、单引号、分号；每个任务独立 commit。
- 模型变更必须带迁移：SQLite 走 `_migrate_sqlite_skill_schema()` 补列，PostgreSQL 走 Alembic `0003`；两条路径都必须幂等（重复执行不报错）。
- 所有新端点必须校验租户；错误信息不得回显口令、SQL 或堆栈。
- 状态判断一律用 `getattr(user, "status", "active")`，保证旧库对象（未刷新）不会因缺属性抛 `AttributeError`。
- 新增任何中文文案必须同步写入 `frontend-enterprise/src/i18n/en.json`，否则 `npm run i18n:check` 失败。
- 不改 `backend/app/channels/service_intake.py:750` 的 `source == "web"` 判断（待审核账号不可能已有渠道绑定）。
- 保持现有测试通过：当前基线（`feat/server-first-optimization` @ `f02c161`）为 2457 passed / 6 个环境性失败（`test_general_skills` 嵌套 sandbox EPERM）/ 2 skipped / 2 xfailed；`ruff check backend` 零告警；前端 56 文件 / 265 用例全绿。
- 测试命令（worktree 无 venv，用主仓 venv）：`PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest <路径>`；ruff 用 `/Users/jacky/works/StaffDeck-Project//Users/jacky/works/StaffDeck-Project/backend/.venv/bin/ruff check backend`。

## Phase A：后端（数据与契约）

### Task 1: `users.status` 列与双路径迁移

**Files:**
- Modify: `backend/app/db/models.py:32-51`（`User` 增加 `status` 字段）
- Modify: `backend/app/api/auth.py:231-246`（`UserRead` 增加 `status`）
- Modify: `backend/app/api/auth.py:711-728`（`_user_read` 填充 `status`）
- Modify: `backend/app/db/database.py:244-258`（SQLite users 分支补列与索引）
- Create: `backend/alembic/versions/0003_user_status.py`
- Test: `backend/tests/test_user_status_migration.py`（新建）

**Interfaces:**
- Produces: `User.status: str = Field(default="active", index=True)`
- Produces: `UserRead.status: str = "active"`
- Produces: Alembic revision `"0003"`，`down_revision = "0002"`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_user_status_migration.py
"""users.status 双路径迁移:SQLite 手写补列与 Alembic 0003 都必须幂等。"""

from sqlalchemy import create_engine, text

from app.db import database

LEGACY_USERS_DDL = """
CREATE TABLE users (
    id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    username VARCHAR NOT NULL,
    display_name VARCHAR,
    role VARCHAR NOT NULL DEFAULT 'member',
    source VARCHAR NOT NULL DEFAULT 'web',
    password_hash VARCHAR NOT NULL,
    created_at DATETIME,
    updated_at DATETIME
)
"""


def _legacy_engine(tmp_path):
    """建一个没有 status 列的旧版 SQLite 库,模拟升级前的开发库。

    迁移函数读模块级 `database.engine`/`database.database_url`,因此必须落盘并用
    monkeypatch 指向它(内存库在每次 connect 时都是新库)。
    """
    db_path = tmp_path / "users.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(LEGACY_USERS_DDL))
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, password_hash) "
                "VALUES ('user_old', 'tenant_demo', 'olduser', 'hash')"
            )
        )
    return engine, f"sqlite:///{db_path}"


def test_sqlite_migration_adds_status_column_and_backfills_active(monkeypatch, tmp_path):
    """旧库补列后,既有账号必须全部是 active,不能掉进待审核。"""
    engine, url = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "database_url", url)
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()

    with engine.connect() as conn:
        columns = {row["name"] for row in conn.execute(text("PRAGMA table_info(users)")).mappings()}
        assert "status" in columns
        assert conn.execute(text("SELECT status FROM users WHERE id = 'user_old'")).scalar() == "active"


def test_sqlite_migration_is_idempotent(monkeypatch, tmp_path):
    """重复执行迁移不能报错(启动时每次都会跑一遍)。"""
    engine, url = _legacy_engine(tmp_path)
    monkeypatch.setattr(database, "database_url", url)
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite_skill_schema()
    database._migrate_sqlite_skill_schema()

    with engine.connect() as conn:
        indexes = {row["name"] for row in conn.execute(text("PRAGMA index_list(users)")).mappings()}
        assert "ix_users_status" in indexes
```

同时补一条 Alembic 幂等测试（追加到 `backend/tests/test_alembic_baseline.py` 末尾，复用其 `pg_url` fixture 与 skip 逻辑）：

```python
def test_user_status_revision_round_trips_on_postgres(pg_url: str):
    """0003 可在 PostgreSQL 上降级再升级:回退删列,重升补列,重复升级幂等。"""
    from alembic import command
    from alembic.config import Config

    from app.db.database import build_engine

    eng = build_engine(pg_url)
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    run_migrations(pg_url)

    alembic_dir = Path(__file__).resolve().parents[1] / "alembic"
    cfg = Config(str(alembic_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", pg_url)

    command.downgrade(cfg, "0002")
    with eng.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'users'")
            )
        }
    assert "status" not in columns

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'users'")
            )
        }
    assert "status" in columns
    eng.dispose()
```

> 该文件顶部需要补 `from pathlib import Path`（当前只有 `from sqlmodel import text`）。

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_user_status_migration.py -q`
Expected: FAIL —`assert "status" in columns`（列不存在）。

- [ ] **Step 3: Write minimal implementation**

`backend/app/db/models.py` 的 `User` 在 `source` 之后插入：

```python
    # 账号状态:active=正常;pending=注册待审核;rejected=审核拒绝/已停用。
    # 默认 active,让引导管理员、演示数据、OIDC 与渠道懒建四条路径零改动。
    status: str = Field(default="active", index=True)
```

`backend/app/api/auth.py` 的 `UserRead` 在 `source` 之后插入：

```python
    status: str = "active"
```

`_user_read()` 增加一行 `status=getattr(user, "status", "active"),`（放在 `source=` 之后）。

`backend/app/db/database.py` 的 users 分支，在 `source` 补列之后追加：

```python
            if "status" not in user_columns:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN status VARCHAR NOT NULL DEFAULT 'active'")
                )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_users_status ON users(status)")
            )
```

新建 `backend/alembic/versions/0003_user_status.py`：

```python
"""add status to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09

0001 baseline 的 users 建表不含 status;本迁移为历史库补列与索引,并保持幂等。
downgrade 显式删列:status 是业务语义字段,回退后代码会退回「无状态」版本。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    """读取 users 当前列名,让 upgrade 可重复执行。"""
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns("users")}


def _index_names() -> set[str]:
    """读取 users 当前索引名,避免重复建索引。"""
    inspector = sa.inspect(op.get_bind())
    return {str(index["name"]) for index in inspector.get_indexes("users")}


def upgrade() -> None:
    """补出 status 列(默认 active)与索引;已存在的部分逐个跳过。"""
    if "status" not in _column_names():
        op.add_column(
            "users",
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
        )
    if "ix_users_status" not in _index_names():
        op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    """回退到 0002:先删索引再删列。"""
    if "ix_users_status" in _index_names():
        op.drop_index("ix_users_status", table_name="users")
    if "status" in _column_names():
        op.drop_column("users", "status")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_user_status_migration.py backend/tests/test_alembic_baseline.py -q
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_staffdeck_seed.py backend/tests/test_auth_hardening.py -q
```
Expected: 全绿（Alembic 用例在无 `STAFFDECK_TEST_PG_URL` 时 skip 属正常）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models.py backend/app/api/auth.py backend/app/db/database.py \
  backend/alembic/versions/0003_user_status.py backend/tests/test_user_status_migration.py \
  backend/tests/test_alembic_baseline.py
git commit -m "feat(db): add user status column with sqlite and alembic migrations"
```

### Task 2: 注册端点 `POST /api/auth/register`

**Files:**
- Modify: `backend/app/api/auth.py`（新增请求模型、限速函数、路由）
- Test: `backend/tests/test_registration.py`（新建）

**Interfaces:**
- Produces: `RegisterRequest(tenant_id, username, display_name, password)`
- Produces: `RegisterResponse(status: str, username: str)`
- Produces: `_rate_limit_register(host: str) -> None`
- Produces: `POST /api/auth/register` → 201 `{"status": "pending", "username": ...}`
- Produces: 409 detail 形状 `{"code": <str>, "message": <str>}`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_registration.py
"""自助注册与审核:创建 pending 账号、拒绝重名、登录态收口与管理端审核。"""

import pytest
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.auth as auth_api
from app.config import Settings
from app.db import get_session
from app.db.models import Tenant, User
from app.security.auth import create_access_token, hash_password


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """每个用例前后清空限速窗口,避免跨用例串扰。"""
    auth_api._reset_login_rate_limit_state()
    yield
    auth_api._reset_login_rate_limit_state()


def _client(
    monkeypatch, *, limit: int = 100, users: list[User] | None = None
) -> tuple[TestClient, Engine]:
    """构建只挂载认证路由的测试客户端,并返回 engine 供落库断言。"""
    settings = Settings(
        database_url="sqlite://",
        app_secret="test-secret",
        redis_url="",
        login_rate_limit_per_minute=limit,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        for user in users or []:
            db.add(user)
        db.commit()

    test_app = FastAPI()
    test_app.include_router(auth_api.router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    return TestClient(test_app), engine


def _token(user: User) -> dict[str, str]:
    """生成 Bearer 头,供需要登录态的端点使用。"""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_register_creates_pending_web_account(monkeypatch) -> None:
    """注册成功返回 201,落库账号为 pending、source=web、role=member。"""
    client, engine = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": " zhangsan ",
        "display_name": "张三", "password": "secret123",
    })
    assert response.status_code == 201
    assert response.json() == {"status": "pending", "username": "zhangsan"}
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "zhangsan")).one()
        assert user.status == "pending"
        assert user.source == "web"
        assert user.role == "member"
        assert user.tenant_id == "tenant_demo"
        assert user.password_hash != "secret123"


def test_register_rejects_duplicate_username(monkeypatch) -> None:
    """同名账号已存在时返回 409,并带上可区分的错误码。"""
    existing = User(id="user_a", tenant_id="tenant_demo", username="zhangsan",
                    password_hash=hash_password("secret"), status="pending")
    client, _ = _client(monkeypatch, users=[existing])
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REGISTRATION_PENDING"


def test_register_rejects_unknown_tenant(monkeypatch) -> None:
    """租户不存在时返回 404,不建任何账号。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_missing", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 404


def test_register_rejects_short_password(monkeypatch) -> None:
    """口令少于 8 位返回 422(由 Pydantic 约束兜底)。"""
    client, _ = _client(monkeypatch)
    response = client.post("/api/auth/register", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "short",
    })
    assert response.status_code == 422


def test_register_is_rate_limited(monkeypatch) -> None:
    """同一来源连续注册超过阈值后返回 429。"""
    client, _ = _client(monkeypatch, limit=2)
    payload = {"tenant_id": "tenant_demo", "username": "a1", "password": "secret123"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a2"}).status_code == 201
    assert client.post("/api/auth/register", json={**payload, "username": "a3"}).status_code == 429
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py -q`
Expected: FAIL — `404 Not Found`（路由不存在）。

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/api/auth.py` 的 `UserCreateRequest` 之前加入请求模型：

```python
class RegisterRequest(BaseModel):
    """自助注册请求体：租户、账号、显示名与口令。"""

    tenant_id: str
    username: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=80)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", "password", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        """先去掉首尾空白再做长度校验，纯空白会因不足长度被拒绝。"""
        return value.strip() if isinstance(value, str) else value


class RegisterResponse(BaseModel):
    """注册受理响应：只回状态与账号，不含内部 id 与口令。"""

    status: str
    username: str
```

在 `_rate_limit_change_password` 之后加入：

```python
def _rate_limit_register(host: str) -> None:
    """注册限速:按来源 IP 独立计数,不占用登录接口的配额。"""
    _rate_limit(
        f"register:{host}",
        redis_prefix=_RATE_LIMIT_KEY_ROOT,
        detail="too many registration attempts",
    )
```

在 `login` 路由之前加入注册路由：

```python
@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(
    request: Request,
    payload: RegisterRequest,
    db: Session = Depends(get_session),
) -> RegisterResponse:
    """自助注册：创建待审核账号，同名已存在时按状态返回可区分的 409。

    注册不消耗任何可用资源（账号为 pending，无法登录）；限速按来源 IP 独立计数。
    拒绝后不允许自助重新注册，否则「拒绝」形同虚设。
    """
    _rate_limit_register(_request_host(request))
    ensure_tenant(db, payload.tenant_id)
    username = payload.username.strip()
    existing = db.exec(
        select(User).where(User.tenant_id == payload.tenant_id, User.username == username)
    ).first()
    if existing:
        status = getattr(existing, "status", "active")
        code = {
            "pending": "REGISTRATION_PENDING",
            "rejected": "REGISTRATION_REJECTED",
        }.get(status, "ACCOUNT_EXISTS")
        raise HTTPException(
            status_code=409,
            detail={"code": code, "message": "account already exists in this tenant"},
        )
    user = User(
        tenant_id=payload.tenant_id,
        username=username,
        display_name=(payload.display_name or username).strip()[:80] or username,
        role=MEMBER_ROLE,
        source="web",
        status="pending",
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    return RegisterResponse(status="pending", username=username)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py -q
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_auth_hardening.py -q
```
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/tests/test_registration.py
git commit -m "feat(auth): add self-registration endpoint creating pending accounts"
```

### Task 3: 登录与登录态的状态收口

**Files:**
- Modify: `backend/app/api/auth.py:294-330`（`login` 状态判断）
- Modify: `backend/app/security/auth.py:57-71`（`get_current_user` 非 active 返 401）
- Test: `backend/tests/test_registration.py`（追加用例）

**Interfaces:**
- Produces: 登录 403 `{"code": "ACCOUNT_PENDING_APPROVAL" | "ACCOUNT_REGISTRATION_REJECTED", "message": ...}`
- Produces: `get_current_user` 对非 active 账号抛 401 `"Account is not active"`

- [ ] **Step 1: Write the failing test**

```python
def test_login_pending_account_returns_403_code(monkeypatch) -> None:
    """待审核账号用正确口令登录返回 403 与明确错误码,便于前端区分文案。"""
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[pending])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_PENDING_APPROVAL"


def test_login_wrong_password_for_pending_account_returns_401(monkeypatch) -> None:
    """口令错误一律 401:不知道口令的人不能靠状态码枚举账号状态。"""
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[pending])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "wrong-pass",
    })
    assert response.status_code == 401


def test_login_rejected_account_returns_403_code(monkeypatch) -> None:
    """已拒绝账号登录返回 403 与 REGISTRATION_REJECTED 错误码。"""
    rejected = User(id="user_r", tenant_id="tenant_demo", username="lisi",
                    password_hash=hash_password("secret123"), status="rejected")
    client, _ = _client(monkeypatch, users=[rejected])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "lisi", "password": "secret123",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_REGISTRATION_REJECTED"


def test_login_active_account_succeeds(monkeypatch) -> None:
    """active 账号不受状态判断影响,仍能正常拿到 token。"""
    active = User(id="user_a", tenant_id="tenant_demo", username="wangwu",
                  password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[active])
    response = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "wangwu", "password": "secret123",
    })
    assert response.status_code == 200
    assert response.json()["user"]["status"] == "active"


def test_get_current_user_rejects_inactive_account() -> None:
    """已签发 token 的账号被停用后,下一次鉴权必须 401(前端据此自动登出)。"""
    from fastapi import HTTPException
    from app.security.auth import create_access_token, get_current_user

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_x", tenant_id="tenant_demo", username="zhaoliu",
                    password_hash=hash_password("secret"), status="rejected"))
        db.commit()
        user = db.get(User, "user_x")
        token = create_access_token(user)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            get_current_user(credentials=credentials, db=db)
        assert exc.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py -q -k "pending or rejected or inactive"`
Expected: FAIL — 登录返回 200（无状态判断）。

- [ ] **Step 3: Write minimal implementation**

`backend/app/api/auth.py` 的 `login`，在口令校验之后、签发 token 之前插入：

```python
    # 口令正确后才判断状态:否则不知道口令的人也能枚举账号状态。
    account_status = getattr(user, "status", "active")
    if account_status != "active":
        code = (
            "ACCOUNT_PENDING_APPROVAL"
            if account_status == "pending"
            else "ACCOUNT_REGISTRATION_REJECTED"
        )
        raise HTTPException(
            status_code=403,
            detail={"code": code, "message": "account is not approved for login"},
        )
```

`backend/app/security/auth.py` 的 `get_current_user`，在租户校验之后插入：

```python
    # 返回 401 而非 403:前端只在 isAuthError()(401)时清会话跳登录页,
    # 用 403 会让被停用用户停留在应用里反复报错。
    if getattr(user, "status", "active") != "active":
        raise HTTPException(status_code=401, detail="Account is not active")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py backend/tests/test_auth_hardening.py -q
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests -k "auth or oidc or session" -q
```
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/app/security/auth.py backend/tests/test_registration.py
git commit -m "feat(auth): block pending and rejected accounts at login and token check"
```

### Task 4: 管理员审核端点与内部可见性过滤

**Files:**
- Modify: `backend/app/api/auth.py`（`UserStatusUpdateRequest` + `PUT /users/{user_id}/status`）
- Modify: `backend/app/api/auth.py:492-548`（`list_users` 非管理员分支加 `status` 过滤）
- Modify: `backend/app/core/human_handoff_service.py:150-155`（`_is_internal_assignee`）
- Modify: `backend/app/api/skills.py:238`（`internal_ids`）
- Test: `backend/tests/test_registration.py`（追加用例）

**Interfaces:**
- Produces: `UserStatusUpdateRequest(tenant_id: str, status: Literal["active", "rejected"])`
- Produces: `PUT /api/auth/users/{user_id}/status` → 200 `UserRead`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_registration.py（_client 与 _token 已在上一个任务定义）


def test_admin_can_approve_pending_account(monkeypatch) -> None:
    """管理员把 pending 改为 active 后返回 200,且账号可登录。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="zhangsan",
                   password_hash=hash_password("secret123"), status="pending")
    client, engine = _client(monkeypatch, users=[admin, pending])
    response = client.put(
        "/api/auth/users/user_p/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(admin),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    login = client.post("/api/auth/login", json={
        "tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret123",
    })
    assert login.status_code == 200


def test_non_admin_cannot_change_status(monkeypatch) -> None:
    """非管理员调用审核端点返回 403。"""
    member = User(id="user_m", tenant_id="tenant_demo", username="zhangsan",
                  password_hash=hash_password("secret123"), status="active")
    target = User(id="user_t", tenant_id="tenant_demo", username="lisi",
                  password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[member, target])
    response = client.put(
        "/api/auth/users/user_t/status",
        json={"tenant_id": "tenant_demo", "status": "active"},
        headers=_token(member),
    )
    assert response.status_code == 403


def test_admin_cannot_reject_self(monkeypatch) -> None:
    """管理员不能把自己改为 rejected,避免唯一管理员自锁。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    client, _ = _client(monkeypatch, users=[admin])
    response = client.put(
        "/api/auth/users/user_admin/status",
        json={"tenant_id": "tenant_demo", "status": "rejected"},
        headers=_token(admin),
    )
    assert response.status_code == 400


def test_status_endpoint_rejects_pending_value(monkeypatch) -> None:
    """审核端点不接受把账号设回 pending(422,由 Literal 约束兜底)。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    target = User(id="user_t", tenant_id="tenant_demo", username="lisi",
                  password_hash=hash_password("secret123"), status="active")
    client, _ = _client(monkeypatch, users=[admin, target])
    response = client.put(
        "/api/auth/users/user_t/status",
        json={"tenant_id": "tenant_demo", "status": "pending"},
        headers=_token(admin),
    )
    assert response.status_code == 422


def test_list_users_hides_pending_accounts_from_members(monkeypatch) -> None:
    """非管理员的账号列表只含 active 的 web 账号,待审核账号不进处理人选择器。"""
    member = User(id="user_m", tenant_id="tenant_demo", username="zhangsan",
                  password_hash=hash_password("secret123"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="lisi",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[member, pending])
    response = client.get("/api/auth/users?tenant_id=tenant_demo", headers=_token(member))
    assert response.status_code == 200
    assert [row["username"] for row in response.json()] == ["zhangsan"]


def test_list_users_shows_pending_accounts_to_admin(monkeypatch) -> None:
    """管理员必须能看到待审核账号,否则审核页没有数据可审。"""
    admin = User(id="user_admin", tenant_id="tenant_demo", username="admin",
                 role="admin", password_hash=hash_password("adminpass"), status="active")
    pending = User(id="user_p", tenant_id="tenant_demo", username="lisi",
                   password_hash=hash_password("secret123"), status="pending")
    client, _ = _client(monkeypatch, users=[admin, pending])
    response = client.get("/api/auth/users?tenant_id=tenant_demo", headers=_token(admin))
    assert response.status_code == 200
    assert {row["username"]: row["status"] for row in response.json()} == {
        "admin": "active",
        "lisi": "pending",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py -q -k "status or list_users"`
Expected: FAIL — 404（端点不存在）与列表含 `lisi`。

- [ ] **Step 3: Write minimal implementation**

`backend/app/api/auth.py` 的 `UserUpdateRequest` 之后加入：

```python
class UserStatusUpdateRequest(BaseModel):
    """账号状态变更请求体：只允许通过审核或拒绝/停用。"""

    tenant_id: str
    status: Literal["active", "rejected"]
```

在 `update_user` 之后加入：

```python
@router.put("/users/{user_id}/status", response_model=UserRead)
def update_user_status(
    user_id: str,
    request: UserStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserRead:
    """管理员审核注册申请：通过(active)或拒绝/停用(rejected)。

    禁止管理员把自己改为 rejected，避免唯一管理员自锁；
    pending 只能由注册产生，不能通过本端点回设。
    """
    _require_admin(current_user, request.tenant_id)
    user = db.get(User, user_id)
    if not user or user.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.id == current_user.id and request.status == "rejected":
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    user.status = request.status
    user.updated_at = utc_now()
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_read(user)
```

`list_users` 的可见性分支改为（**注意**：状态过滤只能加在非管理员分支。管理员必须能看到
`pending`/`rejected` 账号，否则「账号管理」页的审核操作没有数据可审）：

```python
    if not is_admin_user(current_user):
        statement = statement.where(User.source == "web")
        # 待审核/已拒绝账号不参与处理人选择器，避免把未开通的账号派给客户。
        statement = statement.where(User.status == "active")
    elif not include_channel:
        statement = statement.where(User.source == "web")
```

`backend/app/core/human_handoff_service.py` 的 `_is_internal_assignee`：

```python
        user = self.db.get(User, user_id)
        return bool(
            user
            and user.tenant_id == tenant_id
            and user.source == "web"
            and getattr(user, "status", "active") == "active"
        )
```

`backend/app/api/skills.py:238` 附近：

```python
    internal_ids = {
        row.id
        for row in rows
        if row.source == "web" and getattr(row, "status", "active") == "active"
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests/test_registration.py -q
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests -k "handoff or skills or auth" -q
```
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth.py backend/app/core/human_handoff_service.py \
  backend/app/api/skills.py backend/tests/test_registration.py
git commit -m "feat(auth): add admin status review endpoint and hide unapproved accounts"
```

## Phase B：前端

### Task 5: 登录页注册入口与状态提示

**Files:**
- Modify: `frontend-enterprise/src/pages/LoginPage.tsx`
- Modify: `frontend-enterprise/src/i18n/en.json`
- Test: `frontend-enterprise/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Consumes: `POST /api/auth/register`（201）、登录 403 `detail.code`
- Produces: `registerMode: 'login' | 'register'` 状态

- [ ] **Step 1: Write the failing test**

```tsx
it('提交注册申请后展示等待审核提示', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    jsonResponse({ status: 'pending', username: 'zhangsan' }, 201),
  ));
  renderLogin();
  await user.click(screen.getByRole('button', { name: '注册' }));
  await user.type(screen.getByLabelText('账号'), 'zhangsan');
  await user.type(screen.getByLabelText('显示名'), '张三');
  await user.type(screen.getByLabelText('密码'), 'secret123');
  await user.click(screen.getByRole('button', { name: '提交注册申请' }));
  expect(await screen.findByText('注册申请已提交，请等待管理员审核')).toBeTruthy();
});

it('待审核账号登录时展示审核提示而非密码错误', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
    detail: { code: 'ACCOUNT_PENDING_APPROVAL', message: 'account is not approved for login' },
  }, 403)));
  renderLogin();
  await showFormAndEnterCredentials(user, 'zhangsan', 'secret123');
  await user.click(screen.getByRole('button', { name: '登录' }));
  expect(await screen.findByText('账号正在等待管理员审核')).toBeTruthy();
  expect(screen.queryByText('密码输入错误')).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend-enterprise test -- LoginPage`
Expected: FAIL — 找不到「注册」按钮。

- [ ] **Step 3: Write minimal implementation**

`LoginPage.tsx`：
- 新增状态 `const [registerMode, setRegisterMode] = useState(false)`、`const [registerUsername/registerDisplayName/registerPassword, ...]`、`const [notice, setNotice] = useState<string | null>(null)`。
- 未展开态把单个「登录」按钮换成两个并列按钮（`登录` 主按钮、`注册` 描边按钮）。
- `registerMode` 时渲染注册表单（账号 / 显示名 / 密码 + 「提交注册申请」），提交走 `api.post('/api/auth/register', {tenant_id: TENANT_ID, username, display_name, password})`；成功后 `setNotice('注册申请已提交，请等待管理员审核')` 并切回登录态。
- `login()` 的 catch 里读取 `error instanceof ApiError ? error.code : null`；`ACCOUNT_PENDING_APPROVAL` → `setNotice('账号正在等待管理员审核')`，`ACCOUNT_REGISTRATION_REJECTED` → `setNotice('注册申请未通过，请联系管理员')`，其余保持原有字段错误。
- 提示条渲染在表单上方，`role="status"`。

`ApiError.code` **已存在，不需要改 `client.ts`**：`frontend-enterprise/src/api/client.ts:22-36` 的
`ApiError` 已有 `code?: string`，构造函数从 `parseErrorPayload` 取值，而后者对
`detail` 为对象的情形会读 `structured.code`（:340-349），并用
`STABLE_ERROR_CODE_PATTERN = /^[A-Z][A-Z0-9_]+$/`（:302）校验。
`ACCOUNT_PENDING_APPROVAL` / `ACCOUNT_REGISTRATION_REJECTED` 均匹配该模式，可直接使用。

`en.json` 追加以下键（**注意**：`"显示名"` 已存在且当前误译为 `"Show"`，本次改为 `"Display name"`；`"待审核"` 已存在为 `"Pending review"`，直接复用，不要重复添加）：

```json
"注册": "Sign up",
"提交注册申请": "Submit request",
"显示名": "Display name",
"注册申请已提交，请等待管理员审核": "Registration submitted. An administrator will review it.",
"账号正在等待管理员审核": "This account is awaiting administrator approval.",
"注册申请未通过，请联系管理员": "Your registration was rejected. Please contact an administrator.",
"已有账号？去登录": "Already have an account? Sign in",
```

> 若 `scripts/check-i18n.cjs` 报「已存在键」或 JSON 重复键，说明某个文案已在 `en.json` 里（例如 `"状态"`、`"正常"`、`"全部"`、`"通过"`、`"拒绝"`、`"已拒绝"` 均已存在），复用即可，不要重复插入。

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
npm --prefix frontend-enterprise test -- LoginPage
npm --prefix frontend-enterprise run i18n:check
npm --prefix frontend-enterprise run build
```
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add frontend-enterprise/src/pages/LoginPage.tsx \
  frontend-enterprise/src/i18n/en.json frontend-enterprise/src/pages/LoginPage.test.tsx
git commit -m "feat(web): add self-registration entry and approval notices to login page"
```

### Task 6: 账号管理页状态列、筛选与审核操作

**Files:**
- Modify: `frontend-enterprise/src/pages/AccountsPage.tsx`
- Modify: `frontend-enterprise/src/i18n/en.json`
- Modify: `docs/server-deployment.md`（升级顺序补 0003 说明）
- Test: `frontend-enterprise/src/pages/AccountsPage.test.tsx`

**Interfaces:**
- Consumes: `PUT /api/auth/users/{user_id}/status`
- Produces: `AccountStatusBadge({status})` 组件（可静态渲染断言）

- [ ] **Step 1: Write the failing test**

```tsx
it('按状态渲染徽标', () => {
  expect(renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'pending' })))
    .toContain('待审核');
  expect(renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'rejected' })))
    .toContain('已拒绝');
  expect(renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'active' })))
    .toContain('正常');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend-enterprise test -- AccountsPage`
Expected: FAIL — `AccountStatusBadge` 未导出。

- [ ] **Step 3: Write minimal implementation**

`AccountsPage.tsx`：
- `EmployeeAccount` 增加 `status: 'active' | 'pending' | 'rejected'`。
- 导出 `AccountStatusBadge`：`active`→`StatusBadge tone="green"`「正常」、`pending`→`tone="orange"`「待审核」、`rejected`→`tone="red"`「已拒绝」。
- `columns` 在 `role` 之后插入「状态」列；`renderMobileCard` 同步展示徽标。
- 搜索框左侧加 `Select`（全部 / 待审核 / 正常 / 已拒绝），与关键字叠加过滤；`filteredRows` 增加 `statusFilter` 条件。
- 行操作菜单：`pending` 行加「通过」「拒绝」，`rejected` 行加「通过」；调用 `api.put(`/api/auth/users/${row.id}/status`, {tenant_id: TENANT_ID, status})` 后 `load()` 刷新。

`en.json` 里 `"状态": "Status"`、`"正常": "Active"`、`"待审核": "Pending review"`、`"已拒绝": "Rejected"`、`"全部": "All"`、`"通过": "Approve"`、`"拒绝": "Reject"` 全部已存在，直接复用，本任务不需要新增任何键。`"待审核"` 同时被 `frontend-enterprise/src/pages/dashboard/EvolutionPanel.tsx:26` 使用，**不要改它的值**。

`docs/server-deployment.md` §5 升级顺序补一句：

> 含 `0003_user_status` 之后，未执行迁移的库会让 `api` 启动即失败（`assert_schema_current`）。升级必须先迁移再重建 api。

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
npm --prefix frontend-enterprise test
npm --prefix frontend-enterprise run i18n:check
npm --prefix frontend-enterprise run build
PYTHONPATH=$PWD/backend /Users/jacky/works/StaffDeck-Project/backend/.venv/bin/python -m pytest backend/tests -q
/Users/jacky/works/StaffDeck-Project/backend/.venv/bin/ruff check backend
```
Expected: 前端全绿、后端与基线一致（6 个环境性 `test_general_skills` 失败属已知）、ruff 零告警。

- [ ] **Step 5: Commit**

```bash
git add frontend-enterprise/src/pages/AccountsPage.tsx frontend-enterprise/src/i18n/en.json \
  frontend-enterprise/src/pages/AccountsPage.test.tsx docs/server-deployment.md
git commit -m "feat(web): show account status and add approval actions in accounts page"
```

## Review Focus

- **状态收口是否完整**：除 `login` 与 `get_current_user`，还有没有别的路径能拿到可用会话？（OIDC 回调、渠道身份映射、API 密钥鉴权 `backend/app/public_api/auth.py`）
- **401 vs 403 的选择**：登录用 403（凭据正确但未开通），鉴权用 401（前端据此登出）。评审需确认前端 `isAuthError` 只认 401，且不会有中间层把 401 改写成 403。
- **迁移幂等**：SQLite 补列与 Alembic 0003 都必须在「列已存在」时静默跳过；`downgrade` 后再次 `upgrade` 不能失败。
- **默认值安全性**：四条既有建号路径（引导管理员、演示数据、OIDC、渠道懒建）必须全部落 `active`，任何一条落 `pending` 都会让生产首启无法登录。
- **拒绝后重注册**：同名 `rejected` 再注册必须 409，不能因为「允许改判」而放开自助重注册。

## 后续独立计划（不在本计划范围）

- 注册/审核的通知能力（邮件或站内信）——当前系统无任何通知设施。
- 账号状态变更的审计日志（谁在何时把谁改成什么）。
- 注册接口的图形验证码/人机校验。
- 邮箱验证与口令强度策略升级。
