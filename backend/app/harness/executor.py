from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.harness.contracts import (
    HarnessToolCall,
    HarnessToolContext,
    HarnessToolError,
    HarnessToolResult,
)
from app.harness.errors import HarnessExecutionError
from app.harness.registry import HarnessRegistry


class HarnessExecutor:
    """校验并执行单次已注册的 Harness 工具调用。"""

    def __init__(self, registry: HarnessRegistry) -> None:
        """注入工具注册表，后续调用按注册信息解析工具与参数模型。"""
        self.registry = registry

    def execute(
        self,
        context: HarnessToolContext,
        call: HarnessToolCall,
    ) -> HarnessToolResult:
        """校验并执行一次工具调用，所有失败都收敛为 success=False 的结果对象。

        依次做注册表查找、按 argument_model 校验参数、执行 handler 并序列化返回值；
        未注册、参数非法、IO 异常与意外异常分别转成对应错误码而不向调用方抛出。
        """
        started = time.monotonic()
        registered = self.registry.get(call.name)
        if registered is None:
            return self._failure(
                call,
                HarnessToolError(
                    code="TOOL_NOT_FOUND",
                    message=f"Harness tool is not registered: {call.name}",
                ),
                started,
            )

        try:
            arguments = registered.argument_model.model_validate(dict(call.arguments))
        except ValidationError as exc:
            errors = [
                {
                    "type": str(item.get("type", "validation_error")),
                    "path": ".".join(str(part) for part in item.get("loc", ())),
                    "message": str(item.get("msg", "Invalid value")),
                }
                for item in exc.errors(include_url=False)
            ]
            return self._failure(
                call,
                HarnessToolError(
                    code="INVALID_ARGUMENTS",
                    message="Tool arguments failed schema validation.",
                    details={"errors": errors},
                ),
                started,
            )

        try:
            raw_data = registered.handler(context, arguments)
            data = self._json_object(raw_data)
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > context.limits.max_result_bytes:
                raise HarnessExecutionError(
                    "RESULT_TOO_LARGE",
                    "Tool result exceeds the Harness result-size limit.",
                    details={
                        "actual_bytes": len(encoded),
                        "max_bytes": context.limits.max_result_bytes,
                    },
                )
            return HarnessToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                success=True,
                data=data,
                duration_ms=self._duration_ms(started),
            )
        except HarnessExecutionError as exc:
            return self._failure(call, exc.error, started)
        except (OSError, UnicodeError) as exc:
            return self._failure(
                call,
                HarnessToolError(
                    code="IO_ERROR",
                    message="The workspace operation could not be completed.",
                    retryable=isinstance(exc, (BlockingIOError, TimeoutError)),
                    details={"exception_type": type(exc).__name__},
                ),
                started,
            )
        except Exception as exc:
            return self._failure(
                call,
                HarnessToolError(
                    code="INTERNAL_ERROR",
                    message="The Harness tool failed unexpectedly.",
                    details={"exception_type": type(exc).__name__},
                ),
                started,
            )

    @staticmethod
    def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
        """确认 handler 返回值是 JSON 可序列化的对象，并返回其浅拷贝。

        非 Mapping 或含 NaN 等不可序列化值时抛 INVALID_TOOL_RESULT。
        """
        if not isinstance(value, Mapping):
            raise HarnessExecutionError(
                "INVALID_TOOL_RESULT",
                "Harness handlers must return a JSON object.",
            )
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise HarnessExecutionError(
                "INVALID_TOOL_RESULT",
                "Harness handler returned a non-JSON value.",
            ) from exc
        return dict(value)

    @staticmethod
    def _failure(
        call: HarnessToolCall,
        error: HarnessToolError,
        started: float,
    ) -> HarnessToolResult:
        """按原始调用与错误对象拼装失败结果，并带上已消耗的耗时。"""
        return HarnessToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            success=False,
            error=error,
            duration_ms=HarnessExecutor._duration_ms(started),
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        """把单调时钟起点换算成整数毫秒耗时，负数按 0 处理。"""
        return max(0, round((time.monotonic() - started) * 1000))


__all__ = ["HarnessExecutor"]
