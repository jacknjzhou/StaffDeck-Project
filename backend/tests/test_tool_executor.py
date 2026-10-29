import json
import sys
from pathlib import Path

import httpx
import pytest

from app.agents.branching import ensure_private_resource_binding
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall
from app.db.models import A2ATaskEvent, A2ATaskRun, AgentProfile, MCPServer, Tenant, Tool
from app.security.internal_service import INTERNAL_SERVICE_HEADER, internal_service_token
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


def test_resolve_secret_header(monkeypatch):
    """验证请求头中的 ${secret.X} 占位符会从环境变量解析为明文。"""
    monkeypatch.setenv("ORDER_API_TOKEN", "token-123")
    executor = object.__new__(ToolExecutor)

    headers = executor._resolve_headers(
        {"Authorization": "Bearer ${secret.ORDER_API_TOKEN}"},
        {},
    )

    assert headers["Authorization"] == "Bearer token-123"


def test_resolve_basic_auth_header(monkeypatch):
    """验证 basic 认证配置（含密钥占位符）生成标准的 Basic Authorization 头。"""
    monkeypatch.setenv("TOOL_PASSWORD", "123456")
    executor = object.__new__(ToolExecutor)

    headers = executor._resolve_headers(
        {},
        {
            "type": " basic ",
            "basic": {"username": "admin", "password": "${secret.TOOL_PASSWORD}"},
        },
    )

    assert headers["Authorization"] == "Basic YWRtaW46MTIzNDU2"


def test_explicit_authorization_header_takes_precedence_over_basic_auth():
    """验证显式配置的 Authorization 头优先于 basic 认证配置，不被覆盖。"""
    executor = object.__new__(ToolExecutor)

    headers = executor._resolve_headers(
        {"Authorization": "Custom value"},
        {"type": "basic", "basic": {"username": "admin", "password": "123456"}},
    )

    assert headers["Authorization"] == "Custom value"


def test_unknown_auth_type_is_forwarded_as_literal_headers(monkeypatch):
    """验证未知 auth 类型按字面量透传为请求头，对象值序列化为 JSON 字符串。"""
    monkeypatch.setenv("VENDOR_TOKEN", "token-123")
    executor = object.__new__(ToolExecutor)

    headers = executor._resolve_headers(
        {"Content-Type": "application/json"},
        {
            "X-API-Key": "${secret.VENDOR_TOKEN}",
            "X-Scope": "staff",
            "X-Options": {"region": "cn"},
        },
    )

    assert headers["X-API-Key"] == "token-123"
    assert headers["X-Scope"] == "staff"
    assert headers["X-Options"] == '{"region":"cn"}'


def test_internal_mock_request_adds_service_token_only_for_configured_origin() -> None:
    """验证只有发往本机 mock 服务的请求才附带内部服务令牌。"""
    executor = object.__new__(ToolExecutor)
    executor.settings = type(
        "Settings",
        (),
        {"normalized_tool_base_url": "http://127.0.0.1:5173"},
    )()

    internal = executor._request_headers(
        "http://127.0.0.1:5173/api/mock/order/query",
        {"Content-Type": "application/json"},
    )
    external = executor._request_headers(
        "https://example.test/api/mock/order/query",
        {"Content-Type": "application/json"},
    )

    assert internal[INTERNAL_SERVICE_HEADER] == internal_service_token()
    assert INTERNAL_SERVICE_HEADER not in external


def test_execute_rejects_tool_not_bound_to_current_employee() -> None:
    """验证私有工具只授权给绑定员工，其他员工调用返回 NOT_ALLOWED。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = AgentProfile(id="agent_owner", tenant_id="tenant_demo", name="员工 A")
        other = AgentProfile(id="agent_other", tenant_id="tenant_demo", name="员工 B")
        tool = Tool(
            id="tool_private",
            tenant_id="tenant_demo",
            name="private.lookup",
            method="POST",
            url="https://example.test/private",
            enabled=True,
        )
        db.add(owner)
        db.add(other)
        db.add(tool)
        db.flush()
        ensure_private_resource_binding(db, "tenant_demo", owner.id, "tool", tool.id, "active")
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name=tool.name, arguments={}),
            agent_id=other.id,
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "NOT_ALLOWED"


def test_execute_builtin_mcp_tool_success() -> None:
    """验证内置 MCP 工具被正确路由，并把工具返回值原样作为结果数据。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            MCPServer(
                id="server_builtin", tenant_id="tenant_demo", name="builtin", transport="builtin"
            )
        )
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="mcp.demo_echo",
                display_name="MCP Demo Echo",
                tool_type="mcp",
                method="POST",
                url="mcp://builtin.demo/echo",
                mcp_server_id="server_builtin",
                config_json={"tool": "echo"},
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="mcp.demo_echo", arguments={"text": "hello mcp"}),
        )

        assert result.success is True
        assert result.data == {"text": "hello mcp", "length": 9}


def test_execution_policy_uses_tool_timeout_and_falls_back_for_invalid_values() -> None:
    """验证执行策略优先取工具超时配置，覆盖值生效，非法值回落到全局默认。"""
    executor = object.__new__(ToolExecutor)
    executor.settings = type("Settings", (), {"tool_timeout_seconds": 8.0})()

    configured = Tool(
        tenant_id="tenant_demo",
        name="slow.lookup",
        method="POST",
        url="https://example.test/slow",
        config_json={"execution": {"timeout_seconds": 20}},
    )
    invalid = Tool(
        tenant_id="tenant_demo",
        name="bad.lookup",
        method="POST",
        url="https://example.test/bad",
        config_json={"execution": {"timeout_seconds": 9999}},
    )

    assert executor._execution_policy(configured).timeout_seconds == 20
    assert (
        executor._execution_policy(
            configured,
            timeout_seconds_override=3.5,
        ).timeout_seconds
        == 3.5
    )
    assert executor._execution_policy(invalid).timeout_seconds == 8


def test_execute_http_tool_passes_configured_timeout_to_client(monkeypatch) -> None:
    """验证 HTTP 工具按配置的超时时间构造 httpx client。"""
    captured: dict[str, float] = {}

    class FakeClient:
        """记录构造超时时间的假 httpx client。"""
        def __init__(self, *, timeout: float):
            """保存传入的超时时间，供断言使用。"""
            captured["timeout"] = timeout

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def request(self, method, url, headers=None, json=None, params=None):
            """直接返回固定成功响应，避免真实网络请求。"""
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="slow.lookup",
                method="POST",
                url="https://example.test/slow",
                config_json={"execution": {"timeout_seconds": 20}},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo", ToolCall(name="slow.lookup", arguments={})
        )

    assert result.success is True
    assert captured["timeout"] == 20


def test_execute_a2a_tool_sends_standard_send_message_request(monkeypatch) -> None:
    """验证 A2A 工具按 JSON-RPC 发送 SendMessage，并携带版本头与文本参数。"""
    captured: dict[str, object] = {}

    class FakeClient:
        """记录 A2A 请求的假 httpx client。"""
        def __init__(self, *, timeout: float):
            """记录超时时间，供断言默认执行策略生效。"""
            captured["timeout"] = timeout

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def post(self, url, *, headers=None, json=None):
            """记录请求并返回携带 message 结果的成功响应。"""
            captured.update(url=url, headers=headers, payload=json)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": json["id"], "result": {"kind": "message", "parts": [{"text": "done"}]}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="a2a.finance",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                config_json={
                    "a2a_version": "1.0",
                    "accepted_output_modes": ["text/plain"],
                    "execution": {"timeout_seconds": 25},
                },
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.finance", arguments={"query": "查询报销制度"}),
        )

    assert result.success is True
    assert result.data["state"] == "completed"
    assert result.data["task"] == {"kind": "message", "parts": [{"text": "done"}]}
    assert captured["timeout"] == pytest.approx(25, abs=0.01)
    assert captured["headers"]["A2A-Version"] == "1.0"
    assert captured["payload"]["method"] == "SendMessage"
    assert captured["payload"]["params"]["message"]["parts"] == [{"text": "查询报销制度"}]


def test_execute_a2a_waits_for_working_task_and_persists_lifecycle(monkeypatch) -> None:
    """验证 A2A 任务处于 working 时轮询 GetTask 至完成，并落库任务与生命周期事件。"""
    calls: list[str] = []

    class FakeClient:
        """模拟 A2A 服务端：先返回 working，再返回 completed。"""
        def __init__(self, *, timeout: float):
            """保存客户端超时，保持与真实 client 的接口一致。"""
            self.timeout = timeout

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def get(self, url, *, headers=None):
            """返回声明不支持流式的 agent card。"""
            return httpx.Response(
                200,
                json={
                    "protocolVersion": "1.0",
                    "supportedInterfaces": [
                        {
                            "url": "https://agent.example.test/a2a",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                    "capabilities": {"streaming": False},
                },
                request=httpx.Request("GET", url),
            )

        def post(self, url, *, headers=None, json=None):
            """记录调用方法并按 SendMessage/GetTask 返回不同任务状态。"""
            method = json["method"]
            calls.append(method)
            if method == "SendMessage":
                result = {
                    "id": "remote-task-1",
                    "contextId": "ctx-1",
                    "status": {"state": "working"},
                }
            else:
                result = {
                    "id": "remote-task-1",
                    "contextId": "ctx-1",
                    "status": {
                        "state": "completed",
                        "message": {"parts": [{"text": "done"}]},
                    },
                }
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": json["id"], "result": result},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                id="tool_a2a_wait",
                tenant_id="tenant_demo",
                name="a2a.wait",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                config_json={"poll_interval_seconds": 0.001},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.wait", arguments={"query": "run"}),
            session_id="session-1",
        )
        runs = list(db.exec(select(A2ATaskRun)).all())
        events = list(db.exec(select(A2ATaskEvent)).all())

    assert result.success is True
    assert result.data["state"] == "completed"
    assert result.data["task_id"] == "remote-task-1"
    assert calls == ["SendMessage", "GetTask"]
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert {event.event_type for event in events} >= {
        "submitted",
        "message_result",
        "task_polled",
        "completed",
    }


def test_execute_a2a_same_invocation_returns_persisted_result_without_resending(
    monkeypatch,
) -> None:
    """验证同一 invocation_id 重复调用复用已持久化结果，不再重发 SendMessage。"""
    calls: list[str] = []

    class FakeClient:
        """记录 A2A 调用方法的假 httpx client。"""
        def __init__(self, *, timeout: float):
            """忽略超时参数，仅保持接口一致。"""

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def get(self, url, *, headers=None):
            """模拟 agent card 获取失败，走无发现的分支。"""
            raise RuntimeError("no agent card")

        def post(self, url, *, headers=None, json=None):
            """记录方法名并返回已完成的任务结果。"""
            calls.append(json["method"])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {
                        "id": "remote-idempotent",
                        "status": {"state": "completed"},
                    },
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                id="tool_a2a_idempotent",
                tenant_id="tenant_demo",
                name="a2a.idempotent",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                enabled=True,
            )
        )
        db.commit()

        first = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.idempotent", arguments={"query": "run"}),
            invocation_id="call-1",
        )
        second = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.idempotent", arguments={"query": "run"}),
            invocation_id="call-1",
        )
        runs = list(db.exec(select(A2ATaskRun)).all())

    assert first.success is True
    assert second.success is True
    assert second.data == first.data
    assert calls == ["SendMessage"]
    assert len(runs) == 1


def test_execute_a2a_same_invocation_resumes_remote_working_task(monkeypatch) -> None:
    """验证同一 invocation_id 遇到已存在的 working 任务时改走 GetTask 恢复而非重发。"""
    calls: list[str] = []

    class FakeClient:
        """只记录 A2A 调用方法的假 httpx client。"""
        def __init__(self, *, timeout: float):
            """忽略超时参数，仅保持接口一致。"""

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def post(self, url, *, headers=None, json=None):
            """记录方法名并返回已完成的任务结果。"""
            calls.append(json["method"])
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {
                        "id": "remote-recover",
                        "contextId": "context-recover",
                        "status": {"state": "completed"},
                    },
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                id="tool_a2a_recover",
                tenant_id="tenant_demo",
                name="a2a.recover",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                config_json={"discover_agent_card": False, "poll_interval_seconds": 0.001},
                enabled=True,
            )
        )
        db.add(
            A2ATaskRun(
                id="a2arun_recover",
                direction="client",
                tenant_id="tenant_demo",
                tool_id="tool_a2a_recover",
                invocation_id="call-recover",
                endpoint_url="https://agent.example.test/a2a",
                remote_task_id="remote-recover",
                context_id="context-recover",
                status="working",
                request_json={
                    "arguments": {"query": "run"},
                    "message": {
                        "messageId": "message-recover",
                        "role": "ROLE_USER",
                        "parts": [{"text": "run"}],
                    },
                },
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.recover", arguments={"query": "run"}),
            invocation_id="call-recover",
        )
        recovered = db.get(A2ATaskRun, "a2arun_recover")

    assert result.success is True
    assert calls == ["GetTask"]
    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.recovery_attempts == 1


def test_execute_a2a_continues_input_required_task_in_same_session(monkeypatch) -> None:
    """验证上一轮返回 input-required 时，同会话下一轮携带 taskId 与 contextId 续跑。"""
    messages: list[dict[str, object]] = []

    class FakeClient:
        """模拟 A2A 服务端：首次要求补充输入，其后返回完成。"""
        def __init__(self, *, timeout: float):
            """忽略超时参数，仅保持接口一致。"""

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def get(self, url, *, headers=None):
            """模拟 agent card 获取失败，走无发现的分支。"""
            raise RuntimeError("no agent card")

        def post(self, url, *, headers=None, json=None):
            """记录消息并按调用次数返回 input-required 或 completed。"""
            message = json["params"]["message"]
            messages.append(message)
            first = len(messages) == 1
            result = {
                "id": "remote-task-input",
                "contextId": "ctx-input",
                "status": {
                    "state": "input-required" if first else "completed",
                    "message": {
                        "parts": [{"text": "请提供姓名" if first else "张三已查询"}]
                    },
                },
            }
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": json["id"], "result": result},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                id="tool_a2a_continue",
                tenant_id="tenant_demo",
                name="a2a.continue",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                enabled=True,
            )
        )
        db.commit()

        first = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.continue", arguments={"query": "查询员工"}),
            session_id="session-continue",
        )
        second = ToolExecutor(db).execute(
            "tenant_demo",
            ToolCall(name="a2a.continue", arguments={"query": "张三"}),
            session_id="session-continue",
        )

    assert first.success is True
    assert first.data["awaiting_input"] is True
    assert second.success is True
    assert second.data["state"] == "completed"
    assert messages[1]["taskId"] == "remote-task-input"
    assert messages[1]["contextId"] == "ctx-input"


def test_execute_a2a_stream_merges_artifact_before_terminal_status(monkeypatch) -> None:
    """验证 SSE 流中先到的 artifact 更新不会被终态覆盖丢弃。"""
    class FakeStreamResponse:
        """模拟 A2A SSE 流式响应，依次吐出 artifact 与终态事件。"""
        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def raise_for_status(self):
            """模拟状态码检查通过。"""
            return

        def iter_lines(self):
            """按 SSE 格式输出 artifactUpdate 与 statusUpdate 两条事件。"""
            artifact = {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "artifactUpdate": {
                        "taskId": "stream-task",
                        "contextId": "stream-context",
                        "artifact": {
                            "artifactId": "report",
                            "parts": [{"file": {"name": "report.txt", "bytes": "aGVsbG8="}}],
                        },
                    }
                },
            }
            completed = {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "statusUpdate": {
                        "taskId": "stream-task",
                        "contextId": "stream-context",
                        "status": {"state": "completed"},
                        "final": True,
                    }
                },
            }
            return iter(
                [
                    "id: 1",
                    f"data: {json.dumps(artifact)}",
                    "",
                    "id: 2",
                    f"data: {json.dumps(completed)}",
                    "",
                ]
            )

    class FakeClient:
        """声明支持流式并返回假 SSE 流的 httpx client 替身。"""
        def __init__(self, *, timeout: float):
            """忽略超时参数，仅保持接口一致。"""

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def get(self, url, *, headers=None):
            """返回声明支持流式的 agent card。"""
            return httpx.Response(
                200,
                json={"capabilities": {"streaming": True}},
                request=httpx.Request("GET", url),
            )

        def stream(self, method, url, *, headers=None, json=None):
            """返回预先编排好的 SSE 流式响应。"""
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="a2a.stream",
                tool_type="a2a",
                method="POST",
                url="https://agent.example.test/a2a",
                enabled=True,
            )
        )
        db.commit()
        result = ToolExecutor(db).execute(
            "tenant_demo", ToolCall(name="a2a.stream", arguments={"query": "report"})
        )

    assert result.success is True
    assert result.data["state"] == "completed"
    assert result.data["artifacts"][0]["artifactId"] == "report"


def test_execute_mcp_tool_passes_configured_timeout(monkeypatch) -> None:
    """验证 MCP 工具执行时把工具配置的超时时间传给底层调用。"""
    captured: dict[str, float] = {}

    def fake_execute(config, arguments, *, timeout_seconds, tool_name):
        """记录超时时间并返回成功结果，替代真实 MCP 调用。"""
        captured["timeout"] = timeout_seconds
        return {"ok": True}

    monkeypatch.setattr("app.tools.tool_executor.execute_mcp_tool", fake_execute)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            MCPServer(
                id="server_builtin_timeout",
                tenant_id="tenant_demo",
                name="builtin-timeout",
                transport="builtin",
            )
        )
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="mcp.timeout.echo",
                tool_type="mcp",
                method="POST",
                url="mcp://builtin.demo/echo",
                mcp_server_id="server_builtin_timeout",
                config_json={
                    "tool": "echo",
                    "execution": {"timeout_seconds": 20},
                },
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            "tenant_demo", ToolCall(name="mcp.timeout.echo", arguments={"text": "hi"})
        )

    assert result.success is True
    assert captured["timeout"] == 20


def test_execute_builtin_mcp_tool_unknown_config_returns_error() -> None:
    """验证内置 MCP 工具引用未注册的工具名时返回 MCP_ERROR。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            MCPServer(
                id="server_builtin", tenant_id="tenant_demo", name="builtin", transport="builtin"
            )
        )
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="mcp.bad",
                display_name="Bad MCP",
                tool_type="mcp",
                method="POST",
                url="mcp://builtin.demo/missing",
                mcp_server_id="server_builtin",
                config_json={"tool": "missing"},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="mcp.bad", arguments={}),
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "MCP_ERROR"


def test_execute_stdio_mcp_tool_success() -> None:
    """验证 stdio 传输的 MCP 工具能通过子进程完成真实调用并返回结果。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            MCPServer(
                id="server_stdio",
                tenant_id="tenant_demo",
                name="stdio",
                transport="stdio",
                command=sys.executable,
                args_json=[str(_mock_mcp_server_path())],
            )
        )
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="mcp.real_echo",
                display_name="Real MCP Echo",
                tool_type="mcp",
                method="POST",
                url="mcp://stdio/mock/echo",
                mcp_server_id="server_stdio",
                config_json={"tool": "echo"},
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="mcp.real_echo", arguments={"text": "hello real mcp"}),
        )

        assert result.success is True
        assert result.data == {"text": "hello real mcp", "length": 14}


def test_execute_stdio_mcp_tool_error_is_stable() -> None:
    """验证 stdio MCP 工具的参数校验错误以 MCP_ERROR 稳定返回并保留原始信息。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            MCPServer(
                id="server_stdio",
                tenant_id="tenant_demo",
                name="stdio",
                transport="stdio",
                command=sys.executable,
                args_json=[str(_mock_mcp_server_path())],
            )
        )
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="mcp.real_sum",
                display_name="Real MCP Sum",
                tool_type="mcp",
                method="POST",
                url="mcp://stdio/mock/sum",
                mcp_server_id="server_stdio",
                config_json={"tool": "sum"},
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="mcp.real_sum", arguments={"numbers": ["bad"]}),
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "MCP_ERROR"
        assert "numbers" in result.error.message


def test_execute_get_tool_preserves_query_string_when_arguments_empty(monkeypatch) -> None:
    """验证 GET 工具在参数为空时保留 url 自带的查询串，不额外生成 params。"""
    requested: dict[str, object] = {}

    class FakeClient:
        """记录 GET 请求细节的假 httpx client。"""
        def __init__(self, *args, **kwargs):
            """忽略构造参数，仅保持接口一致。"""

        def __enter__(self):
            """上下文入口，直接返回自身。"""
            return self

        def __exit__(self, *args):
            """上下文出口，不吞异常。"""
            return

        def request(self, method, url, headers=None, json=None, params=None):
            """记录请求方法、URL 与 params，并返回固定天气数据。"""
            requested.update({"method": method, "url": url, "params": params})
            return httpx.Response(
                200,
                json={"current": {"temperature_2m": 27.4}},
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            Tool(
                tenant_id="tenant_demo",
                name="weather.forecast",
                display_name="天气查询",
                method="GET",
                url=(
                    "https://api.open-meteo.com/v1/forecast"
                    "?latitude=39.90&longitude=116.40&current=temperature_2m"
                ),
                enabled=True,
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="weather.forecast", arguments={}),
        )

    assert result.success is True
    assert result.data == {"current": {"temperature_2m": 27.4}}
    assert requested == {
        "method": "GET",
        "url": (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=39.90&longitude=116.40&current=temperature_2m"
        ),
        "params": None,
    }


def _mock_mcp_server_path() -> Path:
    """返回仓库内置 mock MCP stdio 服务脚本的绝对路径。"""
    return Path(__file__).resolve().parents[1] / "mock_servers" / "mcp_stdio_server.py"


def _test_session():
    """创建带全量建表的内存 SQLite 会话，作为各用例隔离的数据库环境。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
