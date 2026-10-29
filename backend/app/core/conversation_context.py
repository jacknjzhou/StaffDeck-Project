from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any


DEFAULT_CONTEXT_TOKEN_BUDGET = 32_000
COMPACTION_TRIGGER_RATIO = 0.70
RECENT_ROUND_LIMIT = 6
LONG_SUMMARY_TOKEN_BUDGET = 4_000
MEDIUM_SUMMARY_TOKEN_BUDGET = 4_000
ALLOWED_CONTEXT_ROLES = {"user", "assistant"}
LONG_SUMMARY_PREFIX = "历史的信息可以被总结为："
MEDIUM_SUMMARY_PREFIX = "近期的历史信息总结为："

SummaryBuilder = Callable[[str, str, int], str]


@dataclass(frozen=True)
class ConversationContextSettings:
    """对话上下文压缩参数：token 预算、触发比例、近期轮数上限与两级摘要前缀。"""

    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET
    compaction_trigger_ratio: float = COMPACTION_TRIGGER_RATIO
    recent_round_limit: int = RECENT_ROUND_LIMIT
    long_summary_token_budget: int = LONG_SUMMARY_TOKEN_BUDGET
    medium_summary_token_budget: int = MEDIUM_SUMMARY_TOKEN_BUDGET
    allowed_roles: frozenset[str] = frozenset(ALLOWED_CONTEXT_ROLES)
    long_summary_prefix: str = LONG_SUMMARY_PREFIX
    medium_summary_prefix: str = MEDIUM_SUMMARY_PREFIX

    def normalized(self) -> ConversationContextSettings:
        """返回各字段夹取到合法区间的副本，非法或缺失值回落到默认值。

        token 预算上限 262144，触发比例限制在 0.10~0.95，近期轮数限制在 1~50，
        两级摘要预算都不超过总预算；allowed_roles 只保留 user 与 assistant。
        """
        token_budget = max(1, min(int(self.token_budget), 262_144))
        long_budget = max(1, min(int(self.long_summary_token_budget), token_budget))
        medium_budget = max(1, min(int(self.medium_summary_token_budget), token_budget))
        allowed_roles = frozenset(self.allowed_roles).intersection(ALLOWED_CONTEXT_ROLES)
        return replace(
            self,
            token_budget=token_budget,
            compaction_trigger_ratio=max(
                0.10,
                min(float(self.compaction_trigger_ratio), 0.95),
            ),
            recent_round_limit=max(1, min(int(self.recent_round_limit), 50)),
            long_summary_token_budget=long_budget,
            medium_summary_token_budget=medium_budget,
            allowed_roles=allowed_roles or frozenset(ALLOWED_CONTEXT_ROLES),
            long_summary_prefix=str(self.long_summary_prefix or LONG_SUMMARY_PREFIX).strip(),
            medium_summary_prefix=str(self.medium_summary_prefix or MEDIUM_SUMMARY_PREFIX).strip(),
        )


def build_conversation_context(
    messages: list[dict[str, Any]],
    token_budget: int | None = None,
    *,
    settings: ConversationContextSettings | None = None,
    context_state: dict[str, Any] | None = None,
    summary_builder: SummaryBuilder | None = None,
) -> dict[str, object]:
    """把历史消息压缩并裁剪成可放进一次模型调用的上下文。

    先用 summarized_through_message_id 跳过已摘要部分，投影出摘要与近期消息；
    估算 token 达到 token_budget * compaction_trigger_ratio 时，把较早的消息滚动
    折叠进长期/近期摘要并推进游标；最后按预算整条丢弃或截断末条消息。
    返回 messages、compacted_summary、context_state、metadata 四项。
    """
    resolved = (settings or ConversationContextSettings()).normalized()
    if token_budget is not None:
        resolved = replace(resolved, token_budget=token_budget).normalized()
    normalized = _normalize_messages(messages, resolved.allowed_roles)
    state = _normalize_state(context_state)
    unsummarized, summarized_count = _messages_after_cursor(normalized, state)
    projected = _project_messages(state, unsummarized, resolved)
    trigger_tokens = max(
        1,
        math.floor(resolved.token_budget * resolved.compaction_trigger_ratio),
    )
    compacted_now = False

    if _messages_tokens(projected) >= trigger_tokens:
        recent = _recent_rounds(unsummarized, resolved.recent_round_limit)
        older_count = len(unsummarized) - len(recent)
        older = unsummarized[:older_count]
        if older:
            previous_history = _joined_existing_history(state)
            state["long_term_summary"] = _summarize(
                "长期历史信息",
                previous_history,
                resolved.long_summary_token_budget,
                summary_builder,
            )
            state["medium_term_summary"] = _summarize(
                "近期历史信息",
                _transcript(older),
                resolved.medium_summary_token_budget,
                summary_builder,
            )
            state["summarized_through_message_id"] = older[-1]["_message_id"]
            state["compaction_count"] = int(state.get("compaction_count") or 0) + 1
            unsummarized = recent
            summarized_count += len(older)
            compacted_now = True
            projected = _project_messages(state, unsummarized, resolved)

    projected = _fit_projected_messages(projected, resolved.token_budget, resolved)
    summary = _joined_existing_history(state)
    return {
        "messages": projected,
        "compacted_summary": summary,
        "context_state": state,
        "metadata": {
            "token_budget": resolved.token_budget,
            "compaction_trigger_ratio": resolved.compaction_trigger_ratio,
            "compaction_trigger_tokens": trigger_tokens,
            "estimated_tokens": _messages_tokens(projected),
            "total_messages": len(normalized),
            "included_messages": len(projected),
            "omitted_messages": summarized_count,
            "compacted": bool(summary),
            "compacted_now": compacted_now,
            "long_term_summary": bool(state.get("long_term_summary")),
            "medium_term_summary": bool(state.get("medium_term_summary")),
            "recent_round_limit": resolved.recent_round_limit,
            "long_summary_token_budget": resolved.long_summary_token_budget,
            "medium_summary_token_budget": resolved.medium_summary_token_budget,
            "allowed_roles": sorted(resolved.allowed_roles),
            "long_summary_prefix": resolved.long_summary_prefix,
            "medium_summary_prefix": resolved.medium_summary_prefix,
            "current_turn_time": normalized[-1].get("_created_at") if normalized else None,
        },
    }


def _normalize_state(value: dict[str, Any] | None) -> dict[str, Any]:
    """把持久化的上下文状态收敛成固定的四个字段，缺失值补空串或 0。"""
    source = value if isinstance(value, dict) else {}
    return {
        "long_term_summary": str(source.get("long_term_summary") or "").strip(),
        "medium_term_summary": str(source.get("medium_term_summary") or "").strip(),
        "summarized_through_message_id": str(
            source.get("summarized_through_message_id") or ""
        ).strip(),
        "compaction_count": max(0, int(source.get("compaction_count") or 0)),
    }


def _normalize_messages(
    messages: list[dict[str, Any]],
    allowed_roles: frozenset[str],
) -> list[dict[str, Any]]:
    """过滤出允许角色且有正文的消息，并补齐内部 id 与时间字段。

    只有 user 消息带 images 列表时才保留图片，其余内部字段一律不向外传递。
    """
    normalized: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role not in allowed_roles or not content:
            continue
        normalized_message: dict[str, Any] = {
            "role": role,
            "content": content,
            "_message_id": str(message.get("id") or f"context_message_{index}"),
            "_created_at": _string_time(message.get("created_at")),
        }
        images = message.get("images")
        if role == "user" and isinstance(images, list) and images:
            normalized_message["images"] = images
        normalized.append(normalized_message)
    return normalized


def _messages_after_cursor(
    messages: list[dict[str, Any]], state: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """返回游标之后尚未摘要的消息及已摘要条数。

    游标在历史里找不到说明底层会话已被替换，此时清空两级摘要与计数，
    从零开始重建，避免把旧摘要套到新历史上。
    """
    cursor = str(state.get("summarized_through_message_id") or "")
    if not cursor:
        return messages, 0
    for index, message in enumerate(messages):
        if message["_message_id"] == cursor:
            return messages[index + 1 :], index + 1
    # A missing cursor means the backing history was replaced; rebuild safely.
    state["long_term_summary"] = ""
    state["medium_term_summary"] = ""
    state["summarized_through_message_id"] = ""
    state["compaction_count"] = 0
    return messages, 0


def _project_messages(
    state: dict[str, Any],
    recent: list[dict[str, Any]],
    settings: ConversationContextSettings,
) -> list[dict[str, Any]]:
    """把两级摘要与近期消息投影成模型可见的消息列表。

    摘要以 user 角色插入在最前，缺某一级时补占位文案；近期消息只保留
    role、content、images，内部埋点字段不外泄。
    """
    projected: list[dict[str, Any]] = []
    long_summary = str(state.get("long_term_summary") or "").strip()
    medium_summary = str(state.get("medium_term_summary") or "").strip()
    if long_summary or medium_summary:
        projected.append(
            {
                "role": "user",
                "content": (
                    f"{settings.long_summary_prefix}\n"
                    f"{long_summary or '暂无长期历史摘要。'}"
                ),
            }
        )
    if long_summary or medium_summary:
        projected.append(
            {
                "role": "user",
                "content": (
                    f"{settings.medium_summary_prefix}\n"
                    f"{medium_summary or '暂无近期历史摘要。'}"
                ),
            }
        )
    projected.extend(_public_message(message) for message in recent)
    return projected


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    """只挑出 role、content、images 三个对外字段，丢掉其余内部元数据。"""
    return {
        key: value
        for key, value in message.items()
        if key in {"role", "content", "images"}
    }


def _recent_rounds(
    messages: list[dict[str, Any]], round_limit: int
) -> list[dict[str, Any]]:
    """按用户消息切出最近 round_limit 轮，连同其后的助手回复一起保留。

    没有用户消息时退化为按条数取尾部；轮次不足时原样返回全部消息。
    """
    user_indexes = [
        index for index, message in enumerate(messages) if message.get("role") == "user"
    ]
    if not user_indexes:
        return messages[-round_limit:]
    if len(user_indexes) <= round_limit:
        return messages
    return messages[user_indexes[-round_limit] :]


def _joined_existing_history(state: dict[str, Any]) -> str:
    """把长期与近期摘要用换行拼成一段历史文本，空摘要自动跳过。"""
    return "\n".join(
        value
        for value in (
            str(state.get("long_term_summary") or "").strip(),
            str(state.get("medium_term_summary") or "").strip(),
        )
        if value
    )


def _summarize(
    label: str,
    source: str,
    token_budget: int,
    summary_builder: SummaryBuilder | None,
) -> str:
    """优先用注入的 summary_builder 生成摘要，异常或空结果时回退到本地压缩。

    无论走哪条路径，返回值都按 token_budget 截断；来源为空时直接返回空串。
    """
    if not source.strip():
        return ""
    if summary_builder:
        try:
            summary = str(summary_builder(label, source, token_budget) or "").strip()
            if summary:
                return _trim_text_to_tokens(summary, token_budget)
        except Exception:
            pass
    return _compact_transcript(source, token_budget)


def _transcript(messages: list[dict[str, Any]]) -> str:
    """把消息列表渲染成 `role: content` 的纯文本记录，供摘要使用。"""
    return "\n".join(
        f"{message['role']}: {' '.join(str(message['content']).split())}"
        for message in messages
    )


def _compact_transcript(source: str, token_budget: int) -> str:
    """逐行累积到 token_budget 以内，最后再整体做一次截断兜底。"""
    lines = [" ".join(line.split()) for line in source.splitlines() if line.strip()]
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join([*selected, line])
        if selected and _estimate_tokens(candidate) > token_budget:
            break
        selected.append(line)
    result = "\n".join(selected)
    return _trim_text_to_tokens(result or source, token_budget)


def _fit_projected_messages(
    messages: list[dict[str, Any]],
    token_budget: int,
    settings: ConversationContextSettings,
) -> list[dict[str, Any]]:
    """在预算内修剪消息列表：先删最旧的消息，仍超预算就截断末条内容。

    开头最多两条摘要消息受保护不删，并且始终至少保留一条消息。
    """
    projected = list(messages)
    summary_count = sum(
        1
        for message in projected[:2]
        if str(message.get("content") or "").startswith(
            (settings.long_summary_prefix, settings.medium_summary_prefix)
        )
    )
    while len(projected) > summary_count + 1 and _messages_tokens(projected) > token_budget:
        projected.pop(summary_count)
    if projected and _messages_tokens(projected) > token_budget:
        last = projected[-1]
        remaining = max(1, token_budget - _messages_tokens(projected[:-1]))
        projected[-1] = _trim_message(last, remaining)
    return projected


def _trim_message(message: dict[str, Any], token_budget: int) -> dict[str, Any]:
    """按给定预算截短单条消息的 content，role 等其它字段原样保留。"""
    content_budget = max(1, token_budget - _estimate_tokens(str(message["role"])) - 6)
    return {**message, "content": _trim_text_to_tokens(str(message["content"]), content_budget)}


def _trim_text_to_tokens(text: str, token_budget: int) -> str:
    """把文本截到 token 预算内；超长时按 UTF-8 字节安全截断并补省略号。"""
    if _estimate_tokens(text) <= token_budget:
        return text
    encoded = text.encode("utf-8")
    byte_budget = max(4, token_budget * 4)
    trimmed = encoded[:byte_budget].decode("utf-8", errors="ignore").rstrip()
    return f"{trimmed}..."


def _messages_tokens(messages: list[dict[str, Any]]) -> int:
    """累加一组消息的估算 token 数。"""
    return sum(_message_tokens(message) for message in messages)


def _message_tokens(message: dict[str, Any]) -> int:
    """估算单条消息的 token：role 与 content 各算一次，另加 6 的固定开销。"""
    return _estimate_tokens(str(message["role"])) + _estimate_tokens(
        str(message["content"])
    ) + 6


def _estimate_tokens(text: str) -> int:
    """按 UTF-8 字节数除以 4 粗估 token，最少记为 1。"""
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _string_time(value: object) -> str | None:
    """把时间对象或字符串规范成 ISO 文本；None 与空值返回 None。"""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    text = str(value).strip()
    return text or None
