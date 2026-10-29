from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.core.capability_discovery import model_descriptor
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
    TaskRequirement,
)
from app.db.models import ChatSession, Skill, new_id
from app.session.session_schema import PlannedTaskFrame, TurnPlan


SlashCommandKind = Literal["sop", "skill", "tool"]
_COMMAND_ALIASES: dict[str, SlashCommandKind] = {
    "sop": "sop",
    "流程": "sop",
    "skill": "skill",
    "技能": "skill",
    "tool": "tool",
    "工具": "tool",
}
_COMMAND_PATTERN = re.compile(
    r"^/(?P<kind>sop|skill|tool|流程|技能|工具)(?:\s+|:)(?P<target>\S+)(?:\s+(?P<prompt>[\s\S]*))?$",
    re.IGNORECASE,
)
_INCOMPLETE_COMMAND_PATTERN = re.compile(
    r"^/(?:sop|skill|tool|流程|技能|工具)(?:\s*|:?)$",
    re.IGNORECASE,
)


class SlashCommandSelection(BaseModel):
    """解析后的斜杠指令：类型（sop/skill/tool）、目标标识、附带提示词与原始文本。"""

    kind: SlashCommandKind
    target: str
    prompt: str = ""
    raw: str


class SlashCommandRead(BaseModel):
    """候选列表用的指令条目：类型、目标、展示名、描述与可直接输入的完整命令。"""

    kind: SlashCommandKind
    target: str
    label: str
    description: str = ""
    command: str


class SlashCommandError(RuntimeError):
    """斜杠指令解析或目标定位失败时抛出，带稳定错误码与用户可见文案。"""

    def __init__(self, code: str, message: str):
        """保存错误码与提示文案，并交给 RuntimeError 携带消息本身。"""
        super().__init__(message)
        self.code = code
        self.message = message


def parse_slash_command(message: str) -> SlashCommandSelection | None:
    """把 `/sop 目标 提示词` 这类消息解析成指令选择；不是斜杠指令时返回 None。

    只写了类型没写目标时抛 SLASH_COMMAND_TARGET_REQUIRED，提示用户从候选列表
    中挑选。类型别名兼容中文（流程/技能/工具），大小写不敏感，提示词可省略。
    """
    text = str(message or "").strip()
    match = _COMMAND_PATTERN.fullmatch(text)
    if match is None:
        if _INCOMPLETE_COMMAND_PATTERN.fullmatch(text):
            raise SlashCommandError(
                "SLASH_COMMAND_TARGET_REQUIRED",
                "斜杠指令缺少目标，请从候选列表选择一个 SOP、技能或工具。",
            )
        return None
    raw_kind = match.group("kind").lower()
    kind = _COMMAND_ALIASES[raw_kind]
    target = match.group("target").strip()
    prompt = str(match.group("prompt") or "").strip()
    return SlashCommandSelection(
        kind=kind,
        target=target,
        prompt=prompt,
        raw=text,
    )


def slash_command_message(
    selection: SlashCommandSelection,
    *,
    label: str | None = None,
) -> str:
    """把指令选择还原成一句自然语言请求，供后续轮次当作用户消息。

    指令自带提示词时直接沿用；label 用来把目标 id 换成更适合展示的名称，
    缺省时回落到 target。
    """
    if selection.prompt:
        return selection.prompt
    display = str(label or selection.target).strip()
    if selection.kind == "sop":
        return f"开始并执行 SOP：{display}"
    if selection.kind == "skill":
        return f"使用技能 {display} 完成当前任务"
    return f"调用工具 {display} 完成当前任务"


def resolve_sop(
    selection: SlashCommandSelection,
    skills: list[Skill],
) -> Skill:
    """按 skill_id 或名称在可用 SOP 列表里唯一定位技能。

    非 SOP 指令抛 SLASH_COMMAND_KIND_INVALID；匹配不到或匹配到多个都按不可用
    处理并抛 SLASH_COMMAND_NOT_AVAILABLE，避免误执行其它流程。
    """
    if selection.kind != "sop":
        raise SlashCommandError("SLASH_COMMAND_KIND_INVALID", "当前指令不是 SOP 指令。")
    target = _normalized(selection.target)
    exact = [
        skill
        for skill in skills
        if target in {_normalized(skill.skill_id), _normalized(skill.name)}
    ]
    if len(exact) != 1:
        raise SlashCommandError(
            "SLASH_COMMAND_NOT_AVAILABLE",
            "指定的 SOP 不存在、未发布或未绑定到当前员工。",
        )
    return exact[0]


def resolve_capability(
    selection: SlashCommandSelection,
    manifest: CapabilityManifest,
) -> CapabilityDescriptor:
    """在已授权的能力清单里按多种别名唯一定位技能或工具。

    比对 capability_id、name、slug、source_tool_name 等别名，且只接受 available
    的条目；命中数不为 1 时抛 SLASH_COMMAND_NOT_AVAILABLE。
    """
    expected_kind = "general_skill" if selection.kind == "skill" else "tool"
    target = _normalized(selection.target)
    matches: list[CapabilityDescriptor] = []
    for descriptor in manifest.available:
        if not descriptor.available or descriptor.kind != expected_kind:
            continue
        aliases = {
            _normalized(descriptor.capability_id),
            _normalized(descriptor.name),
        }
        if descriptor.kind == "general_skill":
            aliases.add(_normalized(descriptor.metadata.get("slug")))
            aliases.add(_normalized(descriptor.name.removeprefix("general_skill.")))
        if descriptor.kind == "tool":
            aliases.add(_normalized(descriptor.metadata.get("source_tool_name")))
        if target in aliases:
            matches.append(descriptor)
    if len(matches) != 1:
        noun = "技能" if selection.kind == "skill" else "工具"
        raise SlashCommandError(
            "SLASH_COMMAND_NOT_AVAILABLE",
            f"指定的{noun}不存在、未启用、未绑定，或属于只能由 SOP 明确授权的能力。",
        )
    return matches[0]


def build_slash_turn_plan(
    selection: SlashCommandSelection,
    message: str,
    session: ChatSession,
    skills: list[Skill],
    task_frame_state: list[dict[str, object]] | None = None,
) -> TurnPlan:
    """为斜杠指令直接构造确定性的 TurnPlan，跳过意图识别环节。

    SOP 指令会复用已有任务帧：与当前活动技能一致则 continue_active，存在未结束
    帧则 switch_to_pending，否则 start_new_task，并从 start_node_id 起步。技能与
    工具指令只产出 answer_only 帧，具体能力由调用方另外强制注入。
    """
    if selection.kind == "sop":
        skill = resolve_sop(selection, skills)
        known = _known_sop_frame(skill.skill_id, task_frame_state)
        active = session.active_skill_id == skill.skill_id
        decision = "continue_active" if active else ("switch_to_pending" if known else "start_new_task")
        task_id = str(known.get("task_id") or "") if known else new_id("task")
        step_id = (
            session.active_step_id
            if active and session.active_step_id
            else str((known or {}).get("step_id") or "").strip() or _first_node_id(skill)
        )
        return TurnPlan(
            decision=decision,
            selected_task_id=task_id if decision == "switch_to_pending" else None,
            confidence=1.0,
            user_intent=message,
            reason="用户通过斜杠指令明确选择了 SOP。",
            task_frames=[
                PlannedTaskFrame(
                    task_id=task_id,
                    kind="sop",
                    decision=decision,
                    target_skill_id=skill.skill_id,
                    target_step_id=step_id,
                    user_intent=message,
                    requirements=[message],
                    source_message=message,
                )
            ],
        )
    return TurnPlan(
        decision="answer_only",
        confidence=1.0,
        user_intent=message,
        reason=f"用户通过斜杠指令明确选择了{('技能' if selection.kind == 'skill' else '工具')}。",
        task_frames=[
            PlannedTaskFrame(
                task_id=new_id("task"),
                kind="conversation",
                decision="answer_only",
                user_intent=message,
                requirements=[message],
                source_message=message,
            )
        ],
    )


def force_capability_for_requirement(
    requirement: TaskRequirement,
    projected_manifest: CapabilityManifest,
    descriptor: CapabilityDescriptor,
) -> None:
    """把用户指定的能力强制写进投影清单与任务需求，确保后续步骤真能调用它。

    会去重写入 required_capability_names 并补一条完成标准；descriptor 先经
    model_descriptor 投影，避免把授权内部字段带进模型上下文。
    """
    projected = model_descriptor(descriptor)
    target_manifests = [projected_manifest]
    if requirement.capability_manifest is not projected_manifest:
        target_manifests.append(requirement.capability_manifest)
    for target_manifest in target_manifests:
        target_manifest.available = [
            item for item in target_manifest.available if item.name != projected.name
        ]
        target_manifest.available.append(projected)
        target_manifest.catalog = [
            item for item in target_manifest.catalog if item.name != projected.name
        ]
    if projected.name not in requirement.required_capability_names:
        requirement.required_capability_names.append(projected.name)
    requirement.completion_criteria = _unique(
        [
            *requirement.completion_criteria,
            f"成功调用用户通过斜杠指定的能力：{projected.name}",
        ]
    )


def slash_command_catalog(
    skills: list[Skill],
    manifest: CapabilityManifest,
) -> list[SlashCommandRead]:
    """汇总当前可用的 SOP、技能与工具，生成前端候选指令列表。

    技能与工具只取 manifest 中 available 的 general_skill 与 tool 条目；结果先按
    sop、skill、tool 分组，再依展示名与目标标识排序。
    """
    rows = [
        SlashCommandRead(
            kind="sop",
            target=skill.skill_id,
            label=skill.name,
            description=skill.description or "",
            command=f"/sop {skill.skill_id}",
        )
        for skill in skills
    ]
    for descriptor in manifest.available:
        if not descriptor.available or descriptor.kind not in {"general_skill", "tool"}:
            continue
        if descriptor.kind == "general_skill":
            target = str(descriptor.metadata.get("slug") or descriptor.capability_id).strip()
            kind: SlashCommandKind = "skill"
        else:
            target = str(
                descriptor.metadata.get("source_tool_name") or descriptor.capability_id
            ).strip()
            kind = "tool"
        rows.append(
            SlashCommandRead(
                kind=kind,
                target=target,
                label=(
                    str(descriptor.metadata.get("display_name") or "").strip()
                    or descriptor.name.removeprefix("general_skill.")
                ),
                description=descriptor.description,
                command=f"/{kind} {target}",
            )
        )
    order = {"sop": 0, "skill": 1, "tool": 2}
    return sorted(rows, key=lambda item: (order[item.kind], item.label.lower(), item.target.lower()))


def _known_sop_frame(
    skill_id: str,
    task_frame_state: list[dict[str, object]] | None,
) -> dict[str, object] | None:
    """在任务帧状态里找该 SOP 未结束的帧，优先返回标记为 active 的那一个。"""
    candidates: list[dict[str, object]] = []
    for item in task_frame_state or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("skill_id") or "").strip() != skill_id:
            continue
        if str(item.get("status") or "").strip() in {"completed", "cancelled", "failed"}:
            continue
        candidates.append(item)
    return next(
        (item for item in candidates if item.get("active") is True),
        candidates[0] if candidates else None,
    )


def _first_node_id(skill: Skill) -> str | None:
    """取 SOP 的起始节点：优先 start_node_id，其次首个节点的 node_id 或 step_id。"""
    content = skill.content_json or {}
    start = str(content.get("start_node_id") or "").strip()
    if start:
        return start
    for node in content.get("nodes") or content.get("steps") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("step_id") or "").strip()
        if node_id:
            return node_id
    return None


def _normalized(value: object) -> str:
    """归一化用于匹配的键：压缩空白、去首尾空格并转小写。"""
    return " ".join(str(value or "").split()).strip().casefold()


def _unique(values: list[str]) -> list[str]:
    """按首次出现顺序去重并压缩空白，空串直接丢弃。"""
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in result:
            result.append(text)
    return result


__all__ = [
    "SlashCommandError",
    "SlashCommandRead",
    "SlashCommandSelection",
    "build_slash_turn_plan",
    "force_capability_for_requirement",
    "parse_slash_command",
    "resolve_capability",
    "resolve_sop",
    "slash_command_catalog",
    "slash_command_message",
]
