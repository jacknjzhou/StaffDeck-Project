from __future__ import annotations

import io
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from app.tools.mcp_client import (
    MCPClientError,
    _MCPSession,
    _PipeReader,
    _read_response,
    _send_json,
    _StderrCollector,
    _StdioSession,
    execute_mcp_tool,
    list_mcp_tools,
)


class _PagedToolSession(_MCPSession):
    """模拟带分页游标的 MCP 会话，用于验证工具列表会遍历所有分页。"""
    def __init__(self, *, repeat_cursor: bool = False) -> None:
        """初始化会话并记录是否让第二次分页重复返回同一游标。"""
        super().__init__({}, timeout_seconds=1)
        self.repeat_cursor = repeat_cursor
        self.requests: list[tuple[str, dict[str, object]]] = []

    def _request(self, method: str, params: dict[str, object]):  # type: ignore[override]
        """返回分页工具结果，并记录收到的请求以便断言分页参数。"""
        self.requests.append((method, params))
        if method == "initialize":
            return {"capabilities": {"tools": {"listChanged": True}}}
        if method != "tools/list":
            raise AssertionError(f"unexpected method: {method}")
        if params.get("cursor") is None:
            return {"tools": [{"name": "existing"}], "nextCursor": "page-2"}
        return {
            "tools": [{"name": "newly_added"}],
            "nextCursor": "page-2" if self.repeat_cursor else None,
        }

    def _notify(self, method: str, params: dict[str, object]) -> None:  # type: ignore[override]
        """记录通知请求，保持与真实会话一致的调用痕迹。"""
        self.requests.append((method, params))


class _WindowsAnonymousPipe(io.StringIO):
    """模拟 Windows 匿名管道，fileno 抛错以复现无套接字句柄场景。"""
    def fileno(self) -> int:
        """模拟对非套接字取句柄失败。"""
        raise OSError(10038, "在一个非套接字上尝试了一个操作")


def test_tools_list_discovers_new_tools_from_all_cursor_pages() -> None:
    """验证工具列表会沿 nextCursor 遍历所有分页并合并结果。"""
    session = _PagedToolSession()

    tools, initialize_result = session.list_tools_with_capabilities()

    assert [tool["name"] for tool in tools] == ["existing", "newly_added"]
    assert initialize_result["capabilities"]["tools"]["listChanged"] is True
    assert ("tools/list", {}) in session.requests
    assert ("tools/list", {"cursor": "page-2"}) in session.requests


def test_tools_list_rejects_repeated_cursor_instead_of_looping() -> None:
    """验证分页游标重复出现时报错，而不是陷入无限循环。"""
    with pytest.raises(MCPClientError, match="重复分页游标"):
        _PagedToolSession(repeat_cursor=True).list_tools_with_capabilities()


class _FakeProcess:
    """仅实现 poll/wait 的进程替身，用于模拟子进程退出状态。"""
    def __init__(self, exit_code: int | None = None) -> None:
        """记录进程退出码，None 表示仍在运行。"""
        self.exit_code = exit_code

    def poll(self) -> int | None:
        """返回预设退出码。"""
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        """忽略超时直接返回退出码，未退出时按 0 处理。"""
        del timeout
        return self.exit_code or 0


class _TimeoutReader:
    """总是抛出 TimeoutError 的读取器替身，用于验证读超时处理。"""
    def next_event(self, timeout: float) -> tuple[str, object]:
        """忽略超时参数并立即抛出超时异常。"""
        del timeout
        raise TimeoutError


class _BlockingInput:
    """写入会阻塞的 stdin 替身，用于复现发送请求超时。"""
    def __init__(self) -> None:
        """创建用于放行阻塞写入的事件。"""
        self.release = threading.Event()

    def write(self, value: str) -> int:
        """阻塞到 release 被设置后返回写入长度。"""
        self.release.wait()
        return len(value)

    def flush(self) -> None:
        """空实现，满足 stdin 接口。"""
        return


class _BlockedWriteProcess(_FakeProcess):
    """带阻塞 stdin 的进程替身，用于测试写超时与线程清理。"""
    def __init__(self) -> None:
        """初始化进程替身并挂上阻塞输入流。"""
        super().__init__()
        self.stdin = _BlockingInput()


def test_stdio_response_does_not_use_socket_selector_for_windows_pipe() -> None:
    """验证 Windows 管道读取不依赖 socket selector，仍能正确解析响应。"""
    pipe = _WindowsAnonymousPipe('{"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n')

    response = _read_response(
        _FakeProcess(),  # type: ignore[arg-type]
        _PipeReader(pipe),
        expected_id=7,
        timeout_seconds=1,
    )

    assert response["result"] == {"ok": True}


def test_stdio_response_timeout_is_bounded() -> None:
    """验证读取响应超时会转换为带明确提示的 MCPClientError。"""
    with pytest.raises(MCPClientError, match="等待响应超时"):
        _read_response(
            _FakeProcess(),  # type: ignore[arg-type]
            _TimeoutReader(),  # type: ignore[arg-type]
            expected_id=3,
            timeout_seconds=0.01,
        )


def test_stdio_write_timeout_is_bounded() -> None:
    """验证写入阻塞时会在限定时间内超时报错，而不是永久挂起。"""
    proc = _BlockedWriteProcess()
    try:
        with pytest.raises(MCPClientError, match="发送请求超时"):
            _send_json(
                proc,  # type: ignore[arg-type]
                {"jsonrpc": "2.0", "method": "large", "params": {}},
                timeout_seconds=0.01,
            )
    finally:
        proc.stdin.release.set()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_real_blocked_stdin_timeout_cleans_up_process_and_threads() -> None:
    """验证真实 Node 子进程写超时后会终止进程并回收写线程。"""
    session = _StdioSession(
        {
            "transport": "stdio",
            "command": "node",
            "args": ["-e", "setInterval(() => {}, 1000)"],
        },
        timeout_seconds=0.05,
    )
    proc = None
    started = time.monotonic()

    with pytest.raises(MCPClientError, match="发送请求超时"):
        with session:
            proc = session._require_proc()
            _send_json(
                proc,
                {"jsonrpc": "2.0", "method": "large", "params": {"data": "x" * 2_000_000}},
                timeout_seconds=0.05,
            )

    assert time.monotonic() - started < 2
    assert proc is not None and proc.poll() is not None
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and any(
        thread.name == "mcp-stdin-writer" and thread.is_alive()
        for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert not any(
        thread.name == "mcp-stdin-writer" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_stdio_rejects_oversized_single_response() -> None:
    """验证单条响应超过大小限制时会被拒绝并提示上限。"""
    reader = _PipeReader(io.StringIO("x" * 33), max_line_size=32)

    with pytest.raises(MCPClientError, match="超过 32 字符限制"):
        _read_response(
            _FakeProcess(),  # type: ignore[arg-type]
            reader,
            expected_id=1,
            timeout_seconds=1,
        )


def test_stdio_early_exit_includes_exit_code_and_stderr() -> None:
    """验证子进程提前退出时错误信息包含退出码与 stderr 内容。"""
    stderr = _StderrCollector(io.StringIO("Cannot find module index.js\n"))

    with pytest.raises(MCPClientError) as captured:
        _read_response(
            _FakeProcess(1),  # type: ignore[arg-type]
            _PipeReader(io.StringIO("")),
            expected_id=1,
            timeout_seconds=1,
            stderr=stderr,
        )

    assert "退出码 1" in str(captured.value)
    assert "Cannot find module index.js" in str(captured.value)


def test_stdio_missing_command_has_actionable_error() -> None:
    """验证命令不存在时返回可直接定位问题的错误提示。"""
    with pytest.raises(MCPClientError, match="找不到命令"):
        list_mcp_tools(
            {"transport": "stdio", "command": "staffdeck-command-that-does-not-exist"},
            timeout_seconds=1,
        )


def test_stdio_missing_working_directory_has_actionable_error(tmp_path: Path) -> None:
    """验证工作目录不存在时返回可操作的错误提示。"""
    missing = tmp_path / "missing"

    with pytest.raises(MCPClientError, match="工作目录不存在"):
        list_mcp_tools(
            {"transport": "stdio", "command": sys.executable, "cwd": str(missing)},
            timeout_seconds=1,
        )


def test_stdio_missing_node_entrypoint_has_actionable_error(tmp_path: Path) -> None:
    """验证 Node 入口文件不存在时返回可操作的错误提示。"""
    with pytest.raises(MCPClientError, match="入口文件不存在"):
        list_mcp_tools(
            {
                "transport": "stdio",
                "command": "node",
                "args": ["index.js"],
                "cwd": str(tmp_path),
            },
            timeout_seconds=1,
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_stdio_real_node_process_round_trip() -> None:
    """验证真实 Node 进程下工具发现与调用的完整往返，含中文与表情。"""
    server = textwrap.dedent(
        """
        const readline = require('readline');
        const input = readline.createInterface({ input: process.stdin });
        input.on('line', (line) => {
          const message = JSON.parse(line);
          if (!Object.prototype.hasOwnProperty.call(message, 'id')) return;
          let result;
          if (message.method === 'tools/list') {
            result = { tools: [{ name: 'windows_echo', description: 'Windows 中文回声' }] };
          } else if (message.method === 'tools/call') {
            result = { content: [{ type: 'text', text: JSON.stringify(message.params.arguments) }] };
          } else {
            result = { protocolVersion: '2024-11-05', capabilities: {}, serverInfo: { name: 'smoke', version: '1' } };
          }
          process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }) + '\\n');
        });
        """
    )

    tools = list_mcp_tools(
        {"transport": "stdio", "command": "node", "args": ["-e", server]},
        timeout_seconds=2,
    )

    assert tools == [
        {
            "name": "windows_echo",
            "title": "",
            "description": "Windows 中文回声",
            "input_schema": {},
            "output_schema": {},
            "annotations": {},
            "meta": {},
            "app": None,
        }
    ]

    result = execute_mcp_tool(
        {"transport": "stdio", "command": "node", "args": ["-e", server]},
        {"内容": "你好，Windows 👋"},
        timeout_seconds=2,
        tool_name="windows_echo",
    )

    assert result == {"内容": "你好，Windows 👋"}
