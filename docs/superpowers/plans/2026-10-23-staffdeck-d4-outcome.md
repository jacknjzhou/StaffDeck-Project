# D4 交付记录：技能导入 + MCP 工具接入

> 对应 StaffDeck M4 与 deepchat 侧 D4。本文只记录**实际交付结果与踩到的坑**，
> 设计推导见 `docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md` §5.3 / §5.5。

## 交付范围

| Task | 内容 | 提交 |
|---|---|---|
| 前置 | 工具命名从 `staffdeck:{slug}` 改为 `staffdeck_{slug}` + 摘要 | StaffDeck `74158c5` |
| Task 1 | agentId 与 MCP server 名的稳定派生 | deepchat `ca744f98` |
| Task 2 | 远端 MCP server 注册器 | deepchat `c3b77d64` |
| Task 3 | 技能包下载与安装 | deepchat `e5b7ec44` |
| Task 4 | 接线（准备路由 + 会话前置触发） | deepchat `ee30ec3b` |
| Task 5 | 联调验证 + 文档 | 本次 |

## 三条必须守住的事实

### 1. 工具名逐字相等，客户端零加工

服务端 `tool_namespace.py` 是唯一产出点，分发 payload 的 `tools[]` 与 MCP `tools/list` 共用它。客户端任何 sanitize 都会让白名单错位，表现为「工具被禁用但没人知道为什么」。

名字受两层约束，服务端已同时满足：deepchat 要求 `/^[a-zA-Z0-9_-]+$/`（`main/mcp/toolManager.ts:544`，不匹配即 `continue` 静默丢弃），OpenAI 兼容 API 的 `function.name` 有 64 字符上限。

### 2. 注册必须发生在会话创建前

白名单是「可用工具列表的补集」。注册若晚于 renderer 读列表，第一轮对话零个 `staffdeck_*` 工具，且无重试机会——白名单在建会话时写死。

因此准备是一条显式 route，由 renderer 在 `loadTools()` 之前 await，而非后台预热。

### 3. 技能名带员工前缀，`activeSkills` 要用安装后的名字

数字员工的对话全部跑在内置 agent `deepchat` 下，没有「每员工一个 agent」。隔离靠名字前缀 `de-{employeeId}-{slug}`。

所以会话的 `activeSkills` 不能用 payload 的 `skillIds`（裸 slug）——那些名字在本地技能目录里不存在，传进去会安静地激活不到任何技能。准备的返回值带 `skillNames`，会话用它。

## 联调实测

用 FastAPI `TestClient` 打真实 app（同一 ASGI、同一套中间件与鉴权），脚本见 `backend/tests/manual_d4_handshake.py`。

| 检查项 | 结果 |
|---|---|
| `initialize` → `POST /mcp/agents/{id}` | 200，`serverInfo = {name: 'StaffDeck MCP', version: '1.0.0'}` |
| `tools/list` | 3 个工具，全部匹配 `/^[a-zA-Z0-9_-]+$/`，最长 40 字符 |
| `tools/call` | 200，`isError: false` |
| `GET` 探测 | 405（客户端不得依赖 SSE 长连接） |
| `:export-skill` | 200，`application/zip`，根目录仅 `SKILL.md` |
| `downloadUrl` 实测 | `http://localhost:5173/...`——确认必须由客户端重拼 |
| 坏 key | 401 + `application/problem+json`（`INVALID_API_KEY`），不是 JSON-RPC 错误 |

技能包解包已用**真实导出包**验证（不是手工造的 zip）：`test/fixtures/skills/staffdeck-export-skill.zip` + `test/main/skill/staffdeckSkillArchive.test.ts`。

## 排查记录：三个会静默失效的坑

| 现象 | 根因 | 处置 |
|---|---|---|
| MCP server 连上了但工具列表为空 | 工具名含冒号，被 deepchat 静默丢弃 | 服务端改 `staffdeck_` 前缀 |
| 所有技能都装不上，只说「没装上」 | 契约漏声明 `skills[].slug`，zod 把它剥掉了 | 补上可选字段 |
| 技能装上了但从没被激活 | `activeSkills` 传的是裸 slug，不等于磁盘上的名字 | 准备的返回值带 `skillNames` |

共同点：**都不报错**。前两个是过滤器，第三个是字符串精确匹配。写这类跨进程对齐的代码，静默失败比抛错危险得多。

## 已知限制

- `execution_mode=detached` 的 HTTP 工具返回「已受理」凭证而非最终结果；D4 未实现轮询，需要时按 run 结果查询补。
- 技能安装只在显式准备时发生。管理员改完技能后，用户要重开一次对话才会拉到新版本（版本判据是 `skills[].id`，会正确识别为已变更）。
- 本机 `test_harness_v2.py` 有 3 个既有的 Windows 长路径失败（`WinError 206`），与本次改动无关。

## 全量回归结果（deepchat，本机 Windows）

`vitest run` 全量：**916 文件 / 11578 用例通过**，52 文件 / 121 用例失败。

失败集中在 **POSIX 路径语义在 Windows 上不成立** 一类：`cli/launcherService`（期望 `/home/tester/...`，实际得到反斜杠）、`skill/settings`、`skill/skillExecutionService`（`/package/scripts/run.py` vs `\\package\\scripts\\run.py`）、`documentsTableMigration`、`cuaEmbeddedAdapter`（`/tmp/deepchat-cua-*.sock` 不满足 POSIX socket 命名）、`lightOcrProcessHost`、`memoryVectorStore` 等。与本轮改动**文件零交集**（D4 只碰 `digital-employee/`、`contracts/`、`composition.ts` 与对应测试）。

按模块核对：

| 范围 | 结果 |
|---|---|
| `test/main/digital-employee` | 19 文件 / 213 用例全部通过 |
| `test/main/contracts` | 全部通过 |
| `test/renderer/stores/digitalEmployeeStore` + `pages/digitalEmployee` | 全部通过 |
| StaffDeck D4 相关 7 个测试文件 | 53 passed |

另注：本机全量跑不动一次性过——默认堆 OOM，`--max-old-space-size=8192` + `maxForks=2` 仍 OOM，最终以 6GB 堆 + 2 fork 跑满 19 分钟完成。这是机器规模限制，不是测试缺陷。

## 下一步

D5 对话回流（`POST /external-sessions` + `events:batch`），然后是 StaffDeck M6 观测整合。
