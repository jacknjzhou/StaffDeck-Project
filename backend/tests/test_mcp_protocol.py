from __future__ import annotations

import pytest

from app.mcp_server.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    RpcError,
    error_response,
    negotiate_protocol_version,
    parse_request,
    success_response,
)


def test_parse_request_reads_id_method_and_params():
    """验证解析请求时能读出 id、method、params，且标准请求不算通知。"""
    request_id, method, params, is_notification = parse_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {"cursor": "a"}}
    )
    assert (request_id, method, params, is_notification) == (
        7,
        "tools/list",
        {"cursor": "a"},
        False,
    )


def test_parse_request_treats_missing_id_as_notification():
    """验证缺少 id 的请求按 JSON-RPC 通知处理，params 默认为空字典。"""
    request_id, method, params, is_notification = parse_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert request_id is None
    assert method == "notifications/initialized"
    assert params == {}
    assert is_notification is True


def test_parse_request_rejects_bad_envelopes():
    """验证版本错误、缺 method、非对象信封与 params 非对象都会抛 RpcError。"""
    with pytest.raises(RpcError) as bad_version:
        parse_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})
    assert bad_version.value.code == INVALID_REQUEST

    with pytest.raises(RpcError):
        parse_request({"jsonrpc": "2.0", "id": 1})
    with pytest.raises(RpcError):
        parse_request([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    with pytest.raises(RpcError):
        parse_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2]})


def test_error_response_matches_the_jsonrpc_shape():
    """验证错误响应符合 JSON-RPC 结构，且不携带 result 字段。"""
    body = error_response(3, RpcError(METHOD_NOT_FOUND, "Method not found: ./x"))
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 3
    assert body["error"]["code"] == METHOD_NOT_FOUND
    assert body["error"]["message"] == "Method not found: ./x"
    assert "result" not in body


def test_error_response_carries_data_only_when_present():
    """验证错误响应仅在提供 data 时才输出 data 字段。"""
    plain = error_response(1, RpcError(METHOD_NOT_FOUND, "nope"))
    assert "data" not in plain["error"]
    with_data = error_response(1, RpcError(METHOD_NOT_FOUND, "nope", {"hint": "x"}))
    assert with_data["error"]["data"] == {"hint": "x"}


def test_success_response_keeps_the_request_id():
    """验证成功响应原样回显请求 id，通知的 id 为 None 时也保留。"""
    assert success_response(None, {}) == {"jsonrpc": "2.0", "id": None, "result": {}}
    assert success_response(9, {"ok": True}) == {
        "jsonrpc": "2.0",
        "id": 9,
        "result": {"ok": True},
    }


def test_negotiate_protocol_version_falls_back_instead_of_failing():
    """验证已知版本原样返回，未知或缺失版本回退默认值而不报错。"""
    assert negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"
    # 未知版本不应让客户端连不上：回我方默认值，由客户端决定是否继续
    assert negotiate_protocol_version("1999-01-01") == DEFAULT_PROTOCOL_VERSION
    assert negotiate_protocol_version(None) == DEFAULT_PROTOCOL_VERSION
