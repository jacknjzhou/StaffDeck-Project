from __future__ import annotations

from typing import Any

from app.core.conversation_context import build_conversation_context
from app.knowledge.citations import EMAIL_PATTERN, knowledge_source_candidates
from app.llm.stage_protocol import TURN_STAGE_MESSAGES_KEY

CONTROL_CONTEXT_TOKEN_BUDGET = 32_000
KNOWLEDGE_HISTORY_LIMIT = 1
KNOWLEDGE_RELATED_CONTENT_LIMIT = 4_800
RETRIEVED_KNOWLEDGE_LIMIT = 4


def compact_knowledge_context(
    items: list[dict[str, Any]] | None,
    *,
    max_items: int = KNOWLEDGE_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """压缩检索历史:只保留最近 max_items 条知识结果,并投影掉大字段。"""
    if not isinstance(items, list):
        return []
    selected = [item for item in items if isinstance(item, dict)][-max(1, max_items) :]
    compacted = [_compact_knowledge_result(item) for item in selected]
    return [item for item in compacted if item]


def compact_step_result(payload: dict[str, Any]) -> dict[str, Any]:
    """投影单步结果:把 knowledge_results 换成精简后的 retrieved_knowledge。

    返回新字典,不修改入参,其余字段原样保留。
    """
    projected = dict(payload)
    projected.pop("knowledge_results", None)
    projected["retrieved_knowledge"] = compact_knowledge_context(
        payload.get("knowledge_results") if isinstance(payload.get("knowledge_results"), list) else []
    )
    return projected


def compact_conversation_context(
    context: dict[str, object] | None,
    *,
    token_budget: int | None = None,
) -> dict[str, object]:
    """按 token 预算压缩对话上下文,超出预算时重建消息列表。

    预算依次取 token_budget、metadata.token_budget、CONTROL_CONTEXT_TOKEN_BUDGET;
    上下文非法时返回空上下文,未超预算则原样返回。
    """
    metadata = context.get("metadata") if isinstance(context, dict) else None
    configured_budget = (
        int(metadata.get("token_budget") or 0)
        if isinstance(metadata, dict)
        else 0
    )
    effective_budget = token_budget or configured_budget or CONTROL_CONTEXT_TOKEN_BUDGET
    if not isinstance(context, dict):
        return build_conversation_context([], effective_budget)
    turn_messages = context.setdefault(TURN_STAGE_MESSAGES_KEY, [])
    messages = context.get("messages")
    if not isinstance(messages, list):
        context["messages"] = []
        return context
    if (
        isinstance(metadata, dict)
        and int(metadata.get("estimated_tokens") or 0) <= effective_budget
    ):
        return context
    compacted = build_conversation_context(
        [message for message in messages if isinstance(message, dict)],
        effective_budget,
    )
    compacted[TURN_STAGE_MESSAGES_KEY] = turn_messages
    return compacted


def compact_current_step(
    content: dict[str, Any] | None,
    step_id: str | None,
) -> dict[str, Any] | None:
    """投影当前步骤节点:按 step_id 匹配,缺省回落到 start_node_id;未命中返回 None。"""
    if not isinstance(content, dict):
        return None
    resolved_step_id = step_id or _optional_text(content.get("start_node_id"))
    node = next(
        (
            item
            for item in _skill_nodes(content)
            if isinstance(item, dict)
            and _optional_text(item.get("node_id") or item.get("step_id")) == resolved_step_id
        ),
        None,
    )
    return _project_node(node) if node else None


def compact_step_skill_context(
    content: dict[str, Any] | None,
    step_id: str | None,
    *,
    skill_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    """投影步骤技能上下文:当前步骤,以及沿出边可达的下一步骤与迁移条件。

    step_id 缺省时取 start_node_id;当前步骤缺失时对应字段省略。
    """
    if not isinstance(content, dict):
        return None
    current_step = compact_current_step(content, step_id)
    current_step_id = _optional_text((current_step or {}).get("node_id"))
    nodes_by_id = {
        _optional_text(node.get("node_id") or node.get("step_id")): node
        for node in _skill_nodes(content)
        if isinstance(node, dict)
        and _optional_text(node.get("node_id") or node.get("step_id"))
    }
    edges = content.get("edges")
    next_steps: list[dict[str, Any]] = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        if _optional_text(edge.get("source_node_id")) != current_step_id:
            continue
        target_id = _optional_text(edge.get("next_node_id") or edge.get("target_node_id"))
        target_node = nodes_by_id.get(target_id)
        if not target_node:
            continue
        projected_step = _project_step_agent_node(target_node)
        transition = _project_transition(edge)
        if transition:
            projected_step["transition"] = transition
        next_steps.append(projected_step)
    return _without_empty(
        {
            "current_step": _project_step_agent_node(current_step or {}),
            "next_steps": next_steps,
        }
    )


def compact_router_decision(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """投影路由决策:只保留决策、目标任务/步骤与意图等必要字段,全空时返回 None。"""
    if not isinstance(payload, dict):
        return None
    projected = _without_empty(
        {
            key: payload.get(key)
            for key in (
                "decision",
                "selected_task_id",
                "target_skill_id",
                "target_step_id",
                "confidence",
                "user_intent",
                "reason",
                "clarification_question",
                "slot_hints",
            )
        }
    )
    return projected or None


def compact_step_router_decision(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """步骤级路由决策的精简投影:仅 decision 与截断到 300 字的 user_intent。"""
    if not isinstance(payload, dict):
        return None
    projected = _without_empty(
        {
            "decision": payload.get("decision"),
            "user_intent": _short_text(payload.get("user_intent"), 300),
        }
    )
    return projected or None


def compact_response_step_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    """回复步骤结果投影:保留回复、下一步与转人工等字段,全空时返回 None。"""
    projected = _without_empty(
        {
            key: payload.get(key)
            for key in ("reply", "next_step_id", "is_step_completed", "handoff")
        }
    )
    return projected or None


def compact_citation_hints(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """投影引用提示:只保留展示用的标签、类型、标题与来源路径,丢弃空值。"""
    return [
        {
            key: item.get(key)
            for key in (
                "label",
                "kind",
                "title",
                "source_path",
                "section_path",
            )
            if item.get(key) not in (None, "")
        }
        for item in citations
        if isinstance(item, dict)
    ]


def compact_memory_context(items: list[dict[str, Any]] | None) -> str:
    """把记忆条目渲染成以 "- " 开头的多行文本,单条截断到 1000 字并去重。"""
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = _short_text(item.get("content"), 1_000)
        if content and content not in lines:
            lines.append(content)
    return "\n".join(f"- {line}" for line in lines)


def compact_pending_tasks(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """投影待办任务:保留任务定位、槽位与意图摘要,丢弃全空条目。"""
    if not isinstance(items, list):
        return []
    tasks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task = _without_empty(
            {
                "task_id": item.get("task_id"),
                "status": item.get("status"),
                "skill_id": item.get("skill_id") or item.get("target_skill_id"),
                "step_id": item.get("step_id") or item.get("target_step_id"),
                "slots": item.get("slots") or item.get("slot_hints"),
                "intent_summary": _short_text(
                    item.get("intent_summary") or item.get("user_intent"), 300
                ),
                "source_message": _short_text(item.get("source_message"), 500),
                "resume_policy": item.get("resume_policy"),
            }
        )
        if task:
            tasks.append(task)
    return tasks


def compact_deferred_intents(
    items: list[dict[str, Any]] | None,
    *,
    selected_task_id: str | None = None,
) -> list[str]:
    """提取仍待处理的意图摘要,排除当前选中任务与非 pending 条目,并去重。"""
    if not isinstance(items, list):
        return []
    intents: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if selected_task_id and str(item.get("task_id") or "") == selected_task_id:
            continue
        if str(item.get("status") or "pending") != "pending":
            continue
        intent = _short_text(
            item.get("intent_summary")
            or item.get("user_intent")
            or item.get("source_message"),
            300,
        )
        if intent and intent not in intents:
            intents.append(intent)
    return intents


def compact_awaiting_input(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """投影等待用户补充信息的上下文:技能/步骤、期望字段与问题摘要。"""
    if not isinstance(value, dict):
        return None
    projected = _without_empty(
        {
            "skill_id": value.get("skill_id"),
            "step_id": value.get("step_id"),
            "expected_fields": value.get("expected_fields"),
            "question_summary": _short_text(value.get("question_summary"), 500),
        }
    )
    return projected or None


def _compact_knowledge_result(item: dict[str, Any]) -> dict[str, Any]:
    """压缩单条知识检索结果:查询词截断到 500 字,命中内容交给 _compact_retrieved_knowledge。"""
    query = item.get("query")
    if isinstance(query, dict):
        query = query.get("query")
    return _without_empty(
        {
            "query": _short_text(query, 500),
            "retrieved_knowledge": _compact_retrieved_knowledge(item),
        }
    )


def _compact_retrieved_knowledge(item: dict[str, Any]) -> list[dict[str, Any]]:
    """把知识来源候选压成带标签的精简条目,按身份去重并限制返回条数。

    同组或含多个分块的结果放宽内容长度上限;字段全空的条目直接丢弃。
    """
    candidates = []
    for value in knowledge_source_candidates(item):
        related_chunks = value.get("related_chunk_ids")
        grouped = bool(value.get("related_group_id")) or (
            isinstance(related_chunks, list) and len(related_chunks) > 1
        )
        candidates.append(
            {
                "title": _short_text(value.get("title"), 180),
                "source": _short_text(
                    value.get("section_path")
                    or value.get("source_path")
                    or value.get("concept_id"),
                    300,
                ),
                "summary": _short_text(value.get("summary"), 300),
                "content": _short_text(
                    value.get("content") or value.get("excerpt"),
                    KNOWLEDGE_RELATED_CONTENT_LIMIT if grouped else 800,
                ),
            }
        )

    compacted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        projected = _without_empty(candidate)
        identity = "|".join(
            str(projected.get(key) or "")
            for key in ("source", "title", "content", "summary")
        ).strip("|")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        compacted.append(
            {"label": f"检索到的知识 {len(compacted) + 1}", **projected}
        )
        if len(compacted) >= RETRIEVED_KNOWLEDGE_LIMIT:
            break
    return compacted


def _skill_nodes(content: dict[str, Any]) -> list[dict[str, Any]]:
    """取技能定义中的节点列表(优先 nodes,回落 steps),只保留字典项。"""
    value = content.get("nodes")
    if not isinstance(value, list):
        value = content.get("steps")
    return [item for item in value or [] if isinstance(item, dict)]


def _project_node(node: dict[str, Any]) -> dict[str, Any]:
    """把技能节点投影为提示词所需字段集合,并去掉空值。"""
    projected = {"node_id": node.get("node_id") or node.get("step_id")}
    projected.update(
        {
            key: node.get(key)
            for key in (
                "type",
                "name",
                "instruction",
                "optional",
                "condition",
                "expected_user_info",
                "allowed_actions",
                "knowledge_scope",
                "capability_refs",
                "retry_policy",
            )
        }
    )
    return _without_empty(projected)


def _project_step_agent_node(node: dict[str, Any]) -> dict[str, Any]:
    """投影步骤代理节点:节点标识、类型、指令、期望用户信息与可用能力。"""
    if not isinstance(node, dict):
        return {}
    return _without_empty(
        {
            "node_id": node.get("node_id") or node.get("step_id"),
            "type": node.get("type"),
            "instruction": node.get("instruction"),
            "expected_user_info": node.get("expected_user_info"),
            "allowed_actions": node.get("allowed_actions"),
            "knowledge_scope": node.get("knowledge_scope"),
            "capability_refs": node.get("capability_refs"),
        }
    )


def _project_transition(edge: dict[str, Any]) -> dict[str, Any]:
    """投影边上的迁移信息:仅保留条件与展示标签。"""
    return _without_empty(
        {
            key: edge.get(key)
            for key in (
                "condition",
                "label",
            )
        }
    )


def _short_text(value: object, limit: int) -> str:
    """把任意值压成单行文本并截断到 limit 字符,超长时在空白处断开并补省略号。

    若断点处正好是邮箱地址,则在该词结束后再截断,避免把邮箱切碎。
    """
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    boundary = limit
    token_start = text.rfind(" ", 0, limit) + 1
    token_end = text.find(" ", limit)
    if token_end < 0:
        token_end = len(text)
    boundary_token = text[token_start:token_end].strip("，。；：、,;:()（）[]【】<>")
    if EMAIL_PATTERN.fullmatch(boundary_token):
        boundary = token_end
    return text[:boundary].rstrip() + "..."


def _optional_text(value: object) -> str | None:
    """把值转成去空白后的字符串;结果为空时返回 None。"""
    text = str(value or "").strip()
    return text or None


def _without_empty(value: dict[str, Any]) -> dict[str, Any]:
    """去掉 None、空串、空列表与空字典字段,其余键值原样保留。"""
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }
