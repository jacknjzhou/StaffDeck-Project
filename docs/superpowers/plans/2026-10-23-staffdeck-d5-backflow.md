# D5 对话回流：实施计划

目标：本地执行会话的消息、工具调用与 token 用量全量回流 StaffDeck，管理端可见。

与 D1–D4 的关键差别：**D5 需要先补服务端**。spec §8 把 D5 标为依赖 M6，而 M6 尚未实现——`backend/app/public_api/` 下没有任何 `external-sessions` 端点。所以本阶段是跨仓库的两段：先让服务端能收，再让客户端会发。

## 0. 现状核对（已确认的事实）

| 事实 | 出处 | 影响 |
|---|---|---|
| `ExternalSessionBinding` 表已存在 | `app/db/models.py:318` | 映射表不用新建，字段已够：`credential_id + external_session_id` 唯一 |
| `Message` 表可直接复用 | `app/db/models.py:1406` | `metadata_json` 可装工具调用与用量，不必另建事件表 |
| `sessions:write` scope 已存在 | `credential_profiles.py` | 不新增 scope，避免动凭证体系 |
| 幂等基建已存在 | `app/public_api/idempotency.py` | 直接用 `Idempotency-Key` + 指纹，不重造 |
| 契约里 `externalSessionId` 是 body 字段 | `schemas.py` 的 `PublicSessionCreate` | 与 `external_user_id` 同一风格，保持一致 |

## 1. 服务端（StaffDeck）

### Task 1：`POST /api/v1/external-sessions`

- 新建 `backend/app/public_api/external_sessions.py`，`router = APIRouter(prefix="/external-sessions")`。
- 请求体 `ExternalSessionCreate`：`external_session_id`（必填）、`agent_id`（必填）、`external_user_id`（可选）、`title`（可选）、`metadata`（可选）。
- 语义：按 `(credential_id, external_session_id)` 幂等 upsert。
  - 已存在 → 200，返回既有 `session_id`（不新建 ChatSession，避免同一本地会话在管理端出现两条）。
  - 不存在 → 201，建一条 `ChatSession` + `ExternalSessionBinding`。
- 依赖 `sessions:write`，且必须校验 `agent_id` 属于本租户（复用 `enforce_agent_access` + 存在性检查）。

### Task 2：`POST /api/v1/external-sessions/{session_id}/events:batch`

- 请求体 `ExternalEventsBatch`：`events[]`，每项 `type ∈ {message, tool_call, usage}`。
  - `message`：`role`、`content`、`created_at`（可选）、`event_id`（客户端侧唯一，用于幂等）。
  - `tool_call`：`name`、`status`、`duration_ms`、`error_code`（可选）。
  - `usage`：`prompt_tokens`、`completion_tokens`、`total_tokens`。
- 幂等粒度是**单个事件**，不是整批：客户端重试会重发整批，必须靠 `event_id` 去重。存进 `Message.metadata_json["replay_event_id"]`，重复的跳过。这一条是关键——整批幂等会被「部分成功」打破。
- 返回 `{ "accepted": n, "duplicates": m }`，客户端据此清队列。
- 未知 `session_id` → 404；跨租户 → 同样 404（不泄露存在性）。

### Task 3：审计与观测

- 回流写入走既有 `APIAuditLog`，`resource_type="external_session"`。
- 注意 D0 已踩过的坑：审计写入不能在同请求事务里，必须走既有审计队列（`_audit_queue`），否则 SQLite 写锁冲突。

## 2. 客户端（deepchat）

### Task 4：契约与上报客户端

- 新建 `src/shared/contracts/routes/digitalEmployeeBackflow.routes.ts`（仅设置开关，本地上报不经 IPC 契约暴露给 renderer）。
- 新建 `src/main/digital-employee/backflow.ts`：
  - `ensureExternalSession(employeeId, sessionId)` → 拿 `session_id`，内存缓存 + 落库。
  - `enqueue(event)` → 写入本地队列（复用 knowledge 的 SQLite 存储位置）。
  - `flush()` → 组批上报，成功清队列，失败按指数退避。
- **组批策略**：按 session 聚合，最多 50 条或 5 秒先到者触发。不逐条发——每轮对话会产生多条事件，逐条会让服务端 QPS 与本地 SQLite 写放大。

### Task 5：接线

- 会话创建时（`provisioning` 之后）调 `ensureExternalSession`；`remote` 员工不上报（服务端已有原会话，回流会重复）。
- 每轮结束后把本轮消息 / 工具调用 / 用量入队。
- 设置开关 `backflowEnabled`，默认 **开**（spec 风险表要求默认开启 + 关闭留本地审计日志）。关闭时必须真的不上报，且不留半条。

### Task 6：测试与联调

- 客户端：组批边界（50 条 / 5 秒）、失败退避、`409` 视为已上报、关闭后不入队、`remote` 不入队。
- 服务端：单事件幂等（重发整批只有新事件被接受）、跨租户 404、未知 agent 404、upsert 不产生两条 ChatSession。
- 联调：用真实 app 走一遍「建会话 → 发 3 条事件 → 重发同一批 → 断言 duplicates=3」。

## 3. 不做的事（明确划界）

- 不做管理端看板 UI（属 M6 观测整合，服务端数据先落地即可被现有会话页读到）。
- 不做 hybrid 的段级归属标记（D2 已留待细化，回流先按整会话口径）。
- 不改 `EXTERNAL_USER_ID_HEADER` 的惰性 header（D0 结论：真正用途在 body，D5 正好用上）。

## 4. 顺序

服务端 Task 1→2→3 先落地并单测通过（客户端没法对着不存在的端点写），再客户端 Task 4→5→6。

## 自审

- 幂等粒度选单事件而非整批，是因为重试必然重发整批——整批幂等会让「第二批里夹一条旧事件」整批被拒。
- 不新建事件表，是因为 `Message.metadata_json` 已够装，且管理端会话页已经读 `Message`，新建表反而要改读取侧。
- 默认开启但必须真的可关：审计盲区风险由「关闭动作留本地审计日志」兜，而不是假装默认开启就等于有记录。
