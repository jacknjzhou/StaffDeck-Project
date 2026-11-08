"""MCP stdio 执行策略：命令白名单、子进程环境净化与部署开关。"""

import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.tools import mcp_router, mcp_server_read
from app.db import get_session
from app.db.models import MCPServer, Tenant, User
from app.security.auth import get_current_user
from app.tools.mcp_client import _StdioSession, build_stdio_env
from app.tools.tool_schema import MCPServerConnection


def _enabled_settings(monkeypatch, allowed: str = "npx,uvx,node,python3,uv"):
    """测试里绕过全局配置缓存，显式开启 stdio 并给出白名单。"""
    settings = type("S", (), {
        "mcp_stdio_enabled": True,
        "mcp_stdio_allowed_commands": allowed,
    })()
    monkeypatch.setattr("app.tools.tool_schema.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.tools.get_settings", lambda: settings)
    monkeypatch.setattr("app.tools.mcp_client.get_settings", lambda: settings)


def _test_engine():
    """构造一套隔离的 SQLite 内存库，供 API 与读路径用例共用。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_tenant(engine, tenant_id: str = "tenant_demo") -> None:
    """写入租户行，满足接口的租户存在性校验。"""
    with Session(engine) as db:
        db.add(Tenant(id=tenant_id, name="Demo"))
        db.commit()


def _api_client(engine) -> TestClient:
    """用测试库与管理员身份组装 MCP 路由的 TestClient。"""
    app = FastAPI()
    app.include_router(mcp_router)

    def override_session():
        """每个请求独立开启一次测试库会话。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = _admin_user
    return TestClient(app)


def _seed_server(engine, server_id: str, name: str, transport: str = "stdio",
                 command: str | None = None) -> None:
    """直接落一条 MCP Server 行，模拟历史/种子数据。"""
    with Session(engine) as db:
        db.add(
            MCPServer(
                id=server_id,
                tenant_id="tenant_demo",
                name=name,
                transport=transport,
                command=command,
            )
        )
        db.commit()


def _admin_user() -> User:
    """构造租户管理员，用于走通创建接口的鉴权与租户校验。"""
    return User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="ops",
        role="admin",
        password_hash="test",
    )


def test_stdio_command_outside_allowlist_is_not_allowed(monkeypatch):
    """模型只负责判定，不再在构造期抛错（避免请求 422 与读路径 500）。"""
    _enabled_settings(monkeypatch)
    conn = MCPServerConnection(transport="stdio", command="/bin/sh", args=["-c", "id"])
    assert conn.allowed_stdio_command() is False


def test_default_allowlist_covers_venv_interpreter() -> None:
    """默认白名单必须覆盖 venv 里的解释器（种子 stdio_demo 依赖它）。"""
    conn = MCPServerConnection(transport="stdio", command=sys.executable)
    assert conn.allowed_stdio_command() is True


def test_persisted_stdio_server_with_unlisted_command_reads_back() -> None:
    """回归 Critical：历史/种子的非白名单命令不得让读接口 500。"""
    engine = _test_engine()
    _seed_tenant(engine)
    _seed_server(engine, "mcpsrv_seed_sh", "stdio_seed_sh", command="/bin/sh")
    _seed_server(engine, "mcpsrv_seed_py", "stdio_seed_py", command=sys.executable)

    with Session(engine) as db:
        row = db.get(MCPServer, "mcpsrv_seed_sh")
        assert row is not None
        assert mcp_server_read(row, db).connection.command == "/bin/sh"

    client = _api_client(engine)
    detail = client.get(
        "/api/enterprise/mcp-servers/mcpsrv_seed_sh", params={"tenant_id": "tenant_demo"}
    )
    assert detail.status_code == 200
    assert detail.json()["connection"]["command"] == "/bin/sh"

    listing = client.get("/api/enterprise/mcp-servers", params={"tenant_id": "tenant_demo"})
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()} == {"mcpsrv_seed_sh", "mcpsrv_seed_py"}


def test_create_stdio_server_rejects_non_allowlisted_command(monkeypatch) -> None:
    """写路径在启用 stdio 时仍按白名单拒绝，返回 403 且不落库。"""
    _enabled_settings(monkeypatch)
    engine = _test_engine()
    _seed_tenant(engine)
    client = _api_client(engine)

    response = client.post(
        "/api/enterprise/mcp-servers",
        json={
            "tenant_id": "tenant_demo",
            "name": "stdio_blocked",
            "connection": {"transport": "stdio", "command": "/bin/sh", "args": ["-c", "id"]},
        },
    )

    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"]
    with Session(engine) as db:
        assert db.exec(select(MCPServer)).all() == []


def test_update_stdio_server_rejects_non_allowlisted_command(monkeypatch) -> None:
    """更新接口同样按白名单拒绝，避免绕过创建口子。"""
    _enabled_settings(monkeypatch)
    engine = _test_engine()
    _seed_tenant(engine)
    _seed_server(engine, "mcpsrv_builtin", "builtin_demo", transport="builtin")
    client = _api_client(engine)

    response = client.put(
        "/api/enterprise/mcp-servers/mcpsrv_builtin",
        json={
            "tenant_id": "tenant_demo",
            "name": "builtin_demo",
            "connection": {"transport": "stdio", "command": "/bin/sh"},
        },
    )

    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"]


def test_discover_stdio_rejects_non_allowlisted_command(monkeypatch) -> None:
    """未保存前的探测接口也必须拦下非白名单命令。"""
    _enabled_settings(monkeypatch)
    engine = _test_engine()
    _seed_tenant(engine)
    client = _api_client(engine)

    response = client.post(
        "/api/enterprise/mcp-servers/discover",
        json={
            "tenant_id": "tenant_demo",
            "connection": {"transport": "stdio", "command": "/bin/sh"},
        },
    )

    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"]


def test_saved_stdio_discover_is_blocked_when_disabled() -> None:
    """默认关闭时，即便 Server 已落库，探测也不得放行。"""
    engine = _test_engine()
    _seed_tenant(engine)
    _seed_server(engine, "mcpsrv_npx", "stdio_npx", command="npx")
    client = _api_client(engine)

    response = client.post(
        "/api/enterprise/mcp-servers/mcpsrv_npx/discover",
        json={"tenant_id": "tenant_demo"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "stdio MCP 未在本部署启用"


def test_stdio_accepts_npx(monkeypatch):
    """白名单内启动器可以通过校验。"""
    _enabled_settings(monkeypatch)
    conn = MCPServerConnection(transport="stdio", command="npx", args=["-y", "pkg"])
    assert conn.command == "npx"


def test_allowlist_matches_executable_basename(monkeypatch):
    """白名单按可执行文件名匹配，绝对路径的 npx 同样放行。"""
    _enabled_settings(monkeypatch)
    conn = MCPServerConnection(transport="stdio", command="/usr/bin/npx")
    assert conn.allowed_stdio_command() is True


def test_stdio_env_is_allowlisted(monkeypatch):
    """子进程环境变量只保留基础键与显式配置，不能继承云凭证。"""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_stdio_env({"MCP_TOKEN": "ok"})
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["MCP_TOKEN"] == "ok"


def test_create_stdio_server_returns_403_when_disabled(monkeypatch):
    """部署关闭 stdio 时，创建接口必须在落库前以 403 明确拒绝。"""
    monkeypatch.setattr(
        "app.api.tools.get_settings",
        lambda: SimpleNamespace(mcp_stdio_enabled=False),
    )
    engine = _test_engine()
    _seed_tenant(engine)
    client = _api_client(engine)

    response = client.post(
        "/api/enterprise/mcp-servers",
        json={
            "tenant_id": "tenant_demo",
            "name": "stdio_blocked",
            "connection": {"transport": "stdio", "command": "npx", "args": ["-y", "pkg"]},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "stdio MCP 未在本部署启用"
    with Session(engine) as db:
        assert db.exec(select(MCPServer)).all() == []


def test_stdio_session_refuses_spawn_when_disabled(monkeypatch):
    """纵深防御：即使绕过 API，禁用状态下 stdio 会话也不得拉起任何子进程。"""
    monkeypatch.setattr(
        "app.tools.mcp_client.get_settings",
        lambda: SimpleNamespace(mcp_stdio_enabled=False),
    )
    session = _StdioSession(
        {
            "transport": "stdio",
            "command": "staffdeck-command-that-does-not-exist",
            "args": [],
        },
        timeout_seconds=1,
    )
    with pytest.raises(ValueError, match="disabled on this deployment"), session:
        pass


def test_stdio_session_rechecks_allowlist(monkeypatch):
    """纵深防御：进入会话时重新校验白名单，历史行也绕不过执行期门禁。"""
    monkeypatch.setattr(
        "app.tools.mcp_client.get_settings",
        lambda: SimpleNamespace(
            mcp_stdio_enabled=True,
            mcp_stdio_allowed_commands="npx,uvx,node,python3,uv",
        ),
    )
    session = _StdioSession(
        {"transport": "stdio", "command": "/bin/sh", "args": ["-c", "id"]},
        timeout_seconds=1,
    )
    with pytest.raises(ValueError, match="allowlist"), session:
        pass
