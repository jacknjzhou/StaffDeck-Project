from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """判断当前是否运行在打包后的冻结程序中（PyInstaller 等会设置 sys.frozen）。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """返回后端应用根目录（开发态即 backend/），结果不受当前工作目录影响。"""
    # 开发态：backend/ 目录（app/paths.py 的上上级）
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    """返回只读资源目录：打包态是 _MEIPASS 解包目录，开发态与 app_root() 一致。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return app_root()


def user_data_dir() -> Path:
    """返回可写的用户数据目录，按平台选定位置并在缺失时创建后返回。

    ULTRARAG_DATA_DIR 环境变量优先；macOS 用 Application Support/StaffDeck，
    Windows 用 %APPDATA%/StaffDeck，其它平台用 ~/.local/share/StaffDeck。
    """
    # 环境变量前缀保留 ULTRARAG_（内部标识，不改）；目录名用对外品牌 StaffDeck
    override = os.environ.get("ULTRARAG_DATA_DIR", "").strip()
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "StaffDeck"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "StaffDeck"
    else:
        base = Path.home() / ".local" / "share" / "StaffDeck"
    base.mkdir(parents=True, exist_ok=True)
    return base
