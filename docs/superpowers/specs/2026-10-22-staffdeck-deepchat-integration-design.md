# StaffDeck × deepchat 融合重构设计（服务端化 + 数字员工对接）

- 日期：2026-10-22
- 状态：设计已确认（用户逐节确认后定稿）
- 关联项目：StaffDeck（本仓库）× deepchat-project（fork：`jacknjzhou/deepchat-project`，分支 `202610211551`）
- 后续：实施计划由 writing-plans 流程产出

---

## 1. 背景与目标

StaffDeck 当前以 PyInstaller 桌面单进程（SQLite + 实例锁）形态发布。本次重构将其转变为**服务端模式**：

1. StaffDeck 作为数字员工的**后端服务与配置管理端**部署在 Linux 服务器，保留管理端（`/enterprise/*`）与用户端（`/workspace/*`）功能页，对外提供**完整 OpenAPI**；
2. deepchat 作为数字员工的**使用入口与本地运行时**：数字员工广场展示 StaffDeck 分发的真实配置，对话在 deepchat 本地 Agent 链路执行（简单 SOP）或回源 StaffDeck 远端执行（复杂 SOP）；
3. 两端的 skill / SOP / 工具 / MCP / 插件能力完成映射对接，对话数据全量回流 StaffDeck 审计归档。

### 非目标（本期不做）

- K8s/Helm 部署（预留 Compose→K8s 路径）、多区域、渠道自动分片；
- deepchat 本地配置（个人 MCP/插件）上行到 StaffDeck 统一管理；
- 双向配置同步联邦（明确只下行配置、只上行对话数据）；
- OpenAI Responses 协议、模型协议层的完整门禁落地（见 `design-model-api-protocols.md`，本次只落 Driver 骨架）。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| SOP 执行位置 | **混合模式**：简单 SOP 文本化注入本地执行；复杂 SOP 回源 StaffDeck Harness v2 远端执行；数字员工配置标记 `executionMode` |
| 服务端化深度 | **生产级**：PostgreSQL、多 Worker、OIDC、渠道连接器进程化拆分 |
| 鉴权模型 | **部署级共享密钥**（API Client + credential）；以 `external_user_id` 标识 deepchat 端用户补审计粒度 |
| 知识库位置 | **本地同步检索**：StaffDeck 提供知识同步/导出 API，deepchat 落地本地知识库副本 |
| 对话数据回流 | **全量回流**：deepchat 本地执行对话回传 StaffDeck 持久化为可观测会话 |
| 对接方案 | **方案一：分发 + 执行双通道**（四通道：配置分发 / 本地执行 / 远端执行 / 工具与回流） |

## 3. 两端现状摘要（设计依据）

### 3.1 StaffDeck（本仓库，行级证据）

- **入口**：`backend/app/main.py:57` 主应用刻意关闭 docs/openapi；`backend/single_port_app.py` 叠加 PilotDeck 反代与 SPA 托管；`main.py:155-156` 已挂载 `/api/v1` public API 子应用（`app/public_api/app.py:22-74`，自带 docs/redoc/openapi.json）。
- **OpenAPI v1 已覆盖**：agents CRUD/gallery、sessions、runs（`/agents/{id}/runs:stream` SSE + 异步 Job + `/events` 续传 + `/result` + artifacts）、SOP 全生命周期、knowledge-bases/general-skills/tools/mcp-servers/scheduled-tasks 子资源、webhooks、credentials/api-clients。鉴权为 Bearer `sd_live_*`（pepper 摘要落库，Principal 携带 scopes）。协议：Idempotency-Key、ETag/If-Match、`application/problem+json`、Webhook HMAC 验签（见 `docs/open-api-v1.md`）。
- **领域模型**（`backend/app/db/models.py`，全表 tenant_id）：AgentProfile(数字员工)、Skill=状态机 SOP（SkillCard nodes/edges，`app/skills/skill_schema.py:72-137`）、GeneralSkill（md 技能包，子进程 Runner）、Tool（HTTP，带凭证）+ MCPServer、KnowledgeBase 文档→桶→块三级、ChatSession（挂 SOP 状态机推进状态）、渠道 7 表、Harness 6 表（SessionLease 跨进程互斥、InvocationRecord 工具幂等）、团队 7 表。
- **执行内核**：Harness v2（`app/core/harness_v2_engine.py`，TurnPlanner→TaskFrame→HarnessTaskAgent；TaskFrameStore/TurnStore/recovery sweeper），网页 SSE / 渠道 intake / public API runs 三入口复用同一内核。
- **部署现状**：PyInstaller 桌面 + SQLite + 实例锁（`runtime_lock.py`）；Linux `staffdeck setup --mode local|lan|public` 已支持 headless；两个远程 `/api/v1` 测试环境在用。多实例障碍：实例锁、SQLite 写锁、飞书子进程 connector、进程内 threading.Lock。
- **现成资产**：仓库根 `skills/` 下 4 个面向外部 Agent 的 OpenAPI 调用技能包（SKILL.md + references），可直接装入 deepchat 技能目录。

### 3.2 deepchat-project（分支 202610211551）

- 已实现「数字员工广场」（docs/superpowers/specs/2026-10-21-digital-employee-square-design.md）：数字员工 = 人设 + 四类能力包（SOP/技能/工具/知识库），当前 10 个角色为 mock 数据。
- **数据契约（zod）**：`id/name/role/description/avatar/capabilities/greeting/systemPrompt/sops[]{title,content}/knowledgeBaseIds/skillIds/tools/enabled/updatedAt`；列表路由 `digitalEmployee.list` 返回 `{employees[], fetchedAt}`。
- **Provider 层已预留 HTTP 实现**：`DigitalEmployeeProvider` 接口 + `HttpDigitalEmployeeProvider` 骨架（baseURL/鉴权头可配）——本方案对接的主插槽。
- 能力注入机制：SOP+人设 → 会话创建时拼接 systemPrompt；技能 → `digital-employee:<id>` agentId 隔离目录；工具 → MCP 工具白名单（会话级覆盖）；知识库 → 首条消息前本地 `similarityQuery` 注入。
- 非目标：客户端不做远端对话执行（但其现有会话体系可扩展触发远端 runs，见 §6.4）。

## 4. 总体架构

```
┌────────────────────────────┐          ┌───────────────────────────────────┐
│  deepchat（Electron 桌面端） │          │  StaffDeck 服务端（Linux，生产级）   │
│                            │          │                                   │
│  数字员工广场页              │  HTTPS   │  FastAPI（多 Worker 进程）          │
│   └ HttpDigitalEmployee    │◄────────►│   ├ /api/v1  OpenAPI（含 Swagger） │
│      Provider（预留插槽）    │  共享密钥  │   │   ├ /digital-employees ①分发   │
│  本地 Agent 执行链路         │          │   │   ├ /knowledge-packages ②同步  │
│   ├ 简单SOP→systemPrompt    │          │   │   ├ /external-sessions  ⑤回流  │
│   ├ 本地技能（SKILL.md）     │          │   │   └ /mcp ③MCP Server 端点      │
│   ├ 本地知识库（同步副本）    │   SSE    │   ├ 管理端 /enterprise（保留）      │
│   ├ MCP工具 = 本地 + 远端    │◄─────────│   ├ 用户端 /workspace（保留）       │
│   └ 复杂SOP→runs:stream ④  │          │   ├ Harness v2 + 渠道连接器(拆分)   │
│  回流上报客户端（本地队列）    │─────────►│   └ PostgreSQL + Redis（租约/队列）│
└────────────────────────────┘          └───────────────────────────────────┘
```

**分工原则**：StaffDeck = 数字员工的后端服务 + 配置管理端 + 复杂流程执行内核 + 审计归档；deepchat = 使用入口 + 本地运行时。管理端与用户端功能页原样保留，仅部署形态由桌面单机改为服务端托管。

## 5. StaffDeck 服务端化改造（生产级）

| 改造项 | 现状 | 目标 | 涉及模块 |
|---|---|---|---|
| 数据库 | SQLite + create_all | PostgreSQL；Alembic 迁移基线（从 create_all 导出首版） | `db/models.py` 全量方言审读（JSON 列、布尔、自增）；`config.py:database_url` |
| 实例锁 | `runtime_lock.py` 单实例 | 移除；互斥改由 Redis 租约承担 | HarnessSessionLease（已有跨进程设计→Redis 化）、channel delivery owner+generation |
| 进程模型 | 单 uvicorn | api / worker（定时任务+Harness 恢复清扫+知识 ingest）/ connector（渠道连接器独立进程）三类进程 | `main.py:74-99` 启动钩子按进程角色拆分；飞书 supervisor 子进程模式平移 |
| 入口与部署 | PyInstaller + single_port_app | Docker Compose（api/worker/connector/postgres/redis/nginx）；新增 `backend/server_app.py`：保留 /enterprise、/workspace 前端托管 + /api/* + /api/v1，剥离 PilotDeck 反代与 site-chat | `single_port_app.py` 拆解 |
| 租户边界 | 请求参数显式传 tenant_id | 从凭证/JWT 推导；`ensure_current_user_tenant`（auth.py:65-69）收紧 | `app/security/auth.py`、`public_api/auth.py` |
| 认证 | 自实现 HMAC JWT（14 天） | 启用 OIDC SSO（README 已预留 OIDC_REDIRECT_URI）；JWT 作为会话凭证保留 | `api/auth.py` |
| OpenAPI | 主 app docs 关闭 | 全部新增端点挂 public_api 子应用，保持单一 openapi.json 来源；主 app 维持关闭 | `app/public_api/app.py` |
| 模型协议层 | provider 字段虚设 | 按 design-model-api-protocols.md 落 api_protocol Driver 骨架（不阻塞对接，门禁后续补） | `api/model_configs.py`、模型运行时 |

## 6. OpenAPI 设计（`/api/v1` 新增，随 v1 演进）

### 6.1 通道①：配置分发

```
GET /digital-employees                      → { employees[], fetchedAt, configVersion }
GET /digital-employees/{id}                 → 单个员工全量配置
GET /digital-employees/{id}:export-skill    → GeneralSkill 导出为 SKILL.md 技能包（zip）
```

- payload **对齐 deepchat zod 契约**（§3.2 字段），另加扩展字段：
  - `executionMode: "local" | "remote" | "hybrid"`（员工级；SOP 级另有 `execution_hint` 供 hybrid 员工内区分单条 SOP）
  - `configHash`（员工配置内容摘要，供 deepchat 增量比对）
- deepchat schema 为非严格对象，未知字段不破坏解析；deepchat 侧可选升级消费扩展字段
- 字段映射：

| deepchat 字段 | StaffDeck 来源 |
|---|---|
| id / name / role / description / avatar / capabilities / greeting / systemPrompt | AgentProfile（+metadata_json 中 roleName 等） |
| sops[]{title,content} | Skill（SkillCard 标题+渲染为文本；仅 `execution_hint=local` 的 SOP） |
| skillIds | GeneralSkill（附 `:export-skill` 下载链接） |
| tools | Tool + MCPServer 工具名（`staffdeck:{slug}` 命名空间 + 公共 MCP 工具名） |
| knowledgeBaseIds | KnowledgeBase id |
| enabled | AgentProfile.status |
| updatedAt | 记录更新时间（内容变更时同步推进，保证 configHash 变化可感知） |

- 增量拉取：`ETag` / `If-None-Match`（复用现有 ETag 机制）。

### 6.2 通道②：知识同步

```
GET /digital-employees/{id}/knowledge-packages          → 增量清单（kb_version + 文档 hash 列表）
GET /knowledge-bases/{kb_id}:export-manifest
GET /knowledge-bases/{kb_id}/documents/{doc_id}:download
```

- 同步协议：清单 hash 对比 → 拉取变更文档/条目 → deepchat 本地重建索引；版本不齐全量重拉；失败用旧副本并标 stale。
- 导出仅覆盖**已授权且开启同步开关**的知识库；导出行为写 AuditLog。
- open-api-v1.md 中"部分接口 next_cursor 固定 null"的边界不阻塞本通道（清单接口一次性返回 hash 列表）。

### 6.3 通道③：StaffDeck MCP Server（工具凭证不出服务端）

- streamable HTTP transport，挂 `/api/v1/mcp`，同 API key 鉴权，按 scopes 过滤可调用工具集。
- 暴露三类工具：
  1. **HTTP 工具代执行**：员工绑定的 Tool（method/url/headers/auth 配置全部留在服务端），执行走 HarnessInvocationRecord 幂等 + per-TaskFrame workspace/预算限制；
  2. **知识检索工具**：封装 `knowledge-bases/{id}:search`，返回带 citations 的结果；
  3. **远端续跑工具**（可选）：封装 runs 提交/查询，供 deepchat Agent 主动委派复杂任务。
- 命名空间 `staffdeck:{tool_slug}`，与员工 `tools[]` 白名单一一对应；deepchat 会话级白名单机制不变。
- 公共（无凭证）MCPServer 配置仍随分发 payload 下发连接信息，由 deepchat 本地直连。

### 6.4 通道④：远端执行（复用现有 API，零新增）

- `executionMode=remote|hybrid` 的员工：deepchat「开始对话」时改走 `POST /agents/{id}/sessions` + `POST /agents/{id}/runs:stream`（SSE），流式渲染到聊天窗。
- SOP 状态由 StaffDeck `awaiting_input` 语义驱动；deepchat 提交用户补充信息即续跑（同 session_id）。
- 断线：SSE `Last-Event-ID` 续传（已有）；不可恢复时 hybrid 员工允许转本地模式降级。
- 会话必须携带 `external_user_id` 与 `metadata.source="deepchat"`。

### 6.5 通道⑤：对话回流

```
POST /external-sessions                        → 建立回流会话（external_session_id 幂等）
POST /external-sessions/{id}/events:batch      → 批量上报消息 / 工具调用 / token 用量
```

- 落库为 `source=deepchat` 的 ChatSession + agent_events；管理端观测看板与 Harness 会话同视图展示，**只读**，不参与 SOP 状态机推进。
- 复用 Webhook 体系：新增可选事件 `digital_employee.local_run.completed`。
- 上报幂等：事件携带客户端生成 idempotency key，服务端 24h 窗口去重。

## 7. 能力映射总表（skill / SOP / 工具 / MCP / 插件）

| StaffDeck 侧 | 方向 | deepchat 侧 | 机制 |
|---|---|---|---|
| AgentProfile 数字员工 | → | DigitalEmployee 契约 | 分发 API ① |
| Skill 状态机 SOP（复杂，`remote`） | → | 远端执行，deepchat 只渲染 | runs:stream ④ |
| Skill 状态机 SOP（简单，`local`） | → | 全文注入 systemPrompt | deepchat 现有注入机制 |
| GeneralSkill（md 技能包） | → | 技能目录（SKILL.md 格式） | `:export-skill` 导入 ① |
| Tool（HTTP 工具，带凭证） | → | MCP 工具白名单（远端代执行） | StaffDeck MCP Server ③ |
| MCPServer 配置（公共、无凭证） | → | deepchat 本地 MCP 直连 | 分发 payload 附连接信息 ③ |
| KnowledgeBase | → | 本地知识库同步副本 | 知识同步 ② |
| 仓库根 `skills/staffdeck-api-*`（4 个调用技能包） | → | deepchat 技能目录（可选安装） | 直接复制/导入 |
| deepchat 本地插件 / 个人 MCP | ✗ 不上行 | 端侧自管 | — |
| deepchat 本地执行对话 | ← | 回流归档 | external-sessions ⑤ |

**原则**：配置和能力**只下行**，对话数据**只上行**，规避双向同步冲突。

## 8. 鉴权与安全

- **共享密钥**：API Client + credential（`sd_live_`），scopes：`agents:read`、`capabilities:read`、`knowledge:read`、`mcp:invoke`、`runs:create`、`runs:read`、`sessions:write`（回流）；credential scope 必须为 client scope 子集（现有机制）。
- 所有请求必带 `external_user_id`；回流与远端会话均按该维度记录，弥补共享密钥的审计粒度。
- 审计：知识导出、MCP 工具执行、密钥轮换、回流写入全部进 AuditLog；工具凭证/知识原文不出服务端（知识经显式同步开关导出且留痕）。
- MCP 端点限流；工具执行受 HarnessLimits（读/文件/总量/结果上限）与 ACTION_BUDGET 约束，预算语义透传给 deepchat。
- 服务端公网部署强制 HTTPS 反向代理（复用 staffdeck setup public 模式约束）。

## 9. 错误处理与降级

| 故障 | 行为 |
|---|---|
| 分发 API 不可达 | deepchat 降级本地 mock（其现有 fetch 降级逻辑），UI 标"配置过期" |
| 知识同步失败 | 沿用旧版本副本 + stale 标记，后台重试；服务端返回 412/409 时全量重拉 |
| 远端 runs 中断 | SSE Last-Event-ID 续传；不可恢复时 hybrid 员工可转本地模式，remote 员工明确报错并保留会话 |
| 回流上报失败 | deepchat 本地队列暂存，幂等键重试；服务端 24h 幂等窗口 |
| MCP 工具超时/预算耗尽 | 结构化错误码（含 `ACTION_BUDGET_EXHAUSTED`），deepchat 提示并允许人工接管 |
| PG 主从/连接故障 | api 进程健康检查失败摘除；Harness 租约过期自动恢复（recovery sweeper） |

## 10. 测试与验收

1. **契约金样测试**：deepchat `digitalEmployeeSchema` 抽为 fixture，StaffDeck 分发 payload 必须通过 zod 解析（两端契约永不漂移的守护测试）。
2. **MCP 协议合规**：streamable HTTP 标准客户端互操作测试（初连/工具列表/调用/错误/限流）。
3. **PG 迁移回归**：现有 backend pytest 全量在 PostgreSQL 上通过；SQLite 兼容期保留 CI 矩阵。
4. **并发正确性**：多 Worker 下 Harness 租约、channel delivery、幂等记录、`external_session_id` 去重的竞争测试。
5. **端到端验收路径**：
   - 管理端配置员工（混合执行模式）→ deepchat 广场拉取真实配置 → 本地对话（SOP 注入 + 本地知识引用 + 远端 MCP 工具）→ 管理端看板可见回流；
   - 切换 remote 员工 → deepchat runs:stream 完整对话 → `awaiting_input` 续跑 → 同一看板。

## 11. 实施里程碑

| 阶段 | 内容 | 交付标志 |
|---|---|---|
| M1 | 服务端化基座：PG / 三类进程 / Compose / OIDC / 租户推导收紧 | `docker compose up` 后 `/api/health`、管理端、`/api/v1/docs` 可用 |
| M2 | 分发 API + deepchat HttpDigitalEmployeeProvider 对接 | deepchat 广场显示真实配置（mock 退役为降级路径） |
| M3 | 知识同步 + 技能导出 | 本地对话带知识引用与本地技能 |
| M4 | StaffDeck MCP Server | deepchat 工具白名单内远端工具可执行且凭证不出服务端 |
| M5 | 远端执行通道 | remote/hybrid 员工在 deepchat 完整对话闭环（含续跑） |
| M6 | 回流 + 管理端观测整合 + 审计 | 本地/远端会话同看板；审计可查 |

依赖关系：M1 → M2 → {M3, M4, M5} → M6；M3/M4/M5 可并行。

## 12. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| deepchat zod 契约演进（扩展字段、schema 收紧） | 契约金样测试双向锁定；StaffDeck 分发 payload 保持契约超集 |
| SOP 复杂度判定标准（local vs remote） | 初期人工在管理端标 `execution_hint`；后续可按 SOP 节点数/人工节点/工具编排特征自动建议 |
| 共享密钥泄露面 | 密钥轮换流程（credential 已支持）+ 部署级网络隔离 + 全量审计；如后续需要个人粒度，升级路径为每用户账号密钥（OpenAPI 已支持，无需改架构） |
| 知识本地副本的合规风险 | 同步开关默认关闭 + 导出审计；敏感 KB 不开放同步 |
| 飞书等渠道 connector 进程化拆分工作量 | 该拆分仅 M1 范围内的进程编排调整，durable inbox/outbox 内核已就绪 |

## 13. deepchat 侧改造清单（供对方团队对齐）

1. `HttpDigitalEmployeeProvider` 完整实现：baseURL/鉴权头/ETag 增量/降级 mock（骨架已预留）；
2. 契约消费扩展字段：`executionMode`、`configHash`、`tools` 命名空间；
3. 「开始对话」按 executionMode 分流：local → 现有注入链路；remote/hybrid → 会话创建改调 sessions + runs:stream，SSE 渲染与 `awaiting_input` 续跑 UI；
4. 知识同步任务（清单对比 → 下载 → 本地索引重建 → stale 标记）；
5. 技能导入：`:export-skill` 包 → deepchat 技能目录（其 Skills 管理已有导入能力）；
6. MCP 白名单接入 `staffdeck:*` 远端工具（streamable HTTP 客户端其已有）；
7. 回流上报客户端：本地队列 + 幂等重试；
8. 部署配置：`STAFFDECK_BASE_URL`、`STAFFDECK_API_KEY`、`STAFFDECK_EXTERNAL_USER_ID` 等构建时/运行时注入（其对齐 `docs/managed` 构建期端点注入惯例）。
