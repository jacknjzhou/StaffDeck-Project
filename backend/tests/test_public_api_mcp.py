from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import APIAuditLog, User
from app.db.seed import seed_demo_data
from app.digital_employees.tool_namespace import namespaced_tool_name
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


def _client(monkeypatch):
    """构建带种子数据与管理员令牌的公共 API 测试客户端，返回客户端、引擎与令牌。"""
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
        """用测试引擎为每个请求提供独立 Session。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    return TestClient(app), engine, token


def _key(client, token, scopes):
    """创建指定作用域的 API 客户端与凭据，返回明文 API key。"""
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc-mcp", "scopes": scopes}
    ).json()["id"]
    return client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "mcp", "scopes": scopes},
    ).json()["api_key"]


def _rpc(client, auth, body, **kwargs):
    """向 /mcp 发送带认证头的 JSON-RPC 请求。"""
    return client.post(
        "/mcp", headers={**auth, "Content-Type": "application/json"}, json=body, **kwargs
    )


def test_mcp_requires_the_invoke_scope(monkeypatch):
    """验证缺少 mcp:invoke 作用域的密钥调用 MCP 返回 403 与 INSUFFICIENT_SCOPE。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"


def test_mcp_rejects_a_user_jwt(monkeypatch):
    """验证浏览器会话令牌被拒绝，MCP 端点只接受 API key。"""
    client, _engine, token = _client(monkeypatch)
    response = _rpc(
        client,
        {"Authorization": f"Bearer {token}"},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "API_KEY_REQUIRED"


def test_initialize_issues_a_session_and_reports_capabilities(monkeypatch):
    """验证 initialize 返回协商版本、tools 能力与服务信息，并下发 mcp-session-id 响应头。"""
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


def test_initialize_negotiates_an_unknown_version_downwards(monkeypatch):
    """验证未知协议版本降级到 2024-11-05。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2024-11-05"


def test_notification_gets_an_empty_accepted_response(monkeypatch):
    """验证通知类请求返回 202 且响应体为空。"""
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
    """验证 tools/list 按 _meta 中的员工返回 staffdeck_ 前缀工具，且不泄露凭据字段。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    assert response.status_code == 200, response.text
    tools = response.json()["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert names and all(name.startswith("staffdeck_") for name in names)
    blob = json.dumps(response.json(), ensure_ascii=False).lower()
    assert "headers_json" not in blob and "auth_json" not in blob


def test_tools_list_without_an_employee_is_refused(monkeypatch):
    """验证缺少员工标识时 tools/list 返回 -32602 参数错误。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_tools_call_returns_a_call_tool_result(monkeypatch):
    """验证 tools/call 返回 MCP CallToolResult 结构，包含 text 内容与 isError。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": namespaced_tool_name("expense.quota_query"),
                "arguments": {},
                "_meta": {"agentId": FINANCE_AGENT},
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["content"][0]["type"] == "text"
    assert isinstance(result["isError"], bool)


def test_tools_call_without_an_employee_is_refused(monkeypatch):
    """验证缺少员工标识时 tools/call 返回 -32602。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": namespaced_tool_name("expense.submit"), "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_tools_call_requires_a_name(monkeypatch):
    """验证 tools/call 未提供工具名时返回 -32602。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"arguments": {}, "_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_unknown_method_returns_method_not_found(monkeypatch):
    """验证未知方法返回 -32601 method not found。"""
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
    """验证 GET /mcp 不被支持，返回 405。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.get("/mcp", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 405


def test_every_call_is_audited(monkeypatch):
    """验证每次 MCP 调用都会写入审计日志并记录员工 id。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/list",
            "params": {"_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    with Session(engine) as db:
        rows = db.exec(select(APIAuditLog).where(APIAuditLog.path == "/mcp")).all()
    assert rows
    assert any(row.resource_id == FINANCE_AGENT for row in rows)


# --- employee-addressed endpoint -------------------------------------------------
# A standard MCP client (deepchat included) cannot inject _meta into tools/list:
# its config carries static customHeaders only. The path form is what makes this
# endpoint usable by a real MCP client, so it must stay covered.


def test_agent_path_scopes_tools_list_without_any_meta(monkeypatch):
    """验证路径中的员工 id 可在不传 _meta 时限定 tools/list 范围。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert names and all(name.startswith("staffdeck_") for name in names)


def test_agent_path_scopes_tools_call_without_any_meta(monkeypatch):
    """验证路径员工可在不传 _meta 时执行 tools/call 并返回文本结果。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": namespaced_tool_name("expense.quota_query"), "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["type"] == "text"


def test_agent_path_wins_over_a_conflicting_meta(monkeypatch):
    """验证路径员工优先于冲突的 _meta，按财务员工派发而非无工具的销售员工。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": namespaced_tool_name("expense.quota_query"),
                "arguments": {},
                "_meta": {"agentId": "agent_preset_sales_001"},
            },
        },
    )
    # The path is authoritative; the sales employee has no tools at all, so a
    # meta-driven dispatch would have failed with TOOL_NOT_FOUND here.
    assert response.json()["result"]["isError"] is False


def test_agent_path_rejects_an_employee_outside_the_credential_scope(monkeypatch):
    """验证绑定到财务员工的密钥只能访问该员工，访问其他员工路径返回 403。"""
    client, _engine, token = _client(monkeypatch)
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc-bound", "scopes": ["mcp:invoke"]}
    ).json()["id"]
    key = client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "bound", "scopes": ["mcp:invoke"], "agent_id": FINANCE_AGENT},
    ).json()["api_key"]

    allowed = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert allowed.status_code == 200

    # A key bound to finance must not reach another employee through the path.
    denied = client.post(
        "/mcp/agents/agent_preset_sales_001",
        headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert denied.status_code == 403
