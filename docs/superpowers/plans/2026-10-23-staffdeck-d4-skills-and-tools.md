# StaffDeck 接入 D4：技能导入 + MCP 工具接入 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `executionMode = local` / `hybrid` 的数字员工在 deepchat 本地对话时，① 能调用 StaffDeck 暴露的 `staffdeck_*` 远端工具（HTTP 工具代执行 + 知识检索，凭证留在服务端）；② 能加载 StaffDeck 分发的技能包（`skills[].downloadUrl` → SKILL.md zip）。两者都是「配置下行」，不需要新的上行通道。

**Architecture:** 两条独立链路，都落在 main 侧、都由分发 payload 驱动：

1. **MCP 工具**：会话创建前，按员工是否存在 `staffdeck_*` 工具决定是否注册一个 `type: 'http'` 的 MCP server（URL = `{baseUrl}/mcp/agents/{employeeId}`，鉴权 header 取自现有凭证）。注册后由 deepchat 既有 `ServerManager` + `ToolManager` 连接，工具自动进入模型工具列表；员工 `tools[]` 白名单继续用 `disabledAgentTools` 取补集，**客户端不得对工具名做任何二次加工**（服务端已保证名字通过深层校验）。
2. **技能导入**：按员工 `skills[]` 逐个下载 zip，用既有 `extractSkillArchive` 解包到临时目录，再走 `SkillService.installFromFolderForAgent(agentId, dir)` 装进 `digital-employee:<employeeId>` 隔离目录；版本化判据用 payload 的 `skills[].id`（服务端每次保存都会变）或内容 hash，避免每轮重复安装。

**Tech Stack:** Electron main + zod 契约路由 + Vitest + `fflate`（已装）+ 全局 `fetch`。不新增运行时依赖。

## Global Constraints

- 禁止新增运行时依赖；zip 解包复用 `src/main/skill/archive.ts:extractSkillArchive`（已含 zip-slip / 压缩比 / 体积上限防护）
- API Key 只在 main 内取用，绝不出现在 route output / renderer state / 日志
- **工具名逐字相等**：`employee.tools[]` 与 MCP `tools/list` 的名字由服务端同一函数产出（2026-10-23 已改为 `staffdeck_` 前缀 + 摘要，见 StaffDeck commit `74158c5`）。客户端任何 sanitize 都会让白名单错位，表现为「工具被禁用但没人知道为什么」
- **MCP server 名字**必须稳定且不会与用户自建 server 冲突，用 `staffdeck:<employeeId>` 之前的 `staffdeck-{employeeId}` 形式（server 名不进模型，只作本地键，但 `removeMcpServer` 要能精确命中）
- 单个员工失败不得影响其他员工；网络失败一律降级（没有工具就用不了工具，不阻塞对话）
- 技能安装失败不得阻塞会话创建——记 warning，继续
- 测试目录镜像 src 结构；`test/main/**` 用 `../../../src/`
- pnpm shim 损坏——直接执行：`C:\Users\SRIBD\.workbuddy\binaries\node\versions\22.22.2-6\node.exe node_modules/vitest/vitest.mjs run <path>`
- vitest 与 vue-tsc / oxlint 必须分开串行执行
- Lint = oxlint；每任务单独 commit，不 `--no-verify`
- i18n：zh-CN 为源语言，en 真实翻译，其余 18 locale 复制 en 值

---

## 协议核实结论（对照 StaffDeck 源码，非推测）

| 事实 | 证据 | 对 D4 的影响 |
|---|---|---|
| MCP 端点：`POST /api/v1/mcp` 与 `POST /api/v1/mcp/agents/{agent_id}`，scope `mcp:invoke` | `public_api/mcp.py:144-176` | 必须走带路径段的那一个——标准 MCP 客户端的 `tools/list` 只发 `{}`，无法注入 `_meta` |
| GET 返回 405 + `Allow: POST` | `public_api/mcp.py:178-190` | deepchat 的 `StreamableHTTPClientTransport` 若发 GET 探测会拿到 405，需确认它不把它当致命错误 |
| 鉴权 `Authorization: Bearer sd_live_...`，越权返回 **HTTP 403**（不降级为 JSON-RPC 错误） | `public_api/mcp.py:118-122` | 403 必须能被识别为「凭证问题」而非「工具报错」 |
| 工具名 = `staffdeck_{slug}`（含摘要后缀），仅三个字段 `name`/`description`/`inputSchema` | `mcp_server/catalog.py:47-68` | 无 `inputSchema` 缺省为 `{"type":"object","properties":{}}` |
| 知识检索是虚拟工具 `staffdeck_knowledge_search`，**仅在员工有已开启同步的 KB 时出现** | `mcp_server/catalog.py:20-45` | 工具集是动态的，不能缓存成静态清单 |
| `tools/call` 的 `name` 必须逐字命中目录，否则 `isError: true` + `TOOL_NOT_FOUND` | `mcp_server/invoker.py:108-115` | 名字改写在客户端侧会直接失效 |
| 技能导出：`GET /api/v1/digital-employees/{agent_id}:export-skill?slug={slug}`，返回 `application/zip`，根为 `SKILL.md` | `public_api/digital_employees.py:54-110`、`skill_package.py:9-34` | slug 是 **query 参数**，不是路径段 |
| 技能 zip 内含 `skill_files_json` 声明的路径 + 兜底 `SKILL.md`；路径已做 traversal 过滤 | `skill_package.py:22-45` | 仍需用 deepchat 自己的 `extractSkillArchive` 再过一遍 |
| 分发 payload：`skills[] = {id, name, slug, downloadUrl}`，`downloadUrl` 由服务端 `tool_base_url` 拼出 | `digital_employees/payload.py:114-125` | 与 D3 的 `bundleUrl` 同病：**不可直接用**，要用客户端 baseUrl 重新拼 |
| `skillIds[]` 与 `skills[].slug` 同源（都是 `_slug(row)`） | `payload.py:107`、`188-190` | 白名单可用 slug 对齐 |

### 需要特别当心的一点

`downloadUrl` 里带的是 `base_url = get_settings().normalized_tool_base_url`，D3 已实测它默认 `http://localhost:5173`，与客户端配置无关。**D4 一律用客户端 baseUrl 重拼**：
`{baseUrl}/digital-employees/{employeeId}:export-skill?slug={slug}`（注意 `baseUrl` 已含 `/api/v1`）。

---

## 现状核实（deepchat 侧源码）

| 事实 | 证据 | 影响 |
|---|---|---|
| `MCPServerConfig` 有 `type: 'http'` + `baseUrl` + `customHeaders` | `shared/types/mcp.ts:104-133` | 静态 header 够用，不需要 OAuth |
| `StreamableHTTPClientTransport` 在 `mcpClient.ts:632` 使用 | `mcpClient.ts` | 无需新增传输实现 |
| 工具名 `/^[a-zA-Z0-9_-]+$/` 校验后 `continue` 丢弃 | `mcp/toolManager.ts:544-551` | 服务端已修；客户端不要二次加工 |
| `McpSettings.addMcpServer(name, config)` / `removeMcpServer(name)` / `getMcpServers()` | `mcp/settings.ts:759/860/580` | 用这三个做注册与清理 |
| `ServerManager.startServer(name)` / `stopServer(name)` | `mcp/serverManager.ts:258/473` | 注册后需要启动才连上 |
| `extractSkillArchive(archivePath, targetDir, limits)` | `skill/archive.ts:103` | 直接复用，别写新解压 |
| `SkillService.installFromFolderForAgent(agentId, folderPath, options)` | `skill/index.ts:3283` | 装进 agent 隔离目录；`requireAgentScope` 会校验 agent 存在 |
| `assertSafeSkillAgentId` 只允许 `^[A-Za-z0-9][A-Za-z0-9._-]*$` | `skill/agentSkillRoots.ts:8-20` | `digital-employee:<id>` 含冒号，**非法**！必须改成 `digital-employee-{id}` 之类 |
| `installFromZipForAgent` 内部已 extract + resolve SKILL.md + install | `skill/index.ts:3337-3375` | 可直接用，但它在 main 内做 `requireAgentScope`；比手工 extract 更省事 |
| `disabledAgentTools` 按最终工具名过滤 | `agent/deepchat/runtime/deferredToolExecutor.ts:400-406` | 与 `employee.tools[]` 精确匹配即可 |
| `mcpStore.tools` 是 renderer 侧工具列表，用于白名单计算 | `pages/digitalEmployee/DigitalEmployeeSquarePage.vue:360-363` | 无改动 |

### 关键发现：agentId 不能带冒号

spec 写的 `digital-employee:<id>` **不能用**：`assertSafeSkillAgentId` 拒绝冒号（`skill/agentSkillRoots.ts:12`），且 Windows 目录名不允许冒号。改用 `digital-employee-{employeeId}`，并把 employeeId 收窄到安全字符集。

---

## 任务拆分

### Task 1: agentId 与 MCP server 名的稳定派生

**Files:**
- Create: `src/main/digital-employee/employeeScope.ts`
- Test: `test/main/digital-employee/employeeScope.test.ts`

- [x] **Step 1: 写失败测试**

覆盖：`digital-employee-{safeId}` 形态、含非法字符的 employeeId 被收窄且仍唯一、同一 id 多次派生结果相同、结果满足 `assertSafeSkillAgentId`、MCP server 名与 agentId 不同（前者用于 `McpSettings` 键，后者用于技能目录）。

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现**

- [x] **Step 4: 跑测试 + `vue-tsc` + `tsc -p tsconfig.node.json` + `oxlint`**

- [x] **Step 5: Commit** `feat(digital-employee): derive a safe agent scope and mcp server name per employee`

---

### Task 2: 远端 MCP server 注册器

**Files:**
- Create: `src/main/digital-employee/mcpRegistration.ts`
- Test: `test/main/digital-employee/mcpRegistration.test.ts`

- [x] **Step 1: 写失败测试**

覆盖（至少 10 条）：
1. 员工无 `staffdeck_*` 工具 → 不注册、已有注册被清理
2. 有工具 → 注册一个 `type: 'http'` server，URL 为 `{baseUrl}/mcp/agents/{employeeId}`
3. header 带 `Authorization: Bearer <key>`
4. 已注册且配置未变 → 不重复 add（幂等）
5. baseUrl 变化 → 配置更新（remove + add 或 update）
6. 凭证缺失 → 不注册，且已有注册被清理（避免留着失效 header）
7. 注册后调用 `startServer`
8. 注册失败（add 抛错）→ 不抛给上层，返回失败原因
9. `unregister` 停 server + remove
10. 不影响非本员工（前缀不匹配）的既有 server

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现**

要点：
- server 名 `staffdeck-{safeId}`
- 只做注册/清理，不碰工具白名单（那是 store 的事）
- 幂等：先 `getMcpServers()` 比对，相同就跳过
- 凭证只从注入的 `getConfig()` 取，不入日志

- [x] **Step 4: 跑测试 + 类型检查 + oxlint**

- [x] **Step 5: Commit** `feat(digital-employee): register staffdeck tools as a remote mcp server`

---

### Task 3: 技能包下载与安装

**Files:**
- Create: `src/main/digital-employee/skillSync.ts`
- Test: `test/main/digital-employee/skillSync.test.ts`

- [x] **Step 1: 写失败测试**

覆盖（至少 10 条）：
1. 无技能 → 不下载、不安装
2. 首轮 N 个技能 → N 次下载 + N 次安装
3. 同 slug + 同 `id` → 二轮零下载（版本未变）
4. `id` 变化 → 重新安装（覆盖）
5. 单个技能 404 → 跳过该技能，其余继续
6. 下载非 2xx → 该技能失败，整轮不抛
7. zip 非法（非 zip 内容）→ 安装失败被记下，不抛
8. `downloadUrl` 被忽略：请求打到客户端 baseUrl 拼的端点
9. 安装调用用派生出的安全 agentId
10. 安装成功后记录 `id` 基线；失败的不记录（下轮重试）

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现**

要点：
- 端点：`{baseUrl}/digital-employees/{employeeId}:export-skill?slug={slug}`
- 用 `installFromZipForAgent(agentId, zipPath)`——它内部已 extract + 校验 SKILL.md + 安装，比手工 extract 少一条出错路径
- 临时 zip 写在 `app.getPath('temp')`，finally 清理
- 版本基线存 config entry（复用 D3 的 `knowledgeSyncState` 模式，或新增 `digitalEmployee.skillSync`）

- [x] **Step 4: 跑测试 + 类型检查 + oxlint**

- [x] **Step 5: Commit** `feat(digital-employee): install staffdeck skill packages into the employee scope`

---

### Task 4: 接线（composition + 会话创建前触发）

**Files:**
- Modify: `src/main/app/composition.ts`
- Modify: 会话创建路径（远端/本地分流处）
- Test: `test/main/digital-employee/provisioning.test.ts`

- [x] **Step 1: 写失败测试**

覆盖：只在 `executionMode` 为 local/hybrid 时触发、失败不阻塞会话、同一员工并发触发只跑一次、配置变更后重建。

- [x] **Step 2: 跑测试确认失败**

- [x] **Step 3: 实现**

- [x] **Step 4: 跑测试 + 类型检查 + oxlint**

- [x] **Step 5: Commit** `feat(digital-employee): provision skills and tools when a local employee starts`

---

### Task 5: 文档与联调

- [x] **Step 1: 起本地 StaffDeck，验证 `POST /mcp/agents/{id}` 的 initialize / tools/list**
- [x] **Step 2: 验证 `:export-skill` 返回的 zip 能被 `extractSkillArchive` 解开**
- [x] **Step 3: 修订 spec §5.3 / §5.5，记录 agentId 与 server 名的实际规则**
- [x] **Step 4: 全量回归**
- [x] **Step 5: Commit** `docs(digital-employee): record staffdeck d4 outcome`

---

## 自审

- 两条链路互不依赖，任一失败不影响另一条，也不阻塞会话
- 复用既有解包与安装能力，不重造
- 工具名两侧同源，客户端零加工——这是最容易写错的地方
- agentId 的冒号问题在写代码前就查出来了（spec 原文不可用）
