"""The MCP handshake as a client would actually walk it: initialize, list, call."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import User
from app.db.seed import seed_demo_data
from app.digital_employees.tool_namespace import namespaced_tool_name
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


def _authed_client():
    """构建带管理员令牌与 MCP API Key 的测试客户端,供各用例复用同一套鉴权环境。"""
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
        """用测试内存数据库替换真实会话依赖,每个请求独立开启一次 Session。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc", "scopes": ["*"]}
    ).json()["id"]
    key = client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "mcp", "scopes": ["mcp:invoke", "agents:read"]},
    ).json()["api_key"]
    return client, {"Authorization": f"Bearer {key}"}


def test_full_handshake_then_tool_call():
    """验证完整 MCP 握手(initialize→initialized→tools/list→tools/call)可用且不泄露凭据。"""
    client, auth = _authed_client()

    init = client.post(
        "/mcp",
        headers=auth,
        json={
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
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    tools = listed.json()["result"]["tools"]
    assert tools

    # The names here must be exactly what the distribution payload advertises,
    # otherwise deepchat's session-level allowlist would drop every call.
    payload = client.get("/digital-employees", headers=auth).json()
    finance = next(item for item in payload["employees"] if item["id"] == FINANCE_AGENT)
    assert {item["name"] for item in tools} <= set(finance["tools"]) | {
        namespaced_tool_name("knowledge_search")
    }

    first_tool = next(
        item["name"] for item in tools if item["name"] != namespaced_tool_name("knowledge_search")
    )
    called = client.post(
        "/mcp",
        headers={**auth, "Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": first_tool,
                "arguments": {},
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


def test_every_advertised_tool_is_callable():
    """验证 tools/list 公告的每个工具都能被 tools/call 分发,不会被判定为 TOOL_NOT_FOUND。"""
    client, auth = _authed_client()
    listed = client.post(
        "/mcp",
        headers=auth,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {"agentId": FINANCE_AGENT}},
        },
    )
    names = [item["name"] for item in listed.json()["result"]["tools"]]
    assert names

    for name in names:
        called = client.post(
            "/mcp",
            headers=auth,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}, "_meta": {"agentId": FINANCE_AGENT}},
            },
        )
        result = called.json()["result"]
        # The upstream mock endpoint may fail; what matters is that the call was
        # dispatched rather than refused with TOOL_NOT_FOUND.
        if result["isError"]:
            error = json.loads(result["content"][0]["text"])
            assert error["code"] != "TOOL_NOT_FOUND", f"{name} was advertised but refused"
