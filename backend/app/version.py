from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_APP_VERSION = "0.1.0"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _bundled_version() -> str | None:
    """读取打包进产物的版本号文件，命中即返回，全部缺失时返回 None。

    依次尝试 PyInstaller 解包目录、可执行文件同级目录、macOS .app 的 Resources 目录。
    """
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "staffdeck-version.txt")
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        candidates.append(executable.parent / "staffdeck-version.txt")
        if sys.platform == "darwin" and len(executable.parents) >= 2:
            candidates.append(executable.parents[1] / "Resources" / "staffdeck-version.txt")
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def app_version() -> str:
    """返回应用版本号，优先级为 STAFFDECK_VERSION 环境变量 > 打包版本文件 > 默认值。"""
    return (
        os.environ.get("STAFFDECK_VERSION", "").strip()
        or _bundled_version()
        or DEFAULT_APP_VERSION
    )


def update_check_enabled() -> bool:
    """是否启用更新检查：STAFFDECK_UPDATE_CHECK 显式配置优先，否则仅打包运行时启用。"""
    configured = os.environ.get("STAFFDECK_UPDATE_CHECK", "").strip().lower()
    if configured in _TRUE_VALUES:
        return True
    if configured in _FALSE_VALUES:
        return False
    return bool(getattr(sys, "frozen", False))


__all__ = ["app_version", "update_check_enabled"]
