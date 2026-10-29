"""D4 联调：按 deepchat 客户端的真实请求形状打一遍 StaffDeck 的 MCP 与技能导出。

用 TestClient 而不是起 uvicorn：要走的是「协议对不对」，不是「端口通不通」，
而 TestClient 走的是同一个 ASGI app、同一套中间件与鉴权。

跑法：
    cd backend && .venv/Scripts/python.exe tests/manual_d4_handshake.py
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


def _client() -> tuple[TestClient, dict[str, str]]:
    """构造带演示数据与 API 凭证的 TestClient，返回客户端与鉴权头。"""
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
        """把测试引擎的会话绑定到应用的 get_session 依赖。"""
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
        json={"name": "d4", "scopes": ["mcp:invoke", "agents:read"]},
    ).json()["api_key"]
    return client, {"Authorization": f"Bearer {key}"}


def _rpc(client: TestClient, url: str, auth: dict[str, str], payload: dict) -> dict:
    """发送一次 JSON-RPC 请求并打印状态码，失败时打印响应片段。"""
    response = client.post(url, headers=auth, json=payload)
    print(f"  {payload['method']:<24} -> HTTP {response.status_code}")
    if response.status_code != 200:
        print(f"    body: {response.text[:400]}")
        return {}
    return response.json()


def check_mcp_handshake() -> None:
    """按 deepchat 客户端形状校验 MCP 初始化、工具列表、调用与 GET 探测。"""
    print("\n== 1. MCP handshake ==")
    client, auth = _client()
    url = f"/mcp/agents/{FINANCE_AGENT}"

    init = _rpc(
        client,
        url,
        auth,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "deepchat", "version": "d4"},
            },
        },
    )
    server_info = init.get("result", {}).get("serverInfo", {})
    print(f"    serverInfo: {server_info}")

    listed = _rpc(
        client,
        url,
        auth,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools = listed.get("result", {}).get("tools", [])
    print(f"    tools: {len(tools)}")
    for tool in tools:
        name = tool["name"]
        schema = tool.get("inputSchema")
        print(f"      - {name:<44} inputSchema={'yes' if schema else 'MISSING'}")

    # 客户端侧硬约束：deepchat 的 toolManager 用这个字符集过滤，不匹配即静默丢弃。
    import re

    pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    bad = [tool["name"] for tool in tools if not pattern.match(tool["name"])]
    print(f"    names failing /^[a-zA-Z0-9_-]+$/: {bad or 'none'}")
    long_names = [tool["name"] for tool in tools if len(tool["name"]) > 64]
    print(f"    names over 64 chars: {long_names or 'none'}")

    if tools:
        called = _rpc(
            client,
            url,
            auth,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tools[0]["name"], "arguments": {}},
            },
        )
        result = called.get("result", {})
        print(f"    tools/call isError={result.get('isError')}")

    # GET 是否如 spec 所述返回 405：客户端若发 GET 探测，不能把它当致命错误。
    get_response = client.get(url, headers=auth)
    print(f"    GET -> HTTP {get_response.status_code} (expected 405)")


def check_skill_export() -> None:
    """校验财务数字员工技能导出接口返回的压缩包内容与下载地址。"""
    print("\n== 2. skill export ==")
    client, auth = _client()

    listed = client.get("/digital-employees", headers=auth).json()
    employee = next(
        (item for item in listed["employees"] if item["id"] == FINANCE_AGENT), None
    )
    if not employee:
        print("  finance employee not distributed; aborting")
        return

    skills = employee.get("skills") or []
    print(f"  skills: {[(item['slug'], item['id']) for item in skills]}")
    for skill in skills:
        response = client.get(
            f"/digital-employees/{FINANCE_AGENT}:export-skill",
            headers=auth,
            params={"slug": skill["slug"]},
        )
        print(f"    {skill['slug']}: HTTP {response.status_code}, {len(response.content)} bytes")
        if response.status_code != 200:
            print(f"      body: {response.text[:300]}")
            continue
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            print(f"      entries: {names}")
            print(f"      root has SKILL.md: {'SKILL.md' in names}")

    # downloadUrl 里带的是服务端 tool_base_url，验证它确实不是客户端 baseUrl。
    if skills:
        print(f"  downloadUrl sample: {skills[0].get('downloadUrl')}")


def check_forbidden() -> None:
    """校验错误密钥与越权员工访问返回预期的鉴权失败状态。"""
    print("\n== 3. auth failure shape ==")
    client, _ = _client()
    url = f"/mcp/agents/{FINANCE_AGENT}"
    response = client.post(
        url,
        headers={"Authorization": "Bearer sd_live_deadbeef"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    print(f"  bad key -> HTTP {response.status_code} (expected 401 or 403)")
    print(f"    body: {response.text[:200]}")

    # 跨员工越权：拿到 finance 的凭证去打别的员工。
    other = client.post(
        url.replace(FINANCE_AGENT, "agent_not_mine"),
        headers={"Authorization": "Bearer sd_live_deadbeef"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    print(f"  unknown agent -> HTTP {other.status_code}")


if __name__ == "__main__":
    check_mcp_handshake()
    check_skill_export()
    check_forbidden()
    print("\ndone")
