from __future__ import annotations

from collections.abc import Mapping

from app.harness.contracts import HarnessToolError, JsonValue


class HarnessExecutionError(RuntimeError):
    """可预期且可安全回传给模型的 Harness 失败，携带结构化错误信息。

    异常文本与 message 一致，另通过 self.error 暴露 code/retryable/details。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """构造异常并同时生成 HarnessToolError，供上层序列化给模型。"""
        super().__init__(message)
        self.error = HarnessToolError(
            code=code,
            message=message,
            retryable=retryable,
            details=dict(details or {}),
        )


def harness_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Mapping[str, JsonValue] | None = None,
) -> HarnessExecutionError:
    """以函数形式构造 HarnessExecutionError，便于在表达式位置直接抛出。"""
    return HarnessExecutionError(
        code,
        message,
        retryable=retryable,
        details=details,
    )


__all__ = ["HarnessExecutionError", "harness_error"]
