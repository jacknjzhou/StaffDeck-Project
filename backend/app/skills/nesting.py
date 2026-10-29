from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from app.db.models import Skill


MAX_NESTED_SOP_DEPTH = 8


class SopNestingError(ValueError):
    """SOP 嵌套配置错误：层级超限、出现环、子 SOP 缺失/未发布或子图不完整。"""


def sop_capability_scope(skill: Skill | dict[str, Any]) -> str:
    """读取 SOP 的能力范围并归一化为 sop_specific 或 general，缺失或其他值按 general。"""
    content = skill.content_json if isinstance(skill, Skill) else skill
    return (
        "sop_specific"
        if str((content or {}).get("capability_scope") or "").replace("-", "_")
        == "sop_specific"
        else "general"
    )


def discoverable_sops(skills: Iterable[Skill]) -> list[Skill]:
    """筛出可被其他 SOP 嵌套引用的通用 SOP，即能力范围为 general 的那些。"""
    return [skill for skill in skills if sop_capability_scope(skill) == "general"]


def nested_sop_ids(content: dict[str, Any]) -> list[str]:
    """按出现顺序去重列出 content 中所有 subflow 节点引用的 sub_sop_id。"""
    return list(
        dict.fromkeys(
            str(node.get("sub_sop_id") or "").strip()
            for node in content.get("nodes", [])
            if isinstance(node, dict) and str(node.get("type") or "") == "subflow"
            and str(node.get("sub_sop_id") or "").strip()
        )
    )


def validate_sop_nesting(
    skill_id: str,
    content: dict[str, Any],
    available: Iterable[Skill | dict[str, Any]],
) -> None:
    """校验 SOP 嵌套关系：子 SOP 是否存在、嵌套深度是否超限、有无环，违者抛 SopNestingError。

    只有 published/active 的 SOP 可被嵌套，待校验的 skill_id 自身无论状态都会载入；
    遍历基于内容深拷贝，不修改入参。
    """
    contents: dict[str, dict[str, Any]] = {}
    for row in available:
        row_id, row_status, row_content = _available_sop_parts(row)
        if row_id and (row_status in {"published", "active"} or row_id == skill_id):
            contents[row_id] = deepcopy(row_content)
    contents[skill_id] = deepcopy(content)

    def visit(current_id: str, path: list[str]) -> None:
        """深度优先遍历当前 SOP 的子 SOP：超深、路径成环或子 SOP 缺失即抛 SopNestingError。"""
        if len(path) > MAX_NESTED_SOP_DEPTH:
            raise SopNestingError(
                f"SOP nesting exceeds {MAX_NESTED_SOP_DEPTH} levels: "
                + " -> ".join(path)
            )
        current = contents.get(current_id)
        if current is None:
            raise SopNestingError(f"Nested SOP is missing or unpublished: {current_id}")
        for child_id in nested_sop_ids(current):
            if child_id in path:
                raise SopNestingError(
                    "SOP nesting cycle detected: " + " -> ".join([*path, child_id])
                )
            visit(child_id, [*path, child_id])

    visit(skill_id, [skill_id])


def _available_sop_parts(
    row: Skill | dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """把 Skill 行或普通字典统一解成（skill_id、状态、content）三元组。

    dict 未提供 content 时用扁平字段拼一个最小 content，状态缺省按 published 处理，
    供嵌套校验与展开共用。
    """
    if isinstance(row, Skill):
        return row.skill_id, row.status, row.content_json or {}
    content = row.get("content")
    if not isinstance(content, dict):
        content = {
            "capability_scope": row.get("capability_scope"),
            "nodes": row.get("nodes") if isinstance(row.get("nodes"), list) else [],
        }
    return (
        str(row.get("skill_id") or "").strip(),
        str(row.get("status") or "published").strip(),
        content,
    )


def expand_sop_for_execution(skill: Skill, available: Iterable[Skill]) -> Skill:
    """先校验嵌套关系，再把 subflow 占位节点展开成子 SOP 内联图，返回深拷贝副本。

    不修改传入的 skill；候选只含 published 的子 SOP，skill 自身总会纳入。
    """
    by_id = {row.skill_id: row for row in available if row.status == "published"}
    by_id.setdefault(skill.skill_id, skill)
    validate_sop_nesting(skill.skill_id, skill.content_json or {}, by_id.values())
    expanded = deepcopy(skill)
    expanded.content_json = _expand_content(
        skill.skill_id,
        deepcopy(skill.content_json or {}),
        by_id,
        path=[skill.skill_id],
    )
    return expanded


def expand_visible_sops(skills: Iterable[Skill]) -> list[Skill]:
    """批量展开可见 SOP：单个 SOP 嵌套非法（如子 SOP 归档/缺失）时跳过它，不影响其他。

    配置错误由发布时校验负责暴露给编辑者，这里只保证无关 SOP 与普通对话仍可运行。
    """
    rows = list(skills)
    expanded: list[Skill] = []
    for row in rows:
        try:
            expanded.append(expand_sop_for_execution(row, rows))
        except SopNestingError:
            # An archived/missing child must make its parent unavailable, but
            # it must not prevent unrelated SOPs and ordinary chat from
            # running. Publish-time validation is responsible for surfacing
            # the concrete configuration error to an editor.
            continue
    return expanded


def _expand_content(
    skill_id: str,
    content: dict[str, Any],
    by_id: dict[str, Skill],
    *,
    path: list[str],
) -> dict[str, Any]:
    """把 content 里的 subflow 节点递归内联成子 SOP 图：节点/边 id 加前缀，并重接入口与出口。

    子 SOP 缺失、未发布，或其图缺入口/终止节点时抛 SopNestingError；展开后合并
    required_info 与 response_rules 并去重，同时标记 runtime_expanded=True。
    """
    nodes = [deepcopy(node) for node in content.get("nodes", []) if isinstance(node, dict)]
    edges = [deepcopy(edge) for edge in content.get("edges", []) if isinstance(edge, dict)]
    start_node_id = str(content.get("start_node_id") or "")
    terminal_node_ids = [str(value) for value in content.get("terminal_node_ids", [])]
    required_info = list(content.get("required_info") or [])
    response_rules = list(content.get("response_rules") or [])

    for placeholder in list(nodes):
        if str(placeholder.get("type") or "") != "subflow":
            continue
        placeholder_id = str(placeholder.get("node_id") or "").strip()
        child_id = str(placeholder.get("sub_sop_id") or "").strip()
        child = by_id.get(child_id)
        if not placeholder_id or child is None:
            raise SopNestingError(f"Nested SOP is missing or unpublished: {child_id or placeholder_id}")
        child_content = _expand_content(
            child_id,
            deepcopy(child.content_json or {}),
            by_id,
            path=[*path, child_id],
        )
        prefix = f"{placeholder_id}::{child_id}::"
        child_nodes = child_content.get("nodes", [])
        child_edges = child_content.get("edges", [])
        id_map = {
            str(node.get("node_id") or ""): prefix + str(node.get("node_id") or "")
            for node in child_nodes
            if isinstance(node, dict) and node.get("node_id")
        }
        child_start = id_map.get(str(child_content.get("start_node_id") or ""))
        child_terminals = [
            id_map[node_id]
            for node_id in (str(value) for value in child_content.get("terminal_node_ids", []))
            if node_id in id_map
        ]
        if not child_start or not child_terminals:
            raise SopNestingError(f"Nested SOP graph is incomplete: {child_id}")

        namespaced_nodes: list[dict[str, Any]] = []
        for child_node in child_nodes:
            if not isinstance(child_node, dict):
                continue
            source_node_id = str(child_node.get("node_id") or "")
            if source_node_id not in id_map:
                continue
            next_node = deepcopy(child_node)
            next_node["node_id"] = id_map[source_node_id]
            metadata = dict(next_node.get("metadata") or {})
            metadata.setdefault("nested_sop_path", [*path, child_id])
            metadata.setdefault("source_sop_id", child_id)
            metadata.setdefault("source_node_id", source_node_id)
            metadata.setdefault("parent_sop_node_id", placeholder_id)
            # Expanded child nodes execute inside the parent's Harness task frame.
            # The shared scope makes this contract explicit for trace consumers;
            # expected fields intentionally stay un-namespaced so parent and child
            # can read and fill the same durable session slots.
            metadata.setdefault("slot_scope", "parent_task_frame")
            next_node["metadata"] = metadata
            namespaced_nodes.append(next_node)

        namespaced_edges = [
            {
                **deepcopy(edge),
                "source_node_id": id_map[str(edge.get("source_node_id") or "")],
                "next_node_id": id_map[str(edge.get("next_node_id") or "")],
            }
            for edge in child_edges
            if isinstance(edge, dict)
            and str(edge.get("source_node_id") or "") in id_map
            and str(edge.get("next_node_id") or "") in id_map
        ]

        incoming = [edge for edge in edges if str(edge.get("next_node_id") or "") == placeholder_id]
        outgoing = [edge for edge in edges if str(edge.get("source_node_id") or "") == placeholder_id]
        edges = [
            edge
            for edge in edges
            if str(edge.get("source_node_id") or "") != placeholder_id
            and str(edge.get("next_node_id") or "") != placeholder_id
        ]
        edges.extend({**edge, "next_node_id": child_start} for edge in incoming)
        for terminal_id in child_terminals:
            edges.extend({**edge, "source_node_id": terminal_id} for edge in outgoing)
        edges.extend(namespaced_edges)
        nodes = [node for node in nodes if str(node.get("node_id") or "") != placeholder_id]
        nodes.extend(namespaced_nodes)
        if start_node_id == placeholder_id:
            start_node_id = child_start
        if placeholder_id in terminal_node_ids:
            terminal_node_ids = [
                node_id for node_id in terminal_node_ids if node_id != placeholder_id
            ] + child_terminals
        required_info.extend(child_content.get("required_info") or [])
        response_rules.extend(child_content.get("response_rules") or [])

    content["nodes"] = nodes
    content["edges"] = edges
    content["start_node_id"] = start_node_id
    content["terminal_node_ids"] = list(dict.fromkeys(terminal_node_ids))
    content["required_info"] = list(dict.fromkeys(str(value) for value in required_info))
    content["response_rules"] = list(dict.fromkeys(str(value) for value in response_rules))
    content["runtime_expanded"] = True
    return content
