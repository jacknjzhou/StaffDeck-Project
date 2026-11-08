# StaffDeck 服务端优先交付（Server-First）设计

**日期：** 2026-11-08
**状态：** 已采纳
**相关：** `docs/server-deployment.md`、`docs/superpowers/plans/2026-11-08-server-first-optimization.md`

## 1. 背景与决策

StaffDeck 过去同时维护两条交付路径：桌面安装包（PyInstaller + SQLite + 单端口进程）
与服务端（Docker Compose + PostgreSQL + Redis）。两条路径在配置、迁移、互斥、升级方式
上长期分叉，测试与文档也不得不覆盖两套语义。

**决策：服务端是唯一的生产交付形态。** 用户通过浏览器访问部署方提供的域名；版本推进
由服务端镜像完成；桌面入口仅保留为本地开发工具，不再构建、签名或发布安装包。

理由：

1. 目标客户是企业/机构，天然需要多用户、多租户、集中权限与审计，单机形态无法满足。
2. 双路径导致 `create_all` + 手写 SQLite 迁移与 Alembic 并存，已产生真实的迁移顺序缺陷。
3. 客户端升级不可控（用户长期不升级、旧版本残留），服务端可以原子升级并回滚。
4. CI、备份、监控、SSO 都只能在服务端形态下闭环。

## 2. 目标与非目标

**目标**

- 一条受支持的部署路径：`packaging/docker` 的 Compose 栈，入口为 `backend/server_app.py`。
- 文档、脚本、AGENTS.md 与新贡献者的默认心智模型一致：server-first。
- 上线阻断项（默认凭证、默认密钥、MCP 任意命令、迁移顺序）在发布前关闭。
- 明确“旧单机数据如何一次性迁移”，不再承诺双向兼容。

**非目标**

- 不实现桌面端自动升级、安装包签名、Windows 服务封装。
- 不在本阶段做 Kubernetes/Helm、水平分片、跨区域多活。
- 不重写 Harness 运行时，也不拆分 `chat.py` / `database.py`（另立计划）。
- 不保证 SQLite 在多进程、备份恢复、跨版本升级下的行为。

## 3. 交付拓扑

```
浏览器 ──HTTPS──> nginx ──> api (PROCESS_ROLE=api)
                              │
                    ┌─────────┼──────────┐
                 postgres   redis      worker / connector
```

- `api`：HTTP、SSE、管理端与用户端静态资源；启动前跑迁移。
- `worker`：异步作业、定时任务、孤儿恢复、Webhook 投递。
- `connector`：渠道长连接（微信/企微/钉钉；飞书待服务端 inbox 改造）。
- `postgres`：唯一生产数据库；`redis`：角色租约与跨进程互斥。
- 三个应用容器同镜像，靠 `PROCESS_ROLE` 区分能力集。

## 4. 支持矩阵

| 能力 | 服务端（生产） | 本地开发 |
|---|---|---|
| 数据库 | PostgreSQL 16 + Alembic | SQLite 文件 |
| 多进程 | 支持（Redis 租约） | 单进程 |
| 多租户 | 支持 | 单租户演示 |
| 升级 | 镜像 + 迁移 | git pull + 重建 venv |
| 备份 | `pg_dump` 定时 + 恢复演练 | 不要求 |
| 桌面安装包 | **不支持** | — |
| SQLite 数据 | 仅一次性导入 | 原生 |

## 5. 安全基线（上线前必须满足）

1. `APP_SECRET` 无默认值可用：生产环境使用内置默认值或长度不足时**拒绝启动**。
2. 不存在可用的默认口令：引导管理员密码随机生成或由
   `STAFFDECK_BOOTSTRAP_ADMIN_PASSWORD` 指定；演示数据在生产默认关闭。
3. MCP stdio 只允许白名单启动器，子进程环境变量按白名单构造；创建/修改需平台管理员。
4. PostgreSQL 与 Redis 不对公网暴露；TLS 在入口终止。
5. 登录有速率限制；OIDC 身份按“租户 + 邮箱”绑定，禁止跨租户命中。
6. 上传在读取 body 前校验 `Content-Length`，并受统一上限约束。
7. 备份可用：至少一次恢复演练，备份与密钥分开保管。

以上 1–3 由优化计划任务 1–3 落地，4–7 由任务 7、10 落地。

## 6. 迁移策略

- 旧单机 → 服务端：一次性人工迁移（`docs/server-deployment.md` §10），迁移时两端
  `APP_SECRET` 必须一致，否则渠道凭据无法解密。
- 服务端版本间：只通过 Alembic 增量 revision；`api` 启动前执行，失败即停止滚动。
- 不提供 PostgreSQL → SQLite 回迁；回滚以镜像 + 数据库快照为单位。

## 7. 验收标准

- [ ] 新环境用 Compose 启动后，未设置 `APP_SECRET` 时启动失败并给出可操作错误。
- [ ] 首次启动不再产生 `admin/admin`；日志只输出一次随机引导密码。
- [ ] 以租户管理员创建 `command=/bin/sh` 的 stdio MCP 被拒绝。
- [ ] 从缺少 `team_id` 的旧 SQLite 库启动不再抛 `no such column`。
- [ ] PostgreSQL 上存在 `alembic_version=0002`，且 `create_all` 不再承担生产 schema 变更。
- [ ] 50 个并发 SSE 会话下，`/api/health` 的 p95 延迟 < 200ms。
- [ ] `REDIS_URL` 为空且 `PROCESS_ROLE=api` 时，生产模式启动失败（而非静默无锁）。
- [ ] CI 对每个 PR 跑 ruff + pytest + vitest，红灯阻断合并。
- [ ] `/api/ready` 在数据库不可用时返回 503；容器以非 root 运行。
- [ ] 管理端首屏 JS 主包 < 300KB gzip，且 `/api/enterprise/agents` 不再重复拉取。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 老用户仍持有桌面版并期待升级 | 文档与 README 明示；保留一次性迁移章节；旧安装包不再发版 |
| 强制 `APP_SECRET` 让既有私有部署启动失败 | 在 release notes 提前公告，错误信息给出生成命令 |
| MCP 白名单误伤合法私有 server | 白名单可配置；提供 `STAFFDECK_MCP_STDIO_ALLOWED_COMMANDS` |
| SSE 异步化引入竞态 | 保留同步回退分支，按并发压测验收后再删旧路径 |
| 前端引入 TanStack Query 增大包体 | 路由级拆包与依赖体积预算同时验收 |
