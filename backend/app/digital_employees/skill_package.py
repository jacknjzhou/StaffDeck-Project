from __future__ import annotations

import zipfile
from io import BytesIO

from app.db.models import GeneralSkill


def build_skill_zip(row: GeneralSkill) -> bytes:
    """Package a general skill as a SKILL.md-rooted zip, ready for deepchat import."""
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
    """Drop traversal and absolute paths: the archive is unpacked on the client.

    A path containing a `..` segment is dropped wholesale rather than cleaned,
    because the intent behind such an entry is never legitimate.
    """
    cleaned = raw.replace("\\", "/").strip()
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return ""
    if any(part in {":", "~"} or part.startswith("~") for part in parts):
        return ""
    return "/".join(parts)


def skill_zip_filename(slug: str) -> str:
    safe = "".join(char for char in slug if char.isalnum() or char in {"-", "_"})
    return f"{safe.lstrip('._') or 'skill'}.zip"
