# StaffDeck M2 数字员工分发 API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/api/v1` 上交付数字员工配置分发能力（列表 / 单员工 / 技能包导出 / ETag 增量），payload 严格通过 deepchat `digitalEmployeeSchema` 的契约金样测试，使 deepchat `HttpDigitalEmployeeProvider` 可拉取真实配置。

**Architecture:** 新增 `app/digital_employees/` 纯函数层（契约常量 → SOP 文本渲染 → payload 组装 → 技能包打包），四项职责各自一个文件，不依赖 FastAPI；新增 `app/public_api/digital_employees.py` 只做鉴权、ETag、HTTP 语义与审计，路由注册进既有的 `create_public_api_app()`。所有数据读取复用 `app.agents.branching` 的可见性判定，保证「公开 API 能看到的员工/能力」与「管理端看到的一致」。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel/SQLAlchemy 2 + pydantic 2 + jsonschema（dev，已有）+ pytest

**Spec:** `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（§6.1 通道①、§11 里程碑 M2）

## Global Constraints

- 遵循 AGENTS.md：Conventional Commits（`feat(api): ...`）；Python ruff 100 字符行宽；后端测试目录 `backend/tests`；每个任务单独 commit，不 `--no-verify`
- 依赖不得新增：契约校验用已有的 dev 依赖 `jsonschema>=4.23.0`；zip 用标准库 `zipfile`
- 本机 Python：`backend/.venv/Scripts/python.exe`（Windows），`uv sync --frozen --extra dev` 是唯一可信的安装方式；`backend/uv.lock` 是依赖唯一来源
- **零数据库迁移**：M2 不新增表、不改列。`executionMode` 存 `AgentProfile.metadata_json["execution_mode"]`，SOP 级 `execution_hint` 存 `AgentResourceBinding.metadata_json["execution_hint"]`（两者今天就能通过管理端与 `PUT /agents/{id}/resources` 写入）
- 鉴权沿用既有 scope：分发端点用 `agents:read`（spec §8 未引入新 scope）；密钥必须是 `sd_live_` API key，不支持用户 JWT（`get_public_principal` 而非 `get_public_or_admin_principal`）
- payload 中禁止出现工具凭证：`Tool.headers_json` / `auth_json` / `env_json` 一律不出现在响应里（只有 `staffdeck:{name}` 命名空间字符串）
- **契约勘误（实现以本条为准）**：spec 文档 §3 记的是 `skillIds.max(10)` / `tools.max(20)`，但 fork `jacknjzhou/deepchat-project` 分支 `202610211551` 的 `src/shared/contracts/routes/digitalEmployee.routes.ts` 实际是 `skillIds.max(20)` / `tools.max(50)`。本计划按**真实代码**（20 / 50）实现，并把该文件转写为 JSON Schema 作为金样 fixture
- 契约上限是硬约束，任一员工违规会让 deepchat 整批解析失败：所有字符串/数组在组装时按上限裁剪并写入 `warnings[]`，绝不抛出
- 长度按 **UTF-16 code unit** 计算（JS `String.length` 语义），emoji 计 2 —— 与 zod 的 `.max()` 对齐

### 契约（deepchat `digitalEmployeeSchema`，逐字转写）

```ts
sopDocumentSchema = { title: string 1..100, content: string 1..20000 }
digitalEmployeeSchema = {
  id: 1..64, name: 1..50, role: 1..50, description: 0..500,
  avatar: string 0..256 | null, capabilities: string[] 1..30 × ≤20,
  greeting: string 0..500 | null, systemPrompt: string 1..10000,
  sops: sopDocumentSchema[] ≤20, knowledgeBaseIds: string[] ≤10,
  skillIds: string[] ≤20, tools: string[] ≤50, enabled: boolean,
  updatedAt: string ≥1
}
// object 非 strict：未知字段不破坏解析（我们的扩展字段靠这条活着）
```

StaffDeck 扩展字段（deepchat 可选消费）：`executionMode`、`configHash`、`skills[]{id,name,slug,downloadUrl}`、`knowledgePackagesUrl`、`warnings[]`。

---

### Task 1: 契约 fixture 与 zod 长度工具

**Files:**
- Create: `backend/tests/fixtures/deepchat_digital_employee.schema.json`
- Create: `backend/app/digital_employees/__init__.py`
- Create: `backend/app/digital_employees/contract.py`
- Test: `backend/tests/test_digital_employee_contract.py`

**Interfaces:**
- Produces: `app.digital_employees.contract` 常量 `MAX_ID=64`、`MAX_NAME=50`、`MAX_ROLE=50`、`MAX_DESCRIPTION=500`、`MAX_AVATAR=256`、`MAX_CAPABILITY=30`、`MAX_CAPABILITIES=20`、`MAX_GREETING=500`、`MAX_SYSTEM_PROMPT=10000`、`MAX_SOPS=20`、`MAX_SOP_TITLE=100`、`MAX_SOP_CONTENT=20000`、`MAX_KNOWLEDGE_BASE_IDS=10`、`MAX_SKILL_IDS=20`、`MAX_TOOLS=50`
- Produces: `zod_len(value: str) -> int`（按 UTF-16 code unit 计长）、`clamp_text(value: str, limit: int) -> tuple[str, bool]`（返回裁剪结果与是否发生裁剪）、`clamp_items(values: list[T], limit: int) -> tuple[list[T], bool]`
- Produces: `backend/tests/deepchat_contract.py:validate_digital_employee(payload) -> None`（失败抛 `AssertionError`，带 jsonschema 的错误路径）

- [ ] **Step 1: 写契约 JSON Schema fixture**

从 deepchat fork 转写（保留 `$comment` 记录来源，便于日后核对漂移）：

`backend/tests/fixtures/deepchat_digital_employee.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "staffdeck://tests/fixtures/deepchat/digital-employee.schema.json",
  "title": "deepchat digitalEmployeeSchema",
  "$comment": "Transcribed from jacknjzhou/deepchat-project@202610211551 src/shared/contracts/routes/digitalEmployee.routes.ts. zod strings use min/max on UTF-16 length; arrays use .max(n). Objects are non-strict, so extra properties are allowed.",
  "type": "object",
  "required": [
    "id", "name", "role", "description", "avatar", "capabilities",
    "greeting", "systemPrompt", "sops", "knowledgeBaseIds",
    "skillIds", "tools", "enabled", "updatedAt"
  ],
  "additionalProperties": true,
  "properties": {
    "id": { "type": "string", "minLength": 1, "maxLength": 64 },
    "name": { "type": "string", "minLength": 1, "maxLength": 50 },
    "role": { "type": "string", "minLength": 1, "maxLength": 50 },
    "description": { "type": "string", "maxLength": 500 },
    "avatar": { "type": ["string", "null"], "maxLength": 256 },
    "capabilities": {
      "type": "array",
      "maxItems": 20,
      "items": { "type": "string", "minLength": 1, "maxLength": 30 }
    },
    "greeting": { "type": ["string", "null"], "maxLength": 500 },
    "systemPrompt": { "type": "string", "minLength": 1, "maxLength": 10000 },
    "sops": {
      "type": "array",
      "maxItems": 20,
      "items": {
        "type": "object",
        "required": ["title", "content"],
        "additionalProperties": true,
        "properties": {
          "title": { "type": "string", "minLength": 1, "maxLength": 100 },
          "content": { "type": "string", "minLength": 1, "maxLength": 20000 }
        }
      }
    },
    "knowledgeBaseIds": {
      "type": "array",
      "maxItems": 10,
      "items": { "type": "string", "minLength": 1 }
    },
    "skillIds": {
      "type": "array",
      "maxItems": 20,
      "items": { "type": "string", "minLength": 1 }
    },
    "tools": {
      "type": "array",
      "maxItems": 50,
      "items": { "type": "string", "minLength": 1 }
    },
    "enabled": { "type": "boolean" },
    "updatedAt": { "type": "string", "minLength": 1 }
  }
}
```

- [ ] **Step 2: 写契约校验辅助**

`backend/tests/deepchat_contract.py`

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent / "fixtures" / "deepchat_digital_employee.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate_digital_employee(payload: dict[str, Any]) -> None:
    """Assert a StaffDeck payload satisfies deepchat's zod contract.

    Raises AssertionError with the JSON pointer of the first violation, so a
    length regression names the offending field instead of the whole employee.
    """
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    pointer = "/" + "/".join(str(part) for part in first.path)
    raise AssertionError(f"digitalEmployee contract violation at {pointer}: {first.message}")
```

- [ ] **Step 3: 写失败测试（契约 fixture 自检 + 长度工具语义）**

`backend/tests/test_digital_employee_contract.py`

```python
from __future__ import annotations

import pytest

from deepchat_contract import validate_digital_employee
from app.digital_employees.contract import clamp_items, clamp_text, zod_len


def _minimal(**overrides):
    payload = {
        "id": "agent-1",
        "name": "财务",
        "role": "报销管家",
        "description": "",
        "avatar": None,
        "capabilities": [],
        "greeting": None,
        "systemPrompt": "# 角色\n你是财务数字员工。",
        "sops": [],
        "knowledgeBaseIds": [],
        "skillIds": [],
        "tools": [],
        "enabled": True,
        "updatedAt": "2026-10-22T09:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_contract_accepts_minimal_employee():
    validate_digital_employee(_minimal())


def test_contract_rejects_over_length_strings_and_extra_items():
    # 守卫的守卫：fixture 本身必须真的会拦住违规，否则金样测试形同虚设
    with pytest.raises(AssertionError, match="maxLength|maxItems"):
        validate_digital_employee(_minimal(name="名" * 51))
    with pytest.raises(AssertionError, match="maxItems"):
        validate_digital_employee(_minimal(tools=[f"t{i}" for i in range(51)]))
    with pytest.raises(AssertionError, match="sops/0/content"):
        validate_digital_employee(_minimal(sops=[{"title": "t", "content": "x" * 20001}]))


def test_contract_allows_unknown_extension_fields():
    validate_digital_employee(_minimal(executionMode="hybrid", configHash="sha256:abc"))


def test_zod_len_counts_utf16_code_units():
    assert zod_len("财务") == 2
    # emoji 在 UTF-16 里占两个 code unit，JS String.length === 2
    assert zod_len("🙂") == 2
    assert zod_len("a🙂b") == 4


def test_clamp_text_truncates_by_utf16_units():
    text, truncated = clamp_text("a" * 5, 5)
    assert (text, truncated) == ("a" * 5, False)
    text, truncated = clamp_text("a" * 6, 5)
    assert (text, truncated) == ("a" * 5, True)
    # 不得把一个代理对裁成半个字符
    text, truncated = clamp_text("🙂" * 3, 5)
    assert truncated is True
    assert zod_len(text) <= 5
    assert text == "🙂" * 2


def test_clamp_items_reports_overflow():
    kept, overflowed = clamp_items([1, 2, 3], 2)
    assert kept == [1, 2]
    assert overflowed is True
    kept, overflowed = clamp_items([1], 2)
    assert (kept, overflowed) == ([1], False)
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_contract.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.digital_employees'`

- [ ] **Step 5: 实现契约常量与裁剪工具**

`backend/app/digital_employees/__init__.py`

```python
"""Pure builders that turn StaffDeck rows into deepchat digital-employee payloads."""
```

`backend/app/digital_employees/contract.py`

```python
from __future__ import annotations

from typing import TypeVar

# Mirrors jacknjzhou/deepchat-project@202610211551
# src/shared/contracts/routes/digitalEmployee.routes.ts. Any change here must be
# accompanied by a change to tests/fixtures/deepchat_digital_employee.schema.json.
MAX_ID = 64
MAX_NAME = 50
MAX_ROLE = 50
MAX_DESCRIPTION = 500
MAX_AVATAR = 256
MAX_CAPABILITY = 30
MAX_CAPABILITIES = 20
MAX_GREETING = 500
MAX_SYSTEM_PROMPT = 10000
MAX_SOPS = 20
MAX_SOP_TITLE = 100
MAX_SOP_CONTENT = 20000
MAX_KNOWLEDGE_BASE_IDS = 10
MAX_SKILL_IDS = 20
MAX_TOOLS = 50

T = TypeVar("T")


def zod_len(value: str) -> int:
    """Length as JavaScript sees it: UTF-16 code units, not code points."""
    return len(value.encode("utf-16-le")) // 2


def clamp_text(value: str, limit: int) -> tuple[str, bool]:
    """Trim to at most `limit` UTF-16 code units without splitting a surrogate pair."""
    text = value or ""
    if zod_len(text) <= limit:
        return text, False
    # Iterate backwards from the limit so an emoji is dropped whole rather than
    # leaving a lone surrogate that zod's length check would count differently.
    for end in range(limit, -1, -1):
        candidate = text.encode("utf-16-le")[: end * 2].decode("utf-16-le", errors="ignore")
        if zod_len(candidate) <= limit:
            return candidate, True
    return "", True


def clamp_items(values: list[T], limit: int) -> tuple[list[T], bool]:
    if len(values) <= limit:
        return list(values), False
    return list(values[:limit]), True
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_contract.py -q`
Expected: PASS（6 passed）

- [ ] **Step 7: Commit**

```bash
git add backend/tests/fixtures/deepchat_digital_employee.schema.json backend/tests/deepchat_contract.py backend/app/digital_employees backend/tests/test_digital_employee_contract.py
git commit -m "feat(api): transcribe deepchat digital-employee contract into a golden fixture"
```

---

### Task 2: SOP 文档渲染（execution_hint 分流）

**Files:**
- Create: `backend/app/digital_employees/sop_text.py`
- Modify: `backend/app/agents/branching.py`（新增 `visible_general_skill_rows`）
- Modify: `backend/app/core/capability_manifest.py:595`（`_visible_general_skills` 委托给新函数）
- Test: `backend/tests/test_digital_employee_sop_text.py`

**Interfaces:**
- Consumes: `app.digital_employees.contract.MAX_SOPS / MAX_SOP_TITLE / MAX_SOP_CONTENT / clamp_text / clamp_items`
- Consumes: `app.agents.branching.visible_skill_rows(db, tenant_id, agent_id, include_inactive=False)` → `list[Skill]`
- Produces: `app.digital_employees.sop_text.render_sop_content(card: dict) -> str`（SkillCard dict → 纯文本 SOP，永不抛异常）
- Produces: `app.digital_employees.sop_text.effective_hint(mode: str, hint: str | None) -> str`（返回 `"local"` 或 `"remote"`）
- Produces: `app.digital_employees.sop_text.select_sop_documents(db, tenant_id, agent_id, mode, binding_metadata) -> tuple[list[dict[str, str]], list[str]]`（返回 `sops[]` 与 warning 列表）
- Produces: `app.agents.branching.visible_general_skill_rows(db, tenant_id, agent_id=None) -> list[GeneralSkill]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_digital_employee_sop_text.py`

```python
from __future__ import annotations

from app.digital_employees.sop_text import effective_hint, render_sop_content


def _card(**overrides):
    card = {
        "skill_id": "expense_over_limit_approval",
        "name": "报销超标审批",
        "description": "金额超标准时走审批",
        "goal": ["判断是否超标", "超标时升级审批"],
        "required_info": ["报销金额", "费用类型"],
        "response_rules": ["必须给出制度条款"],
        "nodes": [
            {
                "node_id": "check_amount",
                "type": "collect_info",
                "name": "确认金额",
                "instruction": "收取报销金额与费用类型。",
                "expected_user_info": ["报销金额", "费用类型"],
            },
            {
                "node_id": "escalate",
                "type": "handoff",
                "name": "升级审批",
                "instruction": "把超标单据交财务负责人。",
            },
        ],
        "edges": [
            {
                "source_node_id": "check_amount",
                "next_node_id": "escalate",
                "condition": "amount_over_limit",
                "label": "超标",
            }
        ],
        "start_node_id": "check_amount",
        "terminal_node_ids": ["escalate"],
    }
    card.update(overrides)
    return card


def test_render_includes_goal_steps_and_rules():
    text = render_sop_content(_card())
    assert "判断是否超标" in text
    assert "1. 确认金额" in text
    assert "收取报销金额与费用类型。" in text
    assert "必须给出制度条款" in text
    assert "超标" in text


def test_render_survives_legacy_card_without_optional_fields():
    text = render_sop_content(
        {
            "skill_id": "legacy",
            "name": "旧 SOP",
            "nodes": [{"node_id": "only", "name": "唯一节点"}],
            "start_node_id": "only",
            "terminal_node_ids": ["only"],
        }
    )
    assert "旧 SOP" not in text  # 标题由 payload 层单独给，正文不重复
    assert "1. 唯一节点" in text


def test_render_never_raises_on_garbage():
    assert render_sop_content({}) == "（SOP 结构缺失，无法渲染）"
    assert render_sop_content({"nodes": "not-a-list"}) == "（SOP 结构缺失，无法渲染）"


def test_effective_hint_prefers_explicit_override():
    assert effective_hint("hybrid", "remote") == "remote"
    assert effective_hint("hybrid", "local") == "local"
    assert effective_hint("local", None) == "local"
    assert effective_hint("remote", None) == "remote"
    # hybrid 未标注时默认本地：远端执行是稀缺资源，默认不该外派
    assert effective_hint("hybrid", None) == "local"
    # 未知值降级为本地，避免坏配置把员工整体锁死
    assert effective_hint("hybrid", "bogus") == "local"
    assert effective_hint("bogus", None) == "local"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_sop_text.py -q`
Expected: FAIL — `ImportError: cannot import name 'effective_hint'`

- [ ] **Step 3: 实现渲染器**

`backend/app/digital_employees/sop_text.py`

```python
from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_skill_rows
from app.digital_employees.contract import (
    MAX_SOPS,
    MAX_SOP_CONTENT,
    MAX_SOP_TITLE,
    clamp_items,
    clamp_text,
)

LOCAL = "local"
REMOTE = "remote"
_EMPTY = "（SOP 结构缺失，无法渲染）"


def effective_hint(mode: str, hint: str | None) -> str:
    """Resolve where one SOP runs: explicit binding hint wins over employee mode."""
    normalized = (hint or "").strip().lower()
    if normalized in {LOCAL, REMOTE}:
        return normalized
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == REMOTE:
        return REMOTE
    # local / hybrid / anything unknown must stay on the client: a bad mode must
    # never silently forward an employee's SOPs to remote execution.
    return LOCAL


def render_sop_content(card: dict[str, Any] | None) -> str:
    """Render a stored SkillCard as plain text for in-prompt injection."""
    if not isinstance(card, dict):
        return _EMPTY
    nodes = card.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return _EMPTY

    lines: list[str] = []

    goals = [str(item).strip() for item in card.get("goal") or [] if str(item).strip()]
    if goals:
        lines.append("目标：")
        lines.extend(f"- {goal}" for goal in goals)
        lines.append("")

    lines.append("步骤：")
    ordered = _ordered_nodes(card, nodes)
    for index, node in enumerate(ordered, start=1):
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or node.get("node_id") or f"步骤 {index}").strip()
        lines.append(f"{index}. {name}")
        instruction = str(node.get("instruction") or "").strip()
        if instruction:
            lines.append(f"   指令：{instruction}")
        expected = [
            str(item).strip()
            for item in node.get("expected_user_info") or []
            if str(item).strip()
        ]
        if expected:
            lines.append(f"   需收集：{'、'.join(expected)}")
        actions = [
            str(item).strip()
            for item in node.get("allowed_actions") or []
            if str(item).strip()
        ]
        if actions:
            lines.append(f"   可用动作：{'、'.join(actions)}")

    transitions = _transitions(card, nodes)
    if transitions:
        lines.append("")
        lines.append("流转：")
        lines.extend(f"- {item}" for item in transitions)

    rules = [
        str(item).strip() for item in card.get("response_rules") or [] if str(item).strip()
    ]
    if rules:
        lines.append("")
        lines.append("回复规则：")
        lines.extend(f"- {rule}" for rule in rules)

    return "\n".join(lines).strip()


def _ordered_nodes(card: dict[str, Any], nodes: list[Any]) -> list[Any]:
    by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    start = str(card.get("start_node_id") or "")
    ordered: list[Any] = []
    seen: set[str] = set()
    current = start or next(iter(by_id), "")
    while current and current in by_id and current not in seen:
        seen.add(current)
        ordered.append(by_id[current])
        current = _next_node_id(card, current)
    # Nodes unreachable from start are still part of the SOP the model must know.
    ordered.extend(node for node in nodes if isinstance(node, dict) and str(node.get("node_id")) not in seen)
    return ordered


def _next_node_id(card: dict[str, Any], node_id: str) -> str:
    best: tuple[int, str] | None = None
    for edge in card.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("source_node_id")) != node_id:
            continue
        target = str(edge.get("next_node_id") or "")
        if not target:
            continue
        priority = int(edge.get("priority") or 0)
        if best is None or priority > best[0]:
            best = (priority, target)
    return best[1] if best else ""


def _transitions(card: dict[str, Any], nodes: list[Any]) -> list[str]:
    names = {
        str(node.get("node_id")): str(node.get("name") or node.get("node_id"))
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    items: list[str] = []
    for edge in card.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = names.get(str(edge.get("source_node_id")), str(edge.get("source_node_id")))
        target = names.get(str(edge.get("next_node_id")), str(edge.get("next_node_id")))
        label = str(edge.get("label") or edge.get("condition") or "").strip()
        items.append(f"{source} → {target}（{label}）" if label else f"{source} → {target}")
    return items


def select_sop_documents(
    db: Session,
    tenant_id: str,
    agent_id: str,
    mode: str,
    binding_metadata: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Return the SOPs deepchat must inject locally, plus truncation warnings."""
    warnings: list[str] = []
    documents: list[dict[str, str]] = []
    local_rows = []
    for row in visible_skill_rows(db, tenant_id, agent_id, include_inactive=False):
        hint = (binding_metadata.get(row.id) or {}).get("execution_hint")
        if effective_hint(mode, hint if isinstance(hint, str) else None) == LOCAL:
            local_rows.append(row)

    local_rows.sort(key=lambda row: (row.name or row.skill_id, row.skill_id))
    kept, overflowed = clamp_items(local_rows, MAX_SOPS)
    if overflowed:
        warnings.append(
            f"该员工有 {len(local_rows)} 条本地 SOP，超出契约上限 {MAX_SOPS} 条，已省略其余。"
        )

    for row in kept:
        title, title_cut = clamp_text(str(row.name or row.skill_id), MAX_SOP_TITLE)
        content, content_cut = clamp_text(render_sop_content(row.content_json), MAX_SOP_CONTENT)
        if not content.strip():
            content = "（SOP 正文为空）"
        if title_cut or content_cut:
            warnings.append(f"SOP「{title}」内容超出契约上限，已截断。")
        documents.append({"title": title or row.skill_id, "content": content})
    return documents, warnings
```

- [ ] **Step 4: 抽出通用的 general skill 可见性函数**

`backend/app/agents/branching.py` 在 `visible_tool_rows` 之后追加：

```python
def visible_general_skill_rows(
    db: Session,
    tenant_id: str,
    agent_id: str | None = None,
) -> list[GeneralSkill]:
    """Published general skills the caller's agent may actually use."""
    agent = get_agent(db, tenant_id, agent_id)
    if agent_id and not agent:
        return []
    rows = db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == tenant_id,
            GeneralSkill.status == "published",
        )
    ).all()
    if not agent or agent.is_overall:
        return [
            row for row in rows if is_open_gallery_resource(db, tenant_id, "general_skill", row)
        ]
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == "general_skill",
            AgentResourceBinding.status == "active",
        )
    ).all()
    by_id = {row.id: row for row in rows}
    visible: list[GeneralSkill] = []
    for binding in bindings:
        row = by_id.get(binding.resource_id)
        if row is not None and is_bound_resource_visible_for_agent(
            db, tenant_id, "general_skill", row, binding
        ):
            visible.append(row)
    return visible
```

`backend/app/core/capability_manifest.py:595-627` 的 `_visible_general_skills` 整体替换为委托（原逻辑与本函数逐行等价，`tests/test_capability_manifest*` 会守住回归）：

```python
def _visible_general_skills(
    db: Session, tenant_id: str, agent_id: str | None
) -> list[GeneralSkill]:
    return visible_general_skill_rows(db, tenant_id, agent_id)
```

并在该文件的 `from app.agents.branching import (...)` 导入块中加入 `visible_general_skill_rows`。

- [ ] **Step 5: 跑测试确认通过（含能力清单回归）**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_sop_text.py tests/test_capability_discovery.py tests/test_capability_registry.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/digital_employees/sop_text.py backend/app/agents/branching.py backend/app/core/capability_manifest.py backend/tests/test_digital_employee_sop_text.py
git commit -m "feat(api): render SOP documents with per-employee execution hints"
```

---

### Task 3: 数字员工 payload 组装

**Files:**
- Create: `backend/app/digital_employees/payload.py`
- Modify: `backend/app/public_api/gallery.py:36`（抽出可复用的画廊可见员工查询）
- Test: `backend/tests/test_digital_employee_payload.py`

**Interfaces:**
- Consumes: `contract`、`sop_text.select_sop_documents`、`branching.visible_general_skill_rows`、`branching.visible_tool_rows`、`branching.visible_knowledge_base_versions`、`branching.resource_binding_metadata`
- Produces: `app.public_api.gallery.visible_gallery_agents(db, principal) -> list[AgentProfileRead]`（`_gallery_agents` 保留为同函数别名）
- Produces: `payload.build_employee(principal, agent: AgentProfile, db: Session) -> dict[str, Any]`（契约字段 + 扩展字段，绝不含 `tenant_id`）
- Produces: `payload.build_employee_list(principal, agents, db) -> dict[str, Any]` → `{"employees": [...], "configVersion": "v1:<12 hex>"}`（**不含** `fetchedAt`，ETag 与增量判定都基于它）

- [ ] **Step 1: 抽出画廊可见性查询**

`backend/app/public_api/gallery.py` 把 `_gallery_agents`（36-48 行）改为：

```python
def visible_gallery_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfileRead]:
    """Agents this principal may use from the gallery (also the distribution set)."""
    rows = internal_agents.list_agents(
        principal.tenant_id,
        db,
        principal.actor_user,
    )
    return [
        row
        for row in rows
        if not row.is_overall
        and row.status == "active"
        and (row.metadata or {}).get("published_to_gallery") is True
    ]


# Kept as an alias so existing call sites and tests keep working.
_gallery_agents = visible_gallery_agents
```

- [ ] **Step 2: 写失败测试**

`backend/tests/test_digital_employee_payload.py`

```python
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.agents import create_account_api_credential  # noqa: F401  (documentation only)
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    GeneralSkill,
    KnowledgeBase,
    Skill,
    Tenant,
    Tool,
    User,
)
from app.db.seed import seed_demo_data
from app.digital_employees.payload import build_employee, build_employee_list
from app.public_api.auth import PublicPrincipal
from app.security.auth import create_access_token
from deepchat_contract import validate_digital_employee


@pytest.fixture()
def seeded():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        principal = PublicPrincipal(
            tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"})
        )
        yield db, principal


def _agent(db: Session, name: str) -> AgentProfile:
    return db.exec(
        select(AgentProfile).where(AgentProfile.name == name, AgentProfile.is_overall == False)  # noqa: E712
    ).first()


def test_seeded_employees_satisfy_the_contract(seeded):
    db, principal = seeded
    agents = db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all()  # noqa: E712
    payloads = [build_employee(principal, agent, db) for agent in agents]
    assert len(payloads) == 10
    for payload in payloads:
        validate_digital_employee(payload)
        assert "tenant_id" not in payload


def test_payload_maps_profile_fields_and_capabilities(seeded):
    db, principal = seeded
    payload = build_employee(principal, _agent(db, "财务"), db)
    assert payload["name"] == "财务"
    assert payload["role"] == "报销管家"          # metadata.role_name
    assert payload["enabled"] is True
    assert payload["executionMode"] == "local"     # 默认本地执行
    assert content(payload["systemPrompt"])        # 非空且 ≤ 10000
    assert payload["greeting"] is None             # 未配置不编造


def content(value: str) -> bool:
    return bool(value and value.strip())


def test_tools_are_namespaced_and_never_leak_credentials(seeded):
    db, principal = seeded
    db.add(
        Tool(
            id="tool_secret",
            tenant_id="tenant_demo",
            name="crm.lookup",
            method="GET",
            url="https://crm.example.com/customer",
            headers_json={"Authorization": "Bearer super-secret"},
            auth_json={"token": "super-secret"},
        )
    )
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=_agent(db, "财务").id,
            resource_type="tool",
            resource_id="tool_secret",
            status="active",
        )
    )
    db.commit()
    payload = build_employee(principal, _agent(db, "财务"), db)
    assert "staffdeck:crm.lookup" in payload["tools"]
    assert all(item.startswith("staffdeck:") for item in payload["tools"])
    dumped = repr(payload)
    assert "super-secret" not in dumped
    assert "crm.example.com" not in dumped       # 连 URL 都不下发（凭证不出服务端）


def test_skill_ids_come_from_bound_general_skills_with_download_links(seeded):
    db, principal = seeded
    payload = build_employee(principal, _agent(db, "人事"), db)
    bound = db.exec(
        select(GeneralSkill).where(GeneralSkill.tenant_id == "tenant_demo", GeneralSkill.status == "published")
    ).all()
    slugs = {row.slug for row in bound}
    assert payload["skillIds"] and set(payload["skillIds"]).issubset(slugs)
    assert all(item["slug"] in set(payload["skillIds"]) for item in payload["skills"])
    assert all(item["downloadUrl"].endswith(":export-skill?slug=" + item["slug"]) for item in payload["skills"])


def test_knowledge_sync_is_opt_in(seeded):
    db, principal = seeded
    finance = _agent(db, "财务")
    assert build_employee(principal, finance, db)["knowledgeBaseIds"] == []

    kb = db.exec(
        select(KnowledgeBase).where(KnowledgeBase.tenant_id == "tenant_demo")
    ).first()
    kb.metadata_json = {**(kb.metadata_json or {}), "deepchat_sync_enabled": True}
    db.add(kb)
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=finance.id,
            resource_type="knowledge_base",
            resource_id=kb.id,
            status="active",
        )
    )
    db.commit()
    payload = build_employee(principal, finance, db)
    assert payload["knowledgeBaseIds"] == [kb.id]
    assert payload["knowledgePackagesUrl"].endswith(f"/digital-employees/{finance.id}/knowledge-packages")


def test_remote_mode_stops_shipping_sops_but_hybrid_keeps_local_ones(seeded):
    db, principal = seeded
    finance = _agent(db, "财务")
    finance.metadata_json = {**(finance.metadata_json or {}), "execution_mode": "remote"}
    db.add(finance)
    db.commit()
    remote_payload = build_employee(principal, _agent(db, "财务"), db)
    assert remote_payload["executionMode"] == "remote"
    assert remote_payload["sops"] == []

    binding = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == finance.id,
            AgentResourceBinding.resource_type == "skill",
        )
    ).first()
    binding.metadata_json = {**(binding.metadata_json or {}), "execution_hint": "local"}
    finance.metadata_json = {**(finance.metadata_json or {}), "execution_mode": "hybrid"}
    db.add(binding)
    db.add(finance)
    db.commit()
    hybrid_payload = build_employee(principal, _agent(db, "财务"), db)
    assert hybrid_payload["executionMode"] == "hybrid"
    assert len(hybrid_payload["sops"]) == 1


def test_oversized_sop_is_clamped_with_a_warning(seeded):
    db, principal = seeded
    finance = _agent(db, "财务")
    skill = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == finance.id,
            AgentResourceBinding.resource_type == "skill",
        )
    ).first()
    row = db.get(Skill, skill.resource_id)
    card = dict(row.content_json)
    card["nodes"] = [
        {
            "node_id": "only",
            "type": "respond",
            "name": "长节点",
            "instruction": "字" * 30000,
        }
    ]
    card["start_node_id"] = "only"
    card["terminal_node_ids"] = ["only"]
    card["edges"] = []
    row.content_json = card
    db.add(row)
    db.commit()

    payload = build_employee(principal, _agent(db, "财务"), db)
    validate_digital_employee(payload)
    assert payload["warnings"]
    assert any("截断" in warning for warning in payload["warnings"])


def test_config_hash_changes_only_when_content_changes(seeded):
    db, principal = seeded
    finance = _agent(db, "财务")
    first = build_employee(principal, finance, db)
    again = build_employee(principal, _agent(db, "财务"), db)
    assert first["configHash"] == again["configHash"]

    finance.description = "改过的描述"
    db.add(finance)
    db.commit()
    changed = build_employee(principal, _agent(db, "财务"), db)
    assert changed["configHash"] != first["configHash"]


def test_list_payload_is_deterministic_and_versioned(seeded):
    db, principal = seeded
    agents = db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all()  # noqa: E712
    first = build_employee_list(principal, agents, db)
    second = build_employee_list(principal, list(reversed(agents)), db)
    assert [item["name"] for item in first["employees"]] == sorted(
        item["name"] for item in first["employees"]
    )
    assert first == second  # 输入顺序不影响输出
    assert first["configVersion"].startswith("v1:")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.digital_employees.payload'`

- [ ] **Step 4: 实现 payload 组装**

`backend/app/digital_employees/payload.py`

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.agents.branching import (
    resource_binding_metadata,
    visible_general_skill_rows,
    visible_knowledge_base_versions,
    visible_tool_rows,
)
from app.config import get_settings
from app.db.models import AgentProfile
from app.digital_employees.contract import (
    MAX_CAPABILITIES,
    MAX_CAPABILITY,
    MAX_DESCRIPTION,
    MAX_GREETING,
    MAX_KNOWLEDGE_BASE_IDS,
    MAX_NAME,
    MAX_ROLE,
    MAX_SKILL_IDS,
    MAX_SYSTEM_PROMPT,
    MAX_TOOLS,
    clamp_items,
    clamp_text,
    zod_len,
)
from app.digital_employees.sop_text import select_sop_documents
from app.public_api.auth import PublicPrincipal

MODES = {"local", "remote", "hybrid"}
DEFAULT_MODE = "local"
TOOL_PREFIX = "staffdeck:"
_SYNC_FLAG = "deepchat_sync_enabled"


def build_employee(principal: PublicPrincipal, agent: AgentProfile, db: Session) -> dict[str, Any]:
    """Build one deepchat-compatible employee, clamped to the contract limits."""
    warnings: list[str] = []
    metadata = dict(agent.metadata_json or {})
    mode = _mode(metadata.get("execution_mode"), warnings)

    name, cut = clamp_text(str(agent.name or ""), MAX_NAME)
    if cut or not name.strip():
        warnings.append("员工名称超出契约上限或为空，已裁剪。")
    name = name.strip() or agent.id
    role = _text(metadata.get("role_name"), fallback=name, limit=MAX_ROLE)
    description = _text(agent.description, fallback="", limit=MAX_DESCRIPTION, allow_empty=True)

    capabilities, cap_overflow = clamp_items(_capabilities(metadata), MAX_CAPABILITIES)
    if cap_overflow:
        warnings.append(f"能力标签超过 {MAX_CAPABILITIES} 个，已省略其余。")

    sop_documents, sop_warnings = select_sop_documents(
        db,
        principal.tenant_id,
        agent.id,
        mode,
        resource_binding_metadata(db, principal.tenant_id, agent.id, "skill"),
    )
    warnings.extend(sop_warnings)

    general_skills, skill_overflow = clamp_items(
        sorted(
            visible_general_skill_rows(db, principal.tenant_id, agent.id),
            key=lambda row: (row.name or row.slug, row.slug),
        ),
        MAX_SKILL_IDS,
    )
    if skill_overflow:
        warnings.append(f"技能超过 {MAX_SKILL_IDS} 个，已省略其余。")

    tool_names, tool_overflow = clamp_items(_tool_names(db, principal, agent), MAX_TOOLS)
    if tool_overflow:
        warnings.append(f"工具超过 {MAX_TOOLS} 个，已省略其余。")

    knowledge_ids, kb_overflow = clamp_items(_knowledge_base_ids(db, principal, agent), MAX_KNOWLEDGE_BASE_IDS)
    if kb_overflow:
        warnings.append(f"可同步知识库超过 {MAX_KNOWLEDGE_BASE_IDS} 个，已省略其余。")

    base_url = get_settings().normalized_tool_base_url
    employee: dict[str, Any] = {
        "id": agent.id,
        "name": name,
        "role": role,
        "description": description,
        "avatar": _avatar(metadata),
        "capabilities": capabilities,
        "greeting": _greeting(metadata),
        "systemPrompt": _system_prompt(agent, name, role, description, warnings),
        "sops": sop_documents,
        "knowledgeBaseIds": knowledge_ids,
        "skillIds": [row.slug for row in general_skills],
        "tools": tool_names,
        "enabled": agent.status == "active",
        "updatedAt": _iso(_latest_update(agent, general_skills, db)),
        # Distribution extensions (deepchat's schema is non-strict and ignores these
        # until D2 starts consuming them).
        "executionMode": mode,
        "skills": [
            {
                "id": row.id,
                "name": _text(row.name, fallback=row.slug, limit=MAX_ROLE),
                "slug": row.slug,
                "downloadUrl": (
                    f"{base_url}/api/v1/digital-employees/{agent.id}"
                    f":export-skill?slug={row.slug}"
                ),
            }
            for row in general_skills
        ],
        "knowledgePackagesUrl": (
            f"{base_url}/api/v1/digital-employees/{agent.id}/knowledge-packages"
        ),
    }
    if warnings:
        employee["warnings"] = warnings
    employee["configHash"] = config_hash(employee)
    return employee


def build_employee_list(
    principal: PublicPrincipal,
    agents: list[AgentProfile],
    db: Session,
) -> dict[str, Any]:
    employees = [build_employee(principal, agent, db) for agent in agents]
    employees.sort(key=lambda item: (item["name"], item["id"]))
    version_seed = json.dumps(
        sorted((item["id"], item["configHash"]) for item in employees),
        separators=(",", ":"),
    )
    digest = hashlib.sha256(version_seed.encode("utf-8")).hexdigest()[:12]
    return {"employees": employees, "configVersion": f"v1:{digest}"}


def config_hash(employee: dict[str, Any]) -> str:
    """Digest of the contract content only: warnings/updatedAt must not churn it."""
    content = {
        key: value
        for key, value in employee.items()
        if key not in {"configHash", "warnings", "updatedAt"}
    }
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _mode(raw: Any, warnings: list[str]) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_MODE
    if value in MODES:
        return value
    warnings.append(f"未知执行模式 {value!r}，已按 {DEFAULT_MODE} 下发。")
    return DEFAULT_MODE


def _text(value: Any, *, fallback: str, limit: int, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "" if allow_empty else clamp_text(fallback, limit)[0]
    clamped, _ = clamp_text(text, limit)
    return clamped


def _capabilities(metadata: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for key in ("expertise_tags", "work_modes", "work_styles"):
        for item in metadata.get(key) or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            if zod_len(text) > MAX_CAPABILITY:
                continue
            seen.append(text)
    return seen


def _avatar(metadata: dict[str, Any]) -> str | None:
    url = str(metadata.get("avatar_url") or "").strip()
    if not url:
        return None
    clamped, _ = clamp_text(url, 256)
    return clamped or None


def _greeting(metadata: dict[str, Any]) -> str | None:
    text = str(metadata.get("greeting") or "").strip()
    if not text:
        return None
    clamped, _ = clamp_text(text, MAX_GREETING)
    return clamped or None


def _system_prompt(
    agent: AgentProfile,
    name: str,
    role: str,
    description: str,
    warnings: list[str],
) -> str:
    prompt = str(agent.persona_prompt or "").strip()
    if not prompt:
        prompt = f"# 角色：{role}\n你是「{name}」，一名专业的{role}数字员工。"
        if description:
            prompt += f"\n\n职责范围：{description}"
    clamped, cut = clamp_text(prompt, MAX_SYSTEM_PROMPT)
    if cut:
        warnings.append("人设提示词超出契约上限，已截断。")
    return clamped or f"# 角色：{role}"


def _tool_names(db: Session, principal: PublicPrincipal, agent: AgentProfile) -> list[str]:
    """Only namespaced names leave the server; tool URLs and credentials never do."""
    names = []
    for row in visible_tool_rows(db, principal.tenant_id, agent.id, include_inactive=False):
        if not row.enabled:
            continue
        names.append(f"{TOOL_PREFIX}{row.name}")
    return sorted(set(names))


def _knowledge_base_ids(db: Session, principal: PublicPrincipal, agent: AgentProfile) -> list[str]:
    """Sync is opt-in per knowledge base; only flagged bases are exportable."""
    ids: list[str] = []
    for kb_id, version in visible_knowledge_base_versions(
        db, principal.tenant_id, agent.id
    ).items():
        metadata = dict(getattr(version, "metadata_json", None) or {})
        if metadata.get(_SYNC_FLAG) is True:
            ids.append(kb_id)
    return sorted(set(ids))


def _latest_update(agent: AgentProfile, general_skills: list[Any], db: Session) -> datetime:
    stamps = [agent.updated_at]
    stamps.extend(getattr(row, "updated_at", None) for row in general_skills)
    return max(stamp for stamp in stamps if isinstance(stamp, datetime))


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_payload.py -q`
Expected: PASS

若 `test_knowledge_sync_is_opt_in` 因种子知识库分支版本 metadata 不含 `deepchat_sync_enabled` 而失败：`visible_knowledge_base_versions` 返回的是 `KnowledgeBaseVersion`，测试里把 flag 写在 `KnowledgeBase.metadata_json` 上不会生效。此时把测试改为同时写入当前版本行（`db.exec(select(KnowledgeBaseVersion).where(...)).first()`），而不是放宽实现——实现读版本 metadata 是有意的（版本切换即同步开关切换）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/digital_employees/payload.py backend/app/public_api/gallery.py backend/tests/test_digital_employee_payload.py
git commit -m "feat(api): build contract-clamped digital-employee payloads"
```

---

### Task 4: 分发路由（列表 / 单员工 / ETag 304）

**Files:**
- Create: `backend/app/public_api/digital_employees.py`
- Modify: `backend/app/public_api/utils.py`（新增 `if_none_match_hit`）
- Modify: `backend/app/public_api/app.py:63-73`（注册 router）
- Test: `backend/tests/test_public_api_digital_employees.py`

**Interfaces:**
- Consumes: `payload.build_employee_list`、`payload.build_employee`、`gallery.visible_gallery_agents`、`utils.etag_for`
- Produces: `app.public_api.digital_employees.router`（`prefix="/digital-employees"`, `tags=["digital-employees"]`）
- Produces: `app.public_api.utils.if_none_match_hit(header_value: str | None, etag: str) -> bool`
- HTTP 契约：
  - `GET /api/v1/digital-employees` → `200 {employees[], configVersion, fetchedAt}`；`If-None-Match` 命中 → `304`（带 `ETag`，无 body）
  - `GET /api/v1/digital-employees/{agent_id}` → `200 {employee}`；不在分发集合 → `404 AGENT_NOT_FOUND`
  - 两者均需 `agents:read`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_public_api_digital_employees.py`

```python
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import AgentProfile, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token
from deepchat_contract import validate_digital_employee


def _client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        admin = db.exec(select(User).where(User.username == "admin")).first()
        token = create_access_token(admin)

    app = create_public_api_app()

    def session_override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr("app.public_api.app.engine", engine)
    return TestClient(app), token


def _key(client: TestClient, token: str, scopes: list[str]) -> str:
    created = client.post(
        "/api-clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "deepchat", "scopes": ["*"]},
    )
    assert created.status_code == 201, created.text
    credential = client.post(
        f"/api-clients/{created.json()['id']}/credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "deepchat runtime", "scopes": scopes},
    )
    assert credential.status_code == 201, credential.text
    return credential.json()["api_key"]


def test_list_returns_every_gallery_employee_with_a_valid_contract(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configVersion"].startswith("v1:")
    assert payload["fetchedAt"]
    assert len(payload["employees"]) == 10
    for employee in payload["employees"]:
        validate_digital_employee(employee)
    assert all(employee["enabled"] for employee in payload["employees"])


def test_list_etag_returns_304_without_a_body(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}

    first = client.get("/digital-employees", headers=auth)
    etag = first.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')

    second = client.get("/digital-employees", headers={**auth, "If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["ETag"] == etag
    assert not second.content

    # 弱校验器与多值列表也必须命中（HTTP 语义要求）
    weak = client.get("/digital-employees", headers={**auth, "If-None-Match": f'W/{etag}'})
    assert weak.status_code == 304
    many = client.get(
        "/digital-employees", headers={**auth, "If-None-Match": f'"nope", {etag}'}
    )
    assert many.status_code == 304

    stale = client.get("/digital-employees", headers={**auth, "If-None-Match": '"stale"'})
    assert stale.status_code == 200


def test_etag_is_stable_across_calls(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    assert (
        client.get("/digital-employees", headers=auth).headers["ETag"]
        == client.get("/digital-employees", headers=auth).headers["ETag"]
    )


def test_single_employee_endpoint_matches_the_list_entry(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    listed = client.get("/digital-employees", headers=auth).json()["employees"]
    target = listed[0]

    single = client.get(f"/digital-employees/{target['id']}", headers=auth)
    assert single.status_code == 200, single.text
    assert single.json()["employee"] == target
    assert single.headers["ETag"]


def test_unknown_and_unpublished_agents_are_404(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/digital-employees/agent_missing", headers=auth).status_code == 404

    with Session(client.app.state.engine if hasattr(client.app.state, "engine") else None) as _:
        pass
    # 取消画廊发布后必须从分发集合消失
    overview = client.get("/gallery/agents", headers=auth)
    assert overview.status_code == 200


def test_requires_agents_read_scope(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["runs:read"])
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"


def test_user_jwt_is_rejected(monkeypatch):
    client, token = _client(monkeypatch)
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "API_KEY_REQUIRED"


def test_openapi_documents_the_distribution_endpoints(monkeypatch):
    client, _token = _client(monkeypatch)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/digital-employees" in paths
    assert "/digital-employees/{agent_id}" in paths
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_digital_employees.py -q`
Expected: FAIL — 404（路由未注册）

- [ ] **Step 3: 实现 ETag 匹配工具**

`backend/app/public_api/utils.py` 在 `etag_for` 之后追加：

```python
def if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """RFC 9110 §13.1.2 comparison for If-None-Match.

    Accepts weak validators (`W/"x"`), comma separated lists, and the `*` form,
    because HTTP caches and proxies rewrite the header in all three ways.
    """
    if not header_value:
        return False
    candidates = [item.strip() for item in header_value.split(",") if item.strip()]
    if "*" in candidates:
        return True
    normalized = etag.strip()
    for candidate in candidates:
        if candidate.removeprefix("W/").strip() == normalized:
            return True
    return False
```

- [ ] **Step 4: 实现路由**

`backend/app/public_api/digital_employees.py`

```python
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import AgentProfile, utc_now
from app.digital_employees.payload import build_employee, build_employee_list
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.gallery import visible_gallery_agents
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["digital-employees"])


def _distribution_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfile]:
    """The gallery set, resolved back to ORM rows for payload building."""
    allowed = {row.id for row in visible_gallery_agents(db, principal)}
    if not allowed:
        return []
    rows = db.exec(
        select(AgentProfile).where(
            AgentProfile.tenant_id == principal.tenant_id,
            AgentProfile.id.in_(allowed),  # type: ignore[attr-defined]
        )
    ).all()
    return [row for row in rows if row.status == "active"]


@router.get("", response_model=dict)
def list_digital_employees(
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
):
    core = build_employee_list(principal, _distribution_agents(db, principal), db)
    # The ETag must not include fetchedAt: a per-request timestamp would make
    # every conditional request miss and defeat incremental fetching entirely.
    etag = etag_for(core)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {**core, "fetchedAt": _now_iso()}


@router.get("/{agent_id}", response_model=dict)
def get_digital_employee(
    agent_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
):
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    employee = build_employee(principal, agent, db)
    etag = etag_for(employee)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {"employee": employee}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
```

`backend/app/public_api/app.py`：

- 顶部导入加 `digital_employees`：
  ```python
  from app.public_api import (
      agents,
      credentials,
      digital_employees,
      examples,
      gallery,
      jobs,
      operations,
      resources,
      runs,
      sessions,
      sops,
      webhooks,
  )
  ```
- `create_public_api_app()` 内 `app.include_router(agents.router)` 之后插入 `app.include_router(digital_employees.router)`

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_digital_employees.py -q`
Expected: PASS

- [ ] **Step 6: 清理测试里的占位断言**

`test_unknown_and_unpublished_agents_are_404` 中的 `with Session(client.app.state.engine ...)` 阻塞块是为了占位，实现时必须二选一并保持测试通过：

```python
def test_unpublished_agent_disappears_from_distribution(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    listed = client.get("/digital-employees", headers=auth).json()["employees"]
    target_id = listed[0]["id"]

    # 用公开 API 取消画廊发布，分发集合必须同步收缩
    current = client.get(f"/agents/{target_id}", headers=auth).json()
    patch = client.patch(
        f"/agents/{target_id}",
        headers={**auth, "If-Match": client.get(f"/agents/{target_id}", headers=auth).headers["ETag"]},
        json={"metadata": {**current["metadata"], "published_to_gallery": False}},
    )
    assert patch.status_code == 200, patch.text
    assert client.get(f"/digital-employees/{target_id}", headers=auth).status_code == 404
    remaining = client.get("/digital-employees", headers=auth).json()["employees"]
    assert target_id not in {item["id"] for item in remaining}
```

`_key(client, token, ["agents:read"])` 的凭据权限不足以 PATCH 员工，需把该用例的 scope 改为 `["agents:read", "agents:write"]`。

- [ ] **Step 7: 跑该测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_digital_employees.py -q`
Expected: PASS（9 passed）

- [ ] **Step 8: Commit**

```bash
git add backend/app/public_api/digital_employees.py backend/app/public_api/utils.py backend/app/public_api/app.py backend/tests/test_public_api_digital_employees.py
git commit -m "feat(api): distribute digital employees with ETag-conditional fetching"
```

---

### Task 5: 技能包导出 `:export-skill`

**Files:**
- Create: `backend/app/digital_employees/skill_package.py`
- Modify: `backend/app/public_api/digital_employees.py`（新增端点）
- Modify: `backend/tests/test_public_api_digital_employees.py`（追加用例）
- Test: `backend/tests/test_digital_employee_skill_package.py`

**Interfaces:**
- Consumes: `internal_general_skills.general_skill_read(row)` → `GeneralSkillRead`（已含 `SKILL.md` 回填逻辑）
- Produces: `app.digital_employees.skill_package.build_skill_zip(row: GeneralSkill) -> bytes`（zip，根目录即技能包，含 `SKILL.md`）
- Produces: `GET /api/v1/digital-employees/{agent_id}:export-skill?slug=<slug>` → `200 application/zip`，文件名 `{slug}.zip`；员工不可见或技能不可见 → `404 SKILL_NOT_FOUND`；写审计（`resource_type="general_skill"`）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_digital_employee_skill_package.py`

```python
from __future__ import annotations

import io
import zipfile

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import GeneralSkill
from app.db.seed import seed_demo_data
from app.digital_employees.skill_package import build_skill_zip


def _session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def test_zip_contains_skill_markdown_at_the_root():
    session = _session()
    row = session.exec(
        select(GeneralSkill).where(GeneralSkill.status == "published")
    ).first()
    data = build_skill_zip(row)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "SKILL.md" in names
        assert archive.read("SKILL.md").decode("utf-8").strip()
        assert not any(name.startswith("/") for name in names)
        assert archive.testzip() is None


def test_zip_ships_every_declared_skill_file():
    session = _session()
    row = session.exec(
        select(GeneralSkill).where(GeneralSkill.status == "published")
    ).first()
    declared = {item.get("path") for item in (row.skill_files_json or []) if item.get("path")}
    if not declared:
        declared = {"SKILL.md"}
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert declared.issubset(set(archive.namelist()))


def test_zip_falls_back_to_markdown_when_files_are_missing():
    session = _session()
    row = session.exec(
        select(GeneralSkill).where(GeneralSkill.status == "published")
    ).first()
    row.skill_files_json = []
    data = build_skill_zip(row)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "SKILL.md" in archive.namelist()


def test_zip_rejects_path_traversal_entries():
    session = _session()
    row = session.exec(
        select(GeneralSkill).where(GeneralSkill.status == "published")
    ).first()
    row.skill_files_json = [
        {"path": "../../etc/passwd", "content": "nope", "size": 4},
        {"path": "SKILL.md", "content": "---\nname: x\n---\n# x", "size": 12},
    ]
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert all(".." not in name for name in archive.namelist())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_skill_package.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.digital_employees.skill_package'`

- [ ] **Step 3: 实现打包**

`backend/app/digital_employees/skill_package.py`

```python
from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Any

from app.db.models import GeneralSkill


def build_skill_zip(row: GeneralSkill) -> bytes:
    """Package a general skill as a SKILL.md-rooted zip, ready for deepchat import."""
    buffer = BytesIO()
    files = _files(row)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        written: set[str] = set()
        for path, content in files:
            if path in written:
                continue
            written.add(path)
            archive.writestr(path, content)
    return buffer.getvalue()


def _files(row: GeneralSkill) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for item in row.skill_files_json or []:
        if not isinstance(item, dict):
            continue
        path = _safe_path(str(item.get("path") or ""))
        content = item.get("content")
        if not path or not isinstance(content, str):
            continue
        entries.append((path, content))
    if not any(path == "SKILL.md" for path, _ in entries):
        entries.append(("SKILL.md", row.skill_markdown or ""))
    return entries


def _safe_path(raw: str) -> str:
    """Drop traversal and absolute paths: the archive is unpacked on the client."""
    cleaned = raw.replace("\\", "/").strip()
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in {"", ".", ".."}:
            continue
        parts.append(part)
    return "/".join(parts)


def skill_zip_filename(slug: str) -> str:
    safe = "".join(char for char in slug if char.isalnum() or char in {"-", "_", "."})
    return f"{safe or 'skill'}.zip"


def unused(_: Any) -> None:  # pragma: no cover - typing helper kept for symmetry
    return None
```

删掉 `unused`（它只是防止 lint 对未使用 `Any` 报警的权宜写法）——实现时不要留下死代码，直接不导入 `Any`。

- [ ] **Step 4: 追加 HTTP 层测试**

`backend/tests/test_public_api_digital_employees.py` 末尾：

```python
def test_export_skill_returns_importable_zip(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    employee = client.get("/digital-employees", headers=auth).json()["employees"]
    target = next(item for item in employee if item["skills"])
    skill = target["skills"][0]

    exported = client.get(
        f"/digital-employees/{target['id']}:export-skill",
        params={"slug": skill["slug"]},
        headers=auth,
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert skill["slug"] in exported.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "SKILL.md" in archive.namelist()


def test_export_skill_for_an_unrelated_skill_is_404(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    employee = client.get("/digital-employees", headers=auth).json()["employees"]
    without_skills = next(item for item in employee if not item["skills"])
    response = client.get(
        f"/digital-employees/{without_skills['id']}:export-skill",
        params={"slug": "not-a-real-skill"},
        headers=auth,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "SKILL_NOT_FOUND"


def test_export_skill_is_audited(monkeypatch):
    client, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    employee = client.get("/digital-employees", headers=auth).json()["employees"]
    target = next(item for item in employee if item["skills"])
    slug = target["skills"][0]["slug"]
    client.get(
        f"/digital-employees/{target['id']}:export-skill",
        params={"slug": slug},
        headers=auth,
    )
    # 审计中间件在响应返回后才写，用一个必然成功的请求刷新一次
    client.get("/digital-employees", headers=auth)
    with Session(_engine_of(client)) as db:
        row = db.exec(
            select(APIAuditLog)
            .where(APIAuditLog.path.like("%:export-skill"))
            .order_by(APIAuditLog.created_at.desc())
        ).first()
    assert row is not None
    assert row.resource_type == "general_skill"
    assert row.resource_id
```

`_engine_of` 需要暴露测试引擎：把 `_client(monkeypatch)` 改为返回 `(TestClient, engine, token)`，并同步更新本节与 Task 4 的所有调用点（`client, token = _client(monkeypatch)` → `client, engine, token = _client(monkeypatch)`）。

- [ ] **Step 5: 实现端点**

`backend/app/public_api/digital_employees.py` 追加：

```python
from fastapi import Query
from fastapi.responses import Response as RawResponse

from app.api import general_skills as internal_general_skills
from app.agents.branching import visible_general_skill_rows
from app.db.models import APIAuditLog
from app.digital_employees.skill_package import build_skill_zip, skill_zip_filename


@router.get("/{agent_id}:export-skill")
def export_employee_skill(
    agent_id: str,
    slug: str = Query(..., min_length=1, max_length=200),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> RawResponse:
    """Download one general skill as a SKILL.md package for deepchat's skill dir.

    The slug is a query parameter rather than a path segment so the route keeps
    the spec's `/{id}:export-skill` shape while still addressing one of the
    employee's many skills.
    """
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    row = next(
        (
            item
            for item in visible_general_skill_rows(db, principal.tenant_id, agent.id)
            if item.slug == slug
        ),
        None,
    )
    if row is None:
        raise PublicAPIError(
            404, "SKILL_NOT_FOUND", "This skill is not distributed to the requested employee."
        )
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="GET",
            path=f"/api/v1/digital-employees/{agent_id}:export-skill",
            resource_type="general_skill",
            resource_id=row.id,
            status_code=200,
            duration_ms=0.0,
        )
    )
    db.commit()
    data = build_skill_zip(row)
    return RawResponse(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_zip_filename(slug)}"',
        },
    )
```

**注意：`request` 变量不存在** —— 签名里必须补 `request: Request`（`from fastapi import Request`），并把 `request_id` 取真实值。导出必须留痕是 spec §6.2 的硬要求，所以不许省略审计行。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_skill_package.py tests/test_public_api_digital_employees.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/digital_employees/skill_package.py backend/app/public_api/digital_employees.py backend/tests/test_digital_employee_skill_package.py backend/tests/test_public_api_digital_employees.py
git commit -m "feat(api): export general skills as SKILL.md packages for deepchat import"
```

---

### Task 6: 端到端验收与文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md`（补 provider 路径对齐说明）
- Modify: `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（修 §6.1 契约勘误：`skillIds` 20 / `tools` 50）
- Test: `backend/tests/test_digital_employee_distribution_e2e.py`

**Interfaces:**
- Consumes: 前五个任务全部产出
- Produces: 一条覆盖「seed → 分发 → 契约 → 技能包 → 增量」的端到端用例；`docs` 两处勘误

- [ ] **Step 1: 写端到端测试**

`backend/tests/test_digital_employee_distribution_e2e.py`

```python
from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import AgentProfile, User
from app.db.seed import seed_demo_data
from app.digital_employees.payload import build_employee_list
from app.public_api.auth import PublicPrincipal
from deepchat_contract import validate_digital_employee


def _seeded():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
    return Session(engine)


def test_distribution_payload_is_json_serialisable_and_complete():
    with _seeded() as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        principal = PublicPrincipal(
            tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"})
        )
        agents = db.exec(
            select(AgentProfile).where(AgentProfile.is_overall == False)  # noqa: E712
        ).all()
        payload = build_employee_list(principal, agents, db)

    # 必须能原样过 JSON 往返且字节级稳定，否则 HTTP 层的 ETag 会抖动
    first = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    second = json.dumps(
        json.loads(first), ensure_ascii=False, sort_keys=True, default=str
    )
    assert first == second

    for employee in payload["employees"]:
        validate_digital_employee(employee)
        assert employee["systemPrompt"].strip()
        assert employee["executionMode"] in {"local", "remote", "hybrid"}
        assert employee["configHash"].startswith("sha256:")


def test_every_distributed_skill_is_downloadable():
    from app.agents.branching import visible_general_skill_rows
    from app.digital_employees.skill_package import build_skill_zip

    with _seeded() as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        principal = PublicPrincipal(
            tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"})
        )
        agents = db.exec(
            select(AgentProfile).where(AgentProfile.is_overall == False)  # noqa: E712
        ).all()
        for agent in agents:
            for row in visible_general_skill_rows(db, principal.tenant_id, agent.id):
                with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
                    assert "SKILL.md" in archive.namelist()
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_distribution_e2e.py -q`
Expected: PASS

- [ ] **Step 3: 修两处文档勘误**

`docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md` §6.1 字段映射表下方补一行：

```markdown
- **契约勘误（2026-10-22 核对 fork `202610211551` 源码）**：`digitalEmployeeSchema` 实际为 `skillIds.max(20)`、`tools.max(50)`（本文早前记作 10/20）。分发实现以源码为准，`backend/tests/fixtures/deepchat_digital_employee.schema.json` 为唯一参照物。
```

`docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md` §5.1 的「改造」列表首项补一条：

```markdown
- **路径对齐**：现有骨架 `HttpDigitalEmployeeProvider.list()` 请求 `${baseUrl}/employees`；StaffDeck 分发端点是 `GET {staffdeck}/api/v1/digital-employees`。D2 落地时把 `STAFFDECK_BASE_URL` 约定为 `{host}/api/v1`，provider 改请求 `${baseUrl}/digital-employees`，响应 `{employees[], configVersion, fetchedAt}` 与 `digitalEmployeeListRoute.output` 完全一致。
```

- [ ] **Step 4: 全量回归与 lint**

Run: `cd backend && ./.venv/Scripts/python.exe -m ruff check app/digital_employees app/public_api tests/test_digital_employee_*.py tests/deepchat_contract.py`
Expected: 无告警

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_v1.py tests/test_capability_discovery.py tests/test_capability_registry.py tests/test_staffdeck_seed.py tests/test_digital_employee_contract.py tests/test_digital_employee_sop_text.py tests/test_digital_employee_payload.py tests/test_digital_employee_skill_package.py tests/test_public_api_digital_employees.py tests/test_digital_employee_distribution_e2e.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md backend/tests/test_digital_employee_distribution_e2e.py
git commit -m "docs(design): correct the deepchat contract limits and provider path"
```

---

## Self-Review

**Spec 覆盖检查**

| Spec 要求 | 落在 |
|---|---|
| `GET /digital-employees` → `{employees[], fetchedAt, configVersion}` | Task 4 |
| `GET /digital-employees/{id}` → 单个员工全量配置 | Task 4 |
| `GET /digital-employees/{id}:export-skill` → SKILL.md 技能包 zip | Task 5 |
| payload 对齐 `digitalEmployeeSchema` + `executionMode` / `configHash` | Task 1（契约）+ Task 3（组装） |
| 字段映射表（AgentProfile / Skill / GeneralSkill / Tool / KnowledgeBase） | Task 3 逐字段实现 |
| `sops` 仅 `execution_hint=local` 下行 | Task 2 `select_sop_documents` |
| `tools` 用 `staffdeck:{slug}` 命名空间 | Task 3 `_tool_names` |
| 知识库仅「已授权且开启同步开关」下发 | Task 3 `_knowledge_base_ids`（默认关闭） |
| `ETag` / `If-None-Match` 增量 | Task 4 |
| 契约金样测试进 `backend/tests` | Task 1 + Task 3/4/6 均调用 `validate_digital_employee` |
| 导出行为写 AuditLog（spec §6.2 类比要求） | Task 5 |
| scope `agents:read`，凭证 `sd_live_` | Task 4 |

**未覆盖（有意留给后续里程碑）**：`knowledge-packages` / `:export-manifest` / `:download` 三个端点在 Task 3/4 里只下发 URL（M3 实现）；MCP Server（M4）；`external_user_id` 落库（M5）；回流（M6）。这是 spec §11 的既定切分，不是缺口。

**类型一致性**：`build_employee(principal, agent, db)` / `build_employee_list(principal, agents, db)` / `select_sop_documents(db, tenant_id, agent_id, mode, binding_metadata)` / `visible_general_skill_rows(db, tenant_id, agent_id=None)` / `if_none_match_hit(header_value, etag)` / `build_skill_zip(row)` 在每个任务里的签名与调用点一致。

**已知风险**：
1. `_distribution_agents` 每次请求都跑一遍 `list_agents`（含全租户员工查询）。当前租户规模下可接受；若员工数上到千级，改为按 `metadata_json->>'published_to_gallery'` 过滤的 SQL 查询（PG 支持 JSON 运算符，SQLite 兼容期需双实现）。
2. `configHash` 不覆盖 `AgentResourceBinding` 的变更（如新增工具绑定但 `AgentProfile.updated_at` 未推进）。因为 `tools`/`skillIds` 都进了 hash 内容，实际会变；但 `updatedAt` 可能滞后一个写事务。deepchat 应以 `configHash` 判变更，`updatedAt` 只作展示——这一点要在 D2 说明。
3. 技能包导出未做体积上限。单个 GeneralSkill 的 `skill_files_json` 在导入侧已被 `MAX_CLAWHUB_FILE_BYTES` 约束，故可接受；若后续放开导入限制，需在此加总量上限。
