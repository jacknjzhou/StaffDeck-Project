from __future__ import annotations

from typing import TypeVar

# Mirrors jacknjzhou/deepchat-project@202610211551
# src/shared/contracts/routes/digitalEmployee.routes.ts. Any change here must be
# accompanied by a change to tests/fixtures/deepchat_digital_employee.schema.json.
MAX_ID = 64
MAX_NAME = 50
MAX_ROLE = 50
MAX_DESCRIPTION = 500
MAX_AVATAR = 256
MAX_CAPABILITY = 30
MAX_CAPABILITIES = 20
MAX_GREETING = 500
MAX_SYSTEM_PROMPT = 10000
MAX_SOPS = 20
MAX_SOP_TITLE = 100
MAX_SOP_CONTENT = 20000
MAX_KNOWLEDGE_BASE_IDS = 10
MAX_SKILL_IDS = 20
MAX_TOOLS = 50

T = TypeVar("T")


def zod_len(value: str) -> int:
    """Length as JavaScript sees it: UTF-16 code units, not code points."""
    return len((value or "").encode("utf-16-le")) // 2


def clamp_text(value: str, limit: int) -> tuple[str, bool]:
    """Trim to at most `limit` UTF-16 code units without splitting a surrogate pair."""
    text = value or ""
    if zod_len(text) <= limit:
        return text, False
    # Walk the code-unit budget down until the prefix decodes cleanly, so an emoji
    # on the boundary is dropped whole instead of leaving a lone surrogate.
    for end in range(limit, -1, -1):
        candidate = text.encode("utf-16-le")[: end * 2].decode("utf-16-le", errors="ignore")
        if zod_len(candidate) <= limit:
            return candidate, True
    return "", True


def clamp_items(values: list[T], limit: int) -> tuple[list[T], bool]:
    if len(values) <= limit:
        return list(values), False
    return list(values[:limit]), True
