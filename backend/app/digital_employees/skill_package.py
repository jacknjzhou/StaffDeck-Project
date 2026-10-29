from __future__ import annotations

import zipfile
from io import BytesIO

from app.db.models import GeneralSkill


def build_skill_zip(row: GeneralSkill) -> bytes:
    """把通用技能打包成以 SKILL.md 为根的 zip 字节流，可直接用于 deepchat 导入。

    空路径与重复路径只写入一次，保留首次出现的条目顺序。
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        written: set[str] = set()
        for path, content in _files(row):
            if not path or path in written:
                continue
            written.add(path)
            archive.writestr(path, content)
    return buffer.getvalue()


def _files(row: GeneralSkill) -> list[tuple[str, str]]:
    """收集待写入 zip 的 (路径, 内容) 列表，缺少 SKILL.md 时用 skill_markdown 补齐。

    非 dict 条目、内容不是字符串的条目以及被 _safe_path 判空的路径都会被跳过。
    """
    entries: list[tuple[str, str]] = []
    for item in row.skill_files_json or []:
        if not isinstance(item, dict):
            continue
        path = _safe_path(str(item.get("path") or ""))
        content = item.get("content")
        if not path or not isinstance(content, str):
            continue
        entries.append((path, content))
    if not any(path == "SKILL.md" for path, _ in entries):
        entries.append(("SKILL.md", row.skill_markdown or ""))
    return entries


def _safe_path(raw: str) -> str:
    """清洗条目路径并丢弃穿越路径与绝对路径，因为压缩包最终在客户端解包。

    含 `..` 段的路径整条丢弃而不做规整，这种条目的意图从不正当；无法安全化的路径返回空串。
    """
    cleaned = raw.replace("\\", "/").strip()
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return ""
    if any(part in {":", "~"} or part.startswith("~") for part in parts):
        return ""
    return "/".join(parts)


def skill_zip_filename(slug: str) -> str:
    """由 slug 生成安全的 zip 文件名，只保留字母数字与 `-` `_`。

    前导的点与下划线会被去掉；清洗后为空时回退为 skill.zip。
    """
    safe = "".join(char for char in slug if char.isalnum() or char in {"-", "_"})
    return f"{safe.lstrip('._') or 'skill'}.zip"
