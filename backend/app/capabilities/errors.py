from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityErrorInfo:
    """一次能力调用失败的稳定错误描述，用于构造与调用方约定的错误契约。"""

    code: str
    message: str
    retryable: bool
    request_id: str | None = None
    provider_id: str | None = None
    extensions: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        """把内部字段一次性转换成线上错误契约的载荷。

        缺少 request_id、扩展命名空间不合法、扩展不是 JSON 或超过 64 KiB 时抛 ValueError。
        """
        if not self.request_id:
            raise ValueError("provider errors require request_id")
        extensions = self.extensions or {}
        for namespace, value in extensions.items():
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]*", namespace)
                or namespace in {"core", "staffdeck"}
                or not isinstance(value, dict)
            ):
                raise ValueError(f"invalid provider error extension namespace: {namespace}")
        try:
            encoded = json.dumps(extensions, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider error extensions must be JSON") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("provider error extensions exceed the 64 KiB limit")
        return {
            "error_code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "extensions": self.extensions or {},
        }


class CapabilityProviderError(RuntimeError):
    """稳定的 provider 失败异常；调用方必须依据 code/retryable 分支处理。"""

    def __init__(self, info: CapabilityErrorInfo) -> None:
        """用错误信息中的 message 作为异常消息，并把完整 info 挂在实例上供上层读取。"""
        super().__init__(info.message)
        self.info = info


__all__ = ["CapabilityErrorInfo", "CapabilityProviderError"]
