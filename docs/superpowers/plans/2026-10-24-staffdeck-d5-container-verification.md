# D5 回流链路：容器级验证结论（2026-10-24）

## 为什么要额外做这一轮

D5 原来的端到端用 `TestClient` + 内存 SQLite。它证明的是**逻辑**：路由、幂等、
binding 鉴权都对。但它把 nginx、uvicorn、连接池、psycopg、PG 事务、Alembic 建出的
表结构全部绕过了。

这在本项目不是理论风险。`sqlmodel` 0.0.40+ 解锁 sqlalchemy 2.1，其 `UTCDateTime`
拒绝 `app.db.models.utc_now()` 的 naive datetime —— 这个问题在 SQLite 上完全不出现，
只在 PG 上炸。所以「SQLite 全绿」和「容器能跑」是两个独立结论，必须分别成立。

本轮把整套 `packaging/docker` 起起来，用真 HTTP 打了一遍。

## 环境与结果

| 项目 | 结果 |
| --- | --- |
| 镜像构建（api/worker/connector 三角色同源） | 1m22s，成功 |
| 服务栈 | postgres 16.15 / redis 7 / nginx 1.27 / api / worker / connector 全部 Healthy |
| Alembic 版本 | `0001`（api 容器启动时自动迁移） |
| OpenAPI 路径数 | 91，含 `/agents/{agent_id}/sessions` 与 `.../events:batch` |
| 角色租约 | api/worker/connector 三个 PTTL 各自独立续期（~41–48s） |
| 第二 api 实例 | 被租约正确拒绝（`holds the lease`） |
| 前端壳 | `/enterprise`、`/workspace` 均 200 |
| `scripts/compose_smoke.sh` | exit 0，全部检查通过 |

## 两个新增的验证物

### 1. `backend/tests/manual_d5_backflow_container_e2e.py`

只用 `urllib` 打 `http://localhost:5173`，走 nginx → api → PG。11 段、22 条断言。

关键设计：**不信任 API 的自我声明**。`events:batch` 返回 `{accepted: 4}` 之后，脚本
再用 `psql` 直接查 PG 核对 `sessions` / `external_session_bindings` / `messages` 三张表
的行数、role 分布、`metadata_json` 里的 `tool_name` 与 `total_tokens`。

跑通的结果：

```
== 0 ==  health ok / alembic 0001 / PG 16.15
== 1 ==  admin 登录拿到 JWT
== 2 ==  签发 sd_live_ 凭证（scopes: sessions:write, agents:read）
== 3 ==  建会话 201，返回 session_a80fbc90d62245c5
== 4 ==  重复建会话 → 同一行
== 5 ==  首次 4 条 → {accepted:4, duplicates:0}
== 6 ==  重发 4 条 + 1 新 → {accepted:1, duplicates:4}
== 7 ==  同批再发 → {accepted:0, duplicates:5}
== 8 ==  PG 直查：1 session / 1 binding / 5 messages / roles 正确 / tool_name / total_tokens
== 9 ==  另一凭证写同会话 → 404
== 10 == 未知会话 → 404
== 11 == 空批次 {0,0} / 坏 key 401
```

### 2. `test/main/digital-employee/backflow.container.test.ts`（deepchat 侧）

这个更能说明问题：它用**生产代码** `createStaffdeckBackflow` 打真容器，不是另写一份
请求。6 项全过：

- 首次 flush 自动建远端会话（即本次修复的核心，以前必然 404）
- 整轮重放判重且 `delivered` 仍为 4
- 第二轮追加进同一会话
- 开关关闭返回 `DISABLED`，完全不碰网络
- 坏 key 返回 `HTTP_401` 而不抛错（调用方在发消息路径上）
- 新本地会话首报 + 重报均成功

默认跳过（无 `STAFFDECK_CONTAINER_BASE_URL` 时不参与 CI），因为它要等网络，不该
让每次 `vitest run` 都被它拖住。

一处判据修正留作记录：最初想用 `GET /agents/{id}/sessions/{id}` 证明「两轮进同一
会话」，但该端点要 `sessions:read`，回流凭证只有 `sessions:write`，得到 403；且
即便查得到也只说明行存在，不说明事件落在哪一行。改成把两轮共 8 条一起重放、全判重
且 `delivered == 8` —— 这直接证明 8 条都落在同一会话的幂等域内。

## PG 侧独立核对

```
external_session_id                   | session_id               | msgs
local-container-1792804136927         | session_a71c58ab68af4b6e |    8
local-container-1792804136927-rebuilt | session_52eff2a1d3d74692 |    4
local-container-1792804156467         | session_b069dc4c9ad3493a |    8
local-container-1792804156467-rebuilt | session_0b9cdec37228471c |    4
```

每个本地会话严格对应一行远端会话，两轮 8 条无重复。`uq_external_session_credential_id`
唯一约束 `(credential_id, external_session_id)` 在库层面兜底，客户端的内存缓存只是
省一次往返，不是正确性的依赖。

## 操作备忘

```bash
# 起栈
cd packaging/docker && docker compose up -d
# 服务端视角
backend/.venv/Scripts/python.exe backend/tests/manual_d5_backflow_container_e2e.py
# 客户端视角（deepchat 仓库）
STAFFDECK_CONTAINER_BASE_URL=http://localhost:5173 \
  node node_modules/vitest/vitest.mjs run test/main/digital-employee/backflow.container.test.ts
# 停栈（保留数据卷）
cd packaging/docker && docker compose down
# 停栈并清库（换密码或想重来时必须带 -v）
cd packaging/docker && docker compose down -v
```

注意事项：

- **必须绕开代理**。宿主机有 `http_proxy=127.0.0.1:60871` 时，连 `localhost` 也会被
  劫持成 502。curl 用 `--noproxy '*'`，脚本侧置空 `HTTP_PROXY/HTTPS_PROXY` 并把
  `NO_PROXY` 设为 `127.0.0.1,localhost`。
- **PG 密码只在初始化数据卷时生效**。改了 `.env` 里的 `STAFFDECK_PG_PASSWORD` 却复用
  旧卷，表现是启动卡在认证上、看起来像 hang，需要 `down -v`。
- 本机 `tsc` 是 tsgo 后端，偶发 `VirtualAlloc ... errno=1455` / `out of memory` 而退出码
  为 2。这是进程级内存不足，**不是类型错误**——务必看 `grep -c "error TS"` 而不是只看
  退出码；崩溃时输出里一行 `error TS` 都没有。

## 结论

D5 的回流链路在**部署形态**下成立：真 nginx、真 PG、真迁移、真租约，客户端与
服务端的协议理解一致，幂等与鉴权都按设计生效。

M1 的服务端化基座至此也顺带复验了一遍：多进程角色分工、PG 迁移、租约互斥、前端
托管、OpenAPI 暴露全部正常。
