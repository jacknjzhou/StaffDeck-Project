from __future__ import annotations

from collections.abc import Iterable

from app.db.models import Message

_LEGACY_TEAM_REQUEST_MARKER = "人的需求:"
_LEGACY_ASSIGNMENT_MARKER = "\n派发任务的唯一方式"
_LEGACY_REPAIR_MARKER = "系统提示:你的上一条回复没有包含规定的 ```json 任务代码块"


def is_internal_message(message: Message) -> bool:
    """判断消息是否属于内部消息：带 internal 标记，或旧版团队修复提示词。"""
    metadata = message.metadata_json or {}
    if metadata.get("message_visibility") == "internal":
        return True
    return (
        message.role == "user"
        and metadata.get("interaction_mode") == "team_tl"
        and _LEGACY_REPAIR_MARKER in (message.content or "")
    )


def visible_message_rows(messages: Iterable[Message]) -> list[Message]:
    """过滤掉内部消息及其引发的助手回复，返回可展示给用户的消息列表。"""
    rows = list(messages)
    internal_turn_ids = internal_message_turn_ids(rows)
    visible: list[Message] = []
    for row in rows:
        if is_internal_message(row):
            continue
        metadata = row.metadata_json or {}
        linked_turn_id = str(
            metadata.get("user_message_id") or metadata.get("turn_id") or ""
        ).strip()
        if row.role == "assistant" and linked_turn_id in internal_turn_ids:
            continue
        visible.append(row)
    return visible


def internal_message_turn_ids(messages: Iterable[Message]) -> set[str]:
    """收集内部用户消息的 id，用于连带隐藏它们所触发的助手回复。"""
    return {
        row.id
        for row in messages
        if row.role == "user" and is_internal_message(row)
    }


def visible_message_content(message: Message) -> str:
    """从旧版本保存的团队提示词中还原原始用户文本，其它消息原样返回。"""

    content = message.content or ""
    metadata = message.metadata_json or {}
    if (
        message.role != "user"
        or metadata.get("interaction_mode") != "team_tl"
        or _LEGACY_TEAM_REQUEST_MARKER not in content
    ):
        return content
    visible = content.split(_LEGACY_TEAM_REQUEST_MARKER, 1)[1]
    if _LEGACY_ASSIGNMENT_MARKER in visible:
        visible = visible.split(_LEGACY_ASSIGNMENT_MARKER, 1)[0]
    return visible.strip()


__all__ = [
    "internal_message_turn_ids",
    "is_internal_message",
    "visible_message_content",
    "visible_message_rows",
]
