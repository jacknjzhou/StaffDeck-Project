import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.tools import (
    MCP_APP_RESOURCE_MAX_BYTES,
    _extract_app_resource,
    create_mcp_server,
    call_mcp_app_tool,
    delete_mcp_server,
    discover_mcp_tools,
    discover_mcp_tools_adhoc,
    get_mcp_app_resource,
    list_tools,
    sync_mcp_tools,
)
from app.db.models import MCPServer, Tenant, Tool, User
from app.db.models import AgentProfile, AgentResourceBinding
from app.tools.tool_executor import ToolExecutor
from app.tools.mcp_client import (
    MCPClientError,
    discover_mcp_server,
    execute_mcp_tool_result,
    read_mcp_resource,
)
from app.tools.tool_schema import (
    MCPDiscoverRequest,
    MCPAppToolCallRequest,
    MCPServerConnection,
    MCPServerCreateRequest,
    MCPSyncRequest,
    ToolCall,
)


def _admin_user() -> User:
    """构造租户管理员用户，用于需要管理员权限的 MCP 接口调用。"""
    return User(id="user_admin", tenant_id="tenant_demo", username="ops", role="admin", password_hash="test")


def _member_user() -> User:
    """构造普通成员用户，用于验证成员可执行的发现类接口。"""
    return User(id="user_member", tenant_id="tenant_demo", username="member", role="member", password_hash="test")


def test_discover_builtin_mcp_server_lists_tools() -> None:
    """验证内置 MCP 服务可被成员发现，并返回 echo 等工具及其入参 schema。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        response = discover_mcp_tools_adhoc(
            MCPDiscoverRequest(
                tenant_id="tenant_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _member_user(),
        )

        assert response.success is True
        names = {tool.name for tool in response.tools}
        assert {"echo", "sum", "product_lookup"} <= names
        echo = next(tool for tool in response.tools if tool.name == "echo")
        assert echo.input_schema["properties"]["text"]["type"] == "string"


def test_discover_stdio_mcp_server_lists_tools() -> None:
    """验证通过 stdio 子进程启动的 MCP 服务也能被发现并列出工具。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        response = discover_mcp_tools_adhoc(
            MCPDiscoverRequest(
                tenant_id="tenant_demo",
                connection=MCPServerConnection(
                    transport="stdio",
                    command=sys.executable,
                    args=[str(_mock_mcp_server_path())],
                ),
            ),
            db,
            _member_user(),
        )

        assert response.success is True
        names = {tool.name for tool in response.tools}
        assert {"echo", "sum", "product_lookup"} <= names


def test_mcp_apps_negotiation_preserves_metadata_and_resource() -> None:
    """验证 MCP Apps 协商后保留 app 元数据、只读注解与工具结果 meta，并能读取资源。"""
    config = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_mock_mcp_apps_server_path())],
        "apps_mode": "auto",
    }

    discovery = discover_mcp_server(config)
    tool = discovery["tools"][0]
    assert "io.modelcontextprotocol/ui" in discovery["server_capabilities"]["extensions"]
    assert tool["app"] == {
        "resource_uri": "ui://staffdeck/demo-card",
        "visibility": ["model", "app"],
    }
    assert tool["annotations"]["readOnlyHint"] is True

    result = execute_mcp_tool_result(config, {"message": "hello"}, tool_name="render_card")
    assert result["data"] == {"message": "hello"}
    assert result["meta"] == {"ui": {"render": True}}

    resource = read_mcp_resource(config, "ui://staffdeck/demo-card")
    assert resource["contents"][0]["mimeType"] == "text/html;profile=mcp-app"


def test_mcp_apps_capability_is_not_advertised_when_disabled() -> None:
    """验证关闭 apps_mode 时不会对外声明 MCP Apps 扩展能力。"""
    discovery = discover_mcp_server(
        {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(_mock_mcp_apps_server_path())],
            "apps_mode": "disabled",
        }
    )

    assert discovery["server_capabilities"]["extensions"] == {}


def test_mcp_app_resource_limit_is_ten_mib() -> None:
    """验证 MCP App 资源上限为 10 MiB，超限时抛出含 10 MiB 提示的客户端错误。"""
    assert MCP_APP_RESOURCE_MAX_BYTES == 10 * 1024 * 1024
    accepted = "x" * (2 * 1024 * 1024 + 1)
    text, _meta = _extract_app_resource(
        {
            "contents": [
                {
                    "uri": "ui://staffdeck/large-card",
                    "mimeType": "text/html;profile=mcp-app",
                    "text": accepted,
                }
            ]
        },
        "ui://staffdeck/large-card",
    )
    assert text == accepted

    with pytest.raises(MCPClientError, match="10 MiB"):
        _extract_app_resource(
            {
                "contents": [
                    {
                        "uri": "ui://staffdeck/too-large",
                        "mimeType": "text/html;profile=mcp-app",
                        "text": "x" * (MCP_APP_RESOURCE_MAX_BYTES + 1),
                    }
                ]
            },
            "ui://staffdeck/too-large",
        )


def test_synced_mcp_app_renders_and_calls_read_only_tool() -> None:
    """验证同步后的 MCP App 工具可渲染卡片资源，并能在应用内调用只读工具。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_overall",
                tenant_id="tenant_demo",
                name="整体智能体",
                is_overall=True,
            )
        )
        db.commit()
        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="apps_demo",
                connection=MCPServerConnection(
                    transport="stdio",
                    command=sys.executable,
                    args=[str(_mock_mcp_apps_server_path())],
                ),
                apps_mode="auto",
            ),
            db,
            _admin_user(),
        )
        discovery = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )
        assert discovery.success is True
        assert discovery.tools[0].app is not None
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["render_card"]),
            db,
            current_user=_admin_user(),
        )

        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="apps_demo.render_card", arguments={"message": "hello"}),
            agent_id="agent_overall",
            session_id="session_demo",
        )
        assert result.success is True
        assert result.mcp_app is not None
        assert result.mcp_app.resource_uri == "ui://staffdeck/demo-card"
        assert result.mcp_metadata == {"ui": {"render": True}}

        resource = get_mcp_app_resource(
            server.id,
            "tenant_demo",
            "ui://staffdeck/demo-card",
            "agent_overall",
            db,
            _admin_user(),
        )
        assert "Demo App" in resource.text
        assert resource.meta["ui"]["permissions"] == ["clipboard-write"]

        app_call = call_mcp_app_tool(
            server.id,
            MCPAppToolCallRequest(
                tenant_id="tenant_demo",
                tool_name="render_card",
                arguments={"message": "from app"},
                agent_id="agent_overall",
            ),
            db,
            _admin_user(),
        )
        assert app_call.success is True
        assert app_call.result is not None
        assert app_call.result.data == {"message": "from app"}
        assert app_call.result.mcp_app is None


def test_mcp_app_side_effect_call_requires_confirmation() -> None:
    """验证可能产生副作用且未标注只读的应用内工具调用会要求用户确认。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_overall",
                tenant_id="tenant_demo",
                name="整体智能体",
                is_overall=True,
            )
        )
        server = MCPServer(
            id="server_apps_write",
            tenant_id="tenant_demo",
            name="apps_write",
            transport="stdio",
            command=sys.executable,
            args_json=[str(_mock_mcp_apps_server_path())],
            apps_mode="auto",
            enabled=True,
        )
        db.add(server)
        tool = Tool(
            id="tool_apps_write",
            tenant_id="tenant_demo",
            name="apps_write.render_card",
            display_name="render_card",
            tool_type="mcp",
            method="POST",
            url="mcp://apps_write/render_card",
            mcp_server_id=server.id,
            config_json={
                "tool": "render_card",
                "mcp_apps": {
                    "resource_uri": "ui://staffdeck/demo-card",
                    "visibility": ["model", "app"],
                },
                "mcp_annotations": {},
            },
            enabled=True,
        )
        db.add(tool)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_overall",
                resource_type="tool",
                resource_id=tool.id,
                status="active",
            )
        )
        db.commit()

        response = call_mcp_app_tool(
            server.id,
            MCPAppToolCallRequest(
                tenant_id="tenant_demo",
                tool_name="render_card",
                arguments={"message": "write"},
                agent_id="agent_overall",
            ),
            db,
            _admin_user(),
        )

        assert response.success is False
        assert response.requires_confirmation is True


def test_mcp_app_sop_specific_call_requires_active_sop() -> None:
    """验证 SOP 专属工具在未处于对应 SOP 上下文时调用会返回 403。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_overall",
                tenant_id="tenant_demo",
                name="整体智能体",
                is_overall=True,
            )
        )
        server = MCPServer(
            id="server_apps_sop",
            tenant_id="tenant_demo",
            name="apps_sop",
            transport="stdio",
            command=sys.executable,
            args_json=[str(_mock_mcp_apps_server_path())],
            apps_mode="auto",
            enabled=True,
        )
        db.add(server)
        tool = Tool(
            id="tool_apps_sop",
            tenant_id="tenant_demo",
            name="apps_sop.render_card",
            display_name="render_card",
            tool_type="mcp",
            method="POST",
            url="mcp://apps_sop/render_card",
            mcp_server_id=server.id,
            config_json={
                "tool": "render_card",
                "mcp_apps": {
                    "resource_uri": "ui://staffdeck/demo-card",
                    "visibility": ["model", "app"],
                },
                "mcp_annotations": {"readOnlyHint": True},
            },
            capability_scope="sop_specific",
            allowed_skills_json=["skill_allowed"],
            enabled=True,
        )
        db.add(tool)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_overall",
                resource_type="tool",
                resource_id=tool.id,
                status="active",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            call_mcp_app_tool(
                server.id,
                MCPAppToolCallRequest(
                    tenant_id="tenant_demo",
                    tool_name="render_card",
                    arguments={"message": "read"},
                    agent_id="agent_overall",
                ),
                db,
                _admin_user(),
            )

        assert exc_info.value.status_code == 403


def test_sync_mcp_tools_imports_tools_and_executes() -> None:
    """验证同步会导入指定工具、建立广场绑定，并能通过工具执行器成功调用。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                display_name="内置 Demo MCP",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        sync = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        assert sync.success is True
        assert sync.imported == ["echo"]

        tools = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()
        assert len(tools) == 1
        imported = tools[0]
        assert imported.name == "builtin_demo.echo"
        assert imported.tool_type == "mcp"
        assert imported.config_json == {"tool": "echo"}
        assert imported.input_schema["properties"]["text"]["type"] == "string"
        # display_name 应为工具名（leaf），不能是描述文本（否则列表里名字/描述会叠加）。
        assert imported.display_name == "echo"
        assert imported.description and imported.description != imported.display_name
        # 同步的工具应建立 open gallery 绑定，才能在工具广场列表中可见。
        binding = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == "tenant_demo",
                AgentResourceBinding.resource_type == "tool",
                AgentResourceBinding.resource_id == imported.id,
            )
        ).first()
        assert binding is not None
        # 端到端：工具广场列表应能查到这个同步进来的工具。
        listed = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_overall", db=db)
        assert any(item.name == "builtin_demo.echo" for item in listed)

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="builtin_demo.echo", arguments={"text": "hi"}),
        )
        assert result.success is True
        assert result.data == {"text": "hi", "length": 2}


def test_disabled_mcp_server_blocks_imported_tool_execution() -> None:
    """验证 MCP 服务被禁用后，其已导入工具的执行会返回 MCP_ERROR。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        server = MCPServer(
            id="server_disabled",
            tenant_id="tenant_demo",
            name="disabled",
            transport="builtin",
            enabled=False,
        )
        db.add(server)
        db.add(
            Tool(
                id="tool_disabled_server",
                tenant_id="tenant_demo",
                name="disabled.echo",
                tool_type="mcp",
                method="POST",
                url="mcp://disabled/echo",
                mcp_server_id=server.id,
                config_json={"tool": "echo"},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(
                name="disabled.echo",
                arguments={"text": "must-not-run"},
            ),
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "MCP_ERROR"


def test_sync_mcp_tools_preserves_execution_policy() -> None:
    """验证重复同步不会覆盖工具上已配置的执行策略（如超时时间）。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        server = MCPServer(
            id="server_builtin_policy",
            tenant_id="tenant_demo",
            name="builtin-policy",
            transport="builtin",
        )
        db.add(server)
        db.add(
            Tool(
                id="tool_policy",
                tenant_id="tenant_demo",
                name="mcp.builtin-policy.echo",
                tool_type="mcp",
                method="POST",
                url="mcp://builtin-policy/echo",
                mcp_server_id=server.id,
                config_json={"tool": "echo", "execution": {"timeout_seconds": 20}},
            )
        )
        db.commit()

        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        tool = db.get(Tool, "tool_policy")
        assert tool is not None
        assert tool.config_json == {"tool": "echo", "execution": {"timeout_seconds": 20}}


def test_sync_mcp_tools_scoped_to_employee_binds_privately() -> None:
    """验证在员工范围内同步只建立私有绑定，工具对该员工可见但不进入工具广场。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        sync = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )
        assert sync.success is True
        assert sync.imported == ["echo"]

        imported = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).first()
        assert imported is not None

        # 员工范围内同步应建立私有绑定，工具只对该员工可见，不出现在工具广场。
        employee_tools = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db)
        assert any(item.id == imported.id for item in employee_tools)

        plaza_tools = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_overall", db=db)
        assert all(item.id != imported.id for item in plaza_tools)


def test_sync_is_idempotent_and_updates_schema() -> None:
    """验证重复同步具备幂等性：首次导入全部工具，再次同步只更新不新增。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        first = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo"),
            db,
            current_user=_admin_user(),
        )
        assert first.success is True
        assert len(first.imported) == 3

        second = sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo"),
            db,
            current_user=_admin_user(),
        )
        assert second.success is True
        assert second.imported == []
        assert set(second.updated) == {"echo", "sum", "product_lookup"}

        tools = db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()
        assert len(tools) == 3


def test_discover_saved_server_marks_imported() -> None:
    """验证发现已保存的服务时会标记哪些工具已导入，并返回对应 tool_id。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_overall",
                tenant_id="tenant_demo",
                name="整体智能体",
                is_overall=True,
            )
        )
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        response = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
        )

        assert response.success is True
        by_name = {tool.name: tool for tool in response.tools}
        assert by_name["echo"].imported is True
        assert by_name["echo"].tool_id is not None
        assert by_name["sum"].imported is False


def test_discover_saved_server_marks_imported_in_current_employee_scope() -> None:
    """全局存在不等于员工已导入；员工同步后才应出现在 SOP 能力目录。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_employee",
                tenant_id="tenant_demo",
                name="数字员工",
                is_overall=False,
            )
        )
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_request = MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"])
        sync_mcp_tools(server.id, sync_request, db, current_user=_admin_user())

        before = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
            agent_id="agent_employee",
        )
        before_echo = {tool.name: tool for tool in before.tools}["echo"]
        assert before_echo.imported is False
        assert before_echo.tool_id is None
        assert list_tools(
            tenant_id="tenant_demo",
            bucket=None,
            agent_id="agent_employee",
            db=db,
        ) == []

        sync_mcp_tools(
            server.id,
            sync_request,
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )

        after = discover_mcp_tools(
            server.id,
            MCPDiscoverRequest(tenant_id="tenant_demo"),
            db,
            _admin_user(),
            agent_id="agent_employee",
        )
        after_echo = {tool.name: tool for tool in after.tools}["echo"]
        assert after_echo.imported is True
        assert after_echo.tool_id is not None
        visible = list_tools(
            tenant_id="tenant_demo",
            bucket=None,
            agent_id="agent_employee",
            db=db,
        )
        assert [tool.name for tool in visible] == ["builtin_demo.echo"]


def test_delete_mcp_server_removes_tools() -> None:
    """验证租户级删除服务时，服务记录与其导入的工具都会被清除。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            current_user=_admin_user(),
        )

        result = delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id=None,
            remove_tools=True,
            current_user=_admin_user(),
        )

        assert result == {"status": "deleted"}
        assert db.get(MCPServer, server.id) is None
        assert len(db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()) == 0


def test_delete_mcp_server_in_employee_scope_only_unbinds() -> None:
    """验证员工范围删除只解除绑定并返回 hidden，不删除租户级服务与工具。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_mcp_tools(
            server.id,
            MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"]),
            db,
            agent_id="agent_employee",
            current_user=_admin_user(),
        )

        result = delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id="agent_employee",
            remove_tools=True,
            current_user=_admin_user(),
        )

        assert result == {"status": "hidden"}
        # 工具集与工具行都是租户级资产,员工范围内的"移除"不得删除它们
        assert db.get(MCPServer, server.id) is not None
        assert len(db.exec(select(Tool).where(Tool.mcp_server_id == server.id)).all()) == 1
        assert list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db) == []


def test_resync_restores_tools_removed_from_employee() -> None:
    """移除是可逆的:再次同步必须把工具装回来,否则私有同步的工具会永久失联。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )
        sync_request = MCPSyncRequest(tenant_id="tenant_demo", tool_names=["echo"])
        sync_mcp_tools(server.id, sync_request, db, agent_id="agent_employee", current_user=_admin_user())
        delete_mcp_server(
            server.id,
            "tenant_demo",
            db,
            agent_id="agent_employee",
            remove_tools=True,
            current_user=_admin_user(),
        )
        assert list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db) == []

        sync_mcp_tools(server.id, sync_request, db, agent_id="agent_employee", current_user=_admin_user())

        restored = list_tools(tenant_id="tenant_demo", bucket=None, agent_id="agent_employee", db=db)
        assert [item.name for item in restored] == ["builtin_demo.echo"]


def test_delete_mcp_server_in_employee_scope_without_tools_returns_404() -> None:
    """验证员工范围下删除未绑定任何工具的服务时返回 404。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_employee", tenant_id="tenant_demo", name="数字员工", is_overall=False))
        db.commit()

        server = create_mcp_server(
            MCPServerCreateRequest(
                tenant_id="tenant_demo",
                name="builtin_demo",
                connection=MCPServerConnection(transport="builtin"),
            ),
            db,
            _admin_user(),
        )

        with pytest.raises(HTTPException) as exc:
            delete_mcp_server(
                server.id,
                "tenant_demo",
                db,
                agent_id="agent_employee",
                remove_tools=True,
                current_user=_admin_user(),
            )
        assert exc.value.status_code == 404


def _mock_mcp_server_path() -> Path:
    """返回测试用 stdio MCP 服务脚本路径，用于启动子进程服务。"""
    return Path(__file__).resolve().parents[1] / "mock_servers" / "mcp_stdio_server.py"


def _mock_mcp_apps_server_path() -> Path:
    """返回测试用 MCP Apps stdio 服务脚本路径，用于验证应用资源与协商。"""
    return Path(__file__).resolve().parents[1] / "mock_servers" / "mcp_apps_stdio_server.py"


def _test_session():
    """创建基于内存 SQLite 的共享连接会话，保证每个用例的数据互相隔离。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
