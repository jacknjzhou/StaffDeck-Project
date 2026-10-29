from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ROUTER_GENERATED_MESSAGE_SLOT_KEYS = {
    "message_content",
    "user_message",
    "rewritten_message",
    "normalized_message",
    "current_message",
    "source_message",
}


def strip_router_generated_message_slots(slots: Mapping[str, Any] | None) -> dict[str, Any]:
    """剔除路由层生成的用户消息类槽位，避免把改写后的文本当成技能槽位持久化。"""
    if not isinstance(slots, Mapping):
        return {}
    return {
        str(key): value
        for key, value in slots.items()
        if str(key).strip() not in ROUTER_GENERATED_MESSAGE_SLOT_KEYS
    }
