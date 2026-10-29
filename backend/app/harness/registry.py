from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.harness.contracts import HarnessToolContext, HarnessToolSpec

HarnessToolHandler = Callable[[HarnessToolContext, BaseModel], Mapping[str, Any]]
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class RegisteredHarnessTool:
    """一条已注册工具记录，把对外 spec、入参模型与执行 handler 绑定在一起。"""

    spec: HarnessToolSpec
    argument_model: type[BaseModel]
    handler: HarnessToolHandler


class HarnessRegistry:
    """面向 Harness 运行的类型化、确定性工具注册表。

    注册时校验名称、描述、入参模型与副作用类型，并按注册顺序对外输出。
    """

    def __init__(self) -> None:
        """初始化空注册表，内部以工具名为键保存已注册工具。"""
        self._tools: dict[str, RegisteredHarnessTool] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        argument_model: type[BaseModel],
        handler: HarnessToolHandler,
        side_effect: Literal["read", "write", "delete"] = "read",
    ) -> HarnessToolSpec:
        """校验并注册一个工具，返回生成的 HarnessToolSpec。

        名称需匹配 _TOOL_NAME_PATTERN 且不可重复，描述去空白后不能为空，
        argument_model 必须是 BaseModel 子类，side_effect 只能是 read/write/delete。
        """
        if not _TOOL_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid Harness tool name: {name!r}")
        if name in self._tools:
            raise ValueError(f"Harness tool already registered: {name}")
        if not description.strip():
            raise ValueError("Harness tool description cannot be empty")
        if not isinstance(argument_model, type) or not issubclass(argument_model, BaseModel):
            raise TypeError("argument_model must be a Pydantic BaseModel type")
        if side_effect not in {"read", "write", "delete"}:
            raise ValueError("side_effect must be read, write, or delete")

        spec = HarnessToolSpec(
            name=name,
            description=description.strip(),
            input_schema=argument_model.model_json_schema(),
            side_effect=side_effect,
        )
        self._tools[name] = RegisteredHarnessTool(
            spec=spec,
            argument_model=argument_model,
            handler=handler,
        )
        return spec

    def get(self, name: str) -> RegisteredHarnessTool | None:
        """按名称取已注册工具，未注册时返回 None。"""
        return self._tools.get(name)

    def specs(self) -> Sequence[HarnessToolSpec]:
        """按注册顺序返回所有工具的 spec 元组。"""
        return tuple(tool.spec for tool in self._tools.values())

    def model_tools(self) -> list[dict[str, Any]]:
        """返回可直接交给模型调用的工具描述列表，每项由 spec.as_model_tool 生成。"""
        return [spec.as_model_tool() for spec in self.specs()]

    def names(self) -> tuple[str, ...]:
        """按注册顺序返回全部已注册工具名。"""
        return tuple(self._tools)


__all__ = [
    "HarnessRegistry",
    "HarnessToolHandler",
    "RegisteredHarnessTool",
]
