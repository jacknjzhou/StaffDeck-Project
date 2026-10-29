from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_skill_rows
from app.digital_employees.contract import (
    MAX_SOP_CONTENT,
    MAX_SOP_TITLE,
    MAX_SOPS,
    clamp_items,
    clamp_text,
)

LOCAL = "local"
REMOTE = "remote"
EMPTY_SOP = "（SOP 结构缺失，无法渲染）"


def effective_hint(mode: str, hint: str | None) -> str:
    """判定单条 SOP 在哪里执行：显式 binding hint 优先于员工级 mode。

    未知 mode 一律回落到 LOCAL，避免把员工 SOP 静默转发到远端执行。
    """
    normalized = (hint or "").strip().lower()
    if normalized in {LOCAL, REMOTE}:
        return normalized
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == REMOTE:
        return REMOTE
    # local / hybrid / anything unknown must stay on the client: a bad mode must
    # never silently forward an employee's SOPs into remote execution.
    return LOCAL


def render_sop_content(card: dict[str, Any] | None) -> str:
    """把存储的 SkillCard 渲染成纯文本，供注入 prompt 时使用。

    card 缺失或 nodes 为空时返回 EMPTY_SOP；渲染顺序沿 start_node_id 流转，
    不可达节点按原顺序追加，避免模型漏掉 SOP 中的步骤。
    """
    if not isinstance(card, dict):
        return EMPTY_SOP
    nodes = card.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return EMPTY_SOP

    lines: list[str] = []

    goals = [str(item).strip() for item in card.get("goal") or [] if str(item).strip()]
    if goals:
        lines.append("目标：")
        lines.extend(f"- {goal}" for goal in goals)
        lines.append("")

    lines.append("步骤：")
    ordered = _ordered_nodes(card, nodes)
    for index, node in enumerate(ordered, start=1):
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or node.get("node_id") or f"步骤 {index}").strip()
        lines.append(f"{index}. {name}")
        instruction = str(node.get("instruction") or "").strip()
        if instruction:
            lines.append(f"   指令：{instruction}")
        expected = [
            str(item).strip() for item in node.get("expected_user_info") or [] if str(item).strip()
        ]
        if expected:
            lines.append(f"   需收集：{'、'.join(expected)}")
        actions = [
            str(item).strip() for item in node.get("allowed_actions") or [] if str(item).strip()
        ]
        if actions:
            lines.append(f"   可用动作：{'、'.join(actions)}")

    transitions = _transitions(card, nodes)
    if transitions:
        lines.append("")
        lines.append("流转：")
        lines.extend(f"- {item}" for item in transitions)

    rules = [str(item).strip() for item in card.get("response_rules") or [] if str(item).strip()]
    if rules:
        lines.append("")
        lines.append("回复规则：")
        lines.extend(f"- {rule}" for rule in rules)

    return "\n".join(lines).strip()


def _ordered_nodes(card: dict[str, Any], nodes: list[Any]) -> list[Any]:
    """按 start_node_id 与边的优先级排出节点顺序，并补上从起点不可达的节点。"""
    by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    start = str(card.get("start_node_id") or "")
    ordered: list[Any] = []
    seen: set[str] = set()
    current = start or next(iter(by_id), "")
    while current and current in by_id and current not in seen:
        seen.add(current)
        ordered.append(by_id[current])
        current = _next_node_id(card, current)
    # Nodes unreachable from start are still part of the SOP the model must know.
    ordered.extend(
        node
        for node in nodes
        if isinstance(node, dict) and str(node.get("node_id")) not in seen
    )
    return ordered


def _next_node_id(card: dict[str, Any], node_id: str) -> str:
    """取该节点出边中优先级最高的目标节点 id，没有可用出边时返回空字符串。"""
    best: tuple[int, str] | None = None
    for edge in card.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("source_node_id")) != node_id:
            continue
        target = str(edge.get("next_node_id") or "")
        if not target:
            continue
        priority = int(edge.get("priority") or 0)
        if best is None or priority > best[0]:
            best = (priority, target)
    return best[1] if best else ""


def _transitions(card: dict[str, Any], nodes: list[Any]) -> list[str]:
    """把 SOP 的边渲染成「源 → 目标（条件）」文本，节点 id 尽量替换为可读名称。"""
    names = {
        str(node.get("node_id")): str(node.get("name") or node.get("node_id"))
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }
    items: list[str] = []
    for edge in card.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = names.get(str(edge.get("source_node_id")), str(edge.get("source_node_id")))
        target = names.get(str(edge.get("next_node_id")), str(edge.get("next_node_id")))
        label = str(edge.get("label") or edge.get("condition") or "").strip()
        items.append(f"{source} → {target}（{label}）" if label else f"{source} → {target}")
    return items


def select_sop_documents(
    db: Session,
    tenant_id: str,
    agent_id: str,
    mode: str,
    binding_metadata: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    """挑出需要在客户端本地注入的 SOP 文档，并附带截断类告警。

    仅保留 execution_hint/mode 判定为本地执行的可见技能，按名称排序后受 MAX_SOPS、
    MAX_SOP_TITLE、MAX_SOP_CONTENT 约束；被省略或截断的情况写入 warnings。
    """
    warnings: list[str] = []
    documents: list[dict[str, str]] = []
    local_rows = []
    for row in visible_skill_rows(db, tenant_id, agent_id, include_inactive=False):
        hint = (binding_metadata.get(row.id) or {}).get("execution_hint")
        if effective_hint(mode, hint if isinstance(hint, str) else None) == LOCAL:
            local_rows.append(row)

    local_rows.sort(key=lambda row: (row.name or row.skill_id, row.skill_id))
    kept, overflowed = clamp_items(local_rows, MAX_SOPS)
    if overflowed:
        warnings.append(
            f"该员工有 {len(local_rows)} 条本地 SOP，超出契约上限 {MAX_SOPS} 条，已省略其余。"
        )

    for row in kept:
        title, title_cut = clamp_text(str(row.name or row.skill_id), MAX_SOP_TITLE)
        content, content_cut = clamp_text(render_sop_content(row.content_json), MAX_SOP_CONTENT)
        if not content.strip():
            content = "（SOP 正文为空）"
        if title_cut or content_cut:
            warnings.append(f"SOP「{title}」内容超出契约上限，已截断。")
        documents.append({"title": title or row.skill_id, "content": content})
    return documents, warnings
