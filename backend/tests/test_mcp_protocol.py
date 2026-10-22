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
    request_id, method, params, is_notification = parse_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert request_id is None
    assert method == "notifications/initialized"
    assert params == {}
    assert is_notification is True


def test_parse_request_rejects_bad_envelopes():
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
    body = error_response(3, RpcError(METHOD_NOT_FOUND, "Method not found: ./x"))
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 3
    assert body["error"]["code"] == METHOD_NOT_FOUND
    assert body["error"]["message"] == "Method not found: ./x"
    assert "result" not in body


def test_error_response_carries_data_only_when_present():
    plain = error_response(1, RpcError(METHOD_NOT_FOUND, "nope"))
    assert "data" not in plain["error"]
    with_data = error_response(1, RpcError(METHOD_NOT_FOUND, "nope", {"hint": "x"}))
    assert with_data["error"]["data"] == {"hint": "x"}


def test_success_response_keeps_the_request_id():
    assert success_response(None, {}) == {"jsonrpc": "2.0", "id": None, "result": {}}
    assert success_response(9, {"ok": True}) == {
        "jsonrpc": "2.0",
        "id": 9,
        "result": {"ok": True},
    }


def test_negotiate_protocol_version_falls_back_instead_of_failing():
    assert negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"
    # 未知版本不应让客户端连不上：回我方默认值，由客户端决定是否继续
    assert negotiate_protocol_version("1999-01-01") == DEFAULT_PROTOCOL_VERSION
    assert negotiate_protocol_version(None) == DEFAULT_PROTOCOL_VERSION
