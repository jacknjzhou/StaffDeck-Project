from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from app import paths


def normalize_database_url(url: str) -> str:
    """把相对路径的 sqlite URL 改写成绝对路径，其它形式的 URL 原样返回。

    只处理 sqlite:/// 且非 sqlite://// 和非 :memory: 的相对路径：开发态以仓库
    backend/ 为基准，打包态以 user_data_dir() 为基准，避免启动目录变化连到别的库。
    """
    if (
        not url.startswith("sqlite:///")
        or url.startswith("sqlite:////")
        or url == "sqlite:///:memory:"
    ):
        return url
    raw_path = unquote(url.removeprefix("sqlite:///"))
    if not raw_path or raw_path == ":memory:":
        return url
    path = Path(raw_path)
    if path.is_absolute():
        return url
    base_dir = (
        paths.user_data_dir()
        if paths.is_frozen()
        else Path(__file__).resolve().parents[2]
    )
    return f"sqlite:///{(base_dir / path).resolve()}"


def sqlite_database_path(url: str) -> Path | None:
    """返回 sqlite URL 对应的绝对文件路径；非 sqlite 或内存库返回 None。"""
    normalized = normalize_database_url(url)
    if not normalized.startswith("sqlite:///") or normalized == "sqlite:///:memory:":
        return None
    raw_path = unquote(normalized.removeprefix("sqlite:///"))
    return Path(raw_path).expanduser().resolve()


__all__ = ["normalize_database_url", "sqlite_database_path"]
