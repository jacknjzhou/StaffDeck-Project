from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.tools.tool_schema import ToolCall, ToolResult

TOOL_CALL_HISTORY_SLOT = "_tool_call_history"
TOOL_RESULTS_SLOT = "_tool_results"
IDEMPOTENT_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ToolReplayPolicy:
    """工具调用重放策略:按工具名加参数签名判重,并决定哪些写操作允许重放。"""

    @staticmethod
    def signature(tool_name: str, arguments: dict[str, Any]) -> str:
        """把工具名与参数序列化为稳定的判重签名(键排序,非 JSON 值降级为 str)。"""
        return json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @classmethod
    def record_result(
        cls,
        slots: dict[str, Any],
        tool_call: ToolCall,
        tool_result: ToolResult,
        *,
        history_reader: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        call_signature: Callable[[ToolCall], str] | None = None,
        history_signature: Callable[[dict[str, Any]], str] | None = None,
    ) -> dict[str, Any]:
        """把一次工具调用记入历史与结果列表,返回更新后的 slots 副本。

        同签名的调用不重复进历史,但每次结果都按序追加;history_reader、call_signature
        与 history_signature 可覆盖默认的读取、判重实现。
        """
        updated = dict(slots)
        history = (
            history_reader(updated)
            if history_reader
            else cls.history(updated)
        )
        signature = (
            call_signature(tool_call)
            if call_signature
            else cls.signature(tool_call.name, tool_call.arguments)
        )
        signature_for_history = history_signature or cls.history_item_signature
        history_signatures = {signature_for_history(item) for item in history}
        if signature not in history_signatures:
            history.append({"tool_name": tool_call.name, "arguments": tool_call.arguments})

        raw_results = updated.get(TOOL_RESULTS_SLOT)
        results = list(raw_results) if isinstance(raw_results, list) else []
        results.append(
            {
                "tool_name": tool_call.name,
                "arguments": tool_call.arguments,
                "success": tool_result.success,
                "data": tool_result.data,
                "error": tool_result.error.model_dump() if tool_result.error else None,
            }
        )
        updated[TOOL_CALL_HISTORY_SLOT] = history
        updated[TOOL_RESULTS_SLOT] = results
        return updated

    @staticmethod
    def history(slots: dict[str, Any]) -> list[dict[str, Any]]:
        """读出 slot 中的调用历史并过滤非 dict 条目;字段缺失或类型不符时返回空列表。"""
        raw_history = slots.get(TOOL_CALL_HISTORY_SLOT)
        if not isinstance(raw_history, list):
            return []
        return [item for item in raw_history if isinstance(item, dict)]

    @classmethod
    def history_item_signature(cls, item: dict[str, Any]) -> str:
        """按历史条目的 tool_name 与 arguments 计算签名,字段缺失时按空值处理。"""
        return cls.signature(
            str(item.get("tool_name") or ""),
            item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
        )

    @classmethod
    def configuration(
        cls,
        raw_config: dict[str, Any],
        raw_schema: dict[str, Any],
        *,
        enabled_parser: Callable[[object], bool | None] | None = None,
    ) -> tuple[bool | None, list[str] | None]:
        """从工具配置或 schema 扩展字段解析幂等策略,返回 (是否启用, 关键参数名列表)。

        依次回退到 idempotency / idempotency_policy 与 x-idempotency / x_idempotency;
        未显式启用但 requires_confirmation 为真时默认启用。两个返回值都可能为 None,
        表示配置未给出可识别取值。
        """
        parse_enabled = enabled_parser or cls.enabled_value
        config = raw_config.get("idempotency", raw_config.get("idempotency_policy"))
        if config is None:
            config = raw_schema.get("x-idempotency", raw_schema.get("x_idempotency"))
        enabled: bool | None = None
        key_fields: list[str] | None = None
        if isinstance(config, dict):
            enabled = parse_enabled(
                config.get("enabled", config.get("mode", config.get("scope")))
            )
            fields = (
                config.get("key_fields")
                or config.get("fields")
                or config.get("argument_fields")
            )
            if isinstance(fields, list):
                key_fields = [str(item).strip() for item in fields if str(item).strip()]
        else:
            enabled = parse_enabled(config)

        requires_confirmation = raw_config.get(
            "requires_confirmation", raw_schema.get("requires_confirmation")
        )
        if enabled is None and parse_enabled(requires_confirmation) is True:
            enabled = True
        return enabled, key_fields

    @staticmethod
    def enabled_value(value: object) -> bool | None:
        """把开关取值归一化为 True/False;None 与无法识别的取值返回 None。"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {
            "1",
            "true",
            "yes",
            "on",
            "enabled",
            "enable",
            "replay",
            "session",
            "session_arguments",
        }:
            return True
        if normalized in {
            "0",
            "false",
            "no",
            "off",
            "disabled",
            "disable",
            "none",
            "read_only",
            "readonly",
        }:
            return False
        return None

    @staticmethod
    def arguments(
        arguments: dict[str, Any], key_fields: list[str] | None
    ) -> dict[str, Any]:
        """仅保留 key_fields 中且确实存在的参数;未配置关键字段时原样返回入参。"""
        if not key_fields:
            return arguments
        return {field: arguments.get(field) for field in key_fields if field in arguments}

    @staticmethod
    def default_replay_enabled(method: str) -> bool:
        """按 HTTP 方法给默认判定:POST/PUT/PATCH/DELETE 视为幂等写操作。"""
        return method.upper() in IDEMPOTENT_WRITE_METHODS
