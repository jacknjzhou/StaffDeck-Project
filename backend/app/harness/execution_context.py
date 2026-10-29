from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from app.harness.errors import HarnessExecutionError

SANDBOX_WORKSPACE = "/workspace"
SandboxBackend = Literal["srt", "bubblewrap"]


@dataclass(frozen=True)
class SandboxExecutionContext:
    """维护单次进程执行中宿主机路径与沙箱路径之间的映射契约。"""

    host_workspace: Path
    backend: SandboxBackend

    def __post_init__(self) -> None:
        """校验沙箱后端取值，并把 host_workspace 解析为真实存在的绝对目录。

        后端不在 srt/bubblewrap 内或工作区不是目录时抛 HarnessExecutionError；
        解析结果用 object.__setattr__ 回写，以适配冻结 dataclass。
        """
        if self.backend not in {"srt", "bubblewrap"}:
            raise HarnessExecutionError("SANDBOX_BACKEND_INVALID", "Unknown sandbox backend.")
        root = Path(self.host_workspace).resolve(strict=True)
        if not root.is_dir():
            raise HarnessExecutionError(
                "INVALID_WORKSPACE", "Sandbox workspace must be a directory."
            )
        object.__setattr__(self, "host_workspace", root)

    def process_path(self, raw_path: str | Path) -> str:
        """把宿主机路径映射为当前沙箱后端可用的进程路径。

        相对路径、以及不在工作区内的绝对路径原样返回；bubblewrap 后端把工作区内
        路径换算到 SANDBOX_WORKSPACE 前缀下。
        """
        path = Path(raw_path)
        if not path.is_absolute():
            return str(raw_path)
        try:
            relative = path.resolve(strict=False).relative_to(self.host_workspace)
        except (OSError, ValueError):
            return str(raw_path)
        if self.backend != "bubblewrap":
            return str(self.host_workspace / relative)
        suffix = PurePosixPath(*relative.parts).as_posix()
        return SANDBOX_WORKSPACE if suffix == "." else f"{SANDBOX_WORKSPACE}/{suffix}"

    def map_argv(self, argv: Sequence[str]) -> list[str]:
        """按 process_path 的规则逐项映射命令行参数，返回新的列表。"""
        return [self.process_path(item) for item in argv]

    def map_env(
        self,
        env: Mapping[str, str] | None,
        *,
        path_keys: Sequence[str] = (),
    ) -> dict[str, str]:
        """复制环境变量字典，并映射 path_keys 指定键中的宿主机路径值。

        键缺失或值为空时保持原样，其余键直接沿用。
        """
        mapped = dict(env or {})
        for key in path_keys:
            value = mapped.get(key)
            if value:
                mapped[key] = self.process_path(value)
        return mapped

    def map_payload(
        self,
        payload: Mapping[str, Any],
        *,
        path_keys: Sequence[str] = (),
    ) -> dict[str, Any]:
        """复制载荷字典，并映射 path_keys 指定键中的宿主机路径字符串。

        非字符串、空值或其他键保持原样返回，不修改入参。
        """
        mapped = dict(payload)
        for key in path_keys:
            value = mapped.get(key)
            if isinstance(value, str) and value:
                mapped[key] = self.process_path(value)
        return mapped

    def host_cwd(self, requested: Path | None = None) -> Path:
        """解析并校验沙箱工作目录，要求它位于任务工作区内且真实存在。

        未指定时取 host_workspace；越界或目录不可用时抛 HarnessExecutionError。
        """
        candidate = (requested or self.host_workspace).resolve(strict=True)
        try:
            candidate.relative_to(self.host_workspace)
        except ValueError as exc:
            raise HarnessExecutionError(
                "INVALID_WORKSPACE_CWD",
                "Sandbox working directory must stay inside the task workspace.",
            ) from exc
        if not candidate.is_dir():
            raise HarnessExecutionError(
                "INVALID_WORKSPACE_CWD",
                "Sandbox working directory is unavailable.",
            )
        return candidate

    def sandbox_cwd(self, requested: Path | None = None) -> str:
        """返回经 host_cwd 校验后的工作目录在沙箱内的路径字符串。"""
        return self.process_path(self.host_cwd(requested))


    @classmethod
    def create(cls, host_workspace: Path, backend: str) -> SandboxExecutionContext:
        """用字符串后端名构造实例，合法性交由 __post_init__ 统一校验。"""
        return cls(host_workspace, cast(SandboxBackend, backend))


__all__ = ["SANDBOX_WORKSPACE", "SandboxBackend", "SandboxExecutionContext"]
