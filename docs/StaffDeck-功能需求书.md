# StaffDeck 项目功能需求书

**版本**：v0.5.6（现状功能规格）
**文档类型**：As-Is 功能规格书
**覆盖范围**：本地部署实例 `http://127.0.0.1:5173` 的全部 Web 页面与已实现功能
**编写日期**：2026-10-21
**依据**：前端 `frontend-enterprise/src`、后端 `backend/app` 源码，以及本地实例实测

## 目录

1. 文档说明
2. 系统概览
3. 开放广场平台
4. 数字员工与团队
5. 员工能力
6. 对话端
7. 渠道接入
8. 员工档案与运营
9. 系统管理
10. 权限矩阵与全局规则
11. 页面、路由与接口总表

---

# StaffDeck 项目功能需求书（现状功能规格）

> 文档类型：As-Is 功能规格书
> 覆盖范围：本地部署实例 `http://127.0.0.1:5173` 的 Web 控制台全部页面与已实现能力
> 编写依据：`frontend-enterprise` 前端路由与页面源码、`backend/app` 后端接口与服务实现、仓库 README 与本地实例实际访问
> 适用读者：产品、研发、测试、实施交付与验收人员
> 说明：本文只描述「当前已经实现的功能」，不对未来版本做承诺；凡源码中无法确认的行为，均标注「（待确认）」。

## 1. 文档说明

### 1.1 编写目的

本文用于把 StaffDeck 当前版本的能力固化为可评审、可测试、可验收的功能规格，作为以下工作的基线：

- 功能验收与回归测试用例设计；
- 新成员理解系统的功能地图；
- 后续版本重构、替换或扩展时的现状对照。

### 1.2 范围与边界

- 包含：Web 管理端（`/enterprise/*`）、对话端（`/workspace/*`）、登录与全局引导。
- 包含：页面级信息架构、字段、操作、状态、筛选、空态、权限差异、依赖接口。
- 不包含：模型供应商内部实现、第三方 IM 平台的配置后台、桌面客户端的安装器行为。
- 不包含：性能指标、容量规划、安全渗透结论。

### 1.3 术语

| 术语 | 含义 |
| --- | --- |
| 数字员工 | 具有姓名、职位、岗位描述、人设与能力档案的 AI 员工实体；可被选择为“当前员工”并在对话端服务 |
| 开放广场 / 数字员工广场 | 预置或已发布的数字员工集合，用户可浏览、复制或直接发起对话 |
| 整体员工（is_overall） | 系统级/广场级的模板型员工，不作为普通员工参与日常切换 |
| 当前员工 | 管理端侧边栏的员工切换器所选中的上下文，决定员工档案、能力、定时任务等页面的数据范围 |
| 能力 | 知识库、技能（General Skill）、SOP、工具四类可挂载到员工身上的资源 |
| 能力作用域 | 资源对哪些员工/团队生效的范围控制 |
| SOP | 状态机驱动的流程型技能，用于约束复杂业务流程的执行 |
| 技能蒸馏 / Distill | 从历史会话中抽取可复用能力并生成技能草稿的流程 |
| 团队 / TL | 由多个数字员工组成的协作单元；TL 为团队负责人（Team Leader） |
| 待回答 | 数字员工将问题转交真人处理的队列 |
| 进化 / Evolution | 基于用户反馈生成能力改进候选，经评估后批准、拒绝或回滚的闭环 |
| 渠道 | 企业微信、微信、微信客服、飞书、钉钉等外部 IM 接入方式 |
| Trace | 一次对话运行的可观测事件记录，用于调试与审计 |

### 1.4 系统组成

| 层 | 现状 |
| --- | --- |
| 前端 | React 18 + TypeScript + Vite，单页应用；管理端与对话端共用一套设计系统与侧边栏组件 |
| 后端 | Python 3.11+ / FastAPI，按业务域拆分路由（agents、teams、chat、sessions、knowledge、skills、tools、channels、scheduled_tasks、memories、feedback、evolution、traces、model_configs 等） |
| 运行时 | 单端口部署：一个 FastAPI 进程同时提供页面与 API（默认 `127.0.0.1:5173`） |
| 数据 | 默认 SQLite；生产迁移路径当前以 SQLite 为前提 |
| 会话内核 | Harness v2 为受支持的对话运行时，负责流式执行、工具调用、技能运行与产物 |
| 默认账号 | 管理员 `admin` / `admin`，首次登录后应在账号管理中修改密码 |

### 1.5 角色模型

系统当前只有两类角色，由登录用户信息中的管理员标记判定：

| 角色 | 判定 | 可见范围 |
| --- | --- | --- |
| 管理员 | 用户信息被判定为管理员 | 全部管理端菜单，含账号管理、模型配置、运行设置；可管理全部数字员工与团队 |
| 普通员工 | 非管理员 | 开放广场平台、我的数字员工、我的团队、渠道接入，以及当前员工范围内的档案、能力与运营页面；无系统管理菜单 |

> 权限的具体判断函数与差异点在第 10 章汇总。

## 2. 系统概览

### 2.1 产品定位

StaffDeck 是面向企业的数字员工构建与管理平台：把专业员工的经验、流程与判断标准固化为可长期工作的数字员工，并通过知识库、技能、SOP、工具、记忆、定时任务与渠道接入，让其承担重复性工作，形成可复用、可迭代、可追溯的组织资产。

核心能力主张（与已实现页面一一对应）：

| 主张 | 对应功能 |
| --- | --- |
| 数字员工构建与管理 | 我的数字员工、员工档案、人设编辑、广场发布 |
| 多员工协作 | 我的团队、团队详情、团队群聊、TL 调度 |
| 流程型技能 | SOP 列表与编辑、技能蒸馏 |
| 文档结构感知的知识检索 | 知识库管理、知识图谱、对话中的引用 |
| 自主执行 | 工具与 MCP、定时任务、HTTP/业务接口 |
| 持续迭代 | 记忆、对话日志、反馈与进化面板 |
| 对外服务 | 渠道接入（企业微信/微信/微信客服/飞书/钉钉） |

### 2.2 两个使用端

系统在同一个 Web 应用内提供两个使用端，可互相切换：

| 端 | 路由前缀 | 定位 | 切换入口 |
| --- | --- | --- | --- |
| 管理端 | `/enterprise/*` | 配置与管理数字员工、团队、能力、渠道与系统参数 | 侧边栏底部「对话端」按钮 |
| 对话端 | `/workspace/*` | 面向使用者的会话界面：数字员工广场、会话列表、对话 | 侧边栏底部「管理端」按钮 |

### 2.3 信息架构

管理端侧边栏分为四组，外加员工上下文切换器：

**主导航**

| 顺序 | 菜单 | 路由 |
| --- | --- | --- |
| 1 | 开放广场平台 | `/enterprise/platform`、`/enterprise/platform/:kind` |
| 2 | 我的数字员工 | `/enterprise/agents` |
| 3 | 我的团队 | `/enterprise/teams`、`/enterprise/teams/:teamId` |
| 4 | 渠道接入 | `/enterprise/channels` |
| 5–7（仅管理员） | 账号管理 / 模型配置 / 运行设置 | `/enterprise/accounts`、`/enterprise/models`、`/enterprise/runtime-settings` |

**员工上下文卡片（随当前员工变化）**

| 分组 | 菜单 | 路由 |
| --- | --- | --- |
| 基本资料 | 员工档案 | `/enterprise/dashboard` |
| 基本资料 | 定时任务 | `/enterprise/scheduled-tasks` |
| 基本资料 | 记忆 | `/enterprise/memories` |
| 基本资料 | 对话日志 | `/enterprise/feedback` |
| 员工能力 | 知识库 | `/enterprise/knowledge` |
| 员工能力 | 技能 | `/enterprise/general-skills` |
| 员工能力 | SOP | `/enterprise/skills` |
| 员工能力 | 工具 | `/enterprise/tools` |

**不在侧边栏但可路由到达**

| 页面 | 路由 |
| --- | --- |
| 员工详情/人设 | `/enterprise/persona` |
| 团队群聊 | `/enterprise/teams/:teamId/chat` |
| 知识库新增 | `/enterprise/knowledge/new` |
| 定时任务新建/编辑 | `/enterprise/scheduled-tasks/new`、`/enterprise/scheduled-tasks/:taskId/edit` |
| 技能新建/编辑 | `/enterprise/general-skills/new`、`/enterprise/general-skills/:slug/edit` |
| 技能蒸馏 | `/enterprise/skills/distill`（从 SOP 页进入） |
| 工具新建/编辑/测试 | `/enterprise/tools/new`、`/enterprise/tools/:toolId/edit`、`/enterprise/tools/:toolId/test` |
| MCP 服务新建/编辑 | `/enterprise/tools/mcp/new`、`/enterprise/tools/mcp/:serverId/edit` |

**对话端**

| 页面 | 路由 |
| --- | --- |
| 数字员工广场（对话入口） | `/workspace/gallery` |
| 新建对话（草稿态） | `/workspace/chat/draft/:draftAgentId` |
| 会话 | `/workspace/chat/:sessionId` |

**兼容跳转**：`/`、`/chat`、`/chat/*`、`/enterprise/chat*` 会重定向到上述新路由；`/enterprise` 默认跳转 `/enterprise/dashboard`；未知路由回退到 `/enterprise/dashboard`。

### 2.4 全局交互与提示

| 机制 | 行为 |
| --- | --- |
| 登录态 | 登录令牌保存在前端会话中；应用启动时调用当前用户接口校验，失效则回到登录页 |
| 侧边栏折叠 | 支持展开/收起，状态写入本地存储；移动端自动收起 |
| 员工切换器 | 侧边栏中部展示当前数字员工的头像、姓名与职位；下拉可切换员工或团队，切换后回到员工档案页 |
| 团队上下文 | 选择团队后，页面数据范围切换到团队；若团队尚无 TL 会话则先创建再跳转群聊 |
| 模型未配置提示 | 当企业内不存在任何已启用的模型配置时，页面顶部显示黄色提示条；管理员可点击「去配置」，普通用户提示联系管理员 |
| 空员工引导 | 当账号下不存在任何非整体数字员工时，员工相关页面统一显示空态引导，可新建员工或跳转开放广场 |
| 新手引导 | 首次使用展示新手引导与快速开始；完成后写入本地标记；两者都完成后才不再出现 |
| 更新提醒 | 引导完成后展示应用版本更新提醒 |
| 语言 | 提供语言切换组件；界面文案以中文为主并支持中英文资源（以实际语言包为准） |
| 全局提示 | 统一使用顶部居中的轻提示（成功/失败/警告）与确认弹窗 |

---

## 3. 开放广场平台

本章描述开放广场总览页、分类详情页与两类详情抽屉的现状。前端见 `frontend-enterprise/src/pages/OpenPlatformPage.tsx` 与 `frontend-enterprise/src/components/openPlatform/`，数据由 `backend/app/api/agents.py`、`general_skills.py`、`skills.py`、`knowledge_bases.py`、`tools.py` 提供。

### 3.1 平台总览页（`/enterprise/platform`）

#### 路由与入口
`frontend-enterprise/src/App.tsx` 中 `/enterprise/platform` 与 `/enterprise/platform/:kind` 渲染同一组件 `OpenPlatformPage`，并传入 `isAdmin`；`kind` 非法时回落总览布局。

#### 页面目的
以整体员工（`is_overall`）承载全部广场资源，向全租户展示数字员工、知识库、技能、SOP 与工具，并支持使用员工或复制资源。

#### 页面结构
顶部为标题与说明；下方按种类渲染列（`PlatformColumn.tsx`），每列含标题、数量、信号胶囊、分隔线、卡片滚动区与「查看全部」（列非空时显示）。五类数据并行加载，任一失败提示「加载开放广场失败」。

#### 资源种类（kind）
| kind | 名称 | 可见条件 | 按钮与信号 |
| --- | --- | --- | --- |
| `agents` | 数字员工广场 | 非整体员工、`active`、`metadata.published_to_gallery===true` | 「使用此员工」；聊天可用/支持对话/查看能力 |
| `knowledge` | 知识库广场 | `active`，排除文档、目录、引用均为 0 的「默认知识库」 | 「复制到知识库」；知识图谱/引用来源/可复制 |
| `general-skills` | 技能广场 | `published` | 「复制到技能」；运行测试/MCP/浏览器/能力复用 |
| `skills` | SOP 广场 | `published` | 「复制到 SOP」；流程推进/执行规范/可复制 |
| `tools` | 工具广场 | `enabled` | 「前往工具页」；调用权限/测试可用/工具配置 |

整体员工由员工列表 `is_overall` 定位，找不到时提示「暂时无法找到开放广场」。后端通过 `backend/app/agents/branching.py` 的 `is_open_gallery_resource` 判定整体员工上的资源绑定可见性。

#### 卡片或列表字段
员工卡片（`PlatformEmployeeCard.tsx`）：

| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 名称 | 「名称 @创建者」 | 创建者由 `creatorNameFromMetadata` 按 metadata 多字段回退 |
| 岗位 | `profile.roleName` | 整体员工显示「开放广场」 |
| 在线/下线 | 圆点与文案 | 由 `status` 判定 |
| 描述 | 两行截断 | 可空 |
| 资料/技能/SOP | 三类计数 | 排除 deleted/inactive |

资源卡片（`PlatformResourceCard.tsx`）：

| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 图标 | 32px，缺省文件夹，按种类着色 | 无 |
| 标题 | 资源名称 | 无 |
| 副标题 | 知识库「N 文档 / N 目录 / N 引用」；技能 slug；SOP「skill_id / version」；工具「bucket / 类型」 | 无 |
| 标签 | 最多 2 个 | 知识库为版本与同步状态；技能为能力来源与启用态；SOP 为业务域与调用次数；工具为方法与启用态 |

#### 表单字段
本页无表单，新建与编辑在资源自身页面完成。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 使用员工 | 卡片/抽屉 | 存在在线广场员工 | 必要时 `POST /api/chat/agents/{id}/use`，写入本地 scope，跳 `/enterprise/dashboard` | 「使用数字员工失败」「广场暂无可用数字员工」 |
| 从广场下线 | 管理员悬停/抽屉 | 非整体员工 | `POST /api/enterprise/agents/{id}/gallery:unpublish` | 确认「从广场下线员工「X」？」；成功「员工已从广场下线」；失败「员工广场下线失败」 |
| 复制资源 | 抽屉底部 | 已选目标员工 | 跳 `/enterprise/knowledge|general-skills|skills?add=plaza&resourceId=`；工具跳 `/enterprise/tools?add=plaza`（是否带 resourceId 待确认） | 「请先选择一个员工，再从广场复制资源。」 |
| 删除资源 | 管理员抽屉 | 无 | 按种类 DELETE，携带整体员工 `agent_id` | 确认「删除{类型}「X」？」；成功「已从广场移除」；失败「删除失败」 |
| 创建开放 Skill | 分类页按钮 | 管理员 | 跳 `/enterprise/general-skills/new?scope=gallery` | 找不到整体员工时「暂时无法找到开放广场」 |
| 查看全部 | 列底部 | 列非空 | 进入分类详情页 | 无 |

#### 筛选与搜索
总览页无搜索与筛选，按服务端条件过滤后直接展示；排序沿用接口顺序（待确认）。

#### 状态与空态
加载中每列 3 个 `h-[112px]` 骨架；无内容显示「暂无开放内容」与「发布内容后会在这里展示」。

#### 权限差异
`canManagePlatform` 仅对管理员为真（`auth.ts` 判定 `role==='admin'`）。管理员可见下线、删除与创建开放 Skill；普通用户只能使用员工与复制资源。后端 `ensure_open_gallery_admin` 等价租户管理员；`_ensure_can_copy_from_agent` 对未发布的非整体员工返回 403 "Cannot copy resources from this agent"。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/agents` | 员工列表并定位整体员工 |
| GET | `/api/enterprise/knowledge-bases?agent_id={overall}` | 知识库 |
| GET | `/api/enterprise/general-skills?agent_id={overall}` | 技能 |
| GET | `/api/enterprise/agents/{overall}/skills` | SOP |
| GET | `/api/enterprise/tools?agent_id={overall}` | 工具 |
| POST | `/api/chat/agents/{id}/use` | 切换员工 scope |
| POST | `/api/enterprise/agents/{id}/gallery:unpublish` | 广场下线 |

### 3.2 分类详情页（`/enterprise/platform/:kind`）

#### 路由与入口
由总览页「查看全部」进入，渲染 `PlatformKindDetailView.tsx`。

#### 页面目的
以网格展示某一类资源的全部内容，支持本地搜索、刷新、下线与删除。

#### 页面结构
顶部 `AppHeader` 与「返回开放广场」「创建开放 Skill」（仅技能）「刷新」按钮；下方为统计卡、信号胶囊、搜索框与 1 至 5 列的响应式网格。

#### 卡片或列表字段
与总览页同种卡片一致，员工卡片支持悬停下线。

#### 表单字段
无。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 搜索 | 搜索框 | 无 | 本地匹配标题、描述、副标题、标签 | 「没有匹配的广场内容」 |
| 刷新/返回 | 顶部按钮 | 无 | 重新拉取或回到总览 | 失败「加载开放广场失败」 |
| 下线/删除 | 卡片或抽屉 | 管理员 | 同总览页 | 同总览页 |

#### 筛选与搜索
搜索为本地过滤，占位「搜索{种类名}」；大小写处理待确认。

#### 状态与空态
加载中 8 个骨架；无数据「暂无开放内容」，搜索无命中「没有匹配的广场内容」。

#### 权限差异
删除、下线、创建开放 Skill 仅管理员可见。

#### 依赖接口
与总览页相同，另加：`DELETE /api/enterprise/knowledge-bases/{id}`、`/general-skills/{slug}`、`/skills/{skill_id}`、`/tools/{tool_id}`，均携带整体员工 `agent_id`；非整体上下文由各接口自行按分支删除。

### 3.3 数字员工详情抽屉

#### 路由与入口
点击员工卡片打开 `PlatformEmployeeDrawer.tsx`，右侧 Sheet，宽 400px，不改变 URL。

#### 页面目的
不离开广场查看员工能力，并执行使用或下线。

#### 页面结构
头部为平台标题、上/下一位与关闭；主区为头像、名称、描述、状态徽章、三段统计、两张「分类」卡（平台名称、岗位）与「说明」区；底部为「从广场下线」（管理员）与「使用员工」。

#### 卡片或列表字段
| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 状态徽章 | 在线/下线 | 由 `status` 判定 |
| 三段统计 | 资料/技能/SOP 计数 | 由 `resources` 统计 |
| 分类 | 平台名称与 `roleName` | 无 |
| 说明 | `work_styles` 前 3 个标签与详情文本 | 可空 |

#### 表单字段
无。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 上一位/下一位 | 头部箭头 | 有相邻员工 | 切换内容 | 无 |
| 使用员工 | 底部主按钮 | 员工在线 | 同总览页 | 同总览页 |
| 从广场下线 | 底部次按钮 | 管理员 | 同总览页 | 同总览页 |

#### 筛选与搜索
无搜索，顺序与列表一致。

#### 状态与空态
列表为空时抽屉不会打开，缺失字段留空。

#### 权限差异
下线按钮仅管理员渲染。

#### 依赖接口
`POST /api/chat/agents/{id}/use`、`POST /api/enterprise/agents/{id}/gallery:unpublish`。

### 3.4 资源详情抽屉

#### 路由与入口
点击资源卡片打开 `PlatformResourceDrawer.tsx`，右侧 Sheet，宽 400px。

#### 页面目的
展示资源关键信息，并复制到目标员工或进入工具页。

#### 页面结构
头部为平台标题、上/下一位与关闭；主区为图标、标题、描述、徽章、两张「分类」卡与「说明」；底部为「删除」（管理员）与主操作按钮。

#### 卡片或列表字段
| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 徽章 | SOP 取版本，其余取首个标签 | 无 |
| 分类 | 平台名称与种类元信息 | 无 |
| 说明 | 固定种类文案 `config.detail`，非资源自身描述 | 无 |
| 来源员工 | 由平台标题体现，未单列员工名（待确认） | 无 |

#### 表单字段
无。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 复制/前往 | 底部主按钮 | 工具以外需已选员工 | 跳资源页并带 `add=plaza&resourceId=` | 「请先选择一个员工，再从广场复制资源。」 |
| 删除 | 底部删除 | 管理员 | 调用对应 DELETE | 「已从广场移除」「删除失败」 |

#### 筛选与搜索
无。

#### 状态与空态
无资源时不打开；工具条目直接跳工具页，不进复制弹窗。

#### 权限差异
删除按钮仅管理员渲染。

#### 依赖接口
四类 DELETE 同上；复制由资源页调用 `POST /api/enterprise/agents/{agent_id}/resources/import` 完成，返回 `imported` 与 `missing`（原因含 `resource_not_found`、`not_visible_in_source_agent`、`disabled_in_open_gallery`）。

---

## 4. 数字员工与团队

本章描述管理端数字员工、团队、员工画廊、Persona 与竞标竞技场的现状行为。结论均取自前端 `frontend-enterprise/src` 与后端 `backend/app` 的现有实现。

### 4.1 我的数字员工（`/enterprise/agents`）

#### 路由与入口
路由在 `frontend-enterprise/src/App.tsx` 注册，页面组件为 `pages/AgentsPage.tsx`。列表数据来自 `GET /api/enterprise/agents`，前端再过滤为 `!item.is_overall && canManageEmployeeAgent(item, currentUser)`，即排除整体广场员工，只保留本人创建或管理员可管理的员工。

#### 页面目的
集中管理当前用户可管理的数字员工：查看在线/下线状态、发起对话、上下线、发布到广场、编辑档案与头像、管理运行密钥、删除。

#### 页面结构
自上而下为：三张统计卡（员工总数、下线员工、待审批）、搜索框、下划线标签页（全部员工/在线员工/下线员工）、员工卡片网格。本地实测的区域标签为「数字员工统计」「数字员工分类」，卡片上的操作按钮为「发起对话」「员工操作」，搜索框占位文字为「搜索」。注意页面没有独立的“待审批”标签页，点击“待审批”统计卡会令列表按 `pending` 过滤，但标签页不出现对应项、也无高亮。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 员工名称 | 卡片主标题，非本人创建时附带创建者 | `employeeDisplayNameWithCreator`；`metadata.owner_user_id` 与当前用户比对 |
| 岗位 | 员工岗位名 | `metadata.role_name`，为空时前端回退展示 |
| 在线/下线 | 员工状态标签 | `agent.status`：`active` 为在线，`archived` 为下线 |
| 描述 | 岗位描述摘要 | `agent.description` |
| 工作风格 | 最多展示前 3 个标签 | `metadata.work_styles` |
| 资料/技能/SOP 计数 | 三类资源数量 | `agent.resources`，按资源类型统计并排除 deleted/inactive |

#### 筛选与排序
| 筛选维度 | 可选值 | 说明 |
| --- | --- | --- |
| 标签页 | 全部员工 / 在线员工 / 下线员工 | 分别对应全部、`active`、`archived` |
| 统计卡快捷筛选 | 员工总数 / 下线员工 / 待审批 | “待审批”额外写入 `pending` 过滤条件 |
| 关键词搜索 | 姓名、姓名含创建者、岗位名、描述、工作风格拼接串 | 前端本地过滤，不区分大小写（待确认） |

列表排序直接沿用接口返回顺序，页面未做二次排序。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 选中员工 | 点击卡片主体 | 员工在线且 `canSelectCurrentEmployeeAgent` 通过 | 必要时先调用 `POST /api/chat/agents/{id}/use`，随后持久化员工 scope、广播切换事件、跳转 `/enterprise/dashboard` | 使用接口失败则不跳转（提示文案待确认） |
| 发起对话 | 卡片菜单 | 员工在线 | 跳转 `/workspace/chat/draft/{id}` | 无 |
| 上线/下线 | 卡片菜单 | 可管理 | `PUT /api/enterprise/agents/{id}` 更新 `status` | 接口错误提示（待确认） |
| 发布到广场/从广场下架 | 卡片菜单 | 可管理 | 更新 `metadata.published_to_gallery` 及发布时间/操作人 | 接口错误提示（待确认） |
| 编辑资料 | 卡片菜单 | 可管理 | 打开档案编辑弹窗 | 无 |
| 设置头像 | 卡片菜单 | 可管理 | 打开头像编辑弹窗 | 无 |
| API 密钥 | 卡片菜单（按条件显示） | 可管理 | 打开运行密钥弹窗 | 无 |
| 删除 | 卡片菜单 | 可管理 | 二次确认后 `DELETE /api/enterprise/agents/{id}` | 确认文案为“删除后该员工的所有配置将一并移除，操作不可撤销。” |

删除的后端影响：清理资源绑定；员工挂载的渠道提升新的默认员工；清除该员工全部会话及工作区；移除其团队成员关系；暂停其定时任务；取消待处理的人工转接；最后删除员工。前端在删除当前 scope 员工后，会自动选择下一个在线员工，若没有则清空本地存储的员工 scope。

#### 状态与空态
无任何可管理员工时展示全局空态组件 `EmptyEmployeeState`，文案“还没有数字员工”，管理员与普通成员文案不同，提供“新建数字员工”和“浏览开放广场”两个入口。筛选或搜索无结果时展示“没有匹配的数字员工 / 调整筛选条件，或换个关键词再试试”。

#### 权限差异
前端仅展示可管理员工；后端 `list_agents` 对非管理员只返回整体广场员工与自己可见的员工（本人创建或已发布到广场）。管理类接口由 `_ensure_can_manage_agent` 限定为管理员或员工 owner，越权返回 403。普通成员无法在本页看到他人未发布的员工。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/agents` | 员工列表 |
| POST | `/api/enterprise/agents` | 新建员工 |
| PUT | `/api/enterprise/agents/{id}` | 更新状态、档案、metadata |
| DELETE | `/api/enterprise/agents/{id}` | 删除员工 |
| POST | `/api/chat/agents/{id}/use` | 记录员工使用并切换会话 scope |

### 4.2 新建数字员工弹窗

#### 路由与入口
由 `AgentsPage.tsx` 的新建按钮与 `EmptyEmployeeState` 的“新建数字员工”触发，弹窗实现位于 `App.tsx`。

#### 页面目的
以“复制现有员工”或“从空白开始”两种方式创建租户内数字员工。

#### 页面结构
单页表单弹窗，标题“新建数字员工”，底部取消与提交按钮。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 创建方式 | 从广场复制 / 从空白开始 | 切换后决定是否显示“复制来源” |
| 职位 | 新员工岗位名 | 文本输入 |
| 复制来源 | 被复制的员工 | 仅复制模式显示；候选为当前用户可访问且在线、非整体广场的员工，展示为“开放广场”或“名称 @创建者 · 岗位 · 广场” |
| 数字员工姓名 | 员工名 | 必填，前端 trim 后校验 |
| 岗位描述 | 员工描述 | 可选文本 |

#### 筛选与排序
复制来源按可访问性与在线状态过滤，未提供额外搜索或排序。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 提交创建 | 弹窗底部按钮 | 姓名非空 | `POST /api/enterprise/agents`，成功后创建并切换员工 scope 到新员工 | 姓名为空提示“请填写数字员工姓名”；接口 400/409 显示错误信息（文案待确认） |
| 取消 | 弹窗底部按钮 | 无 | 关闭弹窗，不保存 | 无 |

空白模式下请求携带 `source_mode=blank`，metadata 使用 `employeeBlankMetadata`。复制模式携带 `copy_from_agent_id`；后端校验复制来源权限，复制来源的资源与模型配置。未指定来源时后端按整体广场员工复制资源与模型。职位为空时前端回退为来源岗位或“待补充职位”。

#### 状态与空态
复制来源列表为空时仍可选择空白模式；提交过程中按钮进入loading（待确认具体文案）。

#### 权限差异
创建接口仅校验租户、名称必填与租户内重名（409），代码中未见管理员限制，普通成员可创建；可复制的来源受 `_ensure_can_copy_from_agent` 与前端可访问员工集合双重限制。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/agents` | 复制来源候选 |
| POST | `/api/enterprise/agents` | 创建员工 |

### 4.3 员工档案编辑、头像与运行密钥

#### 路由与入口
均由员工卡片右上角菜单打开：`EmployeeProfileEditor`、`EmployeeAvatarEditor`、`EmployeeApiKeyDialog`。

#### 页面目的
编辑单个数字员工的档案、视觉形象与对外运行密钥。

#### 页面结构
档案编辑为纵向表单弹窗；头像编辑为“预设头像网格 + 上传自定义”；运行密钥为弹窗内列表加“创建密钥”操作。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 数字员工姓名 | 员工名 | 必填 |
| 岗位 | 岗位名 | 写入 `metadata.role_name` |
| 入职时间 | 日期 | 日期选择器 |
| 工作状态 | 在线 / 下线 | 对应 `active` / `archived` |
| 岗位描述 | 员工描述 | 多行文本 |
| 看板摘要 | 员工卡看板文案 | `metadata.system_prompt_summary` |
| 岗位执行约束 | 岗位 Prompt | `agent.persona_prompt` |
| 默认模型 | 模型名 | 只读，展示“统一继承模型配置中的全局默认模型” |
| 最大调用轮次 | 单次 HarnessLoop 上限 | 1–100，默认 32 |
| 掌握方向/工作风格/工作模式 | 三组标签 | 回车或逗号添加，自动去重，各最多 12 个 |
| 发布到广场 | 是否公开 | 开关，写入 `metadata.published_to_gallery` |
| 预设头像 | 11 个岗位头像 | 研发、行政、知识运营、财务、人事、法务、销售、市场、采购、项目管理、数据分析 |
| 自定义头像 | 上传图片 | 仅 image/*，≤5MB，裁剪为 360×360 PNG；超过 650KB 转 JPEG（质量 0.86）；写入 `avatar_kind/preset/text/tone/image` |
| 密钥名称、权限、状态、前缀、创建时间、最后使用时间 | 运行密钥元数据 | `GET /api/enterprise/agents/{id}/api-credentials` |

#### 筛选与排序
标签去重后按录入顺序保存；密钥列表按接口返回顺序展示。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 保存档案 | 表单底部 | 姓名非空、轮次 1–100 | `PUT /api/enterprise/agents/{id}`，更新档案与 metadata | 必填项校验提示（待确认具体文案） |
| 选择预设头像 | 头像网格 | 可管理 | 保存 metadata 头像字段 | 接口错误提示 |
| 上传头像 | 上传按钮 | 图片类型与大小合规 | 裁剪压缩后保存 | 类型/大小不符提示（文案待确认） |
| 创建密钥 | 密钥弹窗 | 可管理 | `POST .../api-credentials`，明文仅显示一次 | 接口错误提示 |
| 轮换密钥 | 密钥行操作 | 密钥存在 | `POST .../api-credentials/{key_id}/rotate` | 接口错误提示 |
| 禁用密钥 | 密钥行操作 | 密钥存在 | `POST .../api-credentials/{key_id}/revoke` | 接口错误提示 |
| 复制密钥 | 创建成功提示 | 明文仍可见 | 写入剪贴板 | 无 |

#### 状态与空态
无密钥时列表为空并展示引导文案（待确认原文）。自定义头像上传成功后按钮区展示预览。

#### 权限差异
档案更新与删除由后端 `_ensure_can_manage_agent` 限制为管理员或 owner；前端菜单仅在 `canManage` 时渲染。密钥接口的具体权限校验沿用员工管理判定（待确认是否单独校验）。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| PUT | `/api/enterprise/agents/{id}` | 保存档案与 metadata |
| GET | `/api/enterprise/agents/{id}/api-credentials` | 密钥列表 |
| POST | `/api/enterprise/agents/{id}/api-credentials` | 创建密钥 |
| POST | `/api/enterprise/agents/{id}/api-credentials/{key_id}/rotate` | 轮换 |
| POST | `/api/enterprise/agents/{id}/api-credentials/{key_id}/revoke` | 禁用 |

### 4.4 我的团队（`/enterprise/teams`）

#### 路由与入口
路由在 `App.tsx` 注册，页面组件 `pages/TeamsPage.tsx`。数据来自 `GET /api/enterprise/teams` 与 `GET /api/enterprise/team-threads`。

#### 页面目的
展示当前租户的团队列表与团队动态，支持创建团队、进入团队详情、开始团队对话和删除团队。

#### 页面结构
三张统计卡（团队总数并显示成员总数、进行中任务、待处理）、创建按钮、团队卡片网格，底部“团队动态”树状区（团队 → 任务 → 线程）。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 团队名称 | 卡片标题 | `team.name` |
| 团队状态 | 正常 / 已归档 | `active` / `archived` |
| 描述 | 团队描述 | `team.description`，空时“暂无描述” |
| 成员头像 | 最多 4 个 | 项目领导 64px 带头像角标，其余 44px |
| 成员数 | 成员总数 | `team.members.length` |
| 进行中/待处理标签 | 任务计数 | `bidding`、`pending`、`in_progress` 计为进行中；`review`、`escalated` 计为待处理 |
| 项目领导 | TL 名称 | `role=leader` 的成员 |
| 动态行 | 团队/任务/TL 会话 | `team-threads` 返回，默认只展开最新团队 |

#### 筛选与排序
无视图切换与搜索。团队动态按线程 `updated_at` 倒序，接口最多返回 50 条；默认展开最近更新的团队。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 创建团队 | 顶部按钮 | 名称非空 | `POST /api/enterprise/teams`，成功后刷新列表 | 空名称提示“请输入团队名称”；重名后端 409（前端提示文案待确认） |
| 进入详情 | 点击团队卡 | 无 | 跳转 `/enterprise/teams/{id}` | 无 |
| 开始对话 | 卡片按钮 | 团队已设置项目领导 | `POST /api/enterprise/teams/{id}/tl/session` 后跳转 `/workspace/chat/{session_id}` | 无 TL 时后端 400，前端提示（文案待确认） |
| 删除团队 | 卡片删除按钮 | 二次确认 | `DELETE /api/enterprise/teams/{id}` | 确认文案“删除后团队及其任务将一并移除，操作不可撤销。” |
| 打开动态行 | 动态树节点 | 无 | TL 会话跳工作区聊天，任务行打开详情 `?task=` | 无 |

创建弹窗字段仅“团队名称（必填）”与“团队描述（可选）”；创建者成为团队 owner。

#### 状态与空态
无团队时展示“暂无团队，点击上方「创建新团队」开始”；动态区无数据时展示“暂无团队动态”。

#### 权限差异
前端的创建、开始对话、删除按钮对所有租户用户渲染；后端删除、成员管理、任务、黑板等写操作通过 `_ensure_team_manager` 限定为团队 owner 或管理员，其他成员点击后收到 403。开始对话（`tl/session`）只校验租户，不校验团队管理权限。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/teams` | 团队列表 |
| POST | `/api/enterprise/teams` | 创建团队 |
| DELETE | `/api/enterprise/teams/{id}` | 删除团队 |
| POST | `/api/enterprise/teams/{id}/tl/session` | 获取/创建 TL 会话 |
| GET | `/api/enterprise/team-threads` | 团队动态 |

### 4.5 团队详情（`/enterprise/teams/:teamId`）

#### 路由与入口
路由在 `App.tsx` 注册，组件 `pages/TeamDetailPage.tsx`。进入时并行加载团队、任务、活跃黑板、事件（limit=50）与员工列表。

#### 页面目的
管理团队编成与运行参数，查看任务看板、竞标过程、验收结论、团队黑板与团队动态。

#### 页面结构
头部操作区（返回团队列表、查看完整日志、开始对话）、成员区（项目领导居中、成员横向滚动）、团队设置、团队黑板、7 列任务看板（竞标中/待认领/进行中/待验收/已完成/已退回/已升级）、任务详情弹窗、团队动态。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 成员卡 | 头像、名称、角色 | `team.members`，TL 带“项目领导”标识 |
| 成员并发上限 | 同时执行任务的成员数 | `config.member_concurrency`，最小 1 |
| 任务超时分钟 | 任务超时阈值 | `config.task_timeout_minutes`，最小 1，默认 30 |
| 竞标反驳轮数 | 竞标辩论轮次 | `config.bid_rebuttal_rounds`，最小 0，默认 1 |
| 黑板条目 | 内容、标签、来源 | `source_type` 人/项目领导/成员；置顶项优先 |
| 任务卡 | 标题、负责人、优先级、创建时间 | 按状态分列 |
| 任务详情 | 状态、负责人、竞标胜出、优先级、描述、执行报告、验收结论、事件时间线 | 任务详情接口 |
| 团队动态 | 事件类型、操作者、相对时间 | 按任务分组，事件最多 50 条 |

#### 筛选与排序
任务看板固定 7 列，按 `bidding/pending/in_progress/review/done/rework/escalated` 分组；黑板置顶优先、其余按创建时间；事件按时间倒序（待确认）。无关键词搜索。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 添加成员 | 成员区添加按钮 | 候选为非整体广场且未入队员工 | `POST .../members`，后端校验员工在线与同租户、重复 409 | 接口错误提示 |
| 移除成员 | 成员卡 | 可管理 | `DELETE .../members/{agent_id}` | 接口错误提示 |
| 设为项目领导 | 非 TL 成员卡 | 可管理 | `PUT .../leader`，原领导降为成员 | 接口错误提示 |
| 保存团队设置 | 设置区 | 三项均为有效数字 | `PUT /api/enterprise/teams/{id}`，合入 config | 非法值提示“请输入有效的数字” |
| 新建任务 | 看板新建按钮 | 标题非空 | `POST .../tasks`；无执行者进入竞标，指定成员则直接入队 | 空标题提示（待确认） |
| 查看完整日志 | 头部按钮 | 无 | `GET .../export`，弹窗展示并下载 JSON | 接口错误提示 |
| 开始对话 | 头部按钮 | 有项目领导 | `POST .../tl/session` 后跳工作区 | 无 TL 400 |
| 新增/编辑/置顶/归档黑板 | 黑板区 | 可管理 | 对应 POST/PUT/archive 接口 | 归档使用浏览器确认框 |
| 沉淀到知识库 | 黑板条目 | 未沉淀 | `POST .../blackboard/{id}/promote`，幂等，成功后显示“已沉淀” | 接口错误提示 |
| 改判执行者 | 任务详情 | 状态为 `bidding` 或 `pending` | 改判接口调整负责人/中标者 | 状态不符 409 |
| 人工验收 | 任务详情 | 状态为 `review` 或 `escalated` | 通过/退回重做/升级，对应 `approve/rework/escalate` | 接口错误提示 |
| 打开任务 | 任务卡或 `?task=` | 无 | 打开详情弹窗 | 无 |

任务详情中，`approve` 显示“验收通过”，`rework` 显示“退回重做”，`escalate` 显示“已升级”；执行记录标注“内部执行记录已归档”。后端状态机为 `bidding → pending` 后待执行；`pending` 可转 `in_progress`；`in_progress` 转 `review`；`review` 可转 `done/rework/escalated`；`rework` 回到 `in_progress`。超时清扫每 60 秒扫描 `bidding/in_progress/review`，超时任务升级为 `escalated` 并记录 `reason=timeout`，待执行唤醒标记为失败。

#### 状态与空态
无团队动态时显示“暂无团队动态”；看板空列不显示占位卡；成员区超出宽度时横向滚动并显示提示；任务池模式与直派模式在新建任务时区分。

#### 权限差异
页面不按角色隐藏操作按钮，所有租户用户均可见添加成员、删除、设置、新建任务、黑板、改判、验收等入口；后端除读取外的写操作统一要求团队 owner 或管理员，其他成员操作返回 403。团队详情读取仅校验租户。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/teams/{id}` | 团队与成员 |
| GET | `/api/enterprise/teams/{id}/tasks` | 任务看板 |
| GET | `/api/enterprise/teams/{id}/blackboard?status=active` | 黑板 |
| GET | `/api/enterprise/teams/{id}/events?limit=50` | 团队动态 |
| POST/DELETE | `/api/enterprise/teams/{id}/members`、`/members/{agent_id}` | 成员增删 |
| PUT | `/api/enterprise/teams/{id}/leader` | 设置项目领导 |
| PUT | `/api/enterprise/teams/{id}` | 团队设置 |
| POST | `/api/enterprise/teams/{id}/tasks` | 新建任务 |
| POST | `/api/enterprise/teams/{id}/tasks/{task_id}/award-override`、`/override`、`/resume` | 改判与恢复 |
| POST | `/api/enterprise/teams/{id}/blackboard`、`/{entry_id}/promote` | 黑板新增与沉淀 |
| GET | `/api/enterprise/teams/{id}/export` | 完整日志 |

### 4.6 团队群聊入口（`/enterprise/teams/:teamId/chat`）

#### 路由与入口
兼容入口，组件 `pages/TeamChatPage.tsx`；团队卡与团队详情的“开始对话”也走同一会话接口。

#### 页面目的
为旧链接提供跳转：确保团队存在 TL 会话，然后进入工作区聊天页。

#### 页面结构
无独立界面，仅展示加载或错误状态。

#### 字段与卡片
无表单字段；跳转目标为 `/workspace/chat/{session_id}`。

#### 筛选与排序
无。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 进入群聊 | 访问路由或点击“开始对话” | 团队存在且有在线 TL | `POST /api/enterprise/teams/{id}/tl/session`，成功后 `replace` 跳转 | 团队不存在显示“团队不存在”；无 TL 返回 400；其他错误展示后端 message |

每个团队只有一个标题含“TL 对话”的会话；项目领导变更时复用该会话并更新承接 Agent。

#### 状态与空态
加载中显示“正在进入团队群聊…”；失败时停留在本页展示错误文案。

#### 权限差异
接口只校验租户匹配，团队 owner、成员、管理员及其他同租户用户均可进入并创建会话。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/enterprise/teams/{id}/tl/session` | 获取/创建 TL 会话 |

### 4.7 员工画廊（`/workspace/gallery`）

#### 路由与入口
经 `ChatGalleryPage` 挂载 `pages/EmployeeGalleryPage.tsx`，属于对话端。

#### 页面目的
面向普通使用者浏览可用数字员工、自己的员工、团队与已发布到广场的员工，并直接发起对话。

#### 页面结构
搜索框、四个标签页（所有员工/我的数字员工/团队对话/数字员工广场）、员工卡或团队卡网格。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 员工卡 | 名称、岗位、状态、描述、工作风格、资源统计 | 复用 `EmployeeCard`，但隐藏右上角菜单 |
| 团队卡 | 名称、成员数、描述、项目领导、成员头像 | 复用 `TeamCard`，成员头像最多 3 个并显示 `+N` |
| 所有员工 | 全部可见在线员工 | `visibleEmployeeAgents(..., {activeOnly:true})` |
| 我的数字员工 | 本人创建 | `metadata.owner_user_id` 等于当前用户 |
| 数字员工广场 | 他人已发布员工 | `metadata.published_to_gallery === true` 且非本人 |
| 团队对话 | 租户内团队 | `GET /api/enterprise/teams` |

#### 筛选与排序
员工搜索覆盖姓名、姓名含创建者、岗位、描述、工作风格与掌握方向标签；团队搜索覆盖团队名、描述与项目领导名。各标签页分别过滤，无额外排序。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 与员工对话 | 点击员工卡 | 员工在线 | 进入草稿会话 `/workspace/chat/draft/{id}` | 无 |
| 与团队对话 | 点击团队卡 | 团队有项目领导 | `POST /api/enterprise/teams/{id}/tl/session` 后跳 `/workspace/chat/{session_id}` | 失败可重试（页面提供重试，文案待确认） |
| 切换标签 | 标签栏 | 无 | 切换列表数据 | 无 |
| 搜索 | 搜索框 | 无 | 本地过滤结果 | 无 |

#### 状态与空态
员工区：有搜索词时“没有匹配的数字员工”，无搜索词时“暂无数字员工”。团队区：有搜索词时“没有匹配的团队”，无搜索词时提示先到管理端创建团队并设置项目领导。非“团队对话”标签页不渲染团队区。

#### 权限差异
所有登录用户可访问；只展示 `active` 且对当前用户可见的员工。未发布且非本人的员工不会出现在“数字员工广场”。团队卡对租户内所有用户可见，不区分是否团队成员。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/chat/agents` | 可用员工列表 |
| GET | `/api/enterprise/teams` | 团队列表 |
| POST | `/api/enterprise/teams/{id}/tl/session` | 进入团队群聊 |

### 4.8 Persona 设置（`/enterprise/persona`）

#### 路由与入口
路由在 `App.tsx` 注册，组件 `pages/PersonaPage.tsx`；表单初始值来自全局选中员工，未选中员工时使用整体广场 Persona。

#### 页面目的
配置数字员工的名称、描述与岗位 Prompt，或在整体广场场景下配置组织默认 Persona。

#### 页面结构
纵向表单：名称、描述、岗位 Prompt（12 行文本域），底部保存按钮。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 名称 | 员工名或组织 Persona 名 | 选中具体员工时必填 |
| 描述 | 简介 | 文本 |
| 岗位 Prompt | 系统提示词 | 必填，写入 `persona_prompt` / `system_prompt` |

#### 筛选与排序
无。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 保存 | 底部按钮 | Prompt 非空；选中员工时名称非空 | 有选中员工：`PUT /api/enterprise/agents/{id}` 保存名称/描述/Prompt/状态；若为整体广场员工，再 `PUT /api/enterprise/persona`；无选中员工：仅 `PUT /api/enterprise/persona` | 校验失败提示“请填写必填项”；接口错误提示 |

#### 状态与空态
未选中员工且组织 Persona 为空时表单为空（是否展示引导待确认）。

#### 权限差异
`GET /api/enterprise/persona` 对任意登录用户开放；`PUT /api/enterprise/persona` 需租户管理员（`ensure_tenant_admin`）；保存具体员工需该员工 owner 或管理员。前端未按角色隐藏保存按钮。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/persona` | 读取组织默认 Persona |
| PUT | `/api/enterprise/persona` | 保存组织默认 Persona |
| PUT | `/api/enterprise/agents/{id}` | 保存员工 Persona |

### 4.9 竞标竞技场（`BiddingArena`）

#### 路由与入口
组件 `components/BiddingArena.tsx`，嵌入团队详情页的任务详情弹窗，任务处于竞标或已裁决时展示。

#### 页面目的
可视化任务竞标过程：候选人 HP、每轮陈述/反驳、评分理由与最终胜者。

#### 页面结构
按候选人聚合的卡片列表，每张卡包含 HP 条与数值、胜者为王/淘汰标记，以及按轮次排列的陈述、反驳、得分和评分理由。

#### 字段与卡片
| 字段名 | 含义 | 来源或条件 |
| --- | --- | --- |
| 候选人 | 参与竞标的成员 | 竞标记录去重后的 `agent_id` |
| HP | 竞标健康值 | `100 − Σ(10 − 得分) × 3`，下限 0 |
| 胜者为王 | 最终中标 | `winnerId` 匹配 |
| 淘汰 | HP 归零或未中标（待确认） | 由 HP 与胜者推导 |
| 轮次 | 第几轮 | `bid.round` |
| 陈述/反驳 | 竞标内容 | `bid.kind` 为 `statement` / `rebuttal` |
| 得分与理由 | TL 评分 | `bid.score`、`bid.score_rationale` |

#### 筛选与排序
按候选人分组，组内按轮次升序；无筛选控件。

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
| --- | --- | --- | --- | --- |
| 查看竞标过程 | 任务详情 | 任务有竞标记录 | 只读展示，无写操作 | 无 |

#### 状态与空态
无竞标记录时不渲染该区域；HP 颜色按阈值区分高低（具体阈值待确认）。

#### 权限差异
只读组件，权限随任务详情页；能打开任务详情的租户用户均可查看。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/teams/{id}/tasks/{task_id}` | 任务详情及竞标记录（具体返回结构待确认） |

### 4.10 权限矩阵

| 操作 | 前端呈现 | 后端校验 | 实际结果 |
| --- | --- | --- | --- |
| 查看员工列表 | 所有登录用户 | 非管理员仅返回整体广场、本人或已发布员工 | 可见范围收敛 |
| 新建员工 | 所有登录用户 | 名称必填 400、租户内重名 409，未见管理员限制 | 普通成员可创建 |
| 编辑/删除/上下线/发布/头像/密钥 | 仅 `canManage` 渲染菜单 | `_ensure_can_manage_agent`：管理员或 owner | 非 owner 403 |
| 选择员工对话 | 所有可见员工 | `use` 接口要求在线、非整体广场、对用户可见 | 不可见或下线员工 400/403 |
| 查看画廊 | 所有登录用户 | 列表仅返回可见且在线员工 | 无越权数据 |
| 创建团队 | 所有登录用户 | 仅校验名称与租户，创建者成为 owner | 普通成员可创建 |
| 删除团队/成员/设置/任务/黑板/改判/验收 | 所有租户用户均可见按钮 | `_ensure_team_manager`：owner 或管理员 | 非 owner 403 |
| 进入团队群聊 | 所有租户用户 | `tl/session` 仅校验租户与 TL 有效性 | 同租户用户均可进入 |
| 保存组织 Persona | 所有登录用户可见保存按钮 | `ensure_tenant_admin` | 非管理员 403 |
| 沉淀黑板到知识库 | 可管理用户 | 团队 owner 或管理员，接口幂等 | 重复沉淀不报错 |

前端权限与实际后端的差异集中在团队详情与团队列表：前端不隐藏管理入口，普通成员可看到全部操作按钮，点击后才由后端 403 拒绝。

---

## 5. 员工能力

员工能力含知识库、技能、SOP、工具，均带 `capability_scope`（仅“通用/仅限 SOP”）。员工归属由数字员工选择器决定；整体与私有版本以标题、操作文案、分支状态区分。

### 5.1 知识库

**路由与入口**：`/enterprise/knowledge`、`/new`。整体员工标题“知识库广场”，员工为“知识库”；列表标签“知识库广场列表/知识库列表”（`frontend-enterprise/src/pages/KnowledgePage.tsx`、`App.tsx`）。

**页面目的**：管理作用域内知识库及其文档、版本、图谱与检索。

**页面结构**：刷新/新增；统计卡（总数、上线、下线、文档数）；搜索、表格/卡片、分页；新增含新建、导入备份包与广场/员工复制（员工范围）。

**列表列**：

| 列名 | 含义 |
| --- | --- |
| 名称 | 名称与描述 |
| 状态 | 已上线/已下线 |
| 能力范围 | 通用/仅限 SOP |
| 创建者 | 创建人 |
| 版本与内容 | 版本、文档、目录、引用数 |
| 操作 | 详情、版本、导出、图谱检查、同步、上下线、删除/移除 |

**表单字段**：列表页仅搜索框，无提交表单。

**操作**：

| 操作 | 前置条件 | 结果/提示 |
| --- | --- | --- |
| 详情/版本/导出/图谱检查 | 无 | 弹窗、下载 OKF 包；“检查通过/发现 N 个建议” |
| 同步/发布 | 员工范围 | “已同步/已发布到广场” |
| 上下线/删除 | 可管理 | “已上线/已下线/已删除/已移除” |

**筛选与分页**：关键字覆盖名称、描述、状态、版本，客户端过滤，每页 10 条。

**空态**：整体“暂无知识库，点击「新增」创建一个吧”；员工“当前员工暂无知识库”。

**权限与作用域**：整体员工仅管理员可管理；员工范围须先选数字员工，否则提示“请先选择一个数字员工”。员工删除仅解绑，整体删除清除文档、索引、引用与版本记录。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET/DELETE | `/api/enterprise/knowledge-bases`、`/{id}` | 列表与删除 |
| GET | `/knowledge-bases/{id}/versions` | 版本与回滚 |
| POST | `/knowledge-bases/{id}/sync-from-overall`、`/promote-to-overall` | 同步与发布 |

#### 文档上传与入库

**路由与入口**：`/enterprise/knowledge/new`，由“新增→新建知识库”进入。

**页面结构**：返回、已有知识库横条、能力范围控件、多文件拖拽区、入库任务卡。

**表单字段**：

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 文件 | 拖拽/多选 | 是 | `.doc/.docx/.txt/.md/.markdown/.html/.htm/.pdf` | 空 |
| 能力范围 | 开关 | 否 | 仅两值 | 通用 |
| 标题 | 无输入 | — | 取文件名 | 文件名 |

**解析与切分**：后端接受 `.txt/.md/.markdown/.html/.htm/.pdf/.docx/.doc`，`.doc` 报“请转换为 .docx 后上传”（`knowledge/parser.py`）；未见大小上限（待确认）。章节 1400、引用来源 900、目录 6000、段落组 4200 字符（`knowledge/service.py`）。

**入库阶段**：排队中→解析→规范化→写入文档→规划页面→写入 OKF→生成引用→刷新 PageIndex→发现 SOP/工具→完成。

**操作与反馈**：每 1400ms 轮询；queued/running/cancel_requested 可取消。完成后弹窗“发现可新增资源”（pending 且非 warning）可确认/拒绝。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/enterprise/knowledge/documents?agent_id=` | 上传并建任务 |
| GET/POST | `/knowledge/jobs/{id}`、`/jobs/{id}/cancel` | 进度与取消 |
| GET/POST | `/knowledge/discoveries`、`/discoveries/{id}/confirm\|reject` | 建议处理 |
| POST | `/api/enterprise/knowledge/okf/import` | 导入 OKF 包 |

#### 详情、版本、图谱与检索

**表单字段**：

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 名称/描述/状态/能力范围 | 文本框、下拉、开关 | 是 | 非空；active/archived；仅两值 | 当前值 |
| 文档标题/状态/内容 | 文本框、下拉、Markdown 编辑/预览 | 是 | 非空；ready/processing/failed/archived | 当前值 |
| 概念标题/类型/摘要/源码 | 文本框、下拉、编辑器 | 标题与源码必填 | 类型限预置；源码服务端校验 | 当前值 |

概念类型 6 类：原始资料、资料页、主题、流程知识、业务规则、查询分析。文档编辑“保存并重建索引”，重建目录、引用与图谱；员工修改写入私有版本。无单文档 DELETE 接口（待确认）。

**版本管理**：列为版本、名称、状态、Head、更新时间、操作；非整体员工且非 Head 可回滚。员工上传生成下一分支版本并置 `diverged`（`agents/branching.py`）。

**知识图谱**：`KnowledgeGraphVisualization.tsx` 支持搜索、布局切换、类型过滤、节点详情，空态“暂无可视化知识/当前知识库还没有可展示的知识概念。”`KnowledgeGraphCanvas.tsx` 将 `links` 与 `citations` 画为两类连线。

**检索参数**：调试区固定 `mode: "debug"`、`max_depth: 3`、`need_evidence_pack: true`；后端默认 `max_bucket_rounds=2`、`max_buckets=4`、`max_chunks=8`、`budget_tokens=4000`、`max_depth=2`（`backend/app/knowledge/schema.py`）。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| PUT | `/knowledge-bases/{id}`、`/knowledge/documents/{id}` | 元信息、文档重建 |
| GET/PUT | `/knowledge/documents/{id}/buckets`、`/buckets/{id}`、`/buckets/{id}/chunks` | 目录与引用读写 |
| PUT | `/knowledge-bases/{id}/okf/concepts/{path}` | 保存概念 |
| POST | `/api/enterprise/knowledge/search` | 检索 |

### 5.2 技能

**路由与入口**：`/enterprise/general-skills`、`/new`、`/:slug/edit`。整体员工标题“技能广场”，员工为“技能”；列表“技能广场列表/技能列表”。

**页面目的**：维护以 `SKILL.md` 为入口的多文件技能包，支持启停、发布、复制与试运行。

**页面结构**：刷新/新增；统计卡（总数、已启用、草稿、已停用）；搜索、状态筛选、表格、分页。

**列表列**：

| 列名 | 含义 |
| --- | --- |
| 名称/描述 | 名称、slug、描述 |
| 文件 | `{skill_files.length \|\| 1} 个文件` |
| 能力范围 | 通用/仅限 SOP |
| 创建者 | 创建人 |
| 状态 | 草稿/已启用/已停用 |
| 更新时间 | 最近修改 |
| 操作 | 编辑、启停、发布、删除/移除 |

**表单字段**：列表页无表单。编辑器含技能名称（必填）、Slug（必填，创建后不可改）、描述、主页链接、能力范围、文件包；无“触发条件/步骤/脚本/参数”字段，语义写在 `SKILL.md`。保存提交 `name/slug/description/homepage/capability_scope/markdown/files/directories/status/original_slug`，新建默认 published。

**操作**：

| 操作 | 前置条件 | 结果/提示 |
| --- | --- | --- |
| 编辑/编辑本地版本 | 可管理 | 进入编辑页 |
| 停用/启用 | 对应状态 | “已停用/已启用技能” |
| 发布到广场 | `metadata.scope === 'agent_private'` | “已发布到广场” |
| 删除/移除 | 整体删除、员工移除 | “已删除/已移除技能” |

**筛选与分页**：关键字覆盖名称、slug、描述、主页；状态 all/published/draft/archived；每页 10 条。

**空态**：整体可管理“暂无技能，点击「新增」创建一个吧”，只读“暂无技能”；员工“当前员工暂无技能”。

**权限与作用域**：整体员工仅管理员可新建；员工范围须先选数字员工；整体员工不显示复制来源。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/general-skills` | 列表 |
| POST | `/general-skills/import`、`/import-package` | 保存、zip/Markdown 导入 |
| POST | `/general-skills/{slug}/publish`、`/archive` | 启停 |
| DELETE | `/general-skills/{slug}` | 删除/移除 |

#### 新建、编辑与导入

**页面结构**：左侧基本信息与文件树，右侧“运行测试”；文件树支持新建文件/文件夹、删除、重命名、拖拽导入。

**校验**：必须含 `SKILL.md`，引用文件须存在且非空；缺入口报“文件夹中没有找到 SKILL.md”。远程包≤96 MiB、单文件≤2 MiB、≤240 文件；本地导入提示“已读取 N 个文件，跳过 M 个无法读取文件”（`api/general_skills.py`）。

**导入来源**：

| 入口 | 支持形式 |
| --- | --- |
| 新建技能 | 空白编辑器 + 本地 zip/Markdown/文件夹 |
| 从开源平台导入 | 平台 slug、GitHub repo/tree/raw SKILL.md、zip URL、owner/repo |
| 从广场/数字员工复制 | 广场多选复制，或选来源员工与技能复制 |

技能无导出入口，也无版本号字段（待确认是否有隐藏能力）。

#### 试运行

选技能、输入问题、选模型后流式调用 `POST /api/enterprise/general-skills/{slug}/run/stream`，请求含 `tenant_id/agent_id/user_id/query/model_config_id/max_attempts`。前端以 10 分钟无事件为超时；展示 `execution_trace/generated_code/stdout/stderr/structured_result/reply`。后端单次默认 12 秒、上限 120 秒，输出截断 20000 字符，自动修复最多 10 次，产物最多 20 个（`backend/app/general_skills/runner.py`）。

### 5.3 SOP

**路由与入口**：`/enterprise/skills`，编辑页 `/enterprise/skills/distill`。标题“SOP”，列表“SOP 广场列表/本地 SOP”；后端仍用 `Skill`/`skill_id` 模型。

**页面目的**：管理流程知识，查看版本、调用与反馈，并通过对话蒸馏编辑。

**页面结构**：刷新/新增（新建空白、从广场/员工复制）；搜索、状态筛选，员工范围另有版本筛选；排行卡（调用、好评、待改进，当前/累计）；表格与分页。

**列表列**：

| 列名 | 含义 |
| --- | --- |
| SOP 名称/SOP ID | 名称与 `skill_id` |
| 业务域/创建者 | `business_domain`、创建人 |
| 版本/本地版本 | 版本号；广场版/已同步/本地版本/已停用 |
| 状态 | 草稿/已启用/已停用 |
| 调用/好评率/差评率 | 当前版本指标 |
| 操作 | 编辑、版本、转草稿、启停、同步/发布、删除 |

**表单字段**：列表页仅搜索与筛选。

**操作**：

| 操作 | 前置条件 | 结果/提示 |
| --- | --- | --- |
| 编辑/版本 | 可管理 | 进入蒸馏页或版本弹窗 |
| 转为草稿/停用/启用 | 整体员工且非 draft；对应状态 | “已转为草稿/已停用/已启用” |
| 同步/发布 | 员工范围 | “已同步/已发布到广场” |
| 删除/移除 | 整体删除、员工移除 | “已删除/已移除” |

**筛选与分页**：关键字覆盖名称、ID、业务域、描述、版本、创建者；状态 all/draft/published/archived；员工范围另有已同步/本地版本/已停用；每页 10 条。

**空态**：整体可管理“暂无 SOP，点击「新增」创建一个吧”，只读“暂无 SOP”；员工“当前员工暂无本地 SOP”。

**权限与作用域**：整体员工仅管理员可新建；员工范围须先选员工；分支状态由 `branch_sync_state`、`branch_status` 决定。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/skills` | 列表 |
| POST | `/skills/{id}/publish`、`/archive`、`/draft` | 启停与转草稿 |
| GET/POST | `/skills/{id}/versions`、`/{version}/rollback` | 版本与回滚 |

#### 编辑与对话蒸馏

**页面结构**：左侧“对话蒸馏”聊天，右侧“流程图/源码”工作区；右上“清空”“保存草稿”。首次发送走 `/skills/distill/stream`，已有草稿走 `/skills/{id}/rewrite/stream`；SSE 事件 `status/chunk_reset/chunk/complete/error`，完成返回 `draft_skill`、`warnings`、`tool_suggestions`，支持停止与断线恢复。代码中无“来源会话选择 + 候选技能列表”界面（待确认）。

**表单字段**：

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 对话说明 | 多行输入 | 是 | 非空才能发送 | 空 |
| 附件 | 上传 `.md/.txt/.doc/.docx` | 否 | 读取中/已读取/读取失败 | 空 |
| SOP 名称/业务域/版本号 | 保存弹窗 | 名称、版本必填 | 非空 | 草稿值、`1.0.0` |
| 节点类型/指令 | 下拉、编辑器 | 类型必填 | 7 种预置类型 | 收集信息/空 |
| 流转规则/终止节点 | 目标+条件+优先级；节点标记 | 终止节点必填 | 目标存在、至少一个终止节点 | 空/最后节点 |

节点类型：收集信息、条件判断、调用工具、检索知识、回复用户、转人工、调用子 SOP。转人工可指定处理人与通知渠道；子 SOP 必须填 `sub_sop_id`；后端校验节点 ID、起点/终止节点与边（`backend/app/skills/skill_schema.py`）。

**人工确认点**：模型给出工具建议时聊天区展示候选，可查看、测试、确认或拒绝，确认后局部更新 SOP。保存弹窗展示 diff，无变化禁用；清空前可选取消、不保存清空、保存并清空。

**限制**：工具目录 48 个、目录文本 12000 字符、单工具参数 12 个、模型修复 2 次（`backend/app/skills/skill_distiller.py`）。新建保存为 published；ID 冲突返回 409，前端自动改名另存。

**版本策略**：版本号取自 `SkillCard.version`，不自动递增；每次保存 upsert 同版本记录，可查看、回滚、删除。员工分支回滚切换本地版本并更新同步状态。

**与技能的关系**：SOP 节点通过 `capability_refs` 选择技能、工具、知识库，区分“已选能力”与“必需能力”；“仅限 SOP”资源只能被节点显式引用。SOP 间以 `subflow` + `sub_sop_id` 嵌套。

**空态**：无草稿“暂无 SOP 草稿/在左侧输入说明或上传文档后开始生成”；无流转规则“还没有后续节点，请新增流转规则。”或“当前节点是终止节点，默认流程结束。”

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/skills/distill/stream`、`/skills/{id}/rewrite/stream` | 生成、改写 |
| GET/POST | `/skills/jobs/{id}/stream`、`/jobs/{id}/cancel` | 恢复与停止 |
| POST/PUT | `/api/enterprise/skills`、`/{id}` | 新建/保存 |
| POST | `/skills/files/extract` | 提取附件 |

### 5.4 工具

**路由与入口**：`/enterprise/tools`、`/new`、`/:toolId/edit`、`/:toolId/test`、`/mcp/new`、`/mcp/:serverId/edit`。整体员工标题“工具广场”，员工为“工具”；列表“工具广场列表/员工工具”。

**页面目的**：管理 HTTP 工具、A2A Agent 与 MCP 工具集，测试请求并同步子工具。

**页面结构**：刷新/新增（新建空白、从广场/员工复制）；统计卡（总数、已启用、分桶）；搜索、分桶筛选、表格；MCP 服务器另列“MCP 服务器（工具集）”；分页。

**列表列**：

| 列名 | 含义 |
| --- | --- |
| 工具名称/分桶 | 展示名、`name`、分组 |
| 类型/能力范围 | MCP/A2A/HTTP；通用/仅限 SOP |
| 创建者 | 创建人 |
| Method/URL | 调用方法与地址 |
| 启用/操作 | 是否启用；编辑、测试、删除/移除 |

MCP 服务器列：名称（“工具集”徽标）、连接方式、MCP Apps（已协商/待协商/未启用）、能力范围、端点、工具数、启用、操作。

**表单字段**：列表页仅搜索与分桶筛选；工具编辑字段如下。

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 工具名称/展示名称/分桶/描述 | 文本框 | 名称必填 | 租户唯一、保存后不可改 | 空/空/未分桶/空 |
| 类型/Method/URL | 单选、下拉、文本框 | 是 | http/a2a/mcp；GET/POST/PUT/PATCH/DELETE；非空 | http/POST/空 |
| 执行模式/异步策略 | 单选、下拉 | 是 | sync/detached；staffdeck_worker/provider_task | sync/staffdeck_worker |
| 状态查询 URL/任务 ID/状态字段/结果字段 | 文本框 | Provider 异步时 URL 必填 | 含 `{taskId}` | 空/taskId/status/result |
| 轮询间隔/最长跟踪/超时 | 数字 | 是 | 1–3600；1–2592000；1–3600 秒 | 5/86400/8 |
| 状态映射/Headers/Auth/Input/Output Schema | JSON | 否 | 合法 JSON | 空 |
| Allowed Skills | 文本框 | 否 | 逗号分隔，留空表示全部技能可调用 | 空 |
| 能力范围/启用 | 开关 | 否 | 仅两值 | 通用/开启 |

A2A 另有 Agent Card URL、强制 Agent Card、流式消息、订阅远程任务、轮询间隔（0.1–30 秒）、高级配置 JSON、连接本机 Codex。

**操作**：

| 操作 | 前置条件 | 结果 |
| --- | --- | --- |
| 编辑 | 非 MCP 子工具 | 进入编辑页 |
| 测试 | 已保存工具 | 进入测试页 |
| 删除/移除 | 整体删除、员工移除 | 删除或解绑 |
| 发现/同步 | 服务器已保存 | “发现 N 个工具”“同步完成：新增 N，更新 M” |

**筛选与分页**：关键字覆盖名称、描述、URL、分桶，支持分桶筛选，每页 10 条。

**空态**：整体可管理“暂无工具，点击「新增」创建一个吧”，只读“暂无工具”；员工“当前员工暂无工具”；MCP 服务器“暂无 MCP 服务器”。

**权限与作用域**：整体员工仅管理员可新建；员工范围须先选员工；MCP 服务器增删改需租户管理员，子工具继承服务器范围并随服务器变更同步。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/tools` | 列表 |
| POST/PUT | `/tools`、`/tools/{id}` | 新建/编辑 |
| POST | `/tools/{id}/test`、`/tools/probe` | 已保存测试、未保存探测 |
| GET | `/tools/mcp-servers` | MCP 服务器列表 |

#### 工具测试与 MCP 服务

**测试页**：展示工具信息、调用地址、Input/Output Schema 与调用测试，按 `input_schema` 生成示例参数。A2A 另有“A2A 持久化任务”面板（时间线、产物、取消、恢复次数），空态“尚无 A2A 调用记录。测试或正式调用后，长任务状态会保留在这里。”

**MCP 服务字段**：

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 名称 | 文本框 | 是 | 字母/数字/下划线，中文转拼音，≤15 字符，保存后不可改 | 空 |
| 展示名称/描述/分桶 | 文本框 | 否 | — | 空/MCP 工具 |
| 连接方式 | 单选 | 是 | Streamable HTTP/SSE/Stdio/内置 Demo | Streamable HTTP |
| URL/Headers | 文本框、JSON | 远程必填 | 非空；Headers 合法 JSON | 空 |
| Command/Args/Env/工作目录 | 文本框、多行、JSON | Stdio 的 Command 必填 | 非空；Args 每行一个；Env 合法 JSON | 空 |
| MCP Apps/能力范围/启用 | 开关 | 否 | 单 App ≤10 MiB；仅两值 | 关闭/通用/开启 |

**连接与错误**：发现/同步前须保存服务器，否则提示“请先保存 MCP 服务器，再导入并同步工具。”Headers/Env 非法 JSON 会报错；不支持的 transport 报“不支持的 MCP transport：…”，调用异常以 `MCP_ERROR`/`MCP_PROBE_ERROR` 返回。内置 Demo 用 `transport=builtin`；MCP Apps 未启用时返回“MCP Apps is not enabled for this server”。

**依赖接口**：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST/PUT | `/tools/mcp-servers`、`/{id}` | 新建/编辑服务器 |
| POST | `/tools/mcp-servers/{id}/discover`、`/sync` | 发现与同步 |
| GET | `/tools/{id}/a2a-runs`、`/mcp-servers/{id}/apps/{uri}` | A2A 记录、MCP App 资源 |

### 5.5 能力作用域控制

`CapabilityScopeControl.tsx` 只提供“通用/仅限 SOP”：关闭为 `general`，开启为 `sop_specific`，未知值归一为 `general`。“全体员工/指定员工/团队”不是控件选项；员工范围由数字员工选择器与 `agent_id` 承载，绑定由后端 `AgentResourceBinding` 与知识库分支决定。

说明文案按资源类型区分：工具、技能、知识库为“仅在 SOP 步骤指定相关资源时可用”，SOP 为“仅能作为子 SOP 被明确调用，不会自动触发”；通用态统一为“闲聊与 SOP 执行中均可自主调用。”

保存行为：控件仅触发 `onChange('general' | 'sop_specific')`，不单独发请求，随页面创建/更新接口提交。MCP 服务器保存后子工具继承其范围，单个工具可再调整；知识库与 SOP 私有分支中，范围随版本内容保存，仅影响本地版本。

---

## 6. 对话端

对话端含数字员工广场与对话页，共用 `useChatSession` 管理会话与流式响应。

### 6.1 数字员工广场

#### 路由与入口
- `/workspace/gallery`，页面 `ChatGalleryPage.tsx`，复用 `EmployeeGalleryPage`。
- 入口：对话侧栏「数字员工广场」；折叠时显示图标。

#### 页面目的
集中展示可对话员工与团队，作为对话入口。

#### 页面结构
左侧对话侧栏；右侧分类 Tab、搜索框、员工/团队卡片与空态。

#### 列表与卡片字段
| 字段名 | 含义 | 来源/条件 |
|---|---|---|
| 头像、显示名、岗位 | 员工形象与名称；开放广场显示「开放广场」；岗位空值「待补充岗位」 | `EmployeeAvatar`、`employeeDisplayNameWithCreator`、`metadata.role_name` 或 `profile.roleName` |
| 在线状态、描述 | 绿点/灰点；描述最多两行，空值「暂无描述」 | `status === 'active'`；`employee.description` |
| 工作风格、统计 | 最多 3 个标签；资料、技能、SOP 数量 | `profile.workStyles`；`resources` 计数 |
| 团队卡 | 团队名、成员数、描述、项目领导、前 3 头像与 `+N` | `TeamCard` |

#### 筛选与排序
| 项 | 规则 |
|---|---|
| 分类 Tab | 所有员工：可见在职员工；我的数字员工：可管理员工；团队对话：已授权团队；数字员工广场：已发布且非本人员工 |
| 搜索 | 员工匹配姓名、姓名+创建者、岗位、描述、工作风格、专长标签；团队匹配名称、描述、领导名；大小写不敏感 |
| 排序 | 无排序控件，按接口返回顺序（待确认） |

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
|---|---|---|---|---|
| 发起员工对话 | 卡片「发起对话」 | 在线且非 busy | `POST /api/chat/agents/{id}/use` 后打开 `/workspace/chat/draft/{id}`，筛选切到该员工 | 「无法打开数字员工」/「发起对话失败」 |
| 发起团队对话 | 团队卡点击 | 团队可用 | `POST /api/enterprise/teams/{id}/tl/session` 后跳转会话 | 「发起团队对话失败」/「未返回团队群聊」 |
| 分类、搜索 | Tab 与搜索框 | 无 | 本地过滤 | 无 |

#### 状态与空态
- 加载失败：「加载员工失败」「加载团队失败」。
- 搜索无结果：员工「没有匹配的数字员工」「换个关键词，或切换员工分类再试试」；团队「没有匹配的团队」「换个关键词再试试」。
- 无搜索：员工「暂无数字员工」「当前分类还没有可用员工」；团队「暂无团队」「请先在管理端创建团队并设置项目领导」。

#### 权限差异
普通用户见自有及已发布广场员工，管理员见全部未隐藏员工；广场只显示已发布非本人员工；离线或 busy 时禁用发起。

#### 依赖接口
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/enterprise/agents?tenant_id=` | 员工列表 |
| GET | `/api/enterprise/teams?tenant_id=` | 团队列表 |
| POST | `/api/chat/agents/{id}/use?tenant_id=` | 标记使用 |
| POST | `/api/enterprise/teams/{id}/tl/session` | 团队 TL 会话 |

### 6.2 对话页

#### 路由与入口
- 草稿态 `/workspace/chat/draft/:draftAgentId`；会话态 `/workspace/chat/:sessionId`。
- 侧栏「新建对话」：筛选为员工时开该员工草稿，否则回广场。

#### 页面目的
承载多轮对话，展示回复、执行过程、引用与交付物。

#### 页面结构
侧栏（展开 220px、折叠 72px）加主区 `ChatHeader`、`MessageList`、`Composer`；弹窗 `ChatDialogs`，空态 `ChatEmptyState`。

#### 草稿态与会话态
| 状态 | 判定 | 表现 |
|---|---|---|
| 草稿态 | 路由含 `draftAgentId`，键 `draft:<agentId>` | 不建服务端会话；首轮收到 `session_created` 后转正式会话并 `replace` 跳转 |
| 会话态 | 路由含 `sessionId` | 加载会话、消息、轨迹，可重命名、删除、反馈 |
| 团队群聊 | `team_id` 非空 | 页头「群聊」徽标；非 TL 会话自动跳转 `/enterprise/teams/:teamId` |
| 团队执行会话 | 团队会话且标题不含「TL 对话」 | 只读，禁止人工发送 |

#### 列表与卡片字段
| 区域/字段 | 含义 | 来源/条件 |
|---|---|---|
| 页头 | 标题，空则会话 ID，再空则「新对话」；团队显示「群聊」徽标并去掉「团队」前缀与「· TL 对话」后缀；非团队有改名铅笔 | `ChatHeader`、`session.title` / `session.id`、`session.team_id` |
| 侧栏会话行 | 头像、标题、副标题、未读红点 | 团队头像取前 4 成员；副标题取 `last_agent_question` 或 `summary` |
| 员工空态 | 「Hello 我是{姓名}！」「我们来做什么？」、角色摘要、最多 3 个风格标签、资料/技能/SOP 数量 | `ChatEmptyState` |
| 团队空态 | 「Hello 我们是{团队名}！」、描述或成员构成、前 5 成员标签、成员/任务/黑板条目数 | `ChatEmptyState` |

#### 筛选与排序
| 项 | 规则 |
|---|---|
| 会话筛选 | 首项「全部会话 · N」；员工项「姓名 · 创建者 · 数量」，仅显示有会话或当前草稿员工；团队项「团队名 · 数量」；按中文名排序 |
| 列表范围 | 团队按 `team_id`；全部显示全部；员工只显示非团队且 `agent_id` 匹配的会话 |
| 排序 | 会话按 `updated_at` 倒序；消息按轮次起始合并，排队消息置后；会话列表 2.5 秒轮询，待回答 15 秒，运行中消息 1.5 秒 |

#### 操作清单
| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
|---|---|---|---|---|
| 发送 | 按钮或 Enter | 会话与模型可用，有内容或附件 | 流式渲染 | 见「异常与边界」 |
| 停止 | 停止按钮 | 存在流式轮次 | 取消并追加「用户已停止生成」 | 无 |
| 重试 | 失败后重发 | 有内容 | 新轮次请求 | 「本次响应连接中断，未能确认服务端会话。请重试发送。」 |
| 重新生成 | 未见独立入口 | — | 由重新发送实现（待确认） | — |
| 回复待回答 | 「待回答」或会话输入 | 会话 pending | 恢复原会话 | 「请输入回复内容」「待回答暂不支持附件」 |

#### 输入区与输入限制
- 文本域最高 200px，占位「输入消息，按 Enter 发送...」；Enter 发送（排除 Shift 与输入法），Shift+Enter 换行；斜杠菜单 ↑/↓、Enter、Esc；行首 Backspace 移除 chip。
- 无内容且无就绪附件、或仍在上传时禁用发送；运行中按钮提示「加入发送队列」。
- 文件多选不限类型；后端限制单次 8 个、单文件 12MB；粘贴远程图片最多 6 张；附件状态「解析中 / 类型·大小 / 上传失败」，可移除。
- 模型未配置：管理员「还没有可用模型配置，发送消息前请先完成模型配置。」并可点「配置模型」；普通用户「系统管理员尚未配置可用模型，暂时无法发送消息。请联系管理员完成模型配置。」

#### 斜杠命令
无固定清单，候选由后端按员工绑定的 SOP、技能、工具动态生成。

| 命令 | 作用 | 无正文时发送文本 |
|---|---|---|
| `/sop <SOP>` | 指定 SOP | 「开始并执行 SOP：{名称}」 |
| `/skill <技能>` | 指定技能 | 「使用技能 {名称} 完成当前任务」 |
| `/tool <工具>` | 指定工具 | 「调用工具 {名称} 完成当前任务」 |
| `/流程`、`/技能`、`/工具` | 中文别名，支持 `:` 分隔 | 同上 |

输入 `/` 最多 10 条匹配；缺目标「斜杠指令缺少目标，请从候选列表选择一个 SOP、技能或工具。」；目标不可用「指定的 SOP 不存在、未发布或未绑定到当前员工。」或「指定的技能/工具不存在、未启用、未绑定，或只能由 SOP 授权。」

#### 状态与空态
- 空态展示员工/团队问候；无历史会话侧栏「暂无历史会话」，加载中 5 行骨架。
- 轨迹受 `show_thinking_trace`、`show_skill_trace`、`show_tool_trace` 控制，失败始终显示；运行中、最近 10 分钟或仅有轨迹的消息默认展开。
- 模型加载中或失败分别提示「模型配置正在加载，请稍后再发送」「无法读取模型配置，请刷新页面后重试」。

#### 权限差异
仅管理员可配置模型；普通用户仅可改名/删除自己的会话；待回答仅可回自己或未分配的，管理员可回全部。

#### 依赖接口
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/chat/agents`、`/api/chat/sessions`、`/api/chat/sessions/{id}`；`/api/chat/sessions/{id}/messages`、`/trace`、`/events` | 员工、会话、消息、轨迹与事件回放 |
| POST | `/api/chat/stream`、`/api/chat/attachments`；`/api/chat/sessions/{id}/cancel` | 发送、上传、停止 |
| GET | `/api/chat/slash-commands`；`/api/chat/handoffs?status=pending`；POST `/api/chat/handoffs/{id}/reply` | 斜杠候选与待回答 |
| POST/DELETE | `/api/chat/messages/{id}/feedback` | 点赞/点踩 |
| PUT/DELETE | `/api/chat/sessions/{id}`；POST `/api/chat/scheduled-tasks` | 重命名、删除、定时任务 |
| GET | `/api/chat/sessions/{id}/artifacts/{task_frame_id}`；`/api/enterprise/model-configs`、`/api/enterprise/teams` | 交付物、模型与团队 |

### 6.3 消息类型与渲染

| 类型 | 呈现内容 | 来源/条件 |
|---|---|---|
| 用户消息 | 文本、附件、排队标识与删除、定时任务标识、命令 chip 与正文 | 服务端或本地排队 |
| 助手消息 | Markdown：标题、列表、引用、表格、代码块、链接、图片 | `MarkdownMessage` |
| 执行记录 | 可折叠时间线，摘要「执行记录」或「执行遇到问题」，含思考、决策、技能、工具、代码、知识行，可看代码/输出 | `TraceLine` |
| 工具调用/错误 | 工具名、输入输出摘要、失败状态、耗时；错误气泡不参与点赞 | `tool_result`、`isError` |
| 知识引用、交付物 | 「知识来源」chip 显示 `[n]` 与标题；「生成文件」可下载，图片可预览 | `knowledge_citations`（按正文 `[n]` 过滤去重）、`harness_artifacts` |
| MCP App | 隔离 iframe，高 360px，标题「MCP App · 工具名」「隔离视图」 | `harness_mcp_app_view` |
| 定时任务草案 | 「定时任务草案」/「已创建」，展示计划、类型、时区、内容、说明，可编辑、忽略、确认 | `interaction_mode === 'scheduled_task'` |
| 团队协作面板/进度 | 领导 @成员、成员回复预览、执行记录、交付物、引用，可补充信息继续执行；收集/综合阶段显示脉冲与状态文案 | 团队会话，最多 4 条；`team_progress` |

### 6.4 会话管理

| 操作 | 触发位置 | 前置条件 | 结果 | 失败提示 |
|---|---|---|---|---|
| 新建 | 侧栏「新建对话」 | 无 | 有员工筛选开草稿，否则开广场 | 无 |
| 未读/已读 | 会话行 | 非当前会话 | `last_agent_question` 或 `summary` 非空、非运行中，且 `updated_at` 晚于已读时间 1 秒以上；打开会话后写入 `skill_agent_session_read_at:<userId>` | 无 |
| 重命名 | 会话行铅笔 | 非团队、标题非空且不超过 80 字 | 更新标题 | 「请输入会话名称」 |
| 删除 | 会话行删除 | 非团队 | 二次确认「删除后无法恢复该会话及其消息记录，确定继续吗？」 | 「删除失败」 |
| 标题生成 | 首轮发送后自动 | 标题为空且无同名事件 | 后台生成并推 `session_title_summarized`，失败用首条用户消息兜底 | 无 |
| 待回答 | 侧栏按钮 | 存在 pending | 展开弹窗；超过 99 显示「99+」 | 「暂无待回答消息」 |

### 6.5 团队群聊特殊行为

- `team_id` 非空即团队群聊；侧栏成员头像堆叠，副标题「N 位成员 · 团队群聊」，标题带「群聊」，无改名/删除。
- 非嵌入模式自动跳团队页（「TL 对话」除外）；仅「TL 对话」可人工发送，执行、竞标、验收会话只读。
- TL 会话注入团队上下文并处理派任务；协作面板轮询成员回复并以气泡嵌入消息流。

### 6.6 异常与边界

| 场景 | 用户可见结果 |
|---|---|
| 空消息 / 附件超限 / 解析中 | 发送按钮禁用；「最多一次上传 8 个文件」；单文件超限「{文件名} 超过上传大小限制」；「文件还在解析中，请稍后发送」 |
| 无可用模型 | 管理员弹配置弹窗；普通用户「系统管理员尚未配置可用模型，请联系管理员完成模型配置」 |
| 待回答会话发送 | 「待回答会话仅支持发送人工回复」「待回答暂不支持附件，请发送文字回复」 |
| 会话加载中 / 无发送权限 / 未绑定员工 | 「任务信息还在加载，请稍后再发送」「当前账号不能直接向该会话发送消息，请从待回答列表回复」「该任务没有绑定数字员工，请新建任务后再发送」 |
| 会话 404 | 移除本地会话，当前会话回广场 |
| 流式中断或超时 | 心跳 20 秒未恢复进入事件回放；仍失败「本次响应连接中断，未能确认服务端会话。请重试发送。」；10 分钟空闲判定超时 |
| MCP App | 加载失败「MCP App 无法展示，已保留上方文本结果。」；副作用工具弹确认框；交付物失效时 404/409，提示「文件下载失败」 |
| 定时任务草案 | 「请输入定时任务名称」「请输入执行内容」「请输入执行计划」 |

---

## 7. 渠道接入

本章描述渠道接入页、五个渠道配置、多员工调度、身份绑定与投递行为。前端见 `frontend-enterprise/src/pages/ChannelsPage.tsx`、`channelPresentation.ts`、`pages/channels/*.tsx`，后端见 `backend/app/api/channels.py` 与 `backend/app/channels/service_*.py`。

### 7.1 渠道接入页（`/enterprise/channels`）

#### 路由与入口
由 `frontend-enterprise/src/App.tsx` 注册，组件为 `ChannelsPage.tsx`，列表与详情在同一路由内切换。

#### 页面目的
创建、配置、启停渠道接入，管理员工、身份与协作人，查看对话与投递记录。

#### 页面结构
列表态为标题、异常横幅、新建按钮与接入卡片；详情态为头部、凭证、智能分发、身份绑定、可调度员工、对话与投递日志；新建为三步弹窗。

#### 卡片或列表字段
| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 名称 | 默认「渠道名+YYYYMMDDHHMM」，可重命名，最长 50 字 | 无 |
| 渠道类型 | 微信/微信客服/企业微信/飞书/钉钉 | 无 |
| 状态 | 待扫码、待配置、已接入、已过期、已停用 | 由 `status` 映射 |
| 绑定对象 | 员工胶囊或团队名，默认员工带徽章 | 无 |

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 渠道类型 | 卡片选择 | 是 | 属于 `SUPPORTED_CHANNELS` | 无 |
| 接入名称 | 文本 | 是 | 最长 50 字 | 渠道名+时间 |
| 绑定对象 | 员工/团队切换 | 是 | `agent_id` 与 `team_id` 二选一；团队须有项目领导 | 无 |
| 智能分发 | 开关 | 否 | `auto_route` 非 False 即开 | 开 |
| 默认人工处理人 | 下拉 | 否 | 仅飞书/企业微信有渠道候选 | 未配置 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 创建接入 | 三步弹窗 | 渠道、名称、绑定对象齐全 | `POST /api/enterprise/channels` | 成功「渠道接入创建成功」；失败「创建渠道接入失败」 |
| 重命名/启停 | 卡片或详情头部 | `canManageBinding` | `PUT /{id}`、`POST /{id}/toggle-status` | 无 |
| 断开接入 | 详情头部 | 创建者或管理员 | `DELETE /{id}` | 确认「断开{渠道}接入？」 |
| 生成绑定码 | 身份绑定区 | 内部成员 | `POST /{id}/bind-code`，6 位、10 分钟 | 「绑定码 X 分 Y 秒后过期」 |
| 编辑可调度员工 | 员工区 | 非团队绑定 | `PUT /{id}` 更新挂载与 `is_default` | 无 |

#### 筛选与搜索
无搜索；管理员可见全部，普通成员仅见自己创建或协管的接入。

#### 状态与空态
`pending` 非扫码渠道「待配置」、扫码渠道「待扫码」；`expired`「已过期」；`disabled`「已停用」。异常横幅：微信「token 已失效，请重新扫码」，其他「当前未连接，请检查凭证或网络」，恢复中「系统将自动重试」。员工区、协作者、投递日志各有空态。

#### 权限差异
`canManageBinding` 允许管理员、拥有者、协作者；`canDeleteBinding` 仅管理员与拥有者。协作者只能改凭证、挂载员工与启停，删除与协作者管理仅创建者与管理员，越权 403 "Only the creator or administrator can manage this channel binding"。

#### 依赖接口
| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET/POST | `/api/enterprise/channels` 与 `/meta` | 元数据、列表与创建 |
| PUT/DELETE | `/api/enterprise/channels/{id}` | 更新与断开 |
| POST | `/{id}/toggle-status` | 启停 |
| GET/POST/DELETE | `/{id}/managers` | 协作人 |
| POST | `/{id}/bind-code` | 绑定码 |
| GET/DELETE | `/my-identity-bindings` | 身份查询与解绑 |
| GET | `/{id}/conversations`、`/{id}/deliveries/days` | 对话与投递日志 |

### 7.2 微信（wechat）

#### 路由与入口
`CHANNEL_META` 中 `setup=qrcode`、无凭证字段，详情页 `channels/WechatSetup.tsx`。

#### 页面目的
扫码完成机器人接入。

#### 页面结构
220px 二维码、状态文案、数字验证码输入（≤8 位）与扫码/刷新/取消。

#### 卡片或列表字段
无。

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 数字验证码 | 数字输入 | 条件必填 | 仅数字、≤8 位 | 空 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 扫码接入 | 主按钮 | 无 | `POST /{id}/wechat/qrcode` 后每 2 秒轮询 | 「请使用微信扫描二维码完成接入」 |
| 提交验证码 | 验证码框 | 待验证 | 轮询携带 `verify_code` | 「请在手机微信上查看并输入显示的数字」 |

#### 筛选与搜索
无。

#### 状态与空态
`scaned`「已扫码，请在手机上确认」；`confirmed`「微信接入成功」；`expired`「二维码已过期」；`verify_code_blocked`「多次输入错误，请重新扫码」。

#### 权限差异
同页面级权限。

#### 依赖接口
`POST /{id}/wechat/qrcode`、`GET /{id}/wechat/qrcode-status`。

### 7.3 企业微信（wecom）

#### 路由与入口
凭证字段 `bot_id`、`secret`、`corp_id`，详情页 `channels/WecomSetup.tsx`。

#### 页面目的
以机器人 ID、Secret 与企业 ID 接入。

#### 页面结构
未配置为表单；已配置显示 ID、连接状态与「重新配置」。

#### 卡片或列表字段
无。

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 机器人 ID | 文本 | 是 | 非空 | 空 |
| 机器人 Secret | 密码 | 是 | 非空 | 空 |
| 企业 ID | 文本 | 是 | 非空，激活后不可修改 | 空 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 保存凭证 | 表单底部 | 字段齐全 | `POST /{id}/wecom/credentials` | 成功「已保存」；缺项「请填写完整凭证」；失败「保存凭证失败」 |

#### 筛选与搜索
无。

#### 状态与空态
已配置态显示连接状态；企业或机器人变更需删除重建。

#### 权限差异
协作者可轮换凭证。

#### 依赖接口
`POST /{id}/wecom/credentials`。

### 7.4 微信客服（wechat_kf）

#### 路由与入口
字段为 `corp_id`、`secret`、`callback_token`、`encoding_aes_key`，详情页 `channels/WechatKfSetup.tsx`。

#### 页面目的
接入微信客服应用并管理客服账号。

#### 页面结构
回调配置卡、Secret 表单、客服账号列表（增删、绑定、二维码）。

#### 卡片或列表字段
| 字段名 | 含义 | 条件 |
| --- | --- | --- |
| 客服账号 ID | 微信侧标识 | 绑定后展示 |

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 企业 ID | 文本 | 是 | 生成回调配置前必填 | 未填「请先填写企业 ID」 |
| 应用 Secret | 密码 | 是 | 非空 | 空 |
| 客服账号名称 | 文本 | 是 | 非空 | 缺项「请输入客服账号名称」 |
| 客服头像 | 文件 | 是 | JPG/PNG，≤2MB | 缺项「请先上传客服头像」 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 复制回调 URL | 回调卡片 | 已创建 | 复制 `{origin}/api/channels/wechat-kf/{id}/callback` | 无 |
| 生成回调配置 | 回调卡片 | 已填企业 ID | `POST /{id}/wechat_kf/callback-config` | 成功「回调配置已生成」 |
| 保存凭证 | 凭证表单 | Secret 非空 | `POST /{id}/wechat_kf/credentials` | 成功「微信客服 API 已连接到数字员工」；失败「保存微信客服凭证失败」 |
| 账号增删/绑定 | 账号区 | 头像与名称齐全 | `accounts`、`account/{open_kfid}`、`contact-way` | 删除确认「确定删除这个微信客服账号吗？」 |

#### 筛选与搜索
无。

#### 状态与空态
Token ≤32 字符、EncodingAESKey 为 43 位英文或数字；验证失败 502「微信客服凭证验证暂时失败」。

#### 权限差异
同页面级权限。

#### 依赖接口
`/wechat_kf/callback-config`、`/wechat_kf/credentials`、`/wechat_kf/accounts`、`/wechat_kf/avatar`、`/wechat_kf/account`、`/wechat_kf/contact-way`。

### 7.5 飞书（feishu）

#### 路由与入口
字段为 `app_id`、`app_secret`，详情页 `channels/FeishuSetup.tsx`。

#### 页面目的
以自建应用凭证接入机器人。

#### 页面结构
凭证表单或已配置摘要，下方为权限清单。

#### 卡片或列表字段
无。

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| App ID | 文本 | 是 | 非空；已配置时禁用 | 空 |
| App Secret | 密码 | 是 | 非空 | 空 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 保存凭证 | 表单底部 | 字段齐全 | `POST /{id}/feishu/credentials` | 成功「已保存」；缺项「请填写完整凭证」 |

#### 筛选与搜索
无。

#### 状态与空态
已配置态显示连接状态；`channel_feishu_trace_enabled` 可关闭 trace 卡片。

#### 权限差异
同页面级权限。

#### 依赖接口
`POST /{id}/feishu/credentials`。

### 7.6 钉钉（dingtalk）

#### 路由与入口
字段为 `client_id`、`client_secret`，详情页 `channels/DingTalkSetup.tsx`。

#### 页面目的
以企业内部应用接入 Stream 模式机器人。

#### 页面结构
凭证表单或已配置摘要；提示「开放平台 → 企业内部应用 → 机器人 → Stream 模式」。

#### 卡片或列表字段
无。

#### 表单字段
| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| Client ID | 文本 | 是 | 非空；已配置时禁用 | 空 |
| Client Secret | 密码 | 是 | 非空 | 空 |

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 保存凭证 | 表单底部 | 字段齐全 | `POST /{id}/dingtalk/credentials` | 成功「已保存」；缺项「请填写完整凭证」 |

#### 筛选与搜索
无。

#### 状态与空态
已配置态显示连接状态。

#### 权限差异
同页面级权限。

#### 依赖接口
`POST /{id}/dingtalk/credentials`。

### 7.7 多员工调度与意图分发

#### 路由与入口
无独立页面，规则在 `service_routing.py`、`service_intake.py`、`service_autoroute.py`。

#### 页面目的
一个接入挂载多名员工，支持显式与自动切换。

#### 页面结构
详情页展示「/员工」「/切换 <员工名>」「/当前」「/帮助」；后端另含「/回复反馈」「/绑定」「/解绑」。

#### 卡片或列表字段
无。

#### 表单字段
无。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 员工列表 | `/员工` | 非团队绑定 | 列出挂载员工并标注默认与当前 | 「输入 /切换 <名字> 切换员工。」 |
| 切换员工 | `/切换 <名字>` 或 `/<名字>` | 名字在挂载列表 | 写指针并设 10 分钟手动保护窗 | 成功「已切换到「X」，后续消息由 TA 回复。」；失败「没有找到员工「X」。」 |
| 自动分发 | 普通消息 | 开关开启、挂载≥2、非转接、非保护窗 | LLM 分类，置信度≥0.75 才切换；SOP 进行中阈值 0.9 | 无 |

#### 筛选与搜索
无。

#### 状态与空态
无挂载行时回退绑定 `agent_id`；指针指向已卸载员工时重置为默认；团队绑定下切换指令回复「该渠道已接入团队，员工切换类指令不可用。」

#### 权限差异
渠道用户均可使用指令，保护窗按外部会话生效。

#### 依赖接口
无独立 HTTP 接口，由渠道回调触发。

### 7.8 身份绑定与消息投递

#### 路由与入口
身份绑定区在渠道详情页；后端在 `service_identity.py`、`service_intake.py`、`service_outbox.py`。

#### 页面目的
合并外部与内部账号以共享记忆。

#### 页面结构
已绑定显示标识、scope 标签与「解除绑定」；未绑定显示绑定按钮。

#### 卡片或列表字段
无。

#### 表单字段
无。

#### 操作
| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 生成绑定码 | 网页端 | 内部成员 | 6 位、10 分钟、每分钟 5 次 | 超限 429「绑定码生成过于频繁，请稍后再试」 |
| 渠道内绑定 | `/绑定 <码>` | 私聊 | 迁移会话与记忆 | 成功「绑定成功，对话将与账号「X」共享记忆与记录。」；无效「绑定码无效或已过期。」 |
| 渠道内解绑 | `/解绑` | 已绑定 | 迁回渠道身份 | 成功「已解绑 StaffDeck 账号「X」，后续对话使用独立访客身份。」 |
| 网页端解绑 | 身份绑定区 | 无 | 删除 `ChannelIdentity` | 多 scope 返回 400「请指定 external_account_scope 后再解绑」 |

#### 筛选与搜索
无。

#### 状态与空态
群聊内绑定与解绑被拒绝：「绑定/解绑只能在私聊中进行。」

#### 权限差异
绑定对象须为内部成员；飞书邀请绑定需管理员。

#### 依赖接口
`POST /{id}/bind-code`、`POST /{id}/identity-bind-code`、`GET /my-identity-bindings`、`DELETE /my-identity-bindings/{channel}`。

#### 消息能力与失败重试
五渠道均支持文本入站；图片、文件入站按适配器实现，微信语音转文字；出站图片与文件能力（待确认）。回复登记为 `ChannelDelivery`，重试上限 8 次，退避 `min(2**attempts, 300)` 秒，超限置 `failed`。凭证错误如「企业变更不允许直接修改，请删除后重新创建绑定」「该外部机器人已被其他渠道绑定使用」。

---

## 8. 员工档案与运营

本章覆盖员工作用域下的运营页面与全局入口（`src/lib/agent-scope-storage.ts`）。

### 8.1 员工档案（`/enterprise/dashboard`）

**路由与入口**：侧边栏「员工档案」；`/enterprise` 重定向至此。`DashboardPage` 以 `profileTab` 复用四条路由，默认 `work`（`src/App.tsx`）。

**页面目的**：员工运营总览。

**页面结构**：页头、四个标签页与进化面板。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 头像 / 姓名 / 岗位 | 身份 | 岗位空时显示姓名 |
| 在线状态 / 创建者 / 入职时间 | 档案 | active「在线」否则「下线」；`employeeCreatorName`、`onboardedAt` |
| 工作风格 / 系统摘要 / hero 指标 | 徽标、摘要、指标 | 最多 3 个；摘要 132 字；资料、技能、SOP、启用任务数 |

**筛选**：侧边栏切换员工；Tab 切换标签页。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 更换头像 / 编辑资料 | hero 区 | 管理员或创建者 | 打开编辑弹窗 | 「更换头像」「编辑资料」 |
| 去对话 | hero 按钮 | 无 | 跳 `/workspace/chat` | 「去对话」 |

**空态**：非管理员无员工时「还没有数字员工」；未选或整体员工时展示「开放广场」（`DashboardPage.tsx:187-243`）。

**权限差异**：`canManageEmployeeAgent` 决定头像、资料与进化面板；切换器只列可用员工。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/agents`、`/skills`、`/knowledge-bases`、`/sessions`、`/feedback/summary` | 员工与运营数据 |

### 8.2 工作记录（默认标签页）

**路由与入口**：`/enterprise/dashboard`，Tab「工作记录」。

**页面目的**：质量、活动与成长。

**页面结构**：4 指标卡、活动时间轴、6 能力卡片、成长时间轴与进化面板。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 对话次数 / 反馈次数 | 总数 | `sessions.length`、`summary.total_feedback` |
| 好评率 / 差评率 | 占比 | 好评或差评数除反馈数四舍五入；无反馈为 0 |
| 活动轨道 | 对话、定时任务、新增 SOP/工具/知识/技能 | 按日、周、月聚合 |
| 能力卡片 | 知识库、技能、SOP、工具、定时任务、对话日志 | 数量 + 跳转 |
| 成长记录 | 新增 SOP、SOP 进化、新增技能、技能升级、新增工具 | 时间升序 |

**筛选**：时间轴切换 Day、Week、Month。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 查看日志 / 进入能力页 | 指标卡、能力卡片 | 无 | 跳 `/enterprise/feedback?agent_id=...` 或对应路由 | 无 |

**空态**：「当日/本周暂无活动记录」「暂无成长轨迹」。

**权限差异**：全员可见；进化面板仅管理者。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/work-record` | 活动与成长 |

### 8.3 定时任务（`/enterprise/scheduled-tasks`）

**路由与入口**：侧边栏「定时任务」或档案页第二标签页；新建 `/enterprise/scheduled-tasks/new`，编辑 `/:taskId/edit`。

**页面目的**：定时唤醒与执行记录。

**页面结构**：统计卡、任务列表与执行记录。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 定时任务 / 计划 | 标题、指令、调度、状态 | `title` + `prompt`；一次性·时间 / 每天 时间 / 每周 周X 时间 / 每月 n 号 时间；启用（蓝）、暂停（橙）、已完成（绿）、已删除（灰） |
| 下次执行 / 已执行 / 最近结果 | 时间、次数、结果 | `next_run_at`、`n 次`；成功、失败、执行中、待补充信息、未完成、已跳过，无则「暂无」 |
| 执行记录 | 任务、状态、计划时间、完成时间、结果、查看会话 | 状态同上 |

**筛选**

| 筛选项 | 控件 | 可选值 |
| --- | --- | --- |
| 任务状态 | 下划线 Tab | 全部、待完成、已完成、已暂停 |
| 执行记录状态 | 下划线 Tab | 全部、待完成、已完成、失败、跳过 |
| 分页 | 分页器 | 每页 10 条 |

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 新增任务 | 页面按钮 | 已选非 overall 员工 | 进入新建页 | 无 |
| 查看记录 / 编辑 / 立即执行 | 行操作 | 编辑与执行需非 archived | 弹窗、编辑页或独立会话 | 执行提示「已创建独立任务会话，后台开始执行」或「已触发后台执行」 |
| 暂停 / 启用 / 删除 | 行操作 | 启用需非 completed；删除需非 archived | 切换状态或软删除 | 删除确认「删除定时任务「title」？」及「删除后不再唤醒该员工，历史执行记录会继续保留。」 |

**空态**：「暂无定时任务」「暂无执行记录」；未选员工需先选择。

**权限差异**：管理员见全部；非管理员仅见本人创建任务；跨租户 403（`backend/app/api/scheduled_tasks.py`）。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET/POST/PUT/DELETE | `/api/enterprise/scheduled-tasks`、`/{task_id}`、`/runs`、`/run-now` | 列表、新建、详情、编辑、软删除、执行记录、立即执行 |

#### 新建 / 编辑定时任务

**页面结构**：页头 +「任务说明」「唤醒计划」两卡片。

**表格列或字段**

| 名称 | 控件 | 必填 | 校验 / 默认值 |
| --- | --- | --- | --- |
| 任务名称 | 输入框 | 是 | ≤80 字；「请填写任务名称」 |
| 任务描述 | 多行（7 行） | 是 | ≤10000 字带计数；「请填写任务描述」 |
| 指定 SOP / 版本策略 | 下拉 + 单选 | 否 | SOP 默认 `__auto__`（仅已发布）；策略默认 `latest`，可选 `pinned` 固定保存时版本 |
| 内部备注 / 启用状态 | 输入框 + 开关 | 否 | 备注可空；状态默认 active |
| 调度类型 | 下拉 | 是 | 每天、每周、每月、一次性；默认每天 |
| 执行时间 / 执行日期 / 每月几号 | 时间控件 + 星期复选 + 数字 | 时间必填；日期每周必填；号数每月必填 | 一次性 datetime-local 其余 time，默认 09:00；默认周一，「请选择星期」；默认 1 号 |
| 最大运行次数 | 数字输入 | 否 | ≥1，不填无限制 |

**筛选**：无。

**操作**：保存固定提交 `concurrency_policy=forbid`、`misfire_policy=coalesce`、`timezone=getClientTimeZone()`；新建成功替换到编辑页并提示「定时任务已保存」。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST / GET / PUT | `/api/enterprise/scheduled-tasks`、`/{task_id}`、`/api/enterprise/skills` | 新建、读取、保存、SOP 选项 |

### 8.4 记忆（`/enterprise/memories`）

**路由与入口**：侧边栏「记忆」或档案页第三标签页。

**页面目的**：按用户隔离的长期记忆。

**页面结构**：筛选区、用户分组列表与详情弹窗。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 用户名 / 用户ID | 归属用户 | 空显示 `-` |
| 类型 / 记忆数 / 最近更新 / 摘要 | 徽标、条数、时间、预览 | profile、preference、fact、summary；`n 次`；本地时间 |
| 操作 | 查看 | 打开详情 |
| 详情 | 用户名、用户ID、记忆数、类型与每条字段 | 每条含 kind、更新时间、importance、session_id、内容、metadata |

**筛选**

| 筛选项 | 控件 | 可选值 |
| --- | --- | --- |
| 用户 / 分页 | 下拉（仅管理员）、分页器 | 全部用户、具体用户；每页 10 条 |
| 用户名 / 用户ID / 搜索 | 输入框 | 匹配用户名、ID、内容 |

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 查看 | 行操作 | 无 | 打开详情 | 无 |
| 清空我的记忆 | 筛选区按钮 | 已登录 | 确认后删除本人记忆 | 「将清空当前员工下你的长期记忆，不会影响其他用户。确定继续？」（无员工时改租户级）；「已清空 n 条记忆」或「没有可清空的记忆」 |

**空态**：「当前员工暂无用户记忆」；无员工「暂无记忆」。

**权限差异**：管理员或员工所有者见全部用户记忆，其他用户仅本人（`backend/app/api/memories.py`）。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / DELETE | `/api/enterprise/memories`、`/memories/me` | 查询与清空本人记忆 |

### 8.5 对话日志（`/enterprise/feedback`）

**路由与入口**：侧边栏「对话日志」或档案页第四标签页；支持 `?agent_id=`。

**页面目的**：会话、评价、归因与导出。

**页面结构**：筛选 Tab、统计汇总、可勾选列表与分页。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 对话任务 / 数字员工 | 标题、所属员工 | 标题空回退 ID；员工按 `agent_id` 解析 |
| 来源 / 用户 | 渠道与用户 | 微信、其他渠道原值、空「网页」 |
| 状态 | 评价徽标 | 差评（红）、好评（绿）、未评价（蓝） |
| 问题归因 / 最近内容 / 时间 | 分析结果、最后消息、会话时间 | 无缺口「暂无缺口」 |
| 详情 | 任务、员工、用户、评价、归因、消息流、Trace、模型输入输出、工具调用 | — |

**筛选**

| 筛选项 | 控件 | 可选值 |
| --- | --- | --- |
| 评价 / 归因 | 下划线 Tab | 全部、好评、差评、未评价、能力不足、工具问题、知识缺失、SOP 问题 |
| 对话用户 / 分页 | 下拉、分页器 | 全部用户（含数量）、具体用户；每页 10 条 |

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 查看 | 行操作 | 无 | 打开详情 | 无 |
| 导出单条 | 行操作 | 无 | 下载 JSON | `staffdeck-conversation-log-{id}.json`；「对话日志 JSON 已导出」 |
| 批量导出 | 页面按钮 | 已选行或筛选结果 | 下载 JSON | 「已导出 n 条对话日志」；超 500 条「单次最多导出 500 条对话日志，请缩小筛选范围后重试」 |

**空态**：「暂无对话日志」。

**权限差异**：按 `_owned_session_ids` 限定会话（`backend/app/api/feedback.py`）。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/feedback/summary`、`/sessions`、`/sessions/{id}` | 汇总、列表与详情 |
| POST | `/api/enterprise/feedback/{feedback_id}/reanalyze` | 重新分析（状态经 `/jobs/{job_id}`） |
| GET/POST | `/api/enterprise/sessions/{id}/export`、`/sessions/export` | 单条 / 批量导出 |

### 8.6 反馈自进化（工作记录页内嵌）

**路由与入口**：工作记录页底部，仅 `canManageEmployeeAgent` 可见（`DashboardPage.tsx:389`）。

**页面目的**：反馈驱动能力改进。

**页面结构**：标题、生成按钮、候选列表与详情（依据、预期结果、Diff）。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 资源名称 / 类型 | 候选目标 | SOP 或 Skill |
| 风险等级 | 风险 | 低、中、高（low/medium/high） |
| 状态 | 候选状态 | 待审核、校验未通过、已批准、已拒绝、已回滚 |
| 证据数 | 来源反馈数 | `source_feedback_ids` 长度 |

**筛选**：无。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 扫描反馈并生成候选 | 面板顶部 | 可管理者 | 生成候选 | 无 |
| 重新校验 | 详情 | 待审核或校验未通过 | 重新评估 | 失败映射 `EVOLUTION_*` |
| 批准 / 拒绝 / 回滚 | 详情 | 批准、拒绝需待审核或校验未通过；回滚需已批准 | 发布、已拒绝或已回滚 | 成功提示 |

**空态**：暂无候选，反馈后可扫描生成。

**权限差异**：接口均要求 `ensure_agent_scope_manager`。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET/POST | `/api/enterprise/agents/{id}/evolution/proposals`、`:analyze`、`/{proposal_id}` | 列表、生成、详情 |
| POST | `.../{proposal_id}:evaluate`、`:approve`、`:reject`、`:rollback` | 审核动作 |

### 8.7 追踪（Traces）与调试（Debug）

**路由与入口**：`src/App.tsx` 未注册路由，侧边栏无入口；源码存在但不可达（待确认）。

**页面目的**：会话轨迹与调试。

**页面结构**：Traces 列表与 JSON 弹层；Debug 输入与四个快照。

**表格列或字段**

| 页面 | 字段 | 含义 / 取值 |
| --- | --- | --- |
| Traces | 会话ID、用户ID、当前技能、当前Step、工具调用、状态、更新时间、查看 | 分页 10 |
| Debug / 快照 | Session ID、聊天输入；Router Decision、Step Agent、Tool Result、Session State | 发 `/api/chat/turn`（enterprise_debugger / enterprise_debug / debug=true）；手风琴 |

**筛选**：无；Traces 仅当前用户会话。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 查看 / 发送消息 | Traces 行、Debug 输入 | 发送需 Session ID | 打开 JSON 或发起对话 | 无 |

**空态**：Traces「暂无 Trace」。

**权限差异**：无额外判断。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/traces`、`/traces/{session_id}` | 轨迹列表与详情 |
| POST | `/api/chat/turn` | Debug 对话 |

### 8.8 教程（Tutorial）

**路由与入口**：未注册路由，侧边栏无入口。

**页面目的**：使用与架构文档。

**页面结构**：左侧目录四组（开始使用、核心功能、架构说明、参考与案例），右侧正文含功能、快速开始、场景、架构、配置、开发指南与 FAQ。

**表格列或字段 / 筛选 / 操作 / 空态 / 权限差异 / 依赖接口**：均无；仅目录锚点滚动。

### 8.9 新手引导与快速开始

**路由与入口**：登录后全局挂载（`App.tsx:1098-1100`），无独立路由。

**页面目的**：新用户引导。

**页面结构**：OnboardingGuide 2 步（欢迎、核心概念）；QuickStartGuide 管理员 10 步、非管理员 9 步，依次覆盖模型 API Key、数字员工、开放广场、员工档案、定时任务、记忆、知识库、技能、SOP 与对话端；有新版则提示。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| `staffdeck_onboarding_guide_seen` / `staffdeck_quick_start_guide_seen` / `staffdeck_update_reminded_version` | 引导与提醒状态 | 无值时自动弹出；关闭、跳过、「开始使用」写 1 并进入快速开始；完成写 1；等于最新版不再提示 |

**筛选**：无。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 跳过 / 关闭 / 开始使用 | 欢迎页按钮 | 未看完 | 写已看并转快速开始 | 「开始使用」 |
| 立即添加 | 快速开始首步 | 管理员 | 打开模型创建 | 「立即添加」 |
| 开始对话 | 快速开始末步 | 完成 | 跳 Gallery 并写已看 | 「开始对话」 |

**空态**：不适用。

**权限差异**：非管理员少一步；快速开始需已看欢迎页且未看自身。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/app/version` | 版本检查 |

### 8.10 登录页

**路由与入口**：未登录时的落地页，无独立路径；页头「登录」展开表单。

**页面目的**：账号密码登录。

**页面结构**：标题、登录按钮与账号密码表单。

**表格列或字段**

| 名称 | 含义 | 取值或条件 |
| --- | --- | --- |
| 账号 / 密码 | 登录名与密码 | 占位符均提示首次使用请输入 admin |

**筛选**：无。

**操作**

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 展开表单 / 清空 | 页头与输入框 | 未登录或有输入 | 显示表单或清空 | 无 |
| 提交 | 按钮或 Enter | 非空 | 成功后写入会话 | 空值「请输入账号」「请输入密码」；失败「账号输入错误」并附后端 message |

**空态**：不适用。

**权限差异**：按租户与用户名匹配，失败 401；角色由 `role` 判定（`backend/app/api/auth.py`）。

**会话保持**：`ultrarag_auth` 存 `{token,user}`，启动用 `/api/auth/me` 校验，401 清会话；token 含租户、用户与过期时间（`src/auth.ts`）。默认 `admin / admin`（`backend/app/db/seed.py:938`）。

**依赖接口**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST / GET | `/api/auth/login`、`/api/auth/me` | 登录、启动校验会话 |
| GET/PUT/DELETE | `/api/auth/me/avatar` | 头像读取、上传、删除 |

---

## 9. 系统管理

本章覆盖租户管理员的三个系统页面、版本提醒与全局鉴权。仅 `role = admin` 可用：非管理员访问路由即重定向员工广场（`frontend-enterprise/src/App.tsx`），侧边栏仅管理员显示三项（`components/AppSidebar.tsx`）；后端写操作校验管理员（`backend/app/security/permissions.py`）。租户取构建期 `VITE_TENANT_ID`，缺省 `tenant_demo`，无切换入口（`src/api/client.ts`）。

### 9.1 账号管理（`/enterprise/accounts`）

#### 路由与入口

路由 `/enterprise/accounts`，组件 `AccountsPage`，侧边栏「账号管理」，页面标题同名（`pages/AccountsPage.tsx`）。

#### 页面目的

维护本租户登录账号：查看、新建、改显示名/密码/角色、删除普通成员。

#### 页面结构

右上「刷新」「新建账号」；白色卡片含「账号列表」、搜索框、桌面表格/移动卡片、分页器；新建与编辑共用 `AccountDialog`，删除用 `ConfirmDialog`。

#### 列表列

| 列名 | 含义 |
| --- | --- |
| 用户名 | 登录名，超出省略 |
| 显示名 | 为空回退用户名 |
| 角色 | 「管理员」蓝标/「普通成员」灰标 |
| 创建时间 | `created_at` |
| 最近更新 | `updated_at` |
| 操作 | 编辑、删除 |

接口只返回 `source=web`；管理员传 `include_channel=true` 才含渠道账号（`backend/app/api/auth.py`，本页未用）。

#### 表单字段

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 用户名 | 文本 | 是（仅新建） | 去空格非空、租户内唯一；编辑不可改 | 空 |
| 显示名 | 文本 | 否 | 空则用用户名；截断 80 字符 | 空 |
| 初始/新密码 | 密码 | 新建必填，编辑选填 | 仅非空；编辑留空不改 | 空 |
| 账号角色 | 下拉 | 是 | `member`/`admin`；编辑自己时禁用 | `member` |

缺项前端提示「请填写账号和密码」；后端 400「Username and password are required」、409「Account already exists」。

#### 操作

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 刷新 | 右上 | 已登录 | 重拉列表 | 「加载账号失败」 |
| 新建 | 右上 | 管理员 | 建号并刷新 | 「账号已创建」 |
| 编辑 | 行菜单 | 管理员 | 改显示名、密码、角色 | 「账号已更新」 |
| 删除 | 行菜单 | 非管理员行（前端禁用） | 确认后删除 | 「删除后该账号无法登录，但其创建的数字员工仍然保留。」 |
| 启用/禁用 | — | — | 无状态与启停接口 | — |
| 重置密码 | 编辑弹窗「新密码」 | 管理员 | 无独立入口 | — |

后端禁删自己或管理员（400「Administrator account cannot be deleted」）；不存在/跨租户 404；自改角色 400「Cannot change your own account role」。删除同步删头像，员工保留。

#### 筛选与分页

前端按用户名、显示名、角色标签匹配；每页 10 条，搜索变化回第一页，有结果才显示分页。

#### 空态

「暂无账号」。

#### 权限与安全

页面与写接口要求管理员；403「Only administrator can create/manage accounts」「Cannot create/manage accounts for another tenant」。密码以 PBKDF2-HMAC-SHA256（12 万次、随机盐）存储并常量时间比较（`security/auth.py`）；无强度策略、失败锁定、账号停用。

#### 账号 API 密钥（子功能）

入口在运行设置「API 全量密钥」卡片「管理密钥」，组件 `components/AccountApiKeyDialog.tsx`；只管理**当前账号自己**的密钥（`/api/auth/me/api-credentials`）；成员因路由限制无界面入口。

- 生成：名称为「{显示名} 账号全量密钥」，权限 `user_full_access`。
- 一次性展示：明文只在创建/轮换响应返回，列表不含明文；标注「仅显示一次」「关闭后无法再次查看」，复制失败自动全选。
- 列表：名称、类型、启用/已禁用、前缀（前 20 字符加省略号）、创建/最后使用时间。
- 轮换：旧密钥立即失效，已禁用记录会复活。
- 禁用：仅 `active` 可用；空态「您还没有创建账号 API 密钥」。

#### 依赖接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/users?tenant_id=` | 列表 |
| POST | `/api/auth/users` | 新建 |
| PUT | `/api/auth/users/{id}` | 编辑 |
| DELETE | `/api/auth/users/{id}?tenant_id=` | 删除 |
| GET/POST | `/api/auth/me/api-credentials` | 本人密钥列表/创建 |
| POST | `/api/auth/me/api-credentials/{id}/rotate` | 轮换 |
| POST | `/api/auth/me/api-credentials/{id}/revoke` | 禁用 |

### 9.2 模型配置（`/enterprise/models`）

#### 路由与入口

路由 `/enterprise/models`，组件 `ModelsPage`。入口为侧边栏「模型配置」（标题「模型」）及快速上手第 2 步，后者派发 `OPEN_MODEL_CREATE_EVENT` 直接开新建弹窗；无可用模型时侧边栏带提示角标（`AppSidebar.tsx`）。

#### 页面目的

按租户维护模型接入（协议、地址、密钥、采样参数），验证后启用并设默认，供员工绑定。

#### 页面结构

4 张统计卡（模型数、已启用、默认模型、协议数）；列表卡片含搜索、表格/移动卡片、分页；右上「刷新」「新建模型」；表单弹窗与删除确认。

#### 列表列

| 列名 | 含义 |
| --- | --- |
| 名称 | 默认带「默认」徽标；下行「已启用/已停用 · 协议」 |
| 模型 | `model` 标识 |
| Base URL | 空显示「-」 |
| API Key | 脱敏值，空显示「-」 |
| 操作 | 编辑、设为默认、测试、删除 |

#### 表单字段

| 字段名 | 控件 | 必填 | 校验 | 默认值 |
| --- | --- | --- | --- | --- |
| 名称 | 文本 | 是 | 去空格非空 | 空 |
| API 协议 | 下拉 | 是 | 四枚举之一；Anthropic 温度上限 1 | `openai_chat_completions` |
| Base URL | 文本 | 否 | http/https、含主机，否则 422 | 空 |
| Model | 文本 | 是 | 去空格非空 | 空 |
| API Key | 密码 | 新建必填 | 缺失 422；编辑留空不改 | 空 |
| Temperature | 数字 | 是 | 0–2（Anthropic 0–1），越界 422 | 0.2 |
| Max Tokens | 数字 | 是 | 前端 128–32000，后端 >0 | 8192 |
| 额外请求参数 | 多行 | 否 | JSON 对象；仅 Chat Completions 显示 | `{}` |
| 设为默认 | 开关 | 否 | 仅启用时提交，需已验证 | 关 |
| 启用 | 开关 | 否 | 开启保存须先验证 | 开 |

协议枚举：`openai_chat_completions`、`openai_responses`、`anthropic_messages`、`gemini_generate_content`（`llm/model_protocols.py`）。无「供应商」字段，后端 `provider` 固定 `openai_compatible`；`protocol_options` 不暴露，私有参数走 `extra_body`。

#### 操作

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 刷新 | 右上 | 管理员 | 重载列表与协议 | 「加载模型失败」 |
| 新建/编辑 | 右上/行菜单 | 管理员 | 开弹窗 | 「请填写名称和 Model」等校验提示 |
| 保存（启用开） | 弹窗 | 参数合法 | 带 `verify_before_save=true`，三探针通过后启用；首个可用模型设默认 | 「测试并保存中」；成功「测试通过，已启用」；失败展示错误码与上游信息 |
| 保存（启用关） | 弹窗 | 参数合法 | 直接保存 | 「已保存」 |
| 测试 | 行菜单 | 管理员 | 文本/流式/JSON 三探针，单项 25/25/35 秒、总限 90 秒；首个可用模型自动启用 | 「正在测试」；陈旧结果提示重测；前端 100 秒超时 |
| 设为默认 | 行菜单 | 已验证且启用 | 清他行默认后设本行 | 「已设为默认」；未验证或未启用 409 |
| 删除 | 行菜单 | 管理员 | 删配置并级联删员工绑定 | 默认模型提示需重设默认；其他提示不可撤销 |

协议、地址、模型、密钥、`protocol_options`、`extra_body` 任一变更都会置为未验证并自动停用、取消默认，需重新验证才能启用。

#### 密钥脱敏规则

`security/encryption.py`：空返回空；≤8 位显示 `****`；否则「前 3 位 + `-****` + 后 4 位」。仅存密文，接口只回 `api_key_masked`；`APP_SECRET` 变更读取报「Secret cannot be decrypted with current APP_SECRET」。

#### 租户维度

接口以 `tenant_id` 定位，租户不符 403「Tenant mismatch」，不跨租户共享；界面无法切换。

#### 筛选与分页

按名称、模型、协议、Base URL 包含匹配；每页 8 条，统计卡用全量。

#### 空态

桌面「暂无模型，点击「新建模型」添加一个吧」；移动「暂无模型」。

#### 权限与安全

路由与写接口要求管理员；401 未登录、403 租户不符；不存在 404「Model config not found」；默认冲突 409。密钥加密存储，验证会真实调用上游。

#### 模型选择器（子功能）

`components/ModelConfigDropdown.tsx`：按钮显示 `name`（回退 `model`、「默认模型」）；无模型时禁用；菜单两行显示名称与「模型标识 · 默认」，当前项打勾，空列表「暂无可用模型」。

#### 依赖接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/model-configs?tenant_id=` | 列表 |
| GET | `/api/enterprise/model-configs/protocols?tenant_id=` | 协议 |
| POST/PUT | `/api/enterprise/model-configs[/{id}]?verify_before_save=` | 新建/更新 |
| DELETE | `/api/enterprise/model-configs/{id}?tenant_id=` | 删除 |
| POST | `/api/enterprise/model-configs/{id}/set-default?tenant_id=` | 设默认 |
| POST | `/api/enterprise/model-configs/{id}/test?tenant_id=&activate_if_initial=` | 测试 |

### 9.3 运行设置（`/enterprise/runtime-settings`）

#### 路由与入口

路由 `/enterprise/runtime-settings`，组件 `RuntimeSettingsPage`，侧边栏「运行设置」，无独立页面头。

#### 页面目的

租户级调整轨迹展示、反思与循环上限、上下文预算与压缩、沙盒与文件目录，并管理本人 API 密钥。

#### 页面结构

标题「运行设置」及副标题「统一影响当前租户下所有数字员工的执行行为。」，右侧「保存设置」；四卡：执行记录与 Agent Loop、上下文与自动压缩（含「恢复上下文默认值」）、执行隔离与文件存储、API 全量密钥（「管理密钥」）。

#### 可调项

| 项 | 控件 | 默认值 | 范围/约束 | 生效 |
| --- | --- | --- | --- | --- |
| 展示思考/技能/工具 | 三个开关 | 均开 | 布尔 | 立即 |
| 反思轮数 | 数字 | 1 | 0–5 整数，0 关闭 | 立即 |
| 单轮最大动作数 | 数字 | 32 | 1–100 整数 | 立即 |
| 上下文 Token 预算 | 数字 | 32000 | 512–262144 整数 | 立即 |
| 压缩触发比例 | 数字 | 0.70 | 0.10–0.95 | 立即 |
| 保留最近对话轮数 | 数字 | 6 | 1–50 整数 | 立即 |
| 长期/近期摘要预算 | 数字 | 各 4000 | 128–32768 整数 | 立即 |
| 纳入角色 | 两个开关 | 均开 | 至少一种；图片仅随用户消息 | 立即 |
| 长期/近期摘要前缀 | 多行 | 分别为「历史的信息可以被总结为：」「近期的历史信息总结为：」 | 非空，≤200 字 | 立即 |
| 启用 SRT 沙盒 | 开关 | 关 | 布尔，仅管理员 | 需重启 |
| 文件存储目录 | 文本 | 空 | 仅沙盒关闭时显示；绝对路径、非根目录、可写，≤1024 | 立即 |
| 网络访问 | 下拉 | `all` | `all`/`allowlist`/`deny`，仅沙盒开启时显示 | 立即 |
| 允许的域名 | 多行 | 空 | 仅白名单显示，≤200 项，支持 `*.example.com` | 立即 |
| 沙盒状态与建议 | 只读 | — | ready/degraded/disabled/不可用；Windows 首次启用给初始化命令 | 只读 |
| 有效目录与更新时间 | 只读 | `harness_workspaces` | `effective_harness_storage_path`；仅年月日 | 只读 |

「恢复上下文默认值」只重置上下文表单值，仍须保存。

#### 保存与生效

前端校验数字、区间、摘要预算之和 ≤ 上下文预算、至少一种角色、前缀非空后 `PUT /api/enterprise/ui-config`。仅沙盒启停翻转时，后端响应后约 1 秒发 `SIGTERM` 并返回 `restart_scheduled=true`；前端提示重启并轮询 `/api/health`（最多 60 次、间隔 1 秒）后刷新，超时提示「StaffDeck 重启超时，请稍后手动刷新页面」。其余提示「运行设置已保存」。重启依赖外部守护，单端口模式行为（待确认）。

#### 操作

| 操作 | 触发位置 | 前置条件 | 结果 | 提示文案 |
| --- | --- | --- | --- | --- |
| 保存设置 | 标题右侧 | 管理员 | 写入；沙盒翻转则重启 | 「运行设置已保存」/「沙盒设置已保存，StaffDeck 正在重启」 |
| 恢复上下文默认值 | 第二卡 | 管理员 | 仅重置表单 | 无 |
| 管理密钥 | 第四卡 | 管理员 | 开密钥弹窗 | 见账号 API 密钥 |
| 修改沙盒 | 第三卡 | 管理员 | 保存后重启 | 状态横幅变化 |

校验文案含「上下文 Token 预算必须在 512–262144 之间」「长期与近期摘要预算之和不能超过上下文预算」；目录错误 422（须绝对路径、非根目录、可写）。

#### 筛选与分页 / 空态

无列表、筛选、分页与列表空态；沙盒关闭时横幅「未启用」「沙盒已由管理员关闭。」，未配目录以默认目录占位。

#### 权限与安全

读取要求租户一致；写入要求管理员，否则 403「Only administrator can manage tenant settings」，跨租户「Tenant mismatch」。参数租户级共享，「单员工会话和团队成员任务共享这套参数」，不支持单员工覆盖。

#### 依赖接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/enterprise/ui-config?tenant_id=` | 读设置与诊断 |
| PUT | `/api/enterprise/ui-config` | 保存并可能重启 |
| GET | `/api/health` | 重启轮询 |
| GET/POST | `/api/auth/me/api-credentials` | 本人密钥 |

### 9.4 版本与更新提醒

#### 版本来源

`backend/app/version.py` 依次取 `STAFFDECK_VERSION`、打包旁 `staffdeck-version.txt`（macOS 另查 `Resources`）、缺省 `0.1.0`；页面不显示版本号。

#### 检查频率与来源

`components/UpdateReminder.tsx` 在启动且两个引导完成后请求一次 `GET /api/app/version`，无轮询。后端访问 `https://github.com/OpenBMB/StaffDeck/releases.atom`（4 秒超时），成功缓存 6 小时、失败 15 分钟；取最大版本，正式版忽略预发布。`STAFFDECK_UPDATE_CHECK` 可强制开关，未设置时仅 `sys.frozen` 开启，关闭时 `check_enabled=false`。

#### 提示方式

满足开关开、检查成功、有更新、有最新版本且本地未记录时，顶部居中 Toast：标题「StaffDeck 有新版本」，正文「v{最新} 已发布，你正在使用 v{当前}」，「查看更新」打开发布页，30 秒消失。

#### 可忽略条件

引导未完成、`check_enabled`/`check_succeeded`/`update_available` 为假、`latest_version` 为空，或 `localStorage` 键 `staffdeck_update_reminded_version` 等于最新版本。该键在 Toast 创建时写入，同版本不再提示；接口异常静默忽略。

### 9.5 系统级安全约束

#### 登录与令牌

`POST /api/auth/login` 接收 `tenant_id`、用户名、密码；用户名未命中且显示名唯一时可按显示名登录。失败统一 401「Invalid username or password」。令牌含 `tenant_id`、`user_id`、`username`、`exp`，有效期 14 天，以 `APP_SECRET` 做 HMAC-SHA256 签名；无刷新与注销接口。前端以 `Authorization: Bearer` 发送，令牌存 `localStorage` 键 `ultrarag_auth`（`src/auth.ts`），退出仅清本地。

#### 鉴权与越权返回

缺失令牌 401「Not authenticated」；拆分失败「Invalid token」；签名不符「Invalid token signature」；载荷错误「Invalid token payload」；过期「Token expired」；用户或租户不符「Invalid user token」。租户不一致 403「Tenant mismatch」；管理员专属 403「Only administrator can manage tenant settings」；不存在 404；模型未验证/未启用 409。前端遇 401 清会话（`src/api/client.ts`）。

#### 密钥加密

模型 Key 用 `cryptography.Fernet`，密钥由 `APP_SECRET` 的 SHA-256 摘要派生；仅存密文，只回脱敏值。公开 API 密钥以 `sd_live_` 开头，仅存摘要，密钥取 `PUBLIC_API_KEY_PEPPER`，未配置回退 `APP_SECRET`（`public_api/auth.py`）。`APP_SECRET` 缺省 `change-me-in-development`（`config.py`），生产必须覆盖。

#### 默认账号与口令策略

初始化建 `tenant_demo` 管理员 `admin`/`admin`（显示名 Administrator），已存在则强制角色为管理员；另建 `user_demo`/`demo`（`db/seed.py`）。登录页占位「首次使用请输入 admin」。无长度、复杂度、过期、复用、失败锁定策略，仅要求非空；密码以 PBKDF2-HMAC-SHA256（12 万次）加盐存储。管理员不可删除、不可自改角色，可改自己密码。

---

## 10. 权限矩阵与全局规则

### 10.1 角色与判定

系统当前只有管理员与普通用户两级角色，不使用独立的权限点配置。

| 判定项 | 位置 | 说明 |
| --- | --- | --- |
| 是否管理员 | 前端 `isEnterpriseAdmin(user)`；后端 `ensure_tenant_admin` / `_ensure_can_manage_agent` | 由登录用户信息中的管理员标记决定 |
| 是否员工 owner | 员工 `metadata.owner_user_id` 与当前用户比对 | 决定能否编辑、删除、上下线、发布该员工 |
| 是否团队 owner | 团队创建者 | 决定能否删除团队、管理成员、修改设置、创建任务、改判与验收 |
| 是否团队 TL | 团队负责人字段 | 决定团队群聊由谁主持，不单独作为管理权限 |

### 10.2 路由级权限

| 路由 | 管理员 | 普通用户 |
| --- | --- | --- |
| `/enterprise/accounts` | 可访问 | 重定向到 `/workspace/gallery` |
| `/enterprise/models` | 可访问 | 重定向到 `/workspace/gallery` |
| `/enterprise/runtime-settings` | 可访问 | 重定向到 `/workspace/gallery` |
| 其余管理端路由 | 可访问 | 可访问，数据范围受员工可见性与 owner 限制 |
| 对话端路由 | 可访问 | 可访问 |

侧边栏同步按角色渲染：账号管理、模型配置、运行设置只在管理员菜单中出现；普通用户不显示该分组。

### 10.3 资源级权限

| 对象 | 查看 | 管理 |
| --- | --- | --- |
| 数字员工 | 非管理员仅可见整体广场员工、本人创建或已发布到广场的员工 | 管理员或员工 owner |
| 员工运行密钥 | 可管理该员工的用户 | 可管理该员工的用户 |
| 团队 | 同租户用户均可查看列表与详情 | 团队 owner 或管理员 |
| 团队群聊 | 同租户用户均可进入 | TL 主持，成员参与 |
| 组织 Persona | 所有登录用户可见入口 | 仅管理员可保存 |
| 知识库/SOP/技能/工具 | 按员工可见性与能力作用域 | 资源所属员工的可管理用户 |
| 渠道配置 | 所有登录用户可见页面 | 保存等写操作按接口鉴权（待确认具体角色限制） |
| 账号、模型、运行设置 | 仅管理员 | 仅管理员 |

### 10.4 前后端权限差异

- 团队列表与团队详情对同租户用户展示全部操作按钮，不在前端隐藏；点击后由后端返回 403。前端不预先做权限置灰。
- 员工卡片的管理菜单按 `canManage` 条件渲染，未具备权限时不显示，属于前端收敛。
- 越权访问管理接口统一返回 403；未登录或令牌失效返回 401，前端捕获后清除本地会话并回到登录页。

### 10.5 数据可见性规则

- 员工列表接口按“整体广场员工 + 本人创建 + 已发布到广场”收敛，普通用户不会看到他人未发布员工。
- 数字员工广场（对话端）只返回可见且在线员工。
- 管理端所有员工相关页面共享“当前员工”上下文；切换员工或团队后，档案、能力、定时任务、记忆、对话日志的数据范围随之切换。

### 10.6 全局异常与提示约定

| 场景 | 表现 |
| --- | --- |
| 未选择员工 | 员工上下文显示“未选择”，员工相关页面展示空态引导 |
| 无可用模型配置 | 顶部黄色提示条；管理员显示「去配置」按钮，普通用户提示联系管理员 |
| 无任何员工 | 员工相关页面统一显示空态，提供「新建数字员工」「浏览开放广场」 |
| 接口失败 | 顶部轻提示展示后端返回的错误信息；错误信息缺失时使用页面级兜底文案 |
| 破坏性操作 | 删除员工、删除团队、删除会话、删除任务、清空记忆等均需二次确认 |
| 加载中 | 列表使用骨架屏或加载态；按钮在请求期间禁用（以各页面实现为准） |
| 移动端 | 侧边栏自动收起，表格在窄屏切换为卡片列表（定时任务、对话日志等） |

---

## 11. 页面、路由与接口总表

### 11.1 页面总表

| # | 页面 | 路由 | 所属端 | 访问限制 | 主要数据来源 |
| --- | --- | --- | --- | --- | --- |
| 1 | 登录 | `/`（未登录时） | 公共 | 无 | `/api/auth/login` |
| 2 | 开放广场平台 | `/enterprise/platform` | 管理端 | 登录用户 | 员工与资源列表接口 |
| 3 | 广场分类详情 | `/enterprise/platform/:kind` | 管理端 | 登录用户 | 按资源种类查询 |
| 4 | 我的数字员工 | `/enterprise/agents` | 管理端 | 登录用户（可见范围收敛） | `/api/enterprise/agents` |
| 5 | 我的团队 | `/enterprise/teams` | 管理端 | 登录用户 | `/api/enterprise/teams`、`/api/enterprise/team-threads` |
| 6 | 团队详情 | `/enterprise/teams/:teamId` | 管理端 | 登录用户（管理操作限 owner/管理员） | `/api/enterprise/teams/{id}` 系列 |
| 7 | 团队群聊 | `/enterprise/teams/:teamId/chat` | 管理端 | 同租户用户 | 团队 TL 会话接口 |
| 8 | 员工档案 | `/enterprise/dashboard` | 管理端 | 登录用户 | 员工、技能、知识、模型、工具、会话、反馈、定时任务聚合 |
| 9 | 记忆 | `/enterprise/memories` | 管理端 | 登录用户 | `/api/enterprise/memories` |
| 10 | 对话日志 | `/enterprise/feedback` | 管理端 | 登录用户 | 会话、反馈、反馈摘要接口 |
| 11 | 定时任务 | `/enterprise/scheduled-tasks` | 管理端 | 登录用户 | `/api/enterprise/scheduled-tasks` |
| 12 | 定时任务新建 | `/enterprise/scheduled-tasks/new` | 管理端 | 登录用户 | 同上 |
| 13 | 定时任务编辑 | `/enterprise/scheduled-tasks/:taskId/edit` | 管理端 | 登录用户 | 同上 |
| 14 | 知识库 | `/enterprise/knowledge` | 管理端 | 登录用户 | `/api/enterprise/knowledge` 系列 |
| 15 | 知识库新增 | `/enterprise/knowledge/new` | 管理端 | 登录用户 | 同上 |
| 16 | 技能 | `/enterprise/general-skills` | 管理端 | 登录用户 | `/api/enterprise/general-skills` |
| 17 | 技能新建/编辑 | `/enterprise/general-skills/new`、`/:slug/edit` | 管理端 | 登录用户 | 同上 |
| 18 | SOP | `/enterprise/skills` | 管理端 | 登录用户 | `/api/enterprise/skills` |
| 19 | 技能蒸馏 | `/enterprise/skills/distill` | 管理端 | 登录用户 | 蒸馏相关接口 |
| 20 | 工具 | `/enterprise/tools` | 管理端 | 登录用户 | `/api/enterprise/tools` |
| 21 | 工具新建/编辑 | `/enterprise/tools/new`、`/:toolId/edit` | 管理端 | 登录用户 | 同上 |
| 22 | 工具测试 | `/enterprise/tools/:toolId/test` | 管理端 | 登录用户 | 工具测试接口 |
| 23 | MCP 服务新建/编辑 | `/enterprise/tools/mcp/new`、`/mcp/:serverId/edit` | 管理端 | 登录用户 | MCP 服务接口 |
| 24 | 渠道接入 | `/enterprise/channels` | 管理端 | 登录用户 | `/api/enterprise/channels` |
| 25 | 账号管理 | `/enterprise/accounts` | 管理端 | 仅管理员 | 账号接口 |
| 26 | 模型配置 | `/enterprise/models` | 管理端 | 仅管理员 | `/api/enterprise/model-configs` |
| 27 | 运行设置 | `/enterprise/runtime-settings` | 管理端 | 仅管理员 | 运行设置接口 |
| 28 | 员工人设 | `/enterprise/persona` | 管理端 | 登录用户（保存限管理员） | Persona 接口 |
| 29 | 数字员工广场 | `/workspace/gallery` | 对话端 | 登录用户 | 广场员工列表 |
| 30 | 新建对话（草稿） | `/workspace/chat/draft/:draftAgentId` | 对话端 | 登录用户 | `/api/chat/agents/{id}/use` |
| 31 | 会话 | `/workspace/chat/:sessionId` | 对话端 | 会话可见用户 | 会话与流式对话接口 |

### 11.2 兼容跳转

| 旧路由 | 目标 |
| --- | --- |
| `/`、`/chat`、`/chat/` | `/workspace/gallery` |
| `/chat/draft/*`、`/chat/session_*` | `/workspace/chat/*` |
| `/enterprise/chat*` | `/workspace/gallery` 或对应会话 |
| `/enterprise` | `/enterprise/dashboard` |
| 未知路由 | `/enterprise/dashboard` |

### 11.3 后端接口分组

| 分组 | 前缀/范围 | 覆盖能力 |
| --- | --- | --- |
| 登录与用户 | `/api/auth` | 登录、当前用户、登出、改密 |
| 数字员工 | `/api/enterprise/agents`、`/api/chat/agents` | 列表、创建、更新、删除、选择员工 |
| 团队 | `/api/enterprise/teams`、`/api/enterprise/team-threads` | 团队、成员、TL、任务、群聊会话 |
| 会话与对话 | `/api/chat`、`/api/enterprise/sessions` | 流式对话、取消、附件、会话管理 |
| 知识库 | `/api/enterprise/knowledge`、`/knowledge-bases` | 文档、检索、图谱、引用 |
| 技能与 SOP | `/api/enterprise/general-skills`、`/api/enterprise/skills` | 技能、SOP、蒸馏 |
| 工具与 MCP | `/api/enterprise/tools`、`/mcp` | 工具、测试、MCP 服务 |
| 渠道 | `/api/enterprise/channels`、微信客服 | 渠道配置、绑定、回调 |
| 定时任务 | `/api/enterprise/scheduled-tasks`、`/api/chat/scheduled-tasks` | 任务、执行记录、立即执行 |
| 记忆与反馈 | `/api/enterprise/memories`、`/api/enterprise/feedback` | 记忆、对话日志、评价 |
| 进化 | `/api/enterprise/.../evolution` | 候选生成、评估、批准、拒绝、回滚 |
| 观测 | `/api/enterprise/traces` | 运行追踪 |
| 模型配置 | `/api/enterprise/model-configs` | 模型供应商、启用、默认模型 |
| 运行设置 | `/api/enterprise/ui-config`、运行设置接口 | 界面与运行时参数 |
| 版本更新 | `/api/enterprise/app-updates` | 版本检查与提醒 |
| 开放 API | `/api/v1`（开关控制） | 对外 API、Webhook、作业与资源管理 |
| 外部业务任务 | `/api/enterprise/external-tasks`、回调 | 外部任务下发与回调 |

### 11.4 附录：待确认项

以下行为在源码中未形成明确结论，验收前建议由产品与研发确认：

1. 各页面接口失败时的具体兜底文案（多数直接透传后端 `detail`）。
2. 渠道配置的写权限是否进一步限定为管理员。
3. 知识库导入的并发上限与超大文件处理策略。
4. 定时任务时区与夏令时的边界处理。
5. 团队超时与自动解散的具体阈值。
6. 运行设置中标注「需重启」的项与实际生效时机。
