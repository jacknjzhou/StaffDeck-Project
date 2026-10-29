from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]


@dataclass(frozen=True)
class HarnessLimits:
    """单个隔离 Harness 工作区生效的资源配额上限。"""

    max_read_bytes: int = 1024 * 1024
    max_file_bytes: int = 10 * 1024 * 1024
    max_workspace_bytes: int = 100 * 1024 * 1024
    max_entries: int = 1000
    max_result_bytes: int = 200 * 1024

    def __post_init__(self) -> None:
        """校验各配额为正整数，且单文件上限不超过工作区总上限。"""
        for name, value in (
            ("max_read_bytes", self.max_read_bytes),
            ("max_file_bytes", self.max_file_bytes),
            ("max_workspace_bytes", self.max_workspace_bytes),
            ("max_entries", self.max_entries),
            ("max_result_bytes", self.max_result_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_file_bytes > self.max_workspace_bytes:
            raise ValueError("max_file_bytes cannot exceed max_workspace_bytes")


@dataclass(frozen=True)
class HarnessToolContext:
    """受信任的执行上下文。

    ``workspace_root`` 由调用方按单个 TaskFrame 准备，模型给出的路径一律相对它解析。
    """

    run_id: str
    workspace_root: Path
    task_frame_id: str | None = None
    tenant_id: str | None = None
    limits: HarnessLimits = field(default_factory=HarnessLimits)
    # Internal/direct callers preserve the fail-closed historical behavior.
    # Tenant runtime code always passes the administrator's explicit setting.
    sandbox_enabled: bool = True
    sandbox_network_mode: Literal["all", "allowlist", "deny"] = "all"
    sandbox_allowed_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验 run_id 非空，并把 workspace_root 规范为绝对路径。"""
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        root = Path(self.workspace_root)
        if not root.is_absolute():
            raise ValueError("workspace_root must be absolute")
        object.__setattr__(self, "workspace_root", root)


@dataclass(frozen=True)
class HarnessToolCall:
    """模型发起的一次工具调用请求。"""

    call_id: str
    name: str
    arguments: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 call_id 与工具名均为非空字符串。"""
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")


@dataclass(frozen=True)
class HarnessToolError:
    """工具失败时的结构化错误，可用 retryable 标明是否值得重试。"""

    code: str
    message: str
    retryable: bool = False
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessToolResult:
    """一次工具调用的执行结果，success 与 error 字段互斥。"""

    call_id: str
    tool_name: str
    success: bool
    data: JsonObject | None = None
    error: HarnessToolError | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        """强制 success 与 error 互斥，拒绝既成功又带错误的矛盾结果。"""
        if self.success == (self.error is not None):
            raise ValueError("successful results cannot have errors and failed results require one")


@dataclass(frozen=True)
class HarnessToolSpec:
    """工具的元数据：名称、说明、入参 schema 与副作用类型。"""

    name: str
    description: str
    input_schema: JsonObject
    side_effect: Literal["read", "write", "delete"] = "read"

    def as_model_tool(self) -> dict[str, Any]:
        """转换成各 chat model 适配器通用的 function 工具结构。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


__all__ = [
    "HarnessLimits",
    "HarnessToolCall",
    "HarnessToolContext",
    "HarnessToolError",
    "HarnessToolResult",
    "HarnessToolSpec",
    "JsonObject",
    "JsonValue",
]
