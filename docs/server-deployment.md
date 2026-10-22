# StaffDeck 服务端部署手册

本文档描述如何以**服务端模式**运行 StaffDeck：FastAPI 多进程 + PostgreSQL + Redis，
通过 Docker Compose 编排，管理端与用户端前端由同一服务托管。

桌面单机模式（PyInstaller 安装包、SQLite）不受影响，两套入口并存：

| 模式 | 入口 | 数据库 | 互斥机制 |
|---|---|---|---|
| 桌面单机 | `desktop_launcher.py` / `single_port_app.py` | SQLite 文件 | 文件锁（`*.runtime.lock`） |
| 服务端 | `backend/server_app.py`（容器内为 `server_app:app`） | PostgreSQL | Redis 角色租约 |

---

## 1. 快速启动

```bash
cd packaging/docker
cp .env.example .env
# 编辑 .env，至少设置这两个值：
#   STAFFDECK_PG_PASSWORD   —— PostgreSQL 密码
#   STAFFDECK_APP_SECRET    —— JWT 签名密钥（长随机串）
docker compose up -d --build
```

启动后：

- 管理端 <http://localhost:5173/enterprise>
- 用户端 <http://localhost:5173/workspace>
- 健康检查 <http://localhost:5173/api/health>
- OpenAPI 文档 <http://localhost:5173/api/v1/openapi.json>

一键验收（构建 + 启动 + 断言，exit 0 即通过）：

```bash
bash scripts/compose_smoke.sh            # 复用现有镜像
bash scripts/compose_smoke.sh --rebuild  # 强制重建
PORT=8080 bash scripts/compose_smoke.sh  # 换端口
```

停止：`cd packaging/docker && docker compose down`（加 `-v` 会连数据卷一起删）。

---

## 2. 容器拓扑

| 服务 | 作用 | 关键配置 |
|---|---|---|
| `postgres` | 主数据库 | 数据卷 `pgdata`，健康检查 `pg_isready` |
| `redis` | 角色租约 / 运行期互斥 | — |
| `api` | HTTP 服务（管理端 + 用户端 + `/api/*` + `/api/v1`） | `PROCESS_ROLE=api`，启动时自动跑 Alembic 迁移 |
| `worker` | 后台任务：异步作业、定时任务、Harness 孤儿恢复、Webhook 投递 | `PROCESS_ROLE=worker` |
| `connector` | 渠道连接器（微信/企微/钉钉/飞书） | `PROCESS_ROLE=connector` |
| `nginx` | 反向代理，含 SSE 友好配置 | 对上游用 Docker DNS 动态解析 |

三个应用容器使用同一个镜像（`packaging/docker/Dockerfile.server`），仅靠
`PROCESS_ROLE` 区分启动的能力集合，因此扩缩容只需改 `PROCESS_ROLE`。

### 为什么 nginx 用变量式 proxy_pass

`proxy_pass http://api:5173` 只在启动时解析一次 DNS，容器重建后 api 会拿到新 IP，
nginx 会持续返回 502 直到手动 reload。配置里改用 `set $upstream_api http://api:5173`
配合 `resolver 127.0.0.11`，每个请求重新解析，容器重建后自动恢复。

---

## 3. 进程角色与互斥

`PROCESS_ROLE` 取值：

- `all`（默认）—— 三类能力全开，等价于旧单进程行为
- `api` / `worker` / `connector` —— 只启用对应能力

**互斥机制按数据库类型自动切换**：

- **SQLite**：沿用文件锁（`runtime_lock`），单机保证。
- **PostgreSQL**：每个能力启动时获取 Redis 租约 `staffdeck:lease:{role}`，
  同角色第二个进程启动会被拒绝（`another {role} process holds the lease`）。

租约 TTL 60 秒，由 `staffdeck-lease-renewer` 线程每 20 秒续期。这个续期是必需的
——否则任何存活超过 60 秒的进程都会让租约静默过期、互斥保护失效。

### 已知限制：飞书渠道

飞书长连接 connector 的子进程直接用 `sqlite3` 写本地库做入站去重，PostgreSQL
部署下无法运行。当前行为是**跳过飞书渠道并打印警告**，其他渠道（微信/企微/钉钉）
不受影响。要恢复飞书，需把该 connector 的入站 staging 改为走服务端 inbox 表，
属后续里程碑工作。

---

## 4. 数据库与迁移

- 迁移由 Alembic 管理，基线版本 `0001`。
- `api` 容器在启动 uvicorn 前自动执行迁移（`STAFFDECK_RUN_MIGRATIONS=1`），
  `worker` / `connector` 设为 `0`，避免并发迁移。
- 容器内 `WORKDIR=/app/backend`，迁移入口为 `python -m app.db.migrations_cli upgrade`。
- 手工执行迁移（例如本地对远程库）：

```bash
cd backend
STAFFDECK_DATABASE_URL="postgresql+psycopg://user:pass@host:5432/staffdeck" \
  python -m app.db.migrations_cli upgrade
```

> **修改模型后必须补迁移**：服务端模式的 `init_db()` 仍会 `create_all`，但生产环境
> 的 schema 变更应以迁移为准，否则新建表/列不会在其他实例上线。

取消 `create_all` 依赖的长期方向见 `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`。

---

## 5. 依赖版本锁定（重要）

`backend/uv.lock` 是唯一可信的依赖解析结果，镜像用 `uv sync --frozen` 安装，
`pyproject.toml` 与 `uv.lock` 不一致时会**构建失败**（这是有意的门禁）。

已知陷阱：`sqlmodel >= 0.0.40` 会拉入 `sqlalchemy 2.1`，其 `UTCDateTime` 强制
时区感知，而 `app.db.models.utc_now()` 返回 naive datetime —— 空库 seed 阶段直接崩溃。
因此 `pyproject.toml` 显式声明：

```toml
"sqlmodel>=0.0.21,<0.0.40",
"redis>=5.0.0,<9.0.0",
```

`redis` 也是显式声明的：它原本仅由 `fakeredis` 作为传递依赖引入，`--no-dev`
的生产安装会缺失，导致角色租约启动即报 `ModuleNotFoundError`。

改动依赖后重新锁定：`cd backend && uv lock`。

---

## 6. 认证与租户

- 管理端/用户端：用户 JWT（`APP_SECRET` 签名，14 天有效期）。
- 对外 API `/api/v1`：Bearer `sd_live_*` 密钥（账号密钥 / 员工运行密钥 / API Client 密钥）。
- **租户由凭证推导**，不再依赖请求参数：`tenant_id` 查询参数已改为可选，
  省略时以 JWT 为准；显式传入且不匹配仍返回 403。

### OIDC SSO（可选）

设置以下环境变量即可启用单点登录：

```
OIDC_ENABLED=1
OIDC_ISSUER=https://your-idp.example.com
OIDC_CLIENT_ID=staffdeck
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=https://staffdeck.example.com/api/auth/oidc/callback
FRONTEND_BASE_URL=https://staffdeck.example.com
```

登录入口 `GET /api/auth/oidc/login`，IdP 回调后按邮箱查找/创建用户并签发常规 JWT，
重定向到 `{FRONTEND_BASE_URL}/login?token=<JWT>`。未启用时该端点返回 404。

---

## 7. 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL，PG 用 `postgresql+psycopg://...` | SQLite 文件 |
| `APP_SECRET` | JWT 签名密钥，**生产必须改** | 内置默认值 |
| `REDIS_URL` | 为空则不启用角色租约（仅用于单进程） | 空 |
| `PROCESS_ROLE` | `all` / `api` / `worker` / `connector` | `all` |
| `STAFFDECK_RUN_MIGRATIONS` | 容器启动前跑迁移 | 空（entrypoint 判断 `1`） |
| `STAFFDECK_ROLE` | 渠道服务开关（`all` / `connector`） | `all` |
| `PUBLIC_API_ENABLED` | 是否挂载 `/api/v1` | `true` |
| `STAFFDECK_PG_PASSWORD` | compose 用，PostgreSQL 密码 | 必填 |
| `OIDC_*` | 见上节 | 未启用 |

完整列表见 `backend/app/config.py`。

---

## 8. 常见问题

**构建报 `The lockfile ... needs to be updated`**
`uv.lock` 与 `pyproject.toml` 不同步。执行 `cd backend && uv lock` 后重新构建。

**api 容器反复重启，日志出现 `ModuleNotFoundError`**
生产镜像用 `--no-dev`，任何仅靠 dev 依赖传递引入的包都会缺失。把它加进
`[project].dependencies` 并重新 `uv lock`。

**启动报 `UTCDateTime has no attribute` / seed 崩溃**
sqlmodel 版本越界，见第 5 节。

**nginx 返回 502，但 api 容器 healthy**
容器重建后 IP 变化。确认 `nginx.conf` 使用 `resolver 127.0.0.11` + 变量式
`proxy_pass`；或用 `docker compose up -d --force-recreate nginx` 重建 nginx。

**`another api process holds the lease`**
同角色已有进程在运行。检查是否有残留容器（`docker compose ps -a`），
或租约未被正常释放（`docker compose exec redis redis-cli GET staffdeck:lease:api`）。
租约自带 60 秒 TTL，进程退出后最多 60 秒自动释放。

**connector 日志 `飞书长连接 connector 需要文件 SQLite`**
PostgreSQL 部署下的预期行为，见第 3 节。

---

## 9. 从桌面模式迁移数据

SQLite → PostgreSQL 没有自动迁移工具。推荐路径：

1. 在 PG 上启动服务端，让它跑完迁移与初始 seed。
2. 用脚本逐表把 SQLite 数据导入 PG（注意 75 张表的外键顺序，先导入被引用方）。
3. 校验 `agent_profiles` / `skills` / `knowledge_*` / `sessions` 行数与内容。

渠道凭证列（`ChannelBinding.credentials_enc`）用 Fernet 加密，密钥来自
`APP_SECRET` 派生的量，**迁移时两端 `APP_SECRET` 必须一致**，否则解密失败。

---

## 10. 相关文档

- 服务端化总体设计：`docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`
- deepchat 接入方案：`docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md`
- 公开 API 使用说明：`docs/open-api-v1.md`
- M1 实施计划：`docs/superpowers/plans/2026-10-22-m1-server-foundation.md`
