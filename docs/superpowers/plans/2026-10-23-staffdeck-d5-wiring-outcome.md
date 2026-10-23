# D5 收尾：自动采集与设置开关

承接 `2026-10-23-staffdeck-d5-outcome.md` 的两条遗留：设置页 UI、事件采集点的自动触发。

做完之后发现一件事：**这两条遗留都不是真的「补个 UI / 接个钩子」**。遗留的背后藏着
一个会让整条回流链路在真实使用中完全不通的缺陷。

## 必须先修的那个：本地会话在服务端不存在

D5 联调时上报能成功，是因为脚本先手工调了 `POST /agents/{id}/sessions`。真实客户端里
那一步是**只有 `executionMode=remote` 才会走**的路径（`remoteRunner.ensureBinding`）——
本地执行的会话从头到尾没在服务端建过任何记录。

而 `events:batch` 要求三件事同时成立：

1. `session_id` 是服务端的 `ChatSession` 行；
2. 该行属于这个 agent、同租户；
3. 发起请求的凭证在 `ExternalSessionBinding` 里拥有它。

本地会话一条都不满足，**直接上报必然 404**。这不是「偶发失败」，是 100% 失败。

修法：`backflow.flush` 第一次拿到某个本地会话时，先按 `external_session_id` 调一次建会话
（服务端按 `(credential_id, external_session_id)` 幂等，重复调用返回同一行），再把返回的
服务端 id 用去打事件端点。

三个配套决定：

| 决定 | 理由 |
|---|---|
| 服务端 id 缓存只放内存 | 它是可随时重建的派生数据。落盘会让「服务端换了库」变成一堆指向不存在会话的陈旧 id，而重建的代价只是一次幂等请求 |
| 缓存命中后 404 → 丢缓存重建一次 | 会话可能被服务端清理过。重建一次仍失败就如实回报，不无限重试 |
| 配置变更时 `reset()` | 服务端会话 id 是按旧凭证的行，新凭证在绑定表里看不到它们 |

## 采集：事件 id 是唯一的不可逆决定

`backflowEvents.collectBackflowEvents` 是纯函数。它唯一不该出错的地方是**事件 id**——
那就是服务端的去重粒度：

- id 只由消息 id 与块位置决定（`m:<msgId>` / `t:<msgId>:<index>` / `u:<msgId>`），
  不含时间戳、不含数组下标。带时间戳则重报产生新 id，归档里出现两份同样的对话；
  带数组下标则消息窗口一滚动，下标全变，同样重复。
- `orderSeq` 游标只在报告成功后前进。失败原地等下一轮重发；因为 id 稳定，重发安全。
- **乐观消息前停住**。流刚结束时用户那条还是 `__optimistic_user_*`，提交后会换成真实 id。
  此时上报会留下永远对不上的 id，且游标一旦越过它，真实记录因为 `orderSeq` 不前进
  就再也采不到。所以遇到临时记录就在本轮停下，等下一轮连同后面助手消息一起报。

正文取可见文本而不是原始 JSON：用户消息在 `DisplayUserMessageContent.text`，助手消息按
顺序拼 `content` 块。整串 JSON 传上去会让管理端的对话记录全是转义字符。

工具调用单独成事件（不混进助手正文）：管理端要按工具名统计成功率与耗时，混进正文就
只能再解析一次。没有名字的工具调用不报——它在管理端只会显示成一串空行。

## 设置开关

`backflowEnabled` 在 D5 已经能被上报侧读到，但设置页没有面板，用户看不到也改不了。
这次补上，三条约束：

- **缺省读成开启**（`!== false`），与上报侧同口径。设置页显示「关闭」而实际在报，
  比两个方向都更糟。
- **省略字段 = 保留当前值**，不写。否则旧版 renderer 一保存就会把用户关掉的回流
  悄悄打开。
- **关闭时显式警示**「管理端看不到这些会话」。默认开启的东西用户不会主动确认，
  关掉之后的后果只能在这里讲清。

开关随「保存配置」一起提交，与同一卡片里的连接配置同一个事务（保存失败时快照回滚
已包含这个键）。

## 端到端实测

`backend/tests/manual_d5_backflow_e2e.py`，走客户端真实请求形状：

```
1. 建远端会话                    201  session_4f92a7c71b0643fc
2. 重复建会话                    201  同一 id（外部会话 id 复用不产生第二行）
3. 首次上报 4 条                 200  {accepted: 4, duplicates: 0}
4. 重发整批 + 1 条新             200  {accepted: 1, duplicates: 4}
5. 同一批再发                    200  {accepted: 0, duplicates: 5}
6. 库内 5 条                     roles=[assistant, system, tool, user, user]
                                 工具行 name/status/duration_ms 齐全，用量行 total_tokens=1280
7. 另一凭证写同一会话            404
8. 不存在的会话                  404
9. 空批次                        200  {accepted: 0, duplicates: 0}
```

跨进程再验一次——直接 import deepchat 的 `createStaffdeckBackflow`，打真的 uvicorn：

```
first flush  -> { ok: true,  reason: null,        delivered: 4 }   建会话 + 上报
resend       -> { ok: true,  reason: null,        delivered: 4 }   重发，库内仍 6 条
second turn  -> { ok: true,  reason: null,        delivered: 2 }
disabled     -> { ok: false, reason: 'DISABLED',  delivered: 0 }
bad key      -> { ok: false, reason: 'HTTP_401',  delivered: 0 }   如实回报，不抛
```

库内最终：1 个 session、1 条 binding、6 条 message，无重复。

## 回归

| 范围 | 结果 |
|---|---|
| StaffDeck `test_external_sessions` + `test_public_api_v1` + `test_public_api_digital_employees` | 42 passed |
| deepchat `test/main` + `digitalEmployeeStore` + `chat-page` | 见下方说明 |
| deepchat `tsc`（node）/ `vue-tsc`（app） | 干净 |

## 一处仍需知道的事

`backflow` 的建会话与 `remoteRunner.ensureBinding` 各自维护一份「本地会话 → 服务端会话」
的映射。两者不会指向不同的行——服务端按 `external_session_id` 幂等，谁先建都返回同一行。
但缓存是两份，配置变更时都要清。当前够用，若将来把两种执行模式混在同一个会话里
（hybrid 的完整切换），需要把这份映射收敛到一处。
