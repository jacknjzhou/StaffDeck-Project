# D5 对话回流：结论

目标：本地执行的会话回流 StaffDeck，管理端可见完整对话。

## 与计划的两处偏差

**一、不新建建会话端点。** 计划里写的是 `POST /external-sessions`，写到一半发现
`POST /agents/{agent_id}/sessions` 已经接受 `external_session_id` 并按
`(credential_id, external_session_id)` 幂等 upsert，deepchat 的 `createSession` 本来就走这条路。
再建一个就是同一规则两处实现——一旦漂移，同一对话会在服务端产生两行，消息按调用方各写一半。
已回退，只保留真正缺失的事件追加端点，并放进既有 sessions 命名空间：
`POST /agents/{agent_id}/sessions/{session_id}/events:batch`。

**二、幂等粒度是单事件，不是整批。** 客户端重试必然重发整批。整批幂等的话，
「第二批里夹一条旧事件」会导致整批被 409 拒掉，随行的新事件全部丢失。改为按
`event_id` 逐条去重，重发只影响重复的那几条。

## 交付

| 侧 | 内容 | 提交 |
|---|---|---|
| StaffDeck | 事件追加端点 + 6 个契约模型 + 12 个测试 | `a6f8477` |
| deepchat | 上报客户端（分块、失败即停、永不抛错） | `bee793f5` |
| deepchat | 契约 + route + composition 接线 + store `reportBackflow` | 同上 |

一处相关修复不在本轮：`19ca27ad` 把 MCP 与技能导出端点改成从主机根拼 `/api/v1`。
这与我 D4 联调实测一致——`/api/v1` 来自外层挂载，不在 `create_public_api_app` 里。

## 联调实测（真实 app）

```
建会话:              201
首次上报 4 条:        200 {"accepted": 4, "duplicates": 0}
重发整批 + 1 条新:    200 {"accepted": 1, "duplicates": 4}
库内消息数:           5
未知会话:             404 SESSION_NOT_FOUND
```

库内分布确认三件事：消息按角色落对（`user` / `assistant`）、工具调用落为 `tool` 行且带
`duration_ms`、用量落为 `system` 行且 `total_tokens=120`。重发整批时只多出 1 条——
这正是单事件幂等要的行为。

## 四个刻意的设计选择

| 选择 | 理由 |
|---|---|
| 上报永不抛错 | 调用方在发消息路径上。一次归档失败最多少一行记录，不能变成用户看到的「发送失败」 |
| 分块上报，失败即停 | 跳过失败块会在归档里留洞，而调用方分不清「已发过」和「丢了」，那一段就永远补不回来 |
| `remote` 员工不上报 | 对话本来就在服务端，再报一次管理端会看到每条消息两份 |
| 开关读 `!== false` 而非 `=== true` | 未设置必须等于「上报」。误关会留审计盲区，误开只是多归档一条用户本来也期望归档的对话 |

## 鉴权三层

`agent 属于本租户` → `session 属于该 agent 且同租户` → **`该凭证在绑定表里拥有这个 session`**。

第三层不能省：只校验租户的话，任何同租户 key 都能往它叫得出名字的会话里写，
等于把只读权限升级成写权限。对应测试 `test_a_second_credential_cannot_append_to_the_first_credential_session`。

## 回归

| 范围 | 结果 |
|---|---|
| StaffDeck `test_external_sessions` + `test_public_api_mcp` + 分发 e2e | 32 passed |
| deepchat `test/main/digital-employee` + `contracts` + 两个 renderer 测试 | 331 passed |
| deepchat `tsc` / `vue-tsc` / `oxlint` | 干净（仅既有 `database.ts` 错误） |

## 遗留

- **设置页 UI 未做**：开关读了 `digitalEmployee.backflowEnabled`，但设置页面板尚未落地（D1 只到 client 层）。行为已正确，UI 随设置页一并补。
- **事件采集点是 `reportBackflow`，尚未挂到每轮结束时自动触发**：需要 renderer 在轮次结束处组装事件。当前 store 方法可用、已测，但自动采集留给对话页接线时补——那里才知道「这一轮」的边界。
- 管理端看板 UI 属 M6 观测整合范围，本次只保证数据可被既有会话页读到。
