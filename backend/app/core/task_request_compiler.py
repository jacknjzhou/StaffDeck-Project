from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.db.models import ChatSession, Skill
from app.session.session_schema import PlannedTaskFrame

CapabilityKind = Literal[
    "general_skill",
    "knowledge",
    "tool",
    "file",
    "internal",
]


class CapabilityDescriptor(BaseModel):
    """单个能力的描述：标识、类型、入参模式与当前可用性。"""
    capability_id: str
    name: str
    kind: CapabilityKind
    capability_scope: Literal["general", "sop_specific"] = "general"
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None


class CapabilityCatalogEntry(BaseModel):
    """能力目录的轻量条目，只保留列举所需字段以控制提示词体积。"""
    capability_id: str
    name: str
    kind: CapabilityKind
    capability_scope: Literal["general", "sop_specific"] = "general"
    description: str = ""


class CapabilityManifest(BaseModel):
    """当前 agent 的能力清单：可用能力、目录摘要与失效引用。"""
    available: list[CapabilityDescriptor] = Field(default_factory=list)
    catalog: list[CapabilityCatalogEntry] = Field(default_factory=list)
    catalog_total: int = 0
    catalog_truncated: bool = False
    catalog_budget_chars: int = 8_000
    unavailable_references: list[CapabilityDescriptor] = Field(default_factory=list)
    snapshot_revision: str = ""

    def allowed_names(self) -> set[str]:
        """返回可用且名称非空的能力名称集合，供白名单校验使用。"""
        return {
            item.name for item in self.available if item.available and str(item.name or "").strip()
        }


class TaskRequirement(BaseModel):
    """交给执行器的单次任务需求，聚合帧信息、SOP 上下文与能力清单。"""
    task_frame_id: str
    kind: Literal["sop", "conversation"]
    goal: str
    # 当前时间（用户时区，含偏移与星期），供模型换算"明天/下周一"等相对
    # 日期。模型无法自知"今天几号"，若不注入则依赖它主动去查；真实案例中
    # 它改为让 shell 代算（`date -d tomorrow; date`），GNU 参数在 macOS 上
    # 报错但被 `;` 吞掉退出码，后一条命令补上今天的日期，静默给出错答案。
    current_time: str = ""
    source_user_message: str = ""
    out_of_scope_task_intents: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    sop_context: dict[str, Any] = Field(default_factory=dict)
    required_slots: list[str] = Field(default_factory=list)
    known_slots: dict[str, Any] = Field(default_factory=dict)
    completion_criteria: list[str] = Field(default_factory=list)
    required_capability_names: list[str] = Field(default_factory=list)
    required_knowledge_base_ids: list[str] = Field(default_factory=list)
    allowed_transitions: list[dict[str, Any]] = Field(default_factory=list)
    memory_projection: list[dict[str, str]] = Field(default_factory=list)
    prior_task_results: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    published_deliverables: list[dict[str, Any]] = Field(default_factory=list)
    capability_manifest: CapabilityManifest = Field(default_factory=CapabilityManifest)


class TaskExecutionResult(BaseModel):
    """执行器返回的单次任务结果，包含状态、槽位更新、产物与错误。"""
    task_frame_id: str
    status: Literal[
        "completed",
        "waiting_external_task",
        "awaiting_user",
        "handoff",
        "failed",
        "blocked",
        "action_budget",
    ]
    reply_fragment: str = ""
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    next_step_id: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_results: list[dict[str, Any]] = Field(default_factory=list)
    capability_results: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    task_summary: str = ""
    action_count: int = 0
    error: dict[str, Any] | None = None
    structured_result: Any | None = None
    loop_checkpoint: dict[str, Any] = Field(default_factory=dict, exclude=True)


class TaskRequestCompiler:
    """把已持久化的任务帧与当前 SOP 节点编译成一份可执行的任务需求。"""

    def compile(
        self,
        frame: PlannedTaskFrame,
        session: ChatSession,
        skill: Skill | None,
        manifest: CapabilityManifest,
        memory_context: list[dict[str, object]] | None = None,
        prior_task_results: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        published_deliverables: list[dict[str, Any]] | None = None,
        source_user_message: str | None = None,
        out_of_scope_task_intents: list[str] | None = None,
        client_timezone: str | None = None,
    ) -> TaskRequirement:
        """按帧、会话与技能上下文组装 TaskRequirement。

        仅 sop 帧会带入会话中已填的槽位；当前节点的 expected_user_info 用于推导
        required_slots，强制能力与知识库要求会追加进 completion_criteria。
        """
        current_node = _current_node(skill, frame.target_step_id or session.active_step_id)
        expected_fields = _text_list((current_node or {}).get("expected_user_info"))
        known_slots = (
            {
                str(key): value
                for key, value in dict(session.slots_json or {}).items()
                if value not in (None, "", [], {})
            }
            if frame.kind == "sop"
            else {}
        )
        required_slots = [
            field for field in expected_fields if not _slot_satisfied(known_slots.get(field))
        ]
        requirements = _unique(
            [
                str((current_node or {}).get("instruction") or "").strip(),
                ("补齐以下字段：" + "、".join(required_slots) if required_slots else ""),
                *frame.requirements,
            ]
        )
        goal = _goal(frame, skill, current_node, requirements)
        completion_criteria = _unique(
            [
                (
                    "收集并确认当前步骤要求的字段：" + "、".join(expected_fields)
                    if expected_fields
                    else ""
                ),
                *(_text_list((skill.content_json or {}).get("goal")) if skill is not None else []),
                "完整处理 TaskRequirement 中的全部子需求。",
            ]
        )
        required_capability_names, required_knowledge_base_ids = _required_step_capabilities(
            current_node,
            manifest,
        )
        if required_capability_names:
            completion_criteria = _unique(
                [
                    *completion_criteria,
                    "成功调用当前 SOP 节点标记为强制执行的能力："
                    + "、".join(required_capability_names),
                ]
            )
        if required_knowledge_base_ids:
            completion_criteria = _unique(
                [
                    *completion_criteria,
                    "检索当前 SOP 节点要求的知识库：" + "、".join(required_knowledge_base_ids),
                ]
            )
        return TaskRequirement(
            task_frame_id=str(frame.task_id or ""),
            kind=frame.kind,
            goal=goal,
            current_time=_current_time_text(client_timezone),
            source_user_message=str(source_user_message or "").strip()[:4_000],
            out_of_scope_task_intents=_unique(
                [str(item or "") for item in out_of_scope_task_intents or []]
            ),
            requirements=requirements or [goal],
            sop_context=_sop_context(skill, current_node),
            required_slots=required_slots,
            known_slots=known_slots,
            completion_criteria=completion_criteria,
            required_capability_names=required_capability_names,
            required_knowledge_base_ids=required_knowledge_base_ids,
            allowed_transitions=_transitions(skill, current_node),
            memory_projection=_memory_projection(memory_context),
            prior_task_results=list(prior_task_results or []),
            attachments=list(attachments or []),
            published_deliverables=list(published_deliverables or []),
            capability_manifest=manifest,
        )


def _current_time_text(client_timezone: str | None = None) -> str:
    """当前时间文本；优先用户所在时区，缺省或非法时回退服务端本地时区。

    注入错误时区的时间比不注入更危险（看起来权威，模型不会质疑），
    因此时区来源必须显式，且结果始终带 UTC 偏移。
    """

    zone: ZoneInfo | None = None
    name = str(client_timezone or "").strip()
    if name:
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = None
    now = datetime.now(zone) if zone is not None else datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    return f"{now.isoformat(timespec='minutes')}（周{weekday}）"


def current_step_capability_refs(skill: Skill | None, step_id: str | None) -> dict[str, list[str]]:
    """汇总当前节点引用的能力：通用技能、工具与知识库。

    capability_refs 是主来源，同时兼容旧 SOP 的 call_tool: 动作与 knowledge_scope，
    合并去重后按三类返回。
    """
    node = _current_node(skill, step_id)
    refs = (node or {}).get("capability_refs")
    result = {
        "general_skill_ids": [],
        "tool_ids": [],
        "knowledge_base_ids": [],
    }
    if isinstance(refs, dict):
        for key in result:
            result[key] = _text_list(refs.get(key))

    # Backward compatibility while old SOP content is migrated.
    for action in _text_list((node or {}).get("allowed_actions")):
        if action.startswith("call_tool:"):
            tool_ref = action.partition(":")[2].strip()
            if tool_ref and tool_ref not in result["tool_ids"]:
                result["tool_ids"].append(tool_ref)
    knowledge_scope = (node or {}).get("knowledge_scope")
    if isinstance(knowledge_scope, dict):
        for kb_id in _text_list(knowledge_scope.get("knowledge_base_ids")):
            if kb_id not in result["knowledge_base_ids"]:
                result["knowledge_base_ids"].append(kb_id)
    return result


def current_step_authorization_skill_ids(
    skill: Skill | None,
    step_id: str | None,
) -> set[str]:
    """返回当前展开节点涉及的每个 SOP 身份，用于能力授权判定。

    嵌套 SOP 节点在父级任务帧内执行，运行时 Skill 仍保留父级 skill_id，
    展开元数据里的 nested_sop_path 与 source_sop_id 才是子调用链的权威来源。
    同时保留父子两级身份，既保留父级工具授权，也允许子级独有授权在展开后继续生效。
    """
    if skill is None:
        return set()
    authorized = {str(skill.skill_id or "").strip()}
    node = _current_node(skill, step_id)
    metadata = (node or {}).get("metadata")
    if isinstance(metadata, dict):
        nested_path = metadata.get("nested_sop_path")
        if isinstance(nested_path, list):
            authorized.update(_text_list(nested_path))
        source_sop_id = str(metadata.get("source_sop_id") or "").strip()
        if source_sop_id:
            authorized.add(source_sop_id)
    return {value for value in authorized if value}


def _current_node(skill: Skill | None, step_id: str | None) -> dict[str, Any] | None:
    """按 step_id 取技能中的节点，缺省时回退到 start_node_id。"""
    if skill is None:
        return None
    content = skill.content_json or {}
    resolved_step_id = str(step_id or content.get("start_node_id") or "").strip()
    for node in content.get("nodes") or content.get("steps") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("step_id") or "").strip()
        if node_id and node_id == resolved_step_id:
            return node
    return None


def _required_step_capabilities(
    current_node: dict[str, Any] | None,
    manifest: CapabilityManifest,
) -> tuple[list[str], list[str]]:
    """把当前节点声明的能力引用解析为强制能力名称与知识库 id。

    仅采纳 manifest 中可用的能力；引用了知识库时统一追加 knowledge_search，
    保证完成闸能校验检索动作。
    """
    if not current_node:
        return [], []
    refs = current_node.get("capability_refs")
    if not isinstance(refs, dict):
        return [], []
    required_tool_refs = set(_text_list(refs.get("required_tool_ids")))
    required_skill_refs = set(_text_list(refs.get("required_general_skill_ids")))
    required_knowledge_base_ids = _text_list(refs.get("required_knowledge_base_ids"))
    required: list[str] = []
    for descriptor in manifest.available:
        if not descriptor.available:
            continue
        matches_tool = descriptor.kind == "tool" and (
            descriptor.capability_id in required_tool_refs or descriptor.name in required_tool_refs
        )
        matches_skill = descriptor.kind == "general_skill" and (
            descriptor.capability_id in required_skill_refs
            or descriptor.name in required_skill_refs
        )
        # 内置能力（如 lark_cli）也允许被 SOP 节点标记为强制执行，
        # 使 finish 闸（REQUIRED_CAPABILITY_NOT_INVOKED）同样生效。
        matches_internal = descriptor.kind == "internal" and (
            descriptor.capability_id in required_tool_refs
            or descriptor.name in required_tool_refs
        )
        if matches_tool or matches_skill or matches_internal:
            required.append(descriptor.name)
    if required_knowledge_base_ids:
        required.append("knowledge_search")
    return _unique(required), required_knowledge_base_ids


def _transitions(skill: Skill | None, current_node: dict[str, Any] | None) -> list[dict[str, Any]]:
    """列出当前节点出边的转移信息，供模型判断合法的下一步。"""
    if skill is None or current_node is None:
        return []
    node_id = str(current_node.get("node_id") or current_node.get("step_id") or "").strip()
    transitions: list[dict[str, Any]] = []
    for edge in (skill.content_json or {}).get("edges") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("source_node_id") or "").strip() != node_id:
            continue
        transitions.append(
            {
                key: edge.get(key)
                for key in ("next_node_id", "condition", "label", "priority")
                if edge.get(key) not in (None, "")
            }
        )
    return transitions


def _goal(
    frame: PlannedTaskFrame,
    skill: Skill | None,
    current_node: dict[str, Any] | None,
    requirements: list[str],
) -> str:
    """生成任务目标：SOP 任务用「技能名 + 节点名」，否则取首条需求或用户意图。"""
    node_name = str((current_node or {}).get("name") or "").strip()
    skill_name = str(getattr(skill, "name", "") or "").strip()
    if frame.kind == "sop" and (node_name or skill_name):
        return f"完成 {skill_name or 'SOP'} 的{node_name or '当前步骤'}。"
    if requirements:
        return requirements[0]
    return str(frame.user_intent or "完成用户本轮请求。").strip()


def _sop_context(skill: Skill | None, current_node: dict[str, Any] | None) -> dict[str, Any]:
    """打包 SOP 上下文（技能标识、名称与当前节点），非 SOP 任务返回空字典。"""
    if skill is None:
        return {}
    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "step": current_node or {},
    }


def _memory_projection(
    memory_context: list[dict[str, object]] | None,
) -> list[dict[str, str]]:
    """把记忆上下文投影为去重、压缩空白后的短文本，单条截断到 1000 字。"""
    projected: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in memory_context or []:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split()).strip()
        if not content or content in seen:
            continue
        seen.add(content)
        projected.append(
            {
                "kind": str(item.get("kind") or "memory"),
                "content": content[:1_000],
            }
        )
    return projected


def _text_list(value: object) -> list[str]:
    """把任意值规整为去重后的字符串列表，非列表输入返回空列表。"""
    if not isinstance(value, list):
        return []
    return _unique([str(item or "").strip() for item in value])


def _unique(values: list[str]) -> list[str]:
    """按压缩空白后的文本去重并保持首次出现顺序，丢弃空串。"""
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in result:
            result.append(text)
    return result


def _slot_satisfied(value: object) -> bool:
    """判断槽位取值是否已填充：None、空串、空列表与空字典都视为未填。"""
    return value not in (None, "", [], {})
