from app.core.tool_replay_policy import (
    TOOL_CALL_HISTORY_SLOT,
    TOOL_RESULTS_SLOT,
    ToolReplayPolicy,
)
from app.tools.tool_schema import ToolCall, ToolError, ToolResult


def test_record_result_deduplicates_history_but_preserves_all_results() -> None:
    """验证重复记录同一调用时历史只去重保留一条，而每次结果都被完整追加。"""
    call = ToolCall(name="orders.create", arguments={"order_id": "O-1"})
    success = ToolResult(tool_name=call.name, success=True, data={"id": "O-1"})

    first = ToolReplayPolicy.record_result({}, call, success)
    second = ToolReplayPolicy.record_result(first, call, success)

    assert second[TOOL_CALL_HISTORY_SLOT] == [
        {"tool_name": "orders.create", "arguments": {"order_id": "O-1"}}
    ]
    assert len(second[TOOL_RESULTS_SLOT]) == 2


def test_record_result_preserves_structured_error() -> None:
    """验证失败结果的结构化错误码与消息会被原样写入结果槽位。"""
    call = ToolCall(name="orders.create", arguments={})
    result = ToolResult(
        tool_name=call.name,
        success=False,
        error=ToolError(code="FAILED", message="failed"),
    )

    slots = ToolReplayPolicy.record_result({}, call, result)

    assert slots[TOOL_RESULTS_SLOT][0]["error"] == {
        "code": "FAILED",
        "message": "failed",
    }


def test_configuration_preserves_legacy_precedence_and_confirmation_fallback() -> None:
    """验证幂等配置的旧优先级保持不变，缺失幂等字段时回落到确认开关。"""
    assert ToolReplayPolicy.configuration(
        {"idempotency": {"enabled": "on", "key_fields": ["order_id", ""]}},
        {},
    ) == (True, ["order_id"])
    assert ToolReplayPolicy.configuration(
        {"requires_confirmation": True},
        {},
    ) == (True, None)
    assert ToolReplayPolicy.configuration(
        {"idempotency": False, "requires_confirmation": True},
        {},
    ) == (False, None)


def test_signature_and_key_arguments_are_deterministic() -> None:
    """验证签名与入参顺序无关，关键参数只保留声明字段，HTTP 方法决定默认重放开关。"""
    left = ToolReplayPolicy.signature("tool", {"b": 2, "a": 1})
    right = ToolReplayPolicy.signature("tool", {"a": 1, "b": 2})

    assert left == right
    assert ToolReplayPolicy.arguments({"a": 1, "b": 2}, ["b", "missing"]) == {"b": 2}
    assert ToolReplayPolicy.default_replay_enabled("post")
    assert not ToolReplayPolicy.default_replay_enabled("get")


def test_delegation_callbacks_preserve_legacy_extension_seams() -> None:
    """验证注入的历史读取、签名与解析回调仍被调用，扩展点行为保持不变。"""
    call = ToolCall(name="orders.create", arguments={"order_id": "O-1"})
    result = ToolResult(tool_name=call.name, success=True)
    calls: list[str] = []

    ToolReplayPolicy.record_result(
        {},
        call,
        result,
        history_reader=lambda slots: calls.append("history") or [],
        call_signature=lambda tool_call: calls.append("call") or tool_call.name,
        history_signature=lambda item: calls.append("item") or str(item),
    )
    assert calls == ["history", "call"]

    parsed: list[object] = []
    assert ToolReplayPolicy.configuration(
        {"idempotency": {"enabled": "custom"}},
        {},
        enabled_parser=lambda value: parsed.append(value) or True,
    ) == (True, None)
    assert parsed == ["custom"]
