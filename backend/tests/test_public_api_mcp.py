from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import APIAuditLog, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


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
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc-mcp", "scopes": scopes}
    ).json()["id"]
    return client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "mcp", "scopes": scopes},
    ).json()["api_key"]


def _rpc(client, auth, body, **kwargs):
    return client.post(
        "/mcp", headers={**auth, "Content-Type": "application/json"}, json=body, **kwargs
    )


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


def test_mcp_rejects_a_user_jwt(monkeypatch):
    """The endpoint is API-key only: a browser session token must not reach it."""
    client, _engine, token = _client(monkeypatch)
    response = _rpc(
        client,
        {"Authorization": f"Bearer {token}"},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "API_KEY_REQUIRED"


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


def test_initialize_negotiates_an_unknown_version_downwards(monkeypatch):
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
            "params": {"_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    assert response.status_code == 200, response.text
    tools = response.json()["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert names and all(name.startswith("staffdeck:") for name in names)
    blob = json.dumps(response.json(), ensure_ascii=False).lower()
    assert "headers_json" not in blob and "auth_json" not in blob


def test_tools_list_without_an_employee_is_refused(monkeypatch):
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
                "name": "staffdeck:expense.quota_query",
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
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = _rpc(
        client,
        {"Authorization": f"Bearer {key}"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "staffdeck:expense.submit", "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_tools_call_requires_a_name(monkeypatch):
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
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert names and all(name.startswith("staffdeck:") for name in names)


def test_agent_path_scopes_tools_call_without_any_meta(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["mcp:invoke"])
    response = client.post(
        f"/mcp/agents/{FINANCE_AGENT}",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "staffdeck:expense.quota_query", "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["type"] == "text"


def test_agent_path_wins_over_a_conflicting_meta(monkeypatch):
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
                "name": "staffdeck:expense.quota_query",
                "arguments": {},
                "_meta": {"agentId": "agent_preset_sales_001"},
            },
        },
    )
    # The path is authoritative; the sales employee has no tools at all, so a
    # meta-driven dispatch would have failed with TOOL_NOT_FOUND here.
    assert response.json()["result"]["isError"] is False


def test_agent_path_rejects_an_employee_outside_the_credential_scope(monkeypatch):
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
