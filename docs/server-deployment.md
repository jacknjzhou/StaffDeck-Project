# StaffDeck 服务端部署手册

本文档描述如何以**服务端模式**交付 StaffDeck：FastAPI 多进程 + PostgreSQL + Redis，
通过 Docker Compose 编排，管理端与用户端前端由同一服务托管，用户通过浏览器访问。

**服务端模式是唯一的生产形态。** 桌面安装包（PyInstaller）、`desktop_launcher.py`
与 `single_port_app.py` 仅保留为本地开发入口，不再作为交付渠道，也不再承诺跨平台
安装、静默升级或单机数据兼容。

| 形态 | 入口 | 数据库 | 用途 |
|---|---|---|---|
| 服务端（生产） | `backend/server_app.py`（容器内 `server_app:app`） | PostgreSQL + Redis | 唯一支持的对外部署方式 |
| 本地开发 | `desktop_launcher.py` / `single_port_app.py` | SQLite 文件 | 开发、调试、单机演示 |
| 桌面安装包 | `packaging/` 中的 PyInstaller 脚本 | SQLite 文件 | 历史遗留，不再发布 |

决策背景与验收标准见
`docs/superpowers/specs/2026-11-08-server-first-platform.md`；上线阻断项与改造顺序见
`docs/superpowers/plans/2026-11-08-server-first-optimization.md`。

---

## 1. 快速启动

```bash
cd packaging/docker
cp .env.example .env
# 编辑 .env，至少设置这两个值：
#   STAFFDECK_PG_PASSWORD   —— PostgreSQL 密码
#   STAFFDECK_APP_SECRET    —— 长随机串，同时用于 JWT 签名与凭据加密
docker compose up -d --build
```

启动后：

- 管理端 <http://localhost:5173/enterprise>
- 用户端 <http://localhost:5173/workspace>
- 健康检查 <http://localhost:5173/api/health>
- OpenAPI 文档 <http://localhost:5173/api/v1/openapi.json>

`STAFFDECK_HOST_PORT` 可以改宿主端口（默认 `5173`）；容器内 nginx 始终监听 80。

一键验收（构建 + 启动 + 断言，exit 0 即通过）：

```bash
bash scripts/compose_smoke.sh            # 复用现有镜像
bash scripts/compose_smoke.sh --rebuild  # 强制重建
PORT=8080 bash scripts/compose_smoke.sh  # 换端口
```

停止：`cd packaging/docker && docker compose down`（加 `-v` 会连数据卷一起删）。

---

## 2. 生产上线检查清单

首次对外提供访问前，逐项确认：

- [ ] `STAFFDECK_APP_SECRET` 是 32 字节以上的随机值，且未提交到仓库；它同时是
      JWT 签名密钥和渠道凭据的加密根，泄露等于全量凭据泄露。
- [ ] 已用管理员账号登录并**修改默认密码**（`admin/admin` 只是引导凭证）。修改后
      在数据库或管理端确认不存在仍使用默认口令的账号。
- [ ] PostgreSQL 与 Redis **没有**映射到公网端口；compose 默认只在容器网络内暴露。
- [ ] nginx 前面有 TLS 终止（云负载均衡或宿主 nginx），并强制 HTTPS；`APP_SECRET`
      与 OIDC secret 只存在于服务端环境变量。
- [ ] 已配置 `pg_dump` 定时备份，并至少完成一次恢复演练；备份文件与
      `STAFFDECK_APP_SECRET` 分开保管。
- [ ] `STAFFDECK_RUN_MIGRATIONS=1` 只在 `api` 容器上；升级时先备份，再滚动重建。
- [ ] 已确认 `curl -fsS http://<host>/api/health` 返回 200，且日志无租约冲突。
- [ ] 若启用 OIDC，`OIDC_REDIRECT_URI` 与 `FRONTEND_BASE_URL` 都指向对外域名，
      且 IdP 侧回调地址完全一致。

> 当前版本的 `APP_SECRET` 仍有内置默认值，服务不会因默认值拒绝启动；管理员
> 账号也仍会在空库初始化时创建。**在上述门禁落地前，必须人工完成前两项检查**，
> 不能把“能启动”当作“可上线”。对应加固任务见优化计划的任务 1–2。

---

## 3. 容器拓扑

| 服务 | 作用 | 关键配置 |
|---|---|---|
| `postgres` | 主数据库 | 数据卷 `pgdata`，健康检查 `pg_isready` |
| `redis` | 角色租约 / 运行期互斥 | 不对宿主暴露端口 |
| `api` | HTTP 服务（管理端 + 用户端 + `/api/*` + `/api/v1`） | `PROCESS_ROLE=api`，启动时跑迁移 |
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

## 4. 进程角色与互斥

`PROCESS_ROLE` 取值：

- `all`（默认）—— 三类能力全开，等价于旧单进程行为
- `api` / `worker` / `connector` —— 只启用对应能力

**互斥机制按数据库类型自动切换**：

- **SQLite**：沿用文件锁（`runtime_lock`），仅限单机开发。
- **PostgreSQL**：每个能力启动时获取 Redis 租约 `staffdeck:lease:{role}`，
  同角色第二个进程启动会被拒绝（`another {role} process holds the lease`）。

租约 TTL 60 秒，由 `staffdeck-lease-renewer` 线程每 20 秒续期。这个续期是必需的
——否则任何存活超过 60 秒的进程都会让租约静默过期、互斥保护失效。

> **`REDIS_URL` 为空时不会启用租约。** 服务端部署必须配置 Redis；只有本地单进程
> 开发才允许为空。多副本 api/worker 在无 Redis 的情况下会失去互斥保证。

### 已知限制：飞书渠道

飞书长连接 connector 的子进程直接用 `sqlite3` 写本地库做入站去重，PostgreSQL
部署下无法运行。当前行为是**跳过飞书渠道并打印警告**，其他渠道（微信/企微/钉钉）
不受影响。要恢复飞书，需把该 connector 的入站 staging 改为走服务端 inbox 表，
属后续里程碑工作。

---

## 5. 数据库与迁移

- 生产数据库只支持 PostgreSQL。SQLite 仅用于本地开发与测试，不承诺多进程、
  备份恢复或跨版本升级。
- 迁移由 Alembic 管理，基线版本 `0001`；新增 schema 变更必须同时提交新的 revision，
  不能只依赖 `create_all`。
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
> 的 schema 变更应以迁移为准，否则新建表/列不会在其他实例上线。启动时的 schema
> 版本校验与 PostgreSQL 增量迁移是优化计划的任务 4。

升级顺序：备份 → 停 `worker`/`connector` → 跑迁移 → 重建 `api` → 重建其余容器。

---

## 6. 依赖版本锁定（重要）

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

## 7. 认证与租户

- 管理端/用户端：用户 JWT（`APP_SECRET` 签名，14 天有效期）。
- 对外 API `/api/v1`：Bearer `sd_live_*` 密钥（账号密钥 / 员工运行密钥 / API Client 密钥）。
- **租户由凭证推导**，不再依赖请求参数：`tenant_id` 查询参数已改为可选，
  省略时以 JWT 为准；显式传入且不匹配仍返回 403。
- 管理员重置密码：`PUT /api/auth/users/{user_id}`；当前没有用户自助改密页面，
  引导管理员改密需由管理员操作或直接更新数据库（见任务 2 的改密端点）。

### OIDC SSO（可选）

设置以下环境变量即可启用单点登录：

```
OIDC_ENABLED=1
OIDC_ISSUER=https://your-idp.example.com
OIDC_CLIENT_ID=staffdeck
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=https://staffdeck.example.com/api/auth/oidc/callback
OIDC_TENANT_ID=tenant_acme
FRONTEND_BASE_URL=https://staffdeck.example.com
```

`OIDC_TENANT_ID` 指定 OIDC 登录绑定的租户。未设置时回退到 `tenant_demo`；
生产多租户部署必须显式设置，否则所有 SSO 用户都会落到演示租户。

登录入口 `GET /api/auth/oidc/login`，IdP 回调后按邮箱在 `OIDC_TENANT_ID`
绑定的租户内查找/创建用户并签发常规 JWT，重定向到
`{FRONTEND_BASE_URL}/login?token=<JWT>`。未启用时该端点返回 404。

匹配规则：

- 邮箱先 `strip().lower()`，再按 `(tenant_id, lower(username))` 查询，避免大小写差异造成重复账号。
- 新用户创建在 `OIDC_TENANT_ID` 租户；`OIDC_TENANT_ID` 未设置时回退 `tenant_demo`。
- 若同邮箱已存在于其他租户，返回 **409** 并提示管理员先在当前租户显式绑定，
  绝不静默选中其他租户账号或新建重复账号。

---

## 8. 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL，PG 用 `postgresql+psycopg://...` | SQLite 文件 |
| `APP_SECRET` | JWT 签名与凭据加密根密钥，**生产必须改** | 内置默认值 |
| `REDIS_URL` | 角色租约；服务端部署必填 | 空 |
| `PROCESS_ROLE` | `all` / `api` / `worker` / `connector` | `all` |
| `STAFFDECK_RUN_MIGRATIONS` | 容器启动前跑迁移 | 空（entrypoint 判断 `1`） |
| `STAFFDECK_ROLE` | 渠道服务开关（`all` / `connector`） | `all` |
| `PUBLIC_API_ENABLED` | 是否挂载 `/api/v1` | `true` |
| `STAFFDECK_PG_PASSWORD` | compose 用，PostgreSQL 密码 | 必填 |
| `STAFFDECK_HOST_PORT` | compose 暴露的宿主端口 | `5173` |
| `OIDC_TENANT_ID` | OIDC 登录绑定的租户；生产多租户必须显式设置 | `tenant_demo` |
| `FORWARDED_ALLOW_IPS` | uvicorn 信任的代理 IP/CIDR（禁 `*`） | `127.0.0.1` |
| `STAFFDECK_FORWARDED_ALLOW_IPS` | compose 覆盖上值 | `172.16.0.0/12,127.0.0.1` |
| `OIDC_*` | 见上节 | 未启用 |

nginx 是 compose 的唯一入口且 api 端口未发布，因此默认只信任 Docker 子网与回环；
若绕过 nginx 直连 api，需同步收紧该值。完整列表见 `backend/app/config.py`。

---

## 9. 常见问题

**构建报 `The lockfile ... needs to be updated`**
`uv.lock` 与 `pyproject.toml` 不同步。执行 `cd backend && uv lock` 后重新构建。

**api 容器反复重启，日志出现 `ModuleNotFoundError`**
生产镜像用 `--no-dev`，任何仅靠 dev 依赖传递引入的包都会缺失。把它加进
`[project].dependencies` 并重新 `uv lock`。

**启动报 `UTCDateTime has no attribute` / seed 崩溃**
sqlmodel 版本越界，见第 6 节。

**nginx 返回 502，但 api 容器 healthy**
容器重建后 IP 变化。确认 `nginx.conf` 使用 `resolver 127.0.0.11` + 变量式
`proxy_pass`；或用 `docker compose up -d --force-recreate nginx` 重建 nginx。

**`another api process holds the lease`**
同角色已有进程在运行。检查是否有残留容器（`docker compose ps -a`），
或租约未被正常释放（`docker compose exec redis redis-cli GET staffdeck:lease:api`）。
租约自带 60 秒 TTL，进程退出后最多 60 秒自动释放。

**connector 日志 `飞书长连接 connector 需要文件 SQLite`**
PostgreSQL 部署下的预期行为，见第 4 节。

**升级后登录失效 / 渠道凭据解不开**
`APP_SECRET` 与旧实例不一致。JWT 与 Fernet 密钥都由它派生，换值会导致会话失效、
渠道凭据无法解密；必须从旧环境原样迁移。

---

## 10. 从旧版单机（SQLite）迁移（一次性）

服务端模式是新的默认交付形态。已经用桌面版积累的数据需要人工迁移一次：

SQLite → PostgreSQL 没有自动迁移工具。推荐路径：

1. 在 PG 上启动服务端，让它跑完迁移与初始 seed。
2. 用脚本逐表把 SQLite 数据导入 PG（注意 75 张表的外键顺序，先导入被引用方）。
3. 校验 `agent_profiles` / `skills` / `knowledge_*` / `sessions` 行数与内容。

渠道凭证列（`ChannelBinding.credentials_enc`）用 Fernet 加密，密钥来自
`APP_SECRET` 派生的量，**迁移时两端 `APP_SECRET` 必须一致**，否则解密失败。

迁移完成后，桌面客户端不再接收更新；后续版本只通过服务端镜像发布。

---

## 11. 相关文档

- 服务端优先决策与验收：`docs/superpowers/specs/2026-11-08-server-first-platform.md`
- 上线阻断与优化计划：`docs/superpowers/plans/2026-11-08-server-first-optimization.md`
- 服务端化总体设计：`docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`
- deepchat 接入方案：`docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md`
- 公开 API 使用说明：`docs/open-api-v1.md`
