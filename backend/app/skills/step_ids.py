from __future__ import annotations

from typing import Any

from app.skills.skill_schema import SkillCard


def skill_card_with_unique_step_ids(card: SkillCard) -> tuple[SkillCard, list[str]]:
    """对技能卡的节点 ID 去重，并同步改写起点、终点与连线上的引用。

    返回去重后的技能卡与修正说明列表；未发生改写时说明列表为空。
    """
    content = card.model_dump(mode="json")
    nodes, warnings = ensure_unique_node_ids(content.get("nodes", []))
    id_map = {
        str(original): str(node.get("node_id") or "")
        for original, node in zip(
            [item.get("node_id") for item in content.get("nodes", []) if isinstance(item, dict)],
            nodes,
            strict=False,
        )
        if original and node.get("node_id")
    }
    content["nodes"] = nodes
    if id_map:
        content["start_node_id"] = id_map.get(content.get("start_node_id"), content.get("start_node_id"))
        content["terminal_node_ids"] = [
            id_map.get(node_id, node_id) for node_id in content.get("terminal_node_ids", [])
        ]
        for edge in content.get("edges", []):
            if not isinstance(edge, dict):
                continue
            edge["source_node_id"] = id_map.get(edge.get("source_node_id"), edge.get("source_node_id"))
            edge["next_node_id"] = id_map.get(edge.get("next_node_id"), edge.get("next_node_id"))
    return SkillCard.model_validate(content), warnings


def ensure_unique_step_ids(steps: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """按 `step_id` 字段对步骤列表去重，返回归一化步骤与修正说明。"""
    return ensure_unique_node_ids(steps, id_field="step_id", label="步骤")


def ensure_unique_node_ids(
    nodes: list[Any],
    id_field: str = "node_id",
    label: str = "节点",
) -> tuple[list[dict[str, Any]], list[str]]:
    """保证节点 ID 唯一：缺失的按序号补全，重复的追加数字后缀。

    返回归一化后的节点列表与修正说明；列表中的非字典项会被直接丢弃，
    修正说明只覆盖真正发生变化的条目。
    """
    used: set[str] = set()
    normalized_nodes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, raw_node in enumerate(nodes):
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        original = str(node.get(id_field) or "").strip()
        base = original or f"node_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        if candidate != original:
            if original:
                warnings.append(f"{label} {index + 1} 的 {id_field} 已修正为 `{candidate}`。")
            else:
                warnings.append(f"{label} {index + 1} 的 {id_field} 已补全为 `{candidate}`。")
            node[id_field] = candidate
        else:
            node[id_field] = original
        used.add(candidate)
        normalized_nodes.append(node)
    return normalized_nodes, warnings
