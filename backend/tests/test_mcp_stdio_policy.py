"""MCP stdio 执行策略：命令白名单、子进程环境净化与部署开关。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.tools import mcp_router
from app.db import get_session
from app.db.models import MCPServer, Tenant, User
from app.security.auth import get_current_user
from app.tools.mcp_client import _StdioSession, build_stdio_env
from app.tools.tool_schema import MCPServerConnection


def _enabled_settings(monkeypatch):
    """测试里绕过全局配置缓存，显式开启 stdio 与白名单。"""
    monkeypatch.setattr("app.tools.tool_schema.get_settings", lambda: type("S", (), {
        "mcp_stdio_enabled": True,
        "mcp_stdio_allowed_commands": "npx,uvx,node,python3,uv",
    })())


def _admin_user() -> User:
    """构造租户管理员，用于走通创建接口的鉴权与租户校验。"""
    return User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="ops",
        role="admin",
        password_hash="test",
    )


def test_stdio_rejects_non_allowlisted_command(monkeypatch):
    """未在白名单中的可执行文件必须被拒绝，避免任意命令执行。"""
    _enabled_settings(monkeypatch)
    with pytest.raises(ValueError, match="allowlist"):
        MCPServerConnection(transport="stdio", command="/bin/sh", args=["-c", "id"])


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
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

    app = FastAPI()
    app.include_router(mcp_router)

    def override_session():
        """每个请求独立开启一次测试库会话。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = _admin_user
    client = TestClient(app)

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
