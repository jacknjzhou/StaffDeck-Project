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

> **实施中修正**：原计划新建 `POST /external-sessions` 建会话端点，写完后发现
> `POST /agents/{agent_id}/sessions` 已经接受 `external_session_id` 且按
> `(credential_id, external_session_id)` 幂等 upsert。再建一个就是同一规则两处实现，
> 一旦漂移，同一对话会在服务端产生两行、消息各写一半。因此只保留真正缺失的事件追加端点，
> 并放进既有 sessions 命名空间。

### Task 1（已取消）：建会话端点

不需要。`create_public_session_row` 已实现完整的幂等 upsert，deepchat 的 `createSession`
本来就走这条路。

### Task 2：`POST /api/v1/agents/{agent_id}/sessions/{session_id}/events:batch`

- 实现于 `backend/app/public_api/external_sessions.py`，复用 sessions 路由前缀。
- 请求体 `ExternalEventsBatch`：`events[]`，每项 `type ∈ {message, tool_call, usage}`。
  - `message`：`role`、`content`、`created_at`（可选）、`event_id`（客户端侧唯一）。
  - `tool_call`：`name`、`status`、`duration_ms`、`error_code`（可选）。
  - `usage`：`prompt_tokens`、`completion_tokens`、`total_tokens`。
- 幂等粒度是**单个事件**，不是整批：客户端重试会重发整批，整批幂等会让「第二批里夹一条旧事件」整批被拒。
- 鉴权三层：agent 属于本租户 → session 属于该 agent 且同租户 → **该凭证在绑定表里拥有这个 session**。第三层不能省：只校验租户，任何同租户 key 都能往它能叫出名字的会话里写，等于把只读权限升级成写权限。
- 落库复用 `Message` 表，事件 id 存 `metadata_json["external_event_id"]`；不新建事件表，因为管理端会话页本来就读 `Message`。

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
