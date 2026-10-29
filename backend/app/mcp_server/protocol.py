from __future__ import annotations

from typing import Any

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


class RpcError(Exception):
    """JSON-RPC 层的错误，原样透传给传输层。

    携带 `code`、`message` 与可选的 `data`，由 `error_response` 序列化成响应体。
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        """记录错误码、错误消息与可选的附加数据。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def parse_request(payload: Any) -> tuple[Any, str, dict[str, Any], bool]:
    """校验 JSON-RPC 信封，拆出处理请求所需的字段。

    返回 `(id, method, params, is_notification)`；缺少 `id` 表示客户端在发
    通知，按 JSON-RPC 2.0 的约定服务端不应回包。信封不合法时抛 `RpcError`。
    """
    if not isinstance(payload, dict):
        raise RpcError(INVALID_REQUEST, "A JSON-RPC request must be an object.")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcError(INVALID_REQUEST, 'jsonrpc must be "2.0".')
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(INVALID_REQUEST, "method must be a non-empty string.")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be an object.")
    request_id = payload.get("id")
    return request_id, method, params, "id" not in payload


def success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """构造带 `result` 的成功响应信封，原样带回请求 `id`。"""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, error: RpcError) -> dict[str, Any]:
    """把 `RpcError` 序列化成错误响应信封，仅在有 `data` 时附带该字段。"""
    body: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
    if error.data is not None:
        body["error"]["data"] = error.data
    return body


def negotiate_protocol_version(requested: str | None) -> str:
    """在请求版本受支持时回显，否则回退到默认版本而不拒绝握手。"""
    if requested and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION
