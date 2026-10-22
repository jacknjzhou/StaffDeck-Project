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
    """A JSON-RPC level failure, carried out to the transport unchanged."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def parse_request(payload: Any) -> tuple[Any, str, dict[str, Any], bool]:
    """Validate a JSON-RPC envelope.

    Returns ``(id, method, params, is_notification)``. A missing ``id`` means the
    client is notifying, and per JSON-RPC 2.0 the server must not answer.
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
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(request_id: Any, error: RpcError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
    if error.data is not None:
        body["error"]["data"] = error.data
    return body


def negotiate_protocol_version(requested: str | None) -> str:
    """Echo a supported version; fall back rather than refuse the handshake."""
    if requested and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION
