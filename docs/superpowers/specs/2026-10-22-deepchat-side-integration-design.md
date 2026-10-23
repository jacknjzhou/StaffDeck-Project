# deepchat 数字员工接入 StaffDeck 服务端方案

- 日期：2026-10-22
- 状态：设计已确认（由总设计文档拆分而来；建议移入 deepchat 仓库 `docs/superpowers/specs/` 维护并纳入其 SDD 流程）
- 范围：deepchat-project（fork：`jacknjzhou/deepchat-project`，基线分支 `202610211551`）。StaffDeck 侧改造见姊妹文档 `2026-10-22-staffdeck-server-side-design.md`
- 技术栈约束：Electron + Vue3 + TS + zod 契约路由 + Pinia + vue-i18n + Vitest；禁止新增运行时依赖；不改动现有 agentStore（deepchat/acp 体系零侵入）

---

## 1. 背景与目标

deepchat 已实现「数字员工广场」（设计见 `docs/superpowers/specs/2026-10-21-digital-employee-square-design.md`）：数字员工 = 人设 + 四类能力包（SOP/技能/工具/知识库），当前 10 个角色（财务/销售/项目管理/人事/IT/采购/数据分析/法务/行政/市场）为 mock 数据。

本方案将 mock Provider 替换为对接 StaffDeck 服务端，使 deepchat 成为数字员工的**使用入口 + 本地运行时**：

1. 广场与对话使用 StaffDeck 分发的真实数字员工配置；
2. 按员工 `executionMode` 分流：简单 SOP 本地注入执行，复杂 SOP 回源 StaffDeck 远端执行；
3. 技能、工具（MCP）、知识库三类能力真实生效；
4. 本地执行对话全量回流 StaffDeck 审计归档。

### 非目标

- 不做远端对话执行服务端（执行内核在 StaffDeck，deepchat 只是调用方）；
- 不把数字员工并入现有 agentStore（保持独立 store + 顶级路由）；
- 不上行 deepchat 本地配置（个人 MCP/插件留端侧自管）；
- 不做数字员工配置编辑 UI（配置来源于 StaffDeck，客户端只读）。

## 2. 已确认的关键决策（两端共识）

| 决策点 | 结论 |
|---|---|
| SOP 执行位置 | 混合模式：员工级 `executionMode`（local/remote/hybrid）+ SOP 级 `execution_hint` |
| 鉴权 | 部署级共享密钥（`sd_live_` API key）；请求必带 `external_user_id` 标识端上用户 |
| 知识库 | 本地同步检索：StaffDeck 分发知识包，deepchat 落地本地副本并 `similarityQuery` |
| 数据回流 | 本地执行对话全量回流（本地队列 + 幂等重试） |
| 原则 | 配置和能力只下行，对话数据只上行 |

## 3. 共享数据契约（zod，两端锁定）

deepchat 侧现有契约（`src/shared/contracts/routes/digitalEmployee.routes.ts`）为权威起点，StaffDeck 分发 payload 以它为超集：

```ts
export const sopDocumentSchema = z.object({
  title: z.string().min(1).max(100),
  content: z.string().min(1).max(20000)
})

export const digitalEmployeeSchema = z.object({
  id: z.string().min(1).max(64),
  name: z.string().min(1).max(50),
  role: z.string().min(1).max(50),
  description: z.string().max(500),
  avatar: z.string().max(256).nullable(),
  capabilities: z.array(z.string().min(1).max(30)).max(20),
  greeting: z.string().max(500).nullable(),
  systemPrompt: z.string().min(1).max(10000),
  sops: z.array(sopDocumentSchema).max(20),
  knowledgeBaseIds: z.array(z.string().min(1)).max(10),
  skillIds: z.array(z.string().min(1)).max(10),
  tools: z.array(z.string().min(1)).max(20),
  enabled: z.boolean(),
  updatedAt: z.string().min(1)
})
// StaffDeck 扩展字段（本方案新增消费）：
//   executionMode: 'local' | 'remote' | 'hybrid'
//   configHash: string          — 员工配置摘要，增量比对
//   skills: [{ id, name, downloadUrl }] — GeneralSkill 导出包下载信息
//   knowledgePackagesUrl — 该员工知识同步清单端点
```

- 增量拉取：`GET /digital-employees` 支持 `ETag`/`If-None-Match`，304 时沿用本地缓存；
- **契约守护**：StaffDeck 仓库以 deepchat `digitalEmployeeSchema` 为 fixture 做金样测试；deepchat 侧同样保留对真实分发样本的解析测试，两端永不漂移。

## 4. StaffDeck 服务端依赖（外部依赖清单）

| 能力 | 端点 | 状态 |
|---|---|---|
| 配置分发 | `GET /api/v1/digital-employees`、`/{id}`、`/{id}:export-skill` | StaffDeck M2 |
| 知识同步 | `GET /api/v1/digital-employees/{id}/knowledge-packages`、`/knowledge-bases/{kb}:export-manifest`、`/documents/{doc}:download` | StaffDeck M3 |
| MCP 工具网关 | `/api/v1/mcp`（streamable HTTP，工具命名空间 `staffdeck_{slug}`） | StaffDeck M4 |
| 远端执行 | `POST /agents/{id}/sessions` + `POST /agents/{id}/runs:stream`（SSE，Last-Event-ID 续传） | 已有 |
| 对话回流 | `POST /external-sessions`、`POST /external-sessions/{id}/events:batch` | StaffDeck M6 |
| 鉴权 | Bearer `sd_live_` 共享密钥；scopes 见 StaffDeck 方案 §8 | 已有 |

各端点协议沿用 StaffDeck OpenAPI v1 惯例：Idempotency-Key、ETag/If-Match、`application/problem+json`、X-Request-ID。

## 5. deepchat 侧改造设计

### 5.1 HttpDigitalEmployeeProvider 完整实现

现状：`src/main/digital-employee/provider.ts` 已有 `DigitalEmployeeProvider` 接口与 mock 实现，HTTP 骨架预留。

改造：
- **路径对齐**：现有骨架 `HttpDigitalEmployeeProvider.list()` 请求 `${baseUrl}/employees`；StaffDeck 分发端点是 `GET {staffdeck}/api/v1/digital-employees`。D2 落地时把 `STAFFDECK_BASE_URL` 约定为 `{host}/api/v1`，provider 改请求 `${baseUrl}/digital-employees`，响应 `{employees[], configVersion, fetchedAt}` 与 `digitalEmployeeListRoute.output` 完全一致。
- 条件请求：列表与单员工端点均返回 `ETag`，服务端按 RFC 9110 §13.1.2 做弱校验器与多值列表匹配；`If-None-Match` 命中返回 `304`（带 `ETag`、无 body）。provider 首次拉取存 `configVersion`，之后带 `If-None-Match` 请求，`304` 时直接复用本地缓存。
- `providerFactory.ts` 按运行配置选择 mock / http 实现（配置缺失时保持 mock，供离线与开发）；
- `HttpDigitalEmployeeProvider`：读取 `STAFFDECK_BASE_URL`、`STAFFDECK_API_KEY`、`STAFFDECK_EXTERNAL_USER_ID`（构建时注入，对齐 `docs/managed` 构建期端点注入惯例）；
- 拉取策略：进入广场/启动时拉取，命中 ETag 缓存则不重新解析；`configHash` 变化的员工标记「配置已更新」；
- 失败降级：网络/服务端错误 → 回落 mock 数据并在广场页显示「配置过期」横幅（复用现有 fetch 降级逻辑）。

### 5.2 executionMode 分流与远端执行通道

现状：广场「开始对话」→ 本地新建会话 → 注入 systemPrompt/能力。

改造：
- 按 `executionMode` 分流：
  - `local`：现有链路不变（SOP+人设 → systemPrompt 拼接；技能 → `digital-employee:<id>` agentId；工具 → MCP 白名单；知识 → 首条消息前 `similarityQuery`）；
  - `remote`：会话创建改调 `POST /agents/{id}/sessions`（携带 `external_session_id`=本地会话 id、`external_user_id`、`metadata.source="deepchat"`），消息经 `POST /agents/{id}/runs:stream`（SSE）发送，流式渲染进聊天窗；`awaiting_input` 状态时展示补充信息输入框，用户提交即同 session 续跑；
  - `hybrid`：按单条 SOP 的 `execution_hint` 路由——local SOP 注入、remote SOP 触发远端 runs；本地/远端在会话内可切换，切换时保留上下文摘要；
- SSE 处理：`run.output.delta` 追加渲染；`run.output.replace` 整体替换；`run.output.completed` 停止流式；断线携带 `Last-Event-ID` 重连续传；`ACTION_BUDGET_EXHAUSTED` 等结构化错误码转为可理解的 UI 提示；
- 聊天页徽标扩展：`local`（端侧）/ `remote`（云端执行）标识。

### 5.3 技能导入

- 分发 payload 的 `skills[].downloadUrl` → 下载 `:export-skill` zip（SKILL.md 包格式，与 deepchat 技能目录同构）；
- 走现有 Skills 管理的导入能力装进 `digital-employee:<id>` agentId 隔离目录；主进程在会话创建时按 `skillIds` 解析注册，不引入「技能安装」确认流程（企业分发场景默认信任）；
- 仓库根 `skills/staffdeck-api-*` 4 个 OpenAPI 调用技能包可作为可选内置项安装（使本地 Agent 具备直接调用 StaffDeck OpenAPI 的能力）。

> **实现修订（2026-10-23，D4 交付后据实记录）**：上面两条里的 `digital-employee:<id>` 与 `skillIds` **都不能照字面实现**，实测各自会静默失效。
>
> **agentId 不能带冒号，也没有「每员工一个 agent」。** 数字员工的全部对话都跑在内置 agent `deepchat` 下（`DIGITAL_EMPLOYEE_AGENT_ID = 'deepchat'`），不存在可以按员工分配的 agent 目录。而且 `assertSafeSkillAgentId`（`main/skill/agentSkillRoots.ts`）只接受 `^[A-Za-z0-9][A-Za-z0-9._-]*$`，冒号直接拒绝；Windows 目录名同样不允许冒号。**技能隔离靠名字前缀**：安装时用 `targetName = de-{employeeId}-{slug}`（`main/digital-employee/employeeScope.ts`），装进内置 agent 的技能根。
>
> **`downloadUrl` 不可用，必须用客户端 baseUrl 重拼。** 该字段由服务端 `get_settings().normalized_tool_base_url` 拼出（默认 `http://localhost:5173`），与客户端实际访问地址无关——D3 的 `bundleUrl` 同病。实际请求 `{clientBaseUrl}/digital-employees/{employeeId}:export-skill?slug={slug}`，slug 是 **query 参数**。
>
> **`skills[].slug` 必须在契约里显式声明。** zod 会剥掉未声明的键，漏声明就变成「每个技能都没有 slug」，导出请求全部 400/404，而界面上只表现为「技能没装上」。已在 `digitalEmployee.routes.ts` 补上（可选，兼容旧 payload）。
>
> **会话的 `activeSkills` 必须用安装后的名字。** 装出来的名字带员工前缀，payload 里的 slug 在本地技能目录中不存在，直接传 `skillIds` 会安静地激活不到任何技能。准备工作（`digitalEmployee.provision`）返回 `skillNames`，会话用它；仅在未做过准备时退回 `skillIds`。
>
> **解包复用 `extractSkillArchive`**，已用真实导出包验证（zip 根即 `SKILL.md`，见 `test/main/skill/staffdeckSkillArchive.test.ts` 与 fixture）。版本判据用 `skills[].id`——服务端每次保存都换 id，无需额外做内容 hash。

### 5.4 知识同步任务

- 新增后台同步器（主进程，worker 化）：
  1. `GET /digital-employees/{id}/knowledge-packages` 取清单（每包 `configHash` + 每个 concept 的 `hash` / `size`）；
  2. 与本地索引对比 hash，差异概念经 `{kb_id}/concepts/{concept_id}:download` 拉取；
  3. 重建本地知识库副本索引（复用现有 knowledgeClient / 本地向量检索）；
- 一致性规则：版本不齐（412/409）→ 全量重拉；同步失败 → 沿用旧副本 + `stale` 标记（对话 UI 可提示「知识可能过期」）；后台定时重试；
- 同步粒度按员工（只同步其 `knowledgeBaseIds`），不全量镜像租户知识。
- **实现对齐（2026-10-22）**：知识包以 OKF bundle（zip）传输，内含 `index.md` + `log.md` + 每概念一个 `{concept_id}.md`；清单里每个 concept 带 `hash`（`sha256` 前 16 位）与 `size`（UTF-8 字节数），本地据此跳过未变概念。同步开关是 `KnowledgeBaseVersion.metadata_json.deepchat_sync_enabled === true`，未开启的库在清单里根本不出现。服务端已无 `:export-manifest` 端点——清单接口本身即 manifest。

### 5.5 MCP 白名单接入远端工具

- 员工 `tools[]` 中 `staffdeck_{slug}` 命名空间的工具 → 注册为 streamable HTTP MCP server（指向 `/api/v1/mcp`），由 deepchat 现有 MCP 客户端连接；
- 会话级工具白名单机制不变（`disabledAgentTools` 取补集）；
- 公共 MCP 工具（分发 payload 附连接信息、无凭证的）→ deepchat 本地直连，与远端工具在同一白名单体系中共存。

> **命名修订（2026-10-23，D4 开工前核实）**：原约定的 `staffdeck:{slug}` 在 deepchat 侧**一个工具都注册不上**。`main/mcp/toolManager.ts:544` 用 `/^[a-zA-Z0-9_-]+$/` 校验最终工具名，不匹配就 `continue` 丢弃，且只打一行 `console.error`——不报错、不中断，表现为「MCP server 连上了但 tools 为空」。冒号必然触发，slug 里的点（`expense.submit`）同样触发；OpenAI 兼容 API 的 `function.name` 也有同构限制（且上限 64 字符）。
>
> 因此服务端改为 `staffdeck_` 前缀 + 非法字符归一 + 摘要后缀，规则见 StaffDeck 侧 spec 的同名修订说明。对 deepchat 的影响：**白名单仍是字符串精确匹配，无需改动**——`employee.tools[]` 与 MCP 工具名由服务端同一函数产出，逐字相等。但 D4 接线时要守住一条：不得在客户端二次加工工具名，任何 sanitize 都会让两侧再次错位。

**接入约束（M4 已交付，D4 按此实现）**：

- 端点 `{host}/api/v1/mcp`，`type: 'http'`，请求头 `Authorization: Bearer <sd_live_*>`（scope `mcp:invoke`）；
- **员工寻址走 URL**：端点有两个形态，`POST {host}/api/v1/mcp/agents/{agent_id}`（推荐）与 `POST {host}/api/v1/mcp`。前者把员工写进 URL，因为 deepchat 的 `MCPServerConfig` 只有静态 `customHeaders`，无法在每次 `tools/list` 里注入 `_meta`；后者需在每次请求携带 `params._meta.agentId`，或使用绑定单员工的凭据。两种来源同时出现时以路径为准；都没有则返回 JSON-RPC `-32602`。员工级凭据（`agent_id` 绑定的 key）已允许申请 `mcp:invoke`；
- `initialize` 响应头带 `Mcp-Session-Id`，后续请求建议回带；服务端不做强制校验（多实例下无粘性），缺失不会报错；
- `GET /mcp` 返回 405：不提供服务器主动推送，客户端不要依赖 SSE 长连接；
- 工具名逐字等于 `tools[]` 里的字符串，不存在大小写/分隔符转换；`tools/list` 里出现的每个工具都保证可被 `tools/call` 派发（已由服务端 e2e 守护）；
- 工具执行是同步的：`execution_mode=detached` 的 HTTP 工具返回的是「已受理」凭证而非最终结果，D4 需自行决定是否轮询。

> **实现修订（2026-10-23，D4 交付后据实记录）——联调实测结果**
>
> | 检查项 | 实测 |
> |---|---|
> | `initialize` → `POST /mcp/agents/{id}` | 200，`serverInfo = {name: 'StaffDeck MCP', version: '1.0.0'}` |
> | `tools/list` | 3 个工具，名字形如 `staffdeck_expense_submit_2cf85157`，均匹配 `/^[a-zA-Z0-9_-]+$/`，最长 40 字符（未触 64 上限） |
> | `tools/call` | 200，`isError: false` |
> | `GET /mcp/agents/{id}` | **405**（确认，客户端不得依赖 SSE 长连接） |
> | 坏 key / 未知 agent | **401** + `application/problem+json`（`INVALID_API_KEY`），不是 JSON-RPC 错误 |
> | `:export-skill` | 200，`application/zip`，根目录仅 `SKILL.md` |
>
> **必须在会话创建前完成注册，这是硬顺序。** 会话级白名单是「当前可用工具列表的补集」：注册晚于 renderer 读取工具列表，第一轮对话就一个 `staffdeck_*` 工具都没有，且之后不会自动重试（白名单在建会话时写死）。因此准备工作是一条显式 route（`digitalEmployee.provision`），由 renderer 在 `loadTools()` **之前**调用，而不是后台异步预热。
>
> **注册是幂等的。** 每次会话都会请求准备，但 MCP server 配置未变时跳过写入——每次 `addMcpServer` 都会触发 MCP 全量重连，工具列表会在对话中途闪动。
>
> **准备永不阻塞对话。** 两条链路（工具注册、技能安装）各自独立吞掉异常并记录失败原因；任一条失败都只意味着「能力少一点」。同一员工的并发准备共享一次在途轮次，因为两者都写共享设置。
>
> **不做准备的两种情况**：`executionMode = remote`（服务端的工具与技能不需要在本地再放一份，且会污染工具列表）与 provider 降级到 mock（假员工 id 只会换来 404）。`remote` 分支还会顺手清掉可能残留的注册。

### 5.6 对话回流上报客户端

- 触点：本地执行会话（`executionMode=local` 及 hybrid 的本地段）每轮结束后上报；
- 实现：`POST /external-sessions`（`external_session_id` = 本地会话 id，幂等）建立映射 → 消息/工具调用/token 用量组批经 `POST /external-sessions/{id}/events:batch` 上报；
- 可靠性：本地 SQLite 队列暂存，指数退避重试；服务端 24h 幂等窗口，409 冲突视为已上报跳过；
- 隐私：上报内容与远端执行同一审计口径；用户可在设置中关闭回流（关闭时管理端无该会话记录，UI 明示）。

> **实现修订（2026-10-23，D5 交付后据实记录）**：上面两条端点都改了，原因分别是「重复」和「会丢数据」。
>
> **建会话不需要新端点。** 原文的 `POST /external-sessions` 与既有 `POST /agents/{agent_id}/sessions` 功能重叠——后者本来就接受 `external_session_id`，并按 `(credential_id, external_session_id)` 幂等 upsert（`sessions.py` 的 `create_public_session_row`），`StaffdeckRemoteClient.createSession` 一直在走它。再开一个就是同一规则两处实现，一旦漂移，同一对话会在服务端产生两行、消息各写一半。**故只保留事件追加端点**，路径放进既有命名空间：`POST /agents/{agent_id}/sessions/{session_id}/events:batch`。
>
> **幂等必须按单事件，不能按整批。** 客户端重试必然重发整批。整批幂等的话，只要重发时夹带一条已存事件，整批就被 409 拒掉，随行的新事件全部丢失。服务端改为按 `event_id` 逐条去重（存于 `Message.metadata_json["external_event_id"]`），响应 `{accepted, duplicates}`；`accepted: 0` 是「重试生效」，不是错误。
>
> **鉴权三层，第三层不可省。** agent 属本租户 → session 属该 agent 且同租户 → **该凭证在绑定表里拥有这个 session**。只校验租户的话，任何同租户 key 都能往它叫得出名字的会话里写，等于把只读权限升级成写权限。
>
> **远端员工不上报。** `executionMode=remote` 的对话本来就在服务端，再报一次会让管理端看到每条消息两份。
>
> **开关缺省视为开启。** 读 `!== false` 而非 `=== true`：误关会留审计盲区，误开只是多归档一条用户本来就期望归档的对话。
>
> **失败即停，不跳过。** 跳过失败块会在归档里留一个洞，而调用方分不清「已发过」和「丢了」，那一段就永远补不回来。
>
> 服务端实现 `backend/app/public_api/external_sessions.py`，客户端 `src/main/digital-employee/backflow.ts` + `backflowRoutes.ts`。

### 5.7 部署与配置注入

- 构建时/运行时配置：`STAFFDECK_BASE_URL`、`STAFFDECK_API_KEY`、`STAFFDECK_EXTERNAL_USER_ID`（端上用户唯一标识生成规则：首启生成 UUID 并持久化）、`STAFFDECK_SYNC_INTERVAL`（知识同步间隔）；
- 密钥存储走 deepchat 现有安全存储（对齐 MCP 凭证管理机制），不落明文配置文件。

## 6. 错误处理与降级（deepchat 侧）

| 故障 | 行为 |
|---|---|
| 分发 API 不可达 | 降级 mock（现有逻辑）+「配置过期」横幅 |
| 知识同步失败 | 旧副本 + stale 标记，后台重试 |
| 远端 runs 中断 | Last-Event-ID 续传；不可恢复时 hybrid 转本地模式，remote 明确报错并保留会话 |
| 回流上报失败 | 本地队列暂存 + 退避重试 |
| MCP 工具超时/预算耗尽 | 结构化错误码转 UI 提示，允许人工接管 |

## 7. 测试计划（deepchat 侧）

1. **契约解析测试**：真实分发样本（含扩展字段）过 `digitalEmployeeSchema` zod 解析；非法样本拒绝；
2. **Provider 测试**：ETag 缓存命中、降级 mock、configHash 变化标记；
3. **分流测试**：local/remote/hybrid 三类员工的会话创建参数与注入行为（store 层断言 client 调用参数，沿用现有测试纪律）；
4. **SSE 消费测试**：delta 追加 / replace 替换 / completed 停止 / Last-Event-ID 续传 / awaiting_input 续跑；
5. **知识同步测试**：hash 对比增量、stale 降级、全量重拉；
6. **回流测试**：组批、幂等重试、关闭回流后不上报；
7. **回归纪律**：vitest 与 vue-tsc/oxlint 分开跑；zh-CN 为源语言，en 同步，其余 locale 补 key；每任务单独 commit。

## 8. 实施里程碑（deepchat 侧，与 StaffDeck M 对应）

| 阶段 | 内容 | 依赖 | 交付标志 |
|---|---|---|---|
| D1 | HttpDigitalEmployeeProvider 完整实现 + 配置注入 + 降级 | StaffDeck M2 | 广场显示真实配置，mock 仅作降级 |
| D2 | executionMode 分流 + remote SSE 渲染 + awaiting_input 续跑 | StaffDeck M5（远端字段补齐） | remote 员工完整对话闭环 |
| D3 | 知识同步任务 + 本地索引 | StaffDeck M3 | 本地对话带知识引用与 stale 提示 |
| D4 | 技能导入 + `staffdeck:*` MCP 工具接入 | StaffDeck M2/M4 | 本地技能生效；远端工具可执行 |
| D5 | 回流上报客户端 + 设置开关 | StaffDeck M6 | 管理端看板可见本地执行会话 |

D1 → {D2, D3, D4} → D5；联调环境使用 StaffDeck M1 交付的 Compose 部署。

## 9. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| 契约演进（StaffDeck 扩展字段增多） | deepchat 侧 schema 保持非严格模式，未知字段忽略；扩展字段消费逐项开关 |
| SSE 在 Electron 渲染进程的稳定性 | 主进程统一消费 SSE → 契约事件转发 renderer（对齐现有事件系统规则） |
| 本地知识副本的隐私合规 | 知识同步仅覆盖开启开关的 KB；企业策略可整体禁用 D3 |
| 回流关闭导致审计盲区 | 默认开启 + 关闭动作留本地审计日志 |
| hybrid 会话内本地/远端切换的上下文丢失 | 切换时生成上下文摘要并注入远端会话首条消息（实现为 D2 细化项） |
