# StaffDeck 员工自助注册与管理员审核 设计

**日期：** 2026-11-09
**状态：** 已采纳
**相关：** `docs/superpowers/plans/2026-11-09-employee-self-registration.md`、`docs/server-deployment.md`

## 1. 背景与决策

当前 StaffDeck 只有登录入口，账号只能由管理员在「账号管理」页逐个创建。内部员工
入职时必须先联系管理员，管理员再手工录入账号与初始口令，口令还要通过聊天工具传递。
这既是运维负担，也是口令泄漏面。

**决策：在 `users` 表增加 `status` 列，提供自助注册入口；注册后账号处于待审核状态，
管理员审核通过前不能登录。**

已评估的替代方案：

| 方案 | 做法 | 结论 |
|---|---|---|
| A（采纳） | `users.status` ∈ `active/pending/rejected` | 采纳 |
| B | 独立 `registration_requests` 表，通过后再建 `users` 行 | 否决 |

选 A 的理由：仓库里已有 15 处 `select(User)`（账号列表、处理人校验、转人工、
渠道绑定、种子数据、OIDC 回调等）。方案 B 意味着每个新查询都要先判断「查哪张表」，
而方案 A 的泄漏面只有 3 个查询点，加一个 `status` 过滤即可收口。方案 B 的额外收益
（申请记录可留档、可重复申请）在当前「内部员工自助开通」的诉求下不成立。

## 2. 目标与非目标

**目标**

- 未登录用户可在登录页自助提交注册申请（账号 / 显示名 / 口令）。
- 申请进入 `pending`；管理员在「账号管理」页看到待审核列表，可「通过」或「拒绝」。
- 未通过审核的账号无法登录，且登录页给出明确原因，而不是笼统的「密码错误」。
- 已登录用户被改为非 `active` 后，下一次请求即失效（不能继续使用旧 token）。
- 待审核/已拒绝账号不会出现在处理人选择器、SOP 处理人等内部可见面。

**非目标**

- 不做邮件/短信/站内信通知。系统当前没有任何通知设施（无 SMTP 配置、无站内信表），
  申请结果只在用户下次登录时展示。引入通知设施是独立计划。
- 不做对外开放注册。注册接口只在部署方给出的内部域名上使用，租户由前端
  `VITE_TENANT_ID` 带入；不提供租户自助创建。
- 不做邮箱验证、不做邀请码、不做密码强度策略升级（沿用 8–128 位长度约束）。
- 不改动渠道懒建账号、OIDC 首登建号的默认状态（它们必须保持 `active`）。

## 3. 状态机

```
       注册            管理员通过
  (无) ────> pending ──────────────> active
                │                      │
                │ 管理员拒绝            │ 管理员拒绝/停用
                └────> rejected <──────┘
                          │
                          └── 管理员通过 ──> active
```

- `active`：正常账号，可登录、可被指派。
- `pending`：注册申请待审核。不能登录；管理员可通过或拒绝。
- `rejected`：申请被拒绝。不能登录；管理员可改判为 `active`。
- **拒绝后不允许自助重新注册**：同名再注册返回 409，提示联系管理员。否则「拒绝」
  形同虚设，攻击者可无限重试。
- 状态只能由管理员通过专用端点变更；`PUT /api/auth/users/{user_id}` 不改状态。

## 4. 契约

### 4.1 数据模型

`backend/app/db/models.py` 的 `User` 增加：

```python
status: str = Field(default="active", index=True)
```

默认值即 `active`，因此 `ensure_bootstrap_admin`（`backend/app/db/seed.py`）、
`seed_demo_data`、OIDC 回调（`backend/app/api/auth.py`）、渠道懒建
（`backend/app/channels/service_identity.py`）四条既有建号路径无需逐处修改。

`UserRead` 增加 `status: str = "active"`，由 `_user_read()`
（`backend/app/api/auth.py`）统一填充——它是全库唯一构造 `UserRead` 的地方。

### 4.2 注册

```
POST /api/auth/register        （无需认证）
{"tenant_id": "tenant_demo", "username": "zhangsan",
 "display_name": "张三", "password": "至少8位"}

201 {"status": "pending", "username": "zhangsan"}
```

- 先按来源 IP 限速（`_rate_limit`，键 `register:{host}`，与登录窗口独立）。
- `ensure_tenant` 校验租户存在，否则 404。
- 用户名 strip 后 1–64 字符；口令 8–128 字符（与自助改密一致）；显示名 ≤80 字符。
- 同名已存在：
  - `pending` → 409 `{"code": "REGISTRATION_PENDING", "message": ...}`
  - `rejected` → 409 `{"code": "REGISTRATION_REJECTED", "message": ...}`
  - `active` → 409 `{"code": "ACCOUNT_EXISTS", "message": ...}`
- 新建行：`source="web"`、`role="member"`、`status="pending"`。
- 响应体不含口令、不含用户 id。

### 4.3 登录

`POST /api/auth/login` 在口令校验通过后增加状态判断：

- `pending` → 403 `{"code": "ACCOUNT_PENDING_APPROVAL", "message": "..."}`
- `rejected` → 403 `{"code": "ACCOUNT_REGISTRATION_REJECTED", "message": "..."}`
- `active` → 正常签发 token

用 403 而不是 401，因为这不是凭据错误；前端按 `code` 分支展示，不能把它塞进
「密码错误」位。口令错误仍然是 401 `Invalid username or password`，且**先校验口令
再判断状态**，避免不知道口令的人也能枚举账号状态。

### 4.4 已登录用户的状态收口

`backend/app/security/auth.py` 的 `get_current_user` 在租户校验后增加：

```python
if getattr(user, "status", "active") != "active":
    raise HTTPException(status_code=401, detail="Account is not active")
```

返回 401 而非 403 是刻意的：前端 `App.tsx` 只在 `isAuthError()`（401）时清除会话并
跳回登录页。若返回 403，被停用的用户会一直停留在应用里反复报错。

### 4.5 管理员审核

```
PUT /api/auth/users/{user_id}/status        （仅管理员）
{"tenant_id": "tenant_demo", "status": "active" | "rejected"}

200 UserRead
```

- 校验 `_require_admin`，目标账号必须属于该租户，否则 404。
- 只接受 `active`/`rejected`；`pending` 不能通过 API 设置（申请创建时才产生）。
- 允许 `rejected → active`（管理员改判）与 `active → rejected`（停用）。
- 不允许管理员把自己的状态改为 `rejected`（400），避免唯一管理员自锁。
- 写 `updated_at`。

### 4.6 内部可见性

- `GET /api/auth/users`：`UserRead` 带 `status`；非管理员分支在 `source == "web"`
  之外再加 `status == "active"`，待审核账号不进处理人选择器。
- `backend/app/core/human_handoff_service.py` 的 `_is_internal_assignee` 与
  `backend/app/api/skills.py` 的 `internal_ids` 同样要求 `status == "active"`。
- `backend/app/channels/service_intake.py` 的绑定冲突提示**不改**：待审核账号
  不可能已有渠道绑定，加状态判断只是噪音。

## 5. 迁移

生产库必须能在不丢数据的前提下补列。

- **SQLite（开发/测试）**：`backend/app/db/database.py` 的
  `_migrate_sqlite_skill_schema()` 的 `users` 分支已有 `role`/`source` 补列先例，
  照抄 `status VARCHAR NOT NULL DEFAULT 'active'` 与
  `CREATE INDEX IF NOT EXISTS ix_users_status ON users(status)`。
- **PostgreSQL（生产）**：新增 `backend/alembic/versions/0003_user_status.py`，
  `down_revision = "0002"`；`upgrade` 用 `sa.inspect(op.get_bind())` 判列/判索引后再加，
  保持与 0002 相同的幂等写法；`downgrade` 先 drop 索引再 drop 列。

**上线影响（必须写进部署文档）**：`assert_schema_current()` 要求
`alembic_version` 等于 head。新增 0003 后，未执行迁移的生产库会在 `api` 启动时
直接失败。这是期望行为——升级顺序仍是「备份 → 停 worker → 迁移 → 重建 api」，
见 `docs/server-deployment.md` §5。

## 6. 前端

### 6.1 登录页（`frontend-enterprise/src/pages/LoginPage.tsx`）

- 未展开态：`[登录]` 与 `[注册]` 两个按钮并列（注册为描边次级按钮）。
- 注册态：展开表单，字段为「账号」「显示名」「密码」；提交按钮「提交注册申请」，
  下方「已有账号？去登录」切回登录态。
- 提交成功：清空表单，展示提示「注册申请已提交，请等待管理员审核」，并切回登录态。
- 登录失败：按 `ApiError.code` 分支——
  `ACCOUNT_PENDING_APPROVAL` → 「账号正在等待管理员审核」；
  `ACCOUNT_REGISTRATION_REJECTED` → 「注册申请未通过，请联系管理员」；
  其余仍走原有的账号/密码字段错误位。状态类提示显示在表单上方的提示条，不标红输入框。

### 6.2 账号管理页（`frontend-enterprise/src/pages/AccountsPage.tsx`）

- `EmployeeAccount` 增加 `status: 'active' | 'pending' | 'rejected'`。
- 表格增加「状态」列，用 `StatusBadge`：`active` 绿「正常」、`pending` 橙「待审核」、
  `rejected` 红「已拒绝」。
- 搜索框左侧加状态筛选（全部 / 待审核 / 正常 / 已拒绝），默认「全部」，
  与搜索关键字叠加过滤；「待审核」标签上显示数量角标。
- 行操作菜单：`pending` 行在「编辑」之前增加「通过」「拒绝」两项；
  `rejected` 行增加「通过」一项；操作调用 4.5 的端点，成功后刷新列表。
- 移动端卡片同步展示状态徽标。

### 6.3 文案

新增的所有中文文案必须同步写入 `frontend-enterprise/src/i18n/en.json`，
否则 `npm run i18n:check` 失败。

## 7. 验收标准

- [ ] 未登录用户能在登录页提交注册申请，数据库出现 `status=pending`、`source=web` 的账号。
- [ ] 待审核账号用正确口令登录返回 403 且 `code=ACCOUNT_PENDING_APPROVAL`；
      用错误口令返回 401（不泄漏状态）。
- [ ] 管理员在账号管理页点击「通过」后，该账号立即可登录。
- [ ] 点击「拒绝」后登录返回 403 `code=ACCOUNT_REGISTRATION_REJECTED`，同名再注册返回 409。
- [ ] 已登录用户被改为 `rejected` 后，下一次 API 调用返回 401，前端自动登出。
- [ ] 待审核账号不出现在处理人选择器（`GET /api/auth/users` 非管理员视图）。
- [ ] 旧 SQLite 库（无 `status` 列）启动时自动补列，已有账号全部为 `active`。
- [ ] PostgreSQL 上 `alembic upgrade head` 到 `0003`，`downgrade -1` 可回退且不丢其他列。
- [ ] 非管理员调用 `PUT /api/auth/users/{id}/status` 返回 403。
- [ ] `ruff check backend`、`pytest backend/tests`、`vitest`、`i18n:check` 全绿。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 注册接口被公开扫描灌垃圾账号 | IP 限速 + 全部落 `pending`，不消耗任何可用资源；管理员可拒绝 |
| 403 状态提示泄漏「某账号存在且待审核」 | 只在口令正确后才返回 403；口令错误一律 401 |
| 管理员误把唯一管理员停用 | 禁止把**自己**改为 `rejected`；管理员账号仍不可删除 |
| 旧库补列后索引缺失导致列表变慢 | SQLite 分支与 Alembic 迁移都显式建 `ix_users_status` |
| 生产升级忘记跑 0003 导致 api 起不来 | `assert_schema_current` 快速失败 + 部署文档升级顺序 + 明确错误信息 |
