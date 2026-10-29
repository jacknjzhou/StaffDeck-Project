from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

import httpx

from app.security.managed_subprocess import ManagedProcess, ManagedProcessError
from app.tools.mcp_builtin import (
    BuiltinMCPError,
    builtin_mcp_tool_definitions,
    execute_builtin_mcp,
)


class MCPClientError(RuntimeError):
    """MCP 连接、握手或调用失败时抛出的统一异常，调用方据此提示连接或协议问题。"""



MCP_APPS_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"


# --------------------------------------------------------------------------- #
# Transport 归一化
# --------------------------------------------------------------------------- #

def normalize_transport(config: dict[str, Any]) -> str:
    """从连接配置推断 transport 类型。

    优先使用显式 transport 字段；否则根据 server/command/url 推断，
    以兼容历史配置。streamable_http 归一化为 http。
    """
    raw = str(config.get("transport") or "").strip().lower()
    if raw == "streamable_http":
        return "http"
    if raw:
        return raw
    server = str(config.get("server") or config.get("server_id") or "").strip()
    if server == "builtin.demo":
        return "builtin"
    if config.get("command"):
        return "stdio"
    if config.get("url") or config.get("endpoint"):
        return "http"
    return "builtin"


# --------------------------------------------------------------------------- #
# 对外入口：调用工具 / 列举工具
# --------------------------------------------------------------------------- #

def execute_mcp_tool(
    config: dict[str, Any],
    arguments: dict[str, Any],
    timeout_seconds: float = 10,
    tool_name: str | None = None,
) -> Any:
    """连接 MCP server 并调用单个工具。

    config 是「server 连接配置」（transport/url/command/headers 等）。
    tool_name 若显式传入则优先使用，否则回退到 config 里的 tool 字段
    （兼容历史「一个 config 一个 tool」的形态）。
    """
    return execute_mcp_tool_result(
        config,
        arguments,
        timeout_seconds=timeout_seconds,
        tool_name=tool_name,
    )["data"]


def execute_mcp_tool_result(
    config: dict[str, Any],
    arguments: dict[str, Any],
    timeout_seconds: float = 10,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """调用 MCP 工具，并保留 Apps 与结构化结果相关的元数据。

    ``execute_mcp_tool`` 仍是兼容入口，只返回原先提取的 payload；
    需要 Apps 信息的调用方改用本函数返回的信封。
    """

    normalized = dict(config or {})
    transport = normalize_transport(normalized)
    name = _resolve_tool_name(normalized, tool_name)
    if transport == "builtin":
        try:
            data = execute_builtin_mcp({**normalized, "tool": name}, arguments)
        except BuiltinMCPError as exc:
            raise MCPClientError(str(exc)) from exc
        return {
            "data": data,
            "content": [],
            "structured_content": None,
            "meta": {},
            "is_error": False,
        }
    session: _MCPSession
    if transport == "stdio":
        session = _StdioSession(normalized, timeout_seconds)
    elif transport in {"http", "streamable_http"}:
        session = _HttpSession(normalized, timeout_seconds)
    elif transport == "sse":
        session = _SseSession(normalized, timeout_seconds)
    else:
        raise MCPClientError(f"不支持的 MCP transport：{transport or '<empty>'}")
    return session.call_tool_envelope(name, arguments)


def list_mcp_tools(
    config: dict[str, Any],
    timeout_seconds: float = 10,
) -> list[dict[str, Any]]:
    """连接 MCP server 并通过 tools/list 发现工具列表。

    返回标准化后的工具定义列表，每项包含 name / description /
    input_schema / output_schema（若 server 提供）。
    """
    return discover_mcp_server(config, timeout_seconds=timeout_seconds)["tools"]


def discover_mcp_server(
    config: dict[str, Any],
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    """发现工具列表，并保留 initialize 响应里的服务端 capabilities。"""

    normalized = dict(config or {})
    transport = normalize_transport(normalized)

    if transport == "builtin":
        try:
            raw = builtin_mcp_tool_definitions(normalized)
        except BuiltinMCPError as exc:
            raise MCPClientError(str(exc)) from exc
    elif transport == "stdio":
        session = _StdioSession(normalized, timeout_seconds)
        raw, initialize_result = session.list_tools_with_capabilities()
    elif transport in {"http", "streamable_http"}:
        session = _HttpSession(normalized, timeout_seconds)
        raw, initialize_result = session.list_tools_with_capabilities()
    elif transport == "sse":
        session = _SseSession(normalized, timeout_seconds)
        raw, initialize_result = session.list_tools_with_capabilities()
    else:
        raise MCPClientError(f"不支持的 MCP transport：{transport or '<empty>'}")
    if transport == "builtin":
        initialize_result = {}
    return {
        "tools": [_normalize_tool_definition(item) for item in raw if isinstance(item, dict)],
        "server_capabilities": (
            initialize_result.get("capabilities", {})
            if isinstance(initialize_result, dict)
            else {}
        ),
        "server_info": (
            initialize_result.get("serverInfo", {})
            if isinstance(initialize_result, dict)
            else {}
        ),
    }


def read_mcp_resource(
    config: dict[str, Any],
    uri: str,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    """读取 MCP App 声明的资源内容（resources/read）。

    内置演示 transport 不提供资源，调用即抛 MCPClientError。
    """
    normalized = dict(config or {})
    transport = normalize_transport(normalized)
    if transport == "builtin":
        raise MCPClientError("内置演示 MCP 不提供 App 资源。")
    if transport == "stdio":
        session: _MCPSession = _StdioSession(normalized, timeout_seconds)
    elif transport in {"http", "streamable_http"}:
        session = _HttpSession(normalized, timeout_seconds)
    elif transport == "sse":
        session = _SseSession(normalized, timeout_seconds)
    else:
        raise MCPClientError(f"不支持的 MCP transport：{transport or '<empty>'}")
    return session.read_resource(uri)


def _resolve_tool_name(config: dict[str, Any], override: str | None) -> str:
    """确定本次调用的工具名：显式 override 优先，其次取 config 里的 tool、tool_name 或 name。

    都为空时抛 MCPClientError，避免向服务端发出无名调用。
    """
    name = str(override or config.get("tool") or config.get("tool_name") or config.get("name") or "").strip()
    if not name:
        raise MCPClientError("MCP 调用缺少 tool 名称。")
    return name


def _normalize_tool_definition(item: dict[str, Any]) -> dict[str, Any]:
    """把原始 tools/list 条目归一化为内部统一的工具定义结构。

    同时从 _meta.ui 提取 app 字段（resource_uri 与 visibility），缺失或类型不符时回退默认值。
    """
    input_schema = item.get("inputSchema") or item.get("input_schema") or {}
    output_schema = item.get("outputSchema") or item.get("output_schema") or {}
    meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    resource_uri = str(
        ui.get("resourceUri") or meta.get("ui/resourceUri") or ""
    ).strip()
    visibility = ui.get("visibility") or meta.get("ui/visibility") or ["model", "app"]
    if not isinstance(visibility, list):
        visibility = ["model", "app"]
    return {
        "name": str(item.get("name") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "input_schema": input_schema if isinstance(input_schema, dict) else {},
        "output_schema": output_schema if isinstance(output_schema, dict) else {},
        "annotations": item.get("annotations") if isinstance(item.get("annotations"), dict) else {},
        "meta": dict(meta),
        "app": (
            {
                "resource_uri": resource_uri,
                "visibility": [
                    value for value in (str(item).strip() for item in visibility) if value
                ],
            }
            if resource_uri
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# JSON-RPC 会话基类
# --------------------------------------------------------------------------- #

class _MCPSession:
    """封装一次 MCP 连接的 initialize + list/call 交互。

    子类实现 `_request`（单次 JSON-RPC 请求/响应）和资源管理。
    """

    def __init__(self, config: dict[str, Any], timeout_seconds: float) -> None:
        """保存连接配置与超时时间，initialize 结果留待握手时回填。"""
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.initialize_result: dict[str, Any] = {}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用工具并只返回 data，兼容不关心 Apps 元数据的调用方。"""
        return self.call_tool_envelope(name, arguments)["data"]

    def call_tool_envelope(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """在会话生命周期内完成握手与 tools/call，返回带 Apps 元数据的结果信封。"""
        with self:
            self._initialize()
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments},
            )
            return _tool_result_envelope(result)

    def list_tools(self) -> list[dict[str, Any]]:
        """发现工具列表，丢弃握手时拿到的服务端 capabilities。"""
        tools, _ = self.list_tools_with_capabilities()
        return tools

    def list_tools_with_capabilities(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """发现工具列表，并一并返回 initialize 得到的服务端 capabilities。"""
        with self:
            self._initialize()
            tools = self._list_all_pages("tools/list", "tools")
            return tools, dict(self.initialize_result)

    def _list_all_pages(self, method: str, result_key: str) -> list[dict[str, Any]]:
        """按 nextCursor 翻页收集 MCP 列表结果。

        服务端可能早期一次返回全部工具、之后才启用分页，所以每次发现都要跟随游标，
        否则新增工具一旦落到第一页之外就永远不可见；游标重复时抛错，避免死循环。
        """

        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor is not None else {}
            result = self._request(method, params)
            if not isinstance(result, dict):
                raise MCPClientError(f"MCP {method} 返回内容不是 object。")
            page = result.get(result_key)
            if page is not None and not isinstance(page, list):
                raise MCPClientError(f"MCP {method} 的 {result_key} 不是数组。")
            items.extend(item for item in (page or []) if isinstance(item, dict))

            raw_cursor = result.get("nextCursor")
            if raw_cursor is None:
                # Tolerate snake_case implementations while keeping the MCP
                # specification's nextCursor as the canonical form.
                raw_cursor = result.get("next_cursor")
            next_cursor = str(raw_cursor).strip() if raw_cursor is not None else ""
            if not next_cursor:
                return items
            if next_cursor in seen_cursors:
                raise MCPClientError(f"MCP {method} 返回了重复分页游标：{next_cursor}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def read_resource(self, uri: str) -> dict[str, Any]:
        """在会话内读取指定 uri 的资源，返回 resources/read 的原始结果。"""
        with self:
            self._initialize()
            result = self._request("resources/read", {"uri": uri})
            if not isinstance(result, dict):
                raise MCPClientError("MCP resources/read 返回内容不是 object。")
            return result

    def _initialize(self) -> None:
        """完成 initialize 握手、记录服务端能力，并补发 initialized 通知。"""
        result = self._request("initialize", _initialize_params(self.config))
        self.initialize_result = dict(result) if isinstance(result, dict) else {}
        self._notify("notifications/initialized", {})

    # 子类实现 ---------------------------------------------------------------
    def __enter__(self) -> "_MCPSession":
        """默认上下文入口，子类可在此建立连接。"""
        return self

    def __exit__(self, *exc: Any) -> None:  # pragma: no cover - default no-op
        """默认上下文出口，不做清理；需要释放资源的子类自行覆盖。"""
        return

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        """子类必须实现的单次 JSON-RPC 请求/响应交互。"""
        raise NotImplementedError

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """子类必须实现的 JSON-RPC 通知发送。"""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# stdio transport
# --------------------------------------------------------------------------- #

class _PipeReader:
    """按行读取文本管道，不依赖 select()，因为 Windows 上的 select() 不接受管道。"""

    def __init__(self, stream: TextIO, max_line_size: int = 4 * 1024 * 1024) -> None:
        """启动后台读线程，把 stdout 的 line/error/eof 事件投递到有界队列。"""
        self._stream = stream
        self._max_line_size = max_line_size
        self._events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=128)
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mcp-stdio-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """读线程主体：逐行读取，超长行直接报错，退出时统一投递 eof 事件。"""
        try:
            while not self._stopped.is_set():
                line = self._stream.readline(self._max_line_size + 1)
                if not line:
                    break
                if len(line) > self._max_line_size:
                    self._put(
                        (
                            "error",
                            MCPClientError(
                                f"MCP stdio 单条响应超过 {self._max_line_size} 字符限制。"
                            ),
                        )
                    )
                    return
                self._put(("line", line))
        except (OSError, ValueError) as exc:
            if not self._stopped.is_set():
                self._put(("error", exc))
        finally:
            self._put(("eof", None))

    def _put(self, event: tuple[str, object]) -> None:
        """向有界队列投递事件；队列满时轮询重试，读取器关闭后放弃。"""
        while not self._stopped.is_set():
            try:
                self._events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue

    def next_event(self, timeout: float) -> tuple[str, object]:
        """取下一个读取事件；超时未取到抛 TimeoutError，由调用方转成业务错误。"""
        try:
            return self._events.get(timeout=max(timeout, 0))
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        """停止读线程并关闭流，最多等待 0.2 秒，避免退出被阻塞。"""
        self._stopped.set()
        with suppress(OSError, ValueError):
            self._stream.close()
        self._thread.join(timeout=0.2)


class _StderrCollector:
    """后台收集子进程 stderr 的有限长度文本，在超时或异常时补充诊断信息。"""

    def __init__(self, stream: TextIO, limit: int = 1000) -> None:
        """启动后台收集线程，最多保留 limit 个字符的 stderr。"""
        self._stream = stream
        self._limit = limit
        self._parts: list[str] = []
        self._length = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="mcp-stderr-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """收集线程主体：逐行累积 stderr，达到上限后丢弃多余内容。"""
        try:
            while chunk := self._stream.readline(256):
                with self._lock:
                    if self._length < self._limit:
                        kept = chunk[: self._limit - self._length]
                        self._parts.append(kept)
                        self._length += len(kept)
        except (OSError, ValueError):
            return

    def text(self) -> str:
        """返回已收集的 stderr 文本；为空返回空串，否则带上 stderr 前缀。"""
        with self._lock:
            value = "".join(self._parts).strip()
        return f" stderr: {value}" if value else ""

    def wait(self, timeout: float = 0.1) -> None:
        """最多等待 timeout 秒，让进程退出前写入的 stderr 有机会被读完。"""
        self._thread.join(timeout)

    def close(self) -> None:
        """关闭 stderr 流并回收收集线程。"""
        with suppress(OSError, ValueError):
            self._stream.close()
        self._thread.join(timeout=0.2)


class _StdioSession(_MCPSession):
    """stdio transport 会话：拉起子进程，用换行分隔的 JSON-RPC 消息通信。"""

    def __init__(self, config: dict[str, Any], timeout_seconds: float) -> None:
        """初始化占位字段；子进程要等上下文进入时才真正启动。"""
        super().__init__(config, timeout_seconds)
        self._proc: subprocess.Popen[str] | None = None
        self._managed_process: ManagedProcess | None = None
        self._stdout_reader: _PipeReader | None = None
        self._stderr_collector: _StderrCollector | None = None
        self._next_id = 0

    def __enter__(self) -> "_StdioSession":
        """按配置拉起受控子进程，并挂上 stdout/stderr 读取器。

        启动失败会区分工作目录不存在、命令缺失、权限不足等情况，统一抛 MCPClientError。
        """
        command = _stdio_command(self.config)
        env = os.environ.copy()
        raw_env = self.config.get("env")
        if isinstance(raw_env, Mapping):
            env.update({str(key): str(value) for key, value in raw_env.items()})
        cwd = str(self.config["cwd"]) if self.config.get("cwd") else None
        _validate_stdio_launch(command, cwd)
        try:
            self._managed_process = ManagedProcess.start(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
            )
            self._proc = self._managed_process.process
        except FileNotFoundError as exc:
            if cwd and not os.path.isdir(cwd):
                raise MCPClientError(f"MCP stdio 工作目录不存在：{cwd}") from exc
            raise MCPClientError(
                f"无法启动 MCP stdio：找不到命令 {command[0]!r}，请确认它已安装并在 PATH 中。"
            ) from exc
        except PermissionError as exc:
            raise MCPClientError(f"无法启动 MCP stdio：没有权限执行 {command[0]!r}。") from exc
        except ManagedProcessError as exc:
            raise MCPClientError(f"MCP stdio 受控进程启动失败：{exc}") from exc
        except OSError as exc:
            raise MCPClientError(f"无法启动 MCP stdio 命令 {command[0]!r}：{exc}") from exc
        if self._proc.stdout is None or self._proc.stderr is None:
            self._managed_process.close()
            self._managed_process = None
            self._proc = None
            raise MCPClientError("MCP stdio 进程管道创建失败。")
        self._stdout_reader = _PipeReader(self._proc.stdout)
        self._stderr_collector = _StderrCollector(self._proc.stderr)
        return self

    def __exit__(self, *exc: Any) -> None:
        """关闭受控子进程并回收读写线程，重复调用安全。"""
        if self._managed_process is not None:
            self._managed_process.close()
            self._managed_process = None
        self._proc = None
        if self._stdout_reader is not None:
            self._stdout_reader.close()
        if self._stderr_collector is not None:
            self._stderr_collector.close()
        self._stdout_reader = None
        self._stderr_collector = None

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        """发送一条 JSON-RPC 请求，等待同 id 的响应并返回其中的 result。"""
        proc = self._require_proc()
        self._next_id += 1
        request_id = self._next_id
        timeout = max(self.timeout_seconds, 0.1)
        deadline = time.monotonic() + timeout
        _send_json(
            proc,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout_seconds=timeout,
        )
        response = _read_response(
            proc,
            self._require_stdout_reader(),
            expected_id=request_id,
            timeout_seconds=max(deadline - time.monotonic(), 0),
            stderr=self._stderr_collector,
        )
        _raise_json_rpc_error(response)
        return response.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """发送不带 id 的 JSON-RPC 通知，不等待响应。"""
        proc = self._require_proc()
        _send_json(
            proc,
            {"jsonrpc": "2.0", "method": method, "params": params},
            timeout_seconds=max(self.timeout_seconds, 0.1),
        )

    def _require_proc(self) -> subprocess.Popen[str]:
        """返回已启动的子进程；会话未启动时抛 MCPClientError。"""
        if self._proc is None:
            raise MCPClientError("MCP stdio 会话未启动。")
        return self._proc

    def _require_stdout_reader(self) -> _PipeReader:
        """返回 stdout 读取器；不可用时抛 MCPClientError。"""
        if self._stdout_reader is None:
            raise MCPClientError("MCP stdio stdout 不可用。")
        return self._stdout_reader


def _stdio_command(config: dict[str, Any]) -> list[str]:
    """从配置拼出 stdio 启动命令（command 加 args）。

    command 支持字符串或数组；缺失 command 或 args 不是数组时抛 MCPClientError。
    """
    command = config.get("command")
    args = config.get("args") or []
    if isinstance(command, list):
        parts = [str(part) for part in command]
    elif isinstance(command, str) and command.strip():
        parts = [command.strip()]
    else:
        raise MCPClientError("stdio MCP 连接缺少 command。")
    if not isinstance(args, list):
        raise MCPClientError("stdio MCP 连接的 args 必须是数组。")
    return [*parts, *[str(arg) for arg in args]]


def _validate_stdio_launch(command: list[str], cwd: str | None) -> None:
    """启动前校验工作目录与脚本入口，尽早给出可读错误。

    仅当命令是 node/bun/deno 且首个非选项参数是脚本时，才检查该入口文件是否存在。
    """
    workdir = Path(cwd).expanduser() if cwd else Path.cwd()
    if cwd and not workdir.is_dir():
        raise MCPClientError(f"MCP stdio 工作目录不存在：{workdir}")

    executable = Path(command[0]).name.lower()
    if executable not in {"node", "node.exe", "bun", "bun.exe", "deno", "deno.exe"}:
        return
    entrypoint = next((arg for arg in command[1:] if not arg.startswith("-")), "")
    if not entrypoint or Path(entrypoint).suffix.lower() not in {".js", ".cjs", ".mjs", ".ts"}:
        return
    entrypoint_path = Path(entrypoint).expanduser()
    resolved = entrypoint_path if entrypoint_path.is_absolute() else workdir / entrypoint_path
    if not resolved.is_file():
        raise MCPClientError(
            f"MCP stdio 入口文件不存在：{resolved}。请检查 Args，或将 cwd 设置为入口文件所在目录。"
        )


# --------------------------------------------------------------------------- #
# HTTP (streamable_http) transport
# --------------------------------------------------------------------------- #

class _HttpSession(_MCPSession):
    """streamable HTTP transport 会话：每次请求独立 POST，并透传 Mcp-Session-Id。"""

    def __init__(self, config: dict[str, Any], timeout_seconds: float) -> None:
        """初始化 HTTP 客户端、请求自增 id 与 Mcp-Session-Id 缓存。"""
        super().__init__(config, timeout_seconds)
        self._client: httpx.Client | None = None
        self._next_id = 0
        self._session_id: str | None = None

    def __enter__(self) -> "_HttpSession":
        """创建带超时的 httpx 客户端。"""
        self._client = httpx.Client(timeout=self.timeout_seconds)
        return self

    def __exit__(self, *exc: Any) -> None:
        """关闭 httpx 客户端，忽略关闭过程中的异常。"""
        if self._client is not None:
            with suppress(Exception):
                self._client.close()
            self._client = None

    def _endpoint(self) -> str:
        """返回配置里的 url 或 endpoint；缺失时抛 MCPClientError。"""
        url = str(self.config.get("url") or self.config.get("endpoint") or "").strip()
        if not url:
            raise MCPClientError("HTTP MCP 连接缺少 url/endpoint。")
        return url

    def _headers(self) -> dict[str, str]:
        """构造请求头：默认 JSON 与 SSE 双 Accept，透传自定义头与会话 id。"""
        raw = self.config.get("headers") if isinstance(self.config.get("headers"), dict) else {}
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **{str(k): str(v) for k, v in raw.items()},
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        """POST 一条 JSON-RPC 请求并解析响应，顺带缓存服务端下发的 session id。"""
        client = self._require_client()
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        try:
            response = client.post(self._endpoint(), headers=self._headers(), json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MCPClientError(f"HTTP MCP 返回异常状态码：{exc.response.status_code}") from exc
        except Exception as exc:
            raise MCPClientError(str(exc)) from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        body = _parse_http_mcp_response(response)
        if not isinstance(body, dict):
            raise MCPClientError("HTTP MCP 返回内容不是 JSON-RPC object。")
        _raise_json_rpc_error(body)
        return body.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """尽力发送 JSON-RPC 通知，失败静默忽略，不影响主流程。"""
        client = self._require_client()
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        with suppress(Exception):
            client.post(self._endpoint(), headers=self._headers(), json=payload)

    def _require_client(self) -> httpx.Client:
        """返回已创建的 httpx 客户端；会话未启动时抛 MCPClientError。"""
        if self._client is None:
            raise MCPClientError("HTTP MCP 会话未启动。")
        return self._client


def _parse_http_mcp_response(response: httpx.Response) -> Any:
    """解析 HTTP MCP 响应，兼容纯 JSON 和 SSE 格式（text/event-stream）。"""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        payload = _last_sse_json(response.text)
        if payload is None:
            raise MCPClientError("SSE 响应中未找到有效的 JSON-RPC data 行。")
        return payload
    try:
        return response.json()
    except Exception as exc:
        raise MCPClientError(f"HTTP MCP 响应解析失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# SSE transport
# --------------------------------------------------------------------------- #

class _SseSession(_MCPSession):
    """SSE transport（MCP 2024-11-05 HTTP+SSE）。

    连接流程：GET server url 建立 SSE 流，从首个 `event: endpoint`
    拿到用于发送 JSON-RPC 的消息端点；后续请求 POST 到该端点，
    响应通过 SSE 流按 id 匹配返回。
    """

    def __init__(self, config: dict[str, Any], timeout_seconds: float) -> None:
        """初始化 SSE 流上下文、事件迭代器与消息端点。"""
        super().__init__(config, timeout_seconds)
        self._client: httpx.Client | None = None
        self._stream_ctx: Any = None
        self._events: Any = None
        self._message_url: str | None = None
        self._next_id = 0

    def __enter__(self) -> "_SseSession":
        """建立 SSE 长连接，从 endpoint 事件拿到消息端点后返回。"""
        self._client = httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, read=None))
        url = str(self.config.get("url") or self.config.get("endpoint") or "").strip()
        if not url:
            raise MCPClientError("SSE MCP 连接缺少 url/endpoint。")
        raw = self.config.get("headers") if isinstance(self.config.get("headers"), dict) else {}
        headers = {"Accept": "text/event-stream", **{str(k): str(v) for k, v in raw.items()}}
        self._stream_ctx = self._client.stream("GET", url, headers=headers)
        response = self._stream_ctx.__enter__()
        response.raise_for_status()
        self._events = _iter_sse_events(response)
        self._message_url = self._await_endpoint(url)
        return self

    def __exit__(self, *exc: Any) -> None:
        """退出 SSE 流上下文并关闭客户端。"""
        if self._stream_ctx is not None:
            with suppress(Exception):
                self._stream_ctx.__exit__(*exc)
            self._stream_ctx = None
        if self._client is not None:
            with suppress(Exception):
                self._client.close()
            self._client = None

    def _await_endpoint(self, base_url: str) -> str:
        """等待 endpoint 事件并解析出消息端点；超时或流结束抛 MCPClientError。"""
        deadline = time.monotonic() + max(self.timeout_seconds, 0.1)
        for event, data in self._events:
            if event == "endpoint":
                return _resolve_endpoint(base_url, data.strip())
            if time.monotonic() > deadline:
                break
        raise MCPClientError("SSE MCP 未返回 endpoint 事件。")

    def _post_headers(self) -> dict[str, str]:
        """构造向消息端点 POST 的请求头（JSON 内容类型加自定义头）。"""
        raw = self.config.get("headers") if isinstance(self.config.get("headers"), dict) else {}
        return {"Content-Type": "application/json", **{str(k): str(v) for k, v in raw.items()}}

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        """把 JSON-RPC 请求 POST 到消息端点，再从 SSE 流里按 id 取回响应。"""
        client = self._require_client()
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            posted = client.post(str(self._message_url), headers=self._post_headers(), json=payload)
            posted.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MCPClientError(f"SSE MCP 返回异常状态码：{exc.response.status_code}") from exc
        except Exception as exc:
            raise MCPClientError(str(exc)) from exc
        body = self._await_response(request_id)
        _raise_json_rpc_error(body)
        return body.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """尽力发送 JSON-RPC 通知，失败静默忽略。"""
        client = self._require_client()
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        with suppress(Exception):
            client.post(str(self._message_url), headers=self._post_headers(), json=payload)

    def _await_response(self, expected_id: int) -> dict[str, Any]:
        """在 SSE 流里等待指定 id 的响应报文，超时抛 MCPClientError。"""
        deadline = time.monotonic() + max(self.timeout_seconds, 0.1)
        for event, data in self._events:
            if event in {"message", ""}:
                with suppress(json.JSONDecodeError):
                    payload = json.loads(data)
                    if isinstance(payload, dict) and payload.get("id") == expected_id:
                        return payload
            if time.monotonic() > deadline:
                break
        raise MCPClientError(f"SSE MCP 等待响应超时：id={expected_id}")

    def _require_client(self) -> httpx.Client:
        """返回客户端与消息端点都已就绪的会话；否则抛 MCPClientError。"""
        if self._client is None or self._message_url is None:
            raise MCPClientError("SSE MCP 会话未启动。")
        return self._client


def _iter_sse_events(response: httpx.Response):
    """迭代 SSE 流，逐个 yield (event_type, data)。"""
    event_type = ""
    data_lines: list[str] = []
    for raw_line in response.iter_lines():
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                yield event_type or "message", "\n".join(data_lines)
            event_type = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())


def _resolve_endpoint(base_url: str, endpoint: str) -> str:
    """把 endpoint 事件的值解析成绝对地址：已是绝对 URL 就原样返回，否则基于流地址拼接。"""
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    from urllib.parse import urljoin

    return urljoin(base_url, endpoint)


def _last_sse_json(text: str) -> Any:
    """从 SSE 响应文本里倒序寻找最后一条可解析的 data: JSON，找不到返回 None。"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("data:"):
            data = line[len("data:"):].strip()
            with suppress(json.JSONDecodeError):
                return json.loads(data)
    return None


# --------------------------------------------------------------------------- #
# 共享工具函数
# --------------------------------------------------------------------------- #

def _initialize_params(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造 initialize 请求参数；apps_mode 为 auto 时声明 MCP Apps 扩展能力。"""
    capabilities: dict[str, Any] = {}
    if str((config or {}).get("apps_mode") or "disabled") == "auto":
        capabilities["extensions"] = {MCP_APPS_EXTENSION_ID: {}}
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": capabilities,
        "clientInfo": {"name": "StaffDeck", "version": "0.1.0"},
    }


def _send_json(
    proc: subprocess.Popen[str],
    payload: dict[str, Any],
    timeout_seconds: float | None = None,
) -> None:
    """把一条 JSON-RPC 消息写入子进程 stdin，用写线程加超时避免调用方阻塞。

    写入失败或超时抛 MCPClientError，并在消息里带上进程退出码。
    """
    if proc.stdin is None:
        raise MCPClientError("MCP stdio stdin 不可用。")
    outcome: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)

    def write() -> None:
        """写线程主体：写入并 flush stdin，异常交给 outcome 队列统一上报。"""
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            outcome.put(None)
        except (BrokenPipeError, OSError, UnicodeError, ValueError) as exc:
            outcome.put(exc)

    writer = threading.Thread(target=write, name="mcp-stdin-writer", daemon=True)
    writer.start()
    try:
        error = outcome.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise MCPClientError("向 MCP stdio server 发送请求超时。") from exc
    if error is not None:
        exit_code = proc.poll()
        suffix = f"（退出码 {exit_code}）" if exit_code is not None else ""
        raise MCPClientError(f"无法向 MCP stdio server 发送请求{suffix}：{error}") from error


def _read_response(
    proc: subprocess.Popen[str],
    reader: _PipeReader,
    expected_id: int,
    timeout_seconds: float,
    stderr: _StderrCollector | None = None,
) -> dict[str, Any]:
    """在 stdout 读取器上等待指定 id 的 JSON-RPC 响应。

    跳过非 JSON 或 id 不匹配的行；超时、读取失败、进程提前退出都转为 MCPClientError，并附 stderr 摘要。
    """
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPClientError(f"MCP stdio 等待响应超时：id={expected_id}{_stderr_text(stderr)}")
        try:
            event, value = reader.next_event(remaining)
        except TimeoutError as exc:
            raise MCPClientError(
                f"MCP stdio 等待响应超时：id={expected_id}{_stderr_text(stderr)}"
            ) from exc
        if event == "error":
            raise MCPClientError(f"读取 MCP stdio 响应失败：{value}{_stderr_text(stderr)}")
        if event == "eof":
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=0.1)
            if stderr is not None:
                stderr.wait()
            exit_code = proc.poll()
            suffix = f"（退出码 {exit_code}）" if exit_code is not None else "（stdout 已关闭）"
            raise MCPClientError(f"MCP stdio server 在返回响应前退出{suffix}。{_stderr_text(stderr)}".strip())
        line = str(value)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == expected_id:
            return payload


def _raise_json_rpc_error(payload: dict[str, Any]) -> None:
    """响应带 error 字段时抛 MCPClientError，优先使用服务端给出的 message。"""
    if "error" not in payload:
        return
    error = payload.get("error") or {}
    if isinstance(error, dict):
        message = str(error.get("message") or error)
    else:
        message = str(error)
    raise MCPClientError(message)


def _extract_tool_result(result: Any) -> Any:
    """兼容入口：只返回 tools/call 结果信封里的 data 部分。"""
    return _tool_result_envelope(result)["data"]


def _tool_result_envelope(result: Any) -> dict[str, Any]:
    """把 tools/call 的原始结果包装成统一信封。

    isError 为真时直接抛错；content 中的 text 片段会尝试按 JSON 解析，
    有 structuredContent 时优先作为 data，多个片段则以列表返回。
    """
    if not isinstance(result, dict):
        return {
            "data": result,
            "content": [],
            "structured_content": None,
            "meta": {},
            "is_error": False,
        }
    if result.get("isError"):
        raise MCPClientError(_content_text(result.get("content")) or "MCP tool returned isError=true。")
    content = result.get("content")
    structured_content = result.get("structuredContent")
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    if not isinstance(content, list):
        data = structured_content if structured_content is not None else result
        return {
            "data": data,
            "content": [],
            "structured_content": structured_content,
            "meta": dict(meta),
            "is_error": False,
        }
    extracted: list[Any] = []
    for item in content:
        if not isinstance(item, dict):
            extracted.append(item)
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "")
            extracted.append(_parse_text_content(text))
        else:
            extracted.append(item)
    if structured_content is not None:
        data = structured_content
    elif len(extracted) == 1:
        data = extracted[0]
    else:
        data = extracted
    return {
        "data": data,
        "content": content,
        "structured_content": structured_content,
        "meta": dict(meta),
        "is_error": False,
    }


def _parse_text_content(text: str) -> Any:
    """尝试把 text 内容解析为 JSON；空串返回空字符串，解析失败则原样返回文本。"""
    stripped = text.strip()
    if not stripped:
        return ""
    with suppress(json.JSONDecodeError):
        return json.loads(stripped)
    return text


def _content_text(content: Any) -> str:
    """拼接 content 数组中所有 text 片段的文本，非 text 项忽略。"""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _stderr_text(collector: _StderrCollector | None) -> str:
    """返回可拼进错误信息的 stderr 摘要；没有收集器时返回空串。"""
    return collector.text() if collector is not None else ""
