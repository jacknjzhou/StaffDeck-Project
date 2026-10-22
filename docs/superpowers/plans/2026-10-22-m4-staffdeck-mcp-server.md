# StaffDeck M4 MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/api/v1/mcp` 交付一个 MCP streamable HTTP 服务端，把员工绑定的 Tool 以 `staffdeck:{slug}` 命名空间暴露给 deepchat，工具凭证全程留在服务端；同时暴露知识检索工具。deepchat 侧只需把该端点注册为 `type: 'http'` 的 MCP server 即可调用。

**Architecture:** 手写 JSON-RPC 2.0 分发器（**不新增依赖**——`uv.lock` 里没有官方 `mcp` SDK，且现有 `app/tools/mcp_client.py` 已经是手写客户端，服务端与之对称）。新增 `app/mcp_server/` 纯函数层（协议编解码 → 工具目录装配 → 调用适配），`app/public_api/mcp.py` 只做鉴权、HTTP 语义与审计。工具执行**完全复用** `ToolExecutor.execute()`，因此凭证注入、超时策略、可见性校验、MCP/a2a 分支、detached 执行全部零改动继承。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel/SQLAlchemy 2 + pydantic 2 + httpx（已有）+ pytest

**Spec:** `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（§6.3 通道③、§8 鉴权、§11 里程碑 M4）

## Global Constraints

- 遵循 AGENTS.md：Conventional Commits（`feat(mcp): ...`）；Python ruff 100 字符行宽；后端测试目录 `backend/tests`；每个任务单独 commit，不 `--no-verify`
- **依赖不得新增**：不引入官方 `mcp` SDK。JSON-RPC 编排用标准库 `json` + FastAPI 既有能力
- 本机 Python：`backend/.venv/Scripts/python.exe`（Windows）；`uv sync --frozen --extra dev` 是唯一可信安装方式
- **零数据库迁移**：M4 不新增表、不改列
- **凭证不出服务端**：`tools/list` 返回的 `inputSchema` 只能来自 `Tool.input_schema`；`Tool.url`、`headers_json`、`auth_json`、`config_json`、`mcp_server_id` 一律不得出现在任何响应里
- **协议版本**：接受 `2024-11-05` 与 `2025-03-26`，其余版本回协商值而非报错（deepchat 客户端发的是 `2024-11-05`，见 `app/tools/mcp_client.py:_initialize_params`）
- **鉴权**：`Authorization: Bearer sd_live_*`，scope `mcp:invoke`（spec §8 明确列出，当前代码里尚未被任何端点使用）。**不接受用户 JWT**
- **路由前缀**：`/api/v1/mcp`（public_api 子应用内 prefix `/mcp`）。该端点必须注册在 `digital_employees.router` **之前**——虽然路径不冲突，但与 M3 保持同一约定便于审阅
- 所有工具调用写 `APIAuditLog`（`resource_type="tool"`）
- 会话：实现 `Mcp-Session-Id`（initialize 时下发，后续请求校验存在性即可），不落库——内存字典 + TTL，多实例部署时降级为无状态（见「已知风险」）

### MCP 协议范围（本期实现）

| 方法 | 方向 | 说明 |
|---|---|---|
| `initialize` | 请求/响应 | 返回 `protocolVersion` / `capabilities{tools:{listChanged:false}}` / `serverInfo`，并下发 `Mcp-Session-Id` 响应头 |
| `notifications/initialized` | 通知 | 202 空体 |
| `ping` | 请求/响应 | `{}` |
| `tools/list` | 请求/响应 | 返回该员工可见的工具集，`name` 为 `staffdeck:{slug}`；支持 `cursor` 分页（默认一次全给） |
| `tools/call` | 请求/响应 | 复用 `ToolExecutor.execute()`；`isError` 由 `ToolResult.success` 决定 |
| 其余方法 | 请求/响应 | JSON-RPC `-32601 Method not found` |

传输：**streamable HTTP**。`POST` 收 JSON-RPC，响应默认 `application/json`；仅当 `Accept` 明确要求且仅要 `text/event-stream` 时回 SSE（单帧 `event: message` + `data: {...}` 后结束）。`GET` 返回 405（不提供服务器主动推送）。

### 工具命名与寻址规则

- 命名空间前缀 `staffdeck:`，与 M2 下发的 `tools[]` 字符串**逐字对应**（`app/digital_employees/payload.py:_tool_names` 用同一个 `TOOL_PREFIX`）
- `tools/call` 的 `name` 必须是 `staffdeck:{slug}`；去掉前缀后作为 `Tool.name` 精确匹配
- 员工上下文来源：`tools/call` 的参数里带 `_meta.agentId`（MCP 规范的 `_meta` 字段），未带则退回「该 credential 绑定的 agent」（`principal.agent_id`）；两者都无则拒绝，**不猜**

### 知识检索工具

固定暴露一个虚拟工具 `staffdeck:knowledge_search`（不占 `Tool` 表），`inputSchema` 内联声明 `{agentId?: string, knowledgeBaseIds?: string[], query: string, maxChunks?: int}`，实现委托 `app.api.knowledge.search_knowledge`（与 `app/public_api/resources.py:114` 同一路径），返回带 `citations` 的结果。仅当该员工有已开启同步的知识库时才出现在 `tools/list` 里。

---

### Task 1: JSON-RPC 编解码与错误映射

**Files:**
- Create: `backend/app/mcp_server/__init__.py`
- Create: `backend/app/mcp_server/protocol.py`
- Test: `backend/tests/test_mcp_protocol.py`

**Interfaces:**
- Produces:
  - `JSONRPC_VERSION = "2.0"`
  - `class RpcError(Exception)`: `__init__(code: int, message: str, data: Any = None)`
  - `PARSE_ERROR = -32700`、`INVALID_REQUEST = -32600`、`METHOD_NOT_FOUND = -32601`、`INVALID_PARAMS = -32602`、`INTERNAL_ERROR = -32603`
  - `parse_request(payload: Any) -> tuple[Any, str, dict[str, Any], bool]` → `(id, method, params, is_notification)`；非法输入抛 `RpcError(INVALID_REQUEST / PARSE_ERROR)`
  - `success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]`
  - `error_response(request_id: Any, error: RpcError) -> dict[str, Any]`
  - `negotiate_protocol_version(requested: str | None) -> str`
  - `SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-03-26", "2024-11-05")`
  - `DEFAULT_PROTOCOL_VERSION = "2024-11-05"`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mcp_protocol.py
from __future__ import annotations

import pytest

from app.mcp_server.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    RpcError,
    error_response,
    negotiate_protocol_version,
    parse_request,
    success_response,
)


def test_parse_request_reads_id_method_and_params():
    request_id, method, params, is_notification = parse_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {"cursor": "a"}}
    )
    assert (request_id, method, params, is_notification) == (7, "tools/list", {"cursor": "a"}, False)


def test_parse_request_treats_missing_id_as_notification():
    request_id, method, params, is_notification = parse_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert request_id is None
    assert method == "notifications/initialized"
    assert params == {}
    assert is_notification is True


def test_parse_request_rejects_bad_envelopes():
    with pytest.raises(RpcError) as bad_version:
        parse_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})
    assert bad_version.value.code == INVALID_REQUEST

    with pytest.raises(RpcError):
        parse_request({"jsonrpc": "2.0", "id": 1})
    with pytest.raises(RpcError):
        parse_request([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])


def test_error_response_matches_the_jsonrpc_shape():
    body = error_response(3, RpcError(METHOD_NOT_FOUND, "Method not found: ./x"))
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 3
    assert body["error"]["code"] == METHOD_NOT_FOUND
    assert body["error"]["message"] == "Method not found: ./x"
    assert "result" not in body


def test_success_response_keeps_the_request_id():
    assert success_response(None, {}) == {"jsonrpc": "2.0", "id": None, "result": {}}


def test_negotiate_protocol_version_falls_back_instead_of_failing():
    assert negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"
    # 未知版本不应让客户端连不上：回我方默认值，由客户端决定是否继续
    assert negotiate_protocol_version("1999-01-01") == DEFAULT_PROTOCOL_VERSION
    assert negotiate_protocol_version(None) == DEFAULT_PROTOCOL_VERSION
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mcp_server'`

- [ ] **Step 3: 实现协议层**

```python
# backend/app/mcp_server/protocol.py
from __future__ import annotations

from typing import Any

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


class RpcError(Exception):
    """A JSON-RPC level failure, carried out to the transport unchanged."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def parse_request(payload: Any) -> tuple[Any, str, dict[str, Any], bool]:
    """Validate a JSON-RPC envelope.

    Returns (id, method, params, is_notification). A missing id means the client
    is notifying and no response may be sent, per JSON-RPC 2.0 §4.1.
    """
    if not isinstance(payload, dict):
        raise RpcError(INVALID_REQUEST, "A JSON-RPC request must be an object.")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcError(INVALID_REQUEST, 'jsonrpc must be "2.0".')
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(INVALID_REQUEST, "method must be a non-empty string.")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be an object.")
    request_id = payload.get("id")
    return request_id, method, params, "id" not in payload


def success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, error: RpcError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
    if error.data is not None:
        body["error"]["data"] = error.data
    return body


def negotiate_protocol_version(requested: str | None) -> str:
    """Echo a supported version; fall back rather than refuse the handshake."""
    if requested and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_protocol.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server/__init__.py backend/app/mcp_server/protocol.py backend/tests/test_mcp_protocol.py
git commit -m "feat(mcp): add a dependency-free JSON-RPC envelope layer"
```

---

### Task 2: 工具目录装配（含凭证脱敏）

**Files:**
- Create: `backend/app/mcp_server/catalog.py`
- Test: `backend/tests/test_mcp_catalog.py`

**Interfaces:**
- Consumes: `app.agents.branching.visible_tool_rows`、`app.agents.branching.visible_knowledge_base_versions`、`app.digital_employees.payload.TOOL_PREFIX`
- Produces:
  - `KNOWLEDGE_SEARCH_TOOL = "staffdeck:knowledge_search"`
  - `build_tool_catalog(db, tenant_id, agent_id) -> list[dict[str, Any]]`：每个元素形如 `{"name": "staffdeck:xxx", "description": str, "inputSchema": dict}`
  - `resolve_tool_name(catalog: list[dict], name: str) -> str`：校验命名空间，返回去前缀的 `Tool.name`；不合法抛 `RpcError(INVALID_PARAMS)`
  - `describe_knowledge_search() -> dict[str, Any]`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mcp_catalog.py
from __future__ import annotations

import json

import pytest
from app.mcp_server.catalog import (
    KNOWLEDGE_SEARCH_TOOL,
    build_tool_catalog,
    resolve_tool_name,
)
from app.mcp_server.protocol import INVALID_PARAMS, RpcError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import seed_demo_data


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        yield db


def _tenant(db) -> str:
    return db.exec(select(User).where(User.username == "admin")).first().tenant_id


def test_catalog_only_contains_namespaced_tools(session):
    catalog = build_tool_catalog(session, _tenant(session), "agent_f2828efc2a2a476d")
    assert catalog, "the finance employee ships with tools in the seed data"
    assert all(item["name"].startswith("staffdeck:") for item in catalog)


def test_catalog_never_leaks_credentials(session):
    catalog = build_tool_catalog(session, _tenant(session), "agent_f2828efc2a2a476d")
    blob = json.dumps(catalog, ensure_ascii=False).lower()
    for forbidden in ("headers_json", "auth_json", "config_json", "bearer ", "http://localhost"):
        assert forbidden not in blob
    for item in catalog:
        # Only the three protocol keys may appear on a tool descriptor.
        assert set(item) <= {"name", "description", "inputSchema"}


def test_catalog_names_round_trip_through_the_namespace(session):
    catalog = build_tool_catalog(session, _tenant(session), "agent_f2828efc2a2a476d")
    bare = resolve_tool_name(catalog, catalog[0]["name"])
    assert not bare.startswith("staffdeck:")
    with pytest.raises(RpcError) as bad_prefix:
        resolve_tool_name(catalog, bare)
    assert bad_prefix.value.code == INVALID_PARAMS
    with pytest.raises(RpcError):
        resolve_tool_name(catalog, "staffdeck:not_in_the_catalog")


def test_knowledge_search_appears_only_with_a_synced_base(session):
    from app.agents.branching import visible_knowledge_base_versions
    from app.db.models import AgentProfile

    tenant_id = _tenant(session)
    agent_id = "agent_f2828efc2a2a476d"
    assert KNOWLEDGE_SEARCH_TOOL not in {i["name"] for i in build_tool_catalog(session, tenant_id, agent_id)}

    versions = visible_knowledge_base_versions(session, tenant_id, agent_id)
    assert versions, "the seed data must attach a knowledge base to the finance employee"
    _, version = next(iter(versions.items()))
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
    session.add(version)
    session.commit()

    names = {i["name"] for i in build_tool_catalog(session, tenant_id, agent_id)}
    assert KNOWLEDGE_SEARCH_TOOL in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mcp_server.catalog'`

- [ ] **Step 3: 实现目录装配**

```python
# backend/app/mcp_server/catalog.py
from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_knowledge_base_versions, visible_tool_rows
from app.digital_employees.payload import TOOL_PREFIX
from app.mcp_server.protocol import INVALID_PARAMS, RpcError

KNOWLEDGE_SEARCH_TOOL = f"{TOOL_PREFIX}knowledge_search"
_KNOWLEDGE_SYNC_FLAG = "deepchat_sync_enabled"


def describe_knowledge_search() -> dict[str, Any]:
    return {
        "name": KNOWLEDGE_SEARCH_TOOL,
        "description": "在数字员工已同步的知识库中做带引用的检索，返回命中的片段与 citations。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": "自然语言问题"},
                "knowledgeBaseIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "留空则检索该员工全部已同步的知识库",
                },
                "maxChunks": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
        },
    }


def _has_synced_knowledge(db: Session, tenant_id: str, agent_id: str) -> bool:
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    return any(
        (version.metadata_json or {}).get(_KNOWLEDGE_SYNC_FLAG) is True
        for version in versions.values()
    )


def build_tool_catalog(db: Session, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
    """The MCP-visible tool set for one employee.

    Only the three protocol fields are emitted: Tool carries the URL and the
    credentials, and none of that may leave the server.
    """
    catalog: list[dict[str, Any]] = []
    for row in visible_tool_rows(db, tenant_id, agent_id, include_inactive=False):
        if not row.enabled:
            continue
        catalog.append(
            {
                "name": f"{TOOL_PREFIX}{row.name}",
                "description": row.description or row.display_name or row.name,
                "inputSchema": row.input_schema or {"type": "object", "properties": {}},
            }
        )
    if _has_synced_knowledge(db, tenant_id, agent_id):
        catalog.append(describe_knowledge_search())
    return sorted(catalog, key=lambda item: item["name"])


def resolve_tool_name(catalog: list[dict[str, Any]], name: str) -> str:
    """Strip the namespace and prove the tool is actually exposed to this agent."""
    if not name.startswith(TOOL_PREFIX):
        raise RpcError(INVALID_PARAMS, f"Tool names must start with {TOOL_PREFIX!r}.")
    bare = name[len(TOOL_PREFIX) :]
    if not any(item["name"] == name for item in catalog):
        raise RpcError(INVALID_PARAMS, "This tool is not available to the requesting employee.")
    return bare
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_catalog.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server/catalog.py backend/tests/test_mcp_catalog.py
git commit -m "feat(mcp): assemble an employee-scoped tool catalog without credentials"
```

---

### Task 3: `tools/call` 适配器（复用 ToolExecutor）

**Files:**
- Create: `backend/app/mcp_server/invoker.py`
- Test: `backend/tests/test_mcp_invoker.py`

**Interfaces:**
- Consumes: `app.tools.tool_executor.ToolExecutor`、`app.tools.tool_schema.ToolCall`、`app.knowledge.service.KnowledgeService`、`app.agents.branching.visible_knowledge_base_version_ids`
- Produces:
  - `call_tool(db, tenant_id, agent_id, name, arguments) -> dict[str, Any]`：返回 MCP 的 `CallToolResult` 形状 `{"content": [{"type": "text", "text": str}], "isError": bool}`
  - `extract_agent_id(params: dict, principal_agent_id: str | None) -> str | None`

> **不要复用 `app.api.knowledge.search_knowledge`**。它的签名是 `(request, db, current_user: User = Depends(get_current_user))`，第一行就 `require_agent_scope_viewer(...)`——那是 FastAPI 的**用户 JWT** 鉴权，MCP 端点的调用方只有一个 API credential，没有 `User`。硬传 `None` 会在 `require_agent_scope_viewer` 里炸。正确做法是绕过该包装函数、直接用内层 `KnowledgeService(db).search(...)`，并由我们自己保证员工可见性（`visible_knowledge_base_version_ids`）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mcp_invoker.py
from __future__ import annotations

import json

import pytest
from app.mcp_server.catalog import KNOWLEDGE_SEARCH_TOOL
from app.mcp_server.invoker import call_tool, extract_agent_id
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import seed_demo_data


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        yield db


def _tenant(db) -> str:
    return db.exec(select(User).where(User.username == "admin")).first().tenant_id


def test_extract_agent_id_prefers_meta_then_the_key_binding():
    assert extract_agent_id({"name": "x", "_meta": {"agentId": "a1"}}, "a2") == "a1"
    assert extract_agent_id({"name": "x"}, "a2") == "a2"
    assert extract_agent_id({"name": "x"}, None) is None


def test_unknown_employee_is_refused_before_any_http_call(session):
    result = call_tool(
        session, _tenant(session), "agent_does_not_exist", "staffdeck:expense.submit", {}
    )
    assert result["isError"] is True
    assert "unknown" in result["content"][0]["text"].lower()


def test_tool_outside_the_employee_scope_is_refused(session):
    result = call_tool(session, _tenant(session), "agent_preset_sales_001", "staffdeck:expense.submit", {})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["code"] == "TOOL_NOT_FOUND"


def test_knowledge_search_returns_citations(session):
    result = call_tool(
        session,
        _tenant(session),
        "agent_f2828efc2a2a476d",
        KNOWLEDGE_SEARCH_TOOL,
        {"query": "报销需要哪些材料"},
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert "citations" in payload
```

> 种子事实（已核实）：财务员工 `agent_f2828efc2a2a476d` 的可见工具是 `expense.quota_query` / `expense.submit` / `invoice.verify`；销售员工 `agent_preset_sales_001` 的可见工具为空。测试按这两条事实写，不要用 `order.refund`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_invoker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mcp_server.invoker'`

- [ ] **Step 3: 实现调用适配**

```python
# backend/app/mcp_server/invoker.py
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_knowledge_base_version_ids
from app.db.models import AgentProfile
from app.knowledge.schema import KnowledgeSearchRequest
from app.knowledge.service import KnowledgeService
from app.mcp_server.catalog import KNOWLEDGE_SEARCH_TOOL, build_tool_catalog
from app.mcp_server.protocol import INVALID_PARAMS, RpcError
from app.digital_employees.payload import TOOL_PREFIX
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall


def extract_agent_id(params: dict[str, Any], principal_agent_id: str | None) -> str | None:
    """Resolve the employee context for a tools/call.

    The MCP ``_meta`` channel is the primary source; a credential bound to one
    employee supplies a fallback. Nothing is guessed beyond that.
    """
    meta = params.get("_meta")
    if isinstance(meta, dict):
        candidate = meta.get("agentId")
        if isinstance(candidate, str) and candidate:
            return candidate
    return principal_agent_id


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    body = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def _knowledge_search(
    db: Session, tenant_id: str, agent_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Search the employee's synced knowledge without going through the JWT layer.

    ``app.api.knowledge.search_knowledge`` is a FastAPI endpoint whose first act
    is ``require_agent_scope_viewer(current_user)``; an MCP caller has a
    credential, not a ``User``, so that wrapper cannot be reused. Visibility is
    enforced here instead, through the same branching helper the wrapper uses.
    """
    allowed = list(visible_knowledge_base_version_ids(db, tenant_id, agent_id))
    if not allowed:
        return _text_result({"citations": [], "chunks": [], "query": arguments.get("query", "")})

    requested_ids = [str(item) for item in (arguments.get("knowledgeBaseIds") or [])]
    if requested_ids:
        # Narrow within the visible set only; a caller cannot widen its own scope.
        from sqlmodel import select

        from app.db.models import KnowledgeBaseVersion

        kept = db.exec(
            select(KnowledgeBaseVersion.id).where(
                KnowledgeBaseVersion.tenant_id == tenant_id,
                KnowledgeBaseVersion.knowledge_base_id.in_(requested_ids),  # type: ignore[attr-defined]
            )
        ).all()
        allowed = [version_id for version_id in allowed if version_id in set(kept)]
        if not allowed:
            return _text_result(
                {"citations": [], "chunks": [], "query": arguments.get("query", "")}
            )

    request = KnowledgeSearchRequest(
        tenant_id=tenant_id,
        agent_id=agent_id,
        query=str(arguments.get("query") or ""),
        knowledge_base_version_ids=allowed,
        max_chunks=int(arguments.get("maxChunks") or 8),
    )
    result = KnowledgeService(db).search(request, None)
    dumped = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    dumped["citations"] = dumped.get("okf_citations") or dumped.get("evidence_pack") or []
    return _text_result(dumped)


def call_tool(
    db: Session,
    tenant_id: str,
    agent_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        return _text_result(
            {"code": "AGENT_NOT_FOUND", "message": "unknown digital employee"}, is_error=True
        )

    catalog = build_tool_catalog(db, tenant_id, agent_id)
    if not any(item["name"] == name for item in catalog):
        return _text_result(
            {"code": "TOOL_NOT_FOUND", "message": f"{name} is not exposed"}, is_error=True
        )

    if name == KNOWLEDGE_SEARCH_TOOL:
        return _knowledge_search(db, tenant_id, agent_id, arguments)

    bare = name[len(TOOL_PREFIX) :]
    result = ToolExecutor(db).execute(
        tenant_id=tenant_id,
        tool_call=ToolCall(name=bare, arguments=arguments),
        agent_id=agent_id,
    )
    if not result.success:
        code = result.error.code if result.error else "EXECUTION_ERROR"
        message = result.error.message if result.error else "tool execution failed"
        return _text_result({"code": code, "message": message}, is_error=True)
    return _text_result(result.data)
```

> `KnowledgeService(db).search(request, None)` 的第二个参数是 `model_config`；传 `None` 时它按租户默认模型解析。若种子环境没有模型配置而该路径需要 LLM，`search` 会抛异常——届时按「已知风险 4」的做法注入 stub，不要改成绕过 `KnowledgeService`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_invoker.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server/invoker.py backend/tests/test_mcp_invoker.py
git commit -m "feat(mcp): dispatch tool calls through the existing executor"
```

---

### Task 4: 传输层与端点

**Files:**
- Create: `backend/app/mcp_server/session.py`
- Create: `backend/app/public_api/mcp.py`
- Modify: `backend/app/public_api/app.py`（导入元组 + `include_router`）
- Test: `backend/tests/test_public_api_mcp.py`

**Interfaces:**
- Produces:
  - `session.py`：`ISSUED_SESSIONS: dict[str, float]`、`issue_session_id() -> str`、`session_is_live(session_id: str | None) -> bool`、`SESSION_TTL_SECONDS = 3600`
  - `mcp.py`：`router = APIRouter(prefix="/mcp", tags=["mcp"])`，`POST ""` 处理 JSON-RPC，`GET ""` → 405

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_public_api_mcp.py
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token


def _client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        token = create_access_token(db.exec(select(User).where(User.username == "admin")).first())

    app = create_public_api_app()

    def override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    return TestClient(app), engine, token


def _key(client, token, scopes):
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post("/api-clients", headers=admin, json={"name": "dc-mcp", "scopes": scopes}).json()["id"]
    return client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "mcp", "scopes": scopes},
    ).json()["api_key"]


def _rpc(client, auth, body, **kwargs):
    return client.post("/mcp", headers={**auth, "Content-Type": "application/json"}, json=body, **kwargs)


def test_mcp_requires_the_invoke_scope(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"


def test_initialize_issues_a_session_and_reports_capabilities(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke", "agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    response = _rpc(
        client,
        auth,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "deepchat", "version": "0.1.0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in body["result"]["capabilities"]
    assert body["result"]["serverInfo"]["name"]
    assert response.headers.get("mcp-session-id")


def test_notification_gets_an_empty_accepted_response(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response.status_code == 202
    assert response.content in (b"", b"null")


def test_tools_list_is_scoped_to_the_employee_in_meta(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": {"agentId": "agent_f2828efc2a2a476d"}},
        },
    )
    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert names and all(name.startswith("staffdeck:") for name in names)
    blob = json.dumps(response.json(), ensure_ascii=False).lower()
    assert "headers_json" not in blob and "auth_json" not in blob


def test_tools_call_without_an_employee_is_refused(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "staffdeck:order.query", "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_unknown_method_returns_method_not_found(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "id": 4, "method": "./sneaky", "params": {}},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_get_is_not_supported(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.get("/mcp", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 405


def test_every_call_is_audited(monkeypatch):
    from app.db.models import APIAuditLog

    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {"_meta": {"agentId": "agent_f2828efc2a2a476d"}}},
    )
    with Session(engine) as db:
        rows = db.exec(select(APIAuditLog).where(APIAuditLog.path == "/mcp")).all()
    assert rows
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_mcp.py -q`
Expected: FAIL — 全部 404（`/mcp` 未注册）

- [ ] **Step 3: 实现会话表**

```python
# backend/app/mcp_server/session.py
from __future__ import annotations

import secrets
import time

SESSION_TTL_SECONDS = 3600
ISSUED_SESSIONS: dict[str, float] = {}


def issue_session_id() -> str:
    session_id = secrets.token_urlsafe(24)
    ISSUED_SESSIONS[session_id] = time.monotonic() + SESSION_TTL_SECONDS
    _evict_expired()
    return session_id


def session_is_live(session_id: str | None) -> bool:
    if not session_id:
        return False
    expiry = ISSUED_SESSIONS.get(session_id)
    if expiry is None:
        return False
    if expiry < time.monotonic():
        ISSUED_SESSIONS.pop(session_id, None)
        return False
    return True


def _evict_expired() -> None:
    now = time.monotonic()
    for key in [key for key, expiry in ISSUED_SESSIONS.items() if expiry < now]:
        ISSUED_SESSIONS.pop(key, None)
```

- [ ] **Step 4: 实现端点**

顺序要点：`initialize` / `notifications/*` / `ping` / `tools/*` 必须都在**同一个** `POST` handler 里按 `method` 分派，不要拆成多个 FastAPI 路由（JSON-RPC 只有一个端点）。

```python
# backend/app/public_api/mcp.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.db import get_session
from app.db.models import APIAuditLog, utc_now
from app.mcp_server.invoker import call_tool, extract_agent_id
from app.mcp_server.catalog import build_tool_catalog
from app.mcp_server.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RpcError,
    error_response,
    negotiate_protocol_version,
    parse_request,
    success_response,
)
from app.mcp_server.session import issue_session_id
from app.public_api.auth import PublicPrincipal, require_scopes

router = APIRouter(prefix="/mcp", tags=["mcp"])

_SERVER_INFO = {"name": "StaffDeck MCP", "version": "1.0.0"}


def _audit(db: Session, principal: PublicPrincipal, request: Request, status_code: int, resource_id: str) -> None:
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="POST",
            path=request.url.path,
            resource_type="tool",
            resource_id=resource_id,
            status_code=status_code,
            duration_ms=0.0,
        )
    )
    db.commit()


@router.post("")
def mcp_endpoint(
    payload: dict,
    request: Request,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),  # noqa: ARG001
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),
    db: Session = Depends(get_session),
):
    try:
        request_id, method, params, is_notification = parse_request(payload)
    except RpcError as error:
        return JSONResponse(error_response(None, error), status_code=200)

    if is_notification:
        # notifications/initialized needs no reply; anything else is dropped too.
        return Response(status_code=202)

    try:
        if method == "initialize":
            result = {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
            }
            response.headers["Mcp-Session-Id"] = issue_session_id()
            _audit(db, principal, request, 200, "initialize")
            return success_response(request_id, result)

        if method == "ping":
            return success_response(request_id, {})

        if method == "tools/list":
            agent_id = extract_agent_id(params, principal.agent_id)
            if not agent_id:
                raise RpcError(INVALID_PARAMS, "tools/list requires _meta.agentId or an agent-bound key.")
            tools = build_tool_catalog(db, principal.tenant_id, agent_id)
            _audit(db, principal, request, 200, agent_id)
            return success_response(request_id, {"tools": tools})

        if method == "tools/call":
            agent_id = extract_agent_id(params, principal.agent_id)
            if not agent_id:
                raise RpcError(INVALID_PARAMS, "tools/call requires _meta.agentId or an agent-bound key.")
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise RpcError(INVALID_PARAMS, "params.name is required.")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise RpcError(INVALID_PARAMS, "params.arguments must be an object.")
            result = call_tool(db, principal.tenant_id, agent_id, name, arguments)
            _audit(db, principal, request, 200, name)
            return success_response(request_id, result)

        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
    except RpcError as error:
        return JSONResponse(error_response(request_id, error), status_code=200)
    except Exception as error:  # noqa: BLE001
        return JSONResponse(
            error_response(request_id, RpcError(INTERNAL_ERROR, str(error))), status_code=200
        )


@router.get("")
def mcp_get_not_supported(
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),  # noqa: ARG001
) -> Response:
    """streamable HTTP without server-initiated streams: GET has nothing to serve."""
    return Response(status_code=405, headers={"Allow": "POST"})
```

> **注意**：上面 `if_none_match` 参数是计划模板残留，实做时**删掉**（MCP 端点不用 ETag 条件请求）。`run_in_threadpool`、`utc_now`、`PARSE_ERROR` 若未使用也一并删掉导入。

- [ ] **Step 5: 注册 router**

在 `backend/app/public_api/app.py`：

```python
from app.public_api import (
    agents,
    credentials,
    digital_employees,
    examples,
    gallery,
    jobs,
    knowledge_sync,
    mcp,
    operations,
    resources,
    runs,
    sessions,
    sops,
    webhooks,
)
```

```python
    app.include_router(knowledge_sync.router)
    app.include_router(mcp.router)
    app.include_router(digital_employees.router)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_mcp.py -q`
Expected: PASS（9 passed）

- [ ] **Step 7: Commit**

```bash
git add backend/app/mcp_server/session.py backend/app/public_api/mcp.py backend/app/public_api/app.py backend/tests/test_public_api_mcp.py
git commit -m "feat(mcp): serve a streamable HTTP MCP endpoint at /api/v1/mcp"
```

---

### Task 5: 端到端验收与文档勘误

**Files:**
- Create: `backend/tests/test_mcp_e2e.py`
- Modify: `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`
- Modify: `docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md`

- [ ] **Step 1: 写端到端测试**

```python
# backend/tests/test_mcp_e2e.py
"""The MCP handshake as a client would actually walk it: initialize, list, call."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


def _authed_client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        token = create_access_token(db.exec(select(User).where(User.username == "admin")).first())

    app = create_public_api_app()

    def override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post("/api-clients", headers=admin, json={"name": "dc", "scopes": ["*"]}).json()["id"]
    key = client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "mcp", "scopes": ["mcp:invoke", "agents:read"]},
    ).json()["api_key"]
    return client, {"Authorization": f"Bearer {key}"}


def test_full_handshake_then_tool_call(monkeypatch):
    client, auth = _authed_client(monkeypatch)

    init = client.post(
        "/mcp",
        headers=auth,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        },
    )
    assert init.status_code == 200
    session_id = init.headers["mcp-session-id"]
    assert init.json()["result"]["serverInfo"]["name"] == "StaffDeck MCP"

    notified = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notified.status_code == 202

    listed = client.post(
        "/mcp",
        headers={**auth, "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {"agentId": FINANCE_AGENT}}},
    )
    tools = listed.json()["result"]["tools"]
    assert tools
    # The names here must be exactly what the distribution payload advertises.
    payload = client.get("/digital-employees", headers=auth).json()
    finance = next(item for item in payload["employees"] if item["id"] == FINANCE_AGENT)
    assert {item["name"] for item in tools} <= set(finance["tools"]) | {"staffdeck:knowledge_search"}

    called = client.post(
        "/mcp",
        headers={**auth, "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "staffdeck:order.refund",
                "arguments": {"orderId": "A-1"},
                "_meta": {"agentId": FINANCE_AGENT},
            },
        },
    )
    assert called.status_code == 200
    result = called.json()["result"]
    assert result["content"][0]["type"] == "text"
    assert isinstance(result["isError"], bool)
    # No credential material may ride along in the tool result either.
    blob = json.dumps(result, ensure_ascii=False).lower()
    for forbidden in ("headers_json", "auth_json", "sd_live_"):
        assert forbidden not in blob
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_mcp_e2e.py -q`
Expected: PASS（1 passed）

如果 `order.refund` 在种子数据里不可见，改用 `listed.json()` 里实际存在的第一个 `staffdeck:` 工具名（不要放宽断言到「任意名字都行」）。

- [ ] **Step 3: 修 spec 勘误**

`docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md` §6.3，在「暴露三类工具」列表后补一条实现说明：

```markdown
**实现说明（M4 落地时确认）**：本期只交付 ①②，③（远端续跑工具）延后——deepchat 已有 `runs:stream` 直连能力（通道④），再包一层 MCP 工具属于重复路径。工具命名严格等于分发 payload 的 `tools[]` 字符串（`staffdeck:{Tool.name}`），`tools/call` 的员工上下文经 MCP 规范的 `_meta.agentId` 传递，无需额外协议字段。知识检索工具为虚拟工具 `staffdeck:knowledge_search`，仅在员工有已开启同步的知识库时出现。
```

`docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md` §5.2（D4 技能/工具段），补：

```markdown
**MCP 注册方式**：StaffDeck 端点为 `{host}/api/v1/mcp`，`type: 'http'`，请求头带 `Authorization: Bearer <sd_live_*>`（scope `mcp:invoke`）。`tools/list` / `tools/call` 必须携带 `params._meta.agentId`，否则服务端无法确定员工上下文并返回 `-32602`。GET 返回 405（不提供服务器推送），客户端不要依赖 SSE 通道。
```

- [ ] **Step 4: 全量回归与 lint**

Run:
```bash
cd backend
./.venv/Scripts/python.exe -m pytest tests/test_mcp_protocol.py tests/test_mcp_catalog.py tests/test_mcp_invoker.py tests/test_public_api_mcp.py tests/test_mcp_e2e.py tests/test_public_api_v1.py tests/test_public_api_digital_employees.py tests/test_public_api_knowledge_sync.py -q
./.venv/Scripts/python.exe -m ruff check app/mcp_server app/public_api/mcp.py tests/test_mcp_*.py tests/test_public_api_mcp.py --statistics
./.venv/Scripts/python.exe -m ruff format --check app/mcp_server app/public_api/mcp.py tests/test_mcp_*.py tests/test_public_api_mcp.py
```
Expected: 全部通过；lint 只允许 B008（FastAPI `Depends` 默认值，项目既有风格）

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_mcp_e2e.py docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md
git commit -m "test(mcp): walk the full handshake end to end

Records two spec corrections: the remote-run MCP tool is deferred because
runs:stream already covers it, and the employee context travels through
_meta.agentId rather than a bespoke field."
```

---

## Self-Review

| 检查项 | 结论 |
|---|---|
| 凭证是否可能泄露 | `build_tool_catalog` 只 emit 三个协议字段，Task 2 有专门的「无凭证」断言；Task 5 在 tool result 上再验一次 |
| 命名空间是否与分发 payload 一致 | 两处都用 `payload.TOOL_PREFIX`，Task 5 做交叉断言（`tools ⊆ payload.tools ∪ {knowledge_search}`） |
| 权限是否双层 | scope `mcp:invoke`（端点）+ `visible_tool_rows`（数据）；`ToolExecutor.execute(agent_id=...)` 内部还会再校验一次 |
| 是否新增依赖 | 否。`json` / `secrets` / `time` / FastAPI 既有能力 |
| 是否需要迁移 | 否 |
| 未知 method 是否被放过 | Task 1 + Task 4 双重覆盖（`METHOD_NOT_FOUND`） |
| 通知是否被错误响应 | `parse_request` 用 `"id" not in payload` 判定；Task 4 返回 202 空体 |

## 已知风险

1. **会话表在进程内内存里**：多实例部署时，initialize 落在 A 实例、tools/call 落在 B 实例会查不到 session。当前实现里 `session_is_live` 只在需要严格校验的地方才用；**本计划的端点并不强制校验 session**（只下发不校验），因此多实例下不会报错，只是失去了会话粘性语义。若将来要强制，需挪到 Redis——不要用 `SESSION_TTL_SECONDS` 之外的机制偷偷加锁。
2. **`tools/list` 每次请求重建目录**：与 M2 的 `_distribution_agents` 同性质（每请求一次可见性查询）。百级工具可接受，千级需缓存（按 `agent_id` + `Tool.updated_at` 最大值做 key）。
3. **`ToolExecutor` 的 detached 分支**：`execution_mode == "detached"` 的 HTTP 工具会走异步受理路径，返回的是受理凭证而不是最终结果。MCP 语义下 `tools/call` 是同步的，这类工具在 MCP 里表现为「已受理，请轮询」——本期不做轮询工具，需在文档里告知 deepchat。
4. **知识检索需要模型**：`search_knowledge` 内部若依赖 LLM 路由，在无模型配置的环境下会失败。Task 3 的测试用种子数据（本地向量/关键词路径），若实际走向 LLM 分支需把该用例标 `pytest.mark.slow` 或注入 stub。
