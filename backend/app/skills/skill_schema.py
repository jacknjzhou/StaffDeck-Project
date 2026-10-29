from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.capability_scope import CapabilityScope


class SkillCapabilityRefs(BaseModel):
    """SOP 节点生效期间显式暴露的能力引用集合（通用技能、工具、知识库）。"""

    general_skill_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    required_general_skill_ids: list[str] = Field(default_factory=list)
    required_tool_ids: list[str] = Field(default_factory=list)
    required_knowledge_base_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_refs(self) -> "SkillCapabilityRefs":
        """校验 required_* 必选能力都包含在对应已选能力列表中，否则抛 ValueError。"""
        pairs = (
            ("required_general_skill_ids", self.required_general_skill_ids, self.general_skill_ids),
            ("required_tool_ids", self.required_tool_ids, self.tool_ids),
            (
                "required_knowledge_base_ids",
                self.required_knowledge_base_ids,
                self.knowledge_base_ids,
            ),
        )
        for field_name, required, allowed in pairs:
            invalid = sorted(set(required) - set(allowed))
            if invalid:
                raise ValueError(
                    f"{field_name} must be a subset of its selected capability ids: "
                    + ", ".join(invalid)
                )
        return self


class SkillGraphNode(BaseModel):
    """SOP 图中的单个节点：类型、指令、槽位期望、能力引用与人工处理人。"""

    node_id: str
    type: str = "collect_info"
    name: str
    instruction: str = ""
    optional: bool = False
    condition: Optional[str] = None
    expected_user_info: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    knowledge_scope: dict[str, Any] = Field(default_factory=dict)
    capability_refs: SkillCapabilityRefs = Field(default_factory=SkillCapabilityRefs)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sub_sop_id: Optional[str] = None
    # 人工节点指定处理人(handoff / handoff_human 节点)。None 表示未指定,
    # 运行时回退到渠道默认处理人 → 数字员工负责人 → 租户管理员。
    assignee_user_id: Optional[str] = None
    # 处理人通知渠道:None=按默认(能达则通知);"web"=仅网页端收件箱;
    # "feishu" 等=已绑定渠道身份的成员按该渠道转接。需配合 assignee_user_id 使用。
    assignee_notify_channel: Optional[str] = None


class SkillGraphEdge(BaseModel):
    """SOP 图的有向边：源/目标节点、流转条件、优先级与展示标签。"""

    source_node_id: str
    next_node_id: str
    condition: Optional[str] = None
    priority: int = 0
    label: Optional[str] = None


class SkillCard(BaseModel):
    """SOP 技能卡的完整定义：元数据、触发意图、节点边图与中断策略；写入即严格校验。"""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    name: str
    version: str = "1.0.0"
    business_domain: Optional[str] = None
    description: str = ""
    capability_scope: CapabilityScope = "general"
    step_timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    trigger_intents: list[str] = Field(default_factory=list)
    user_utterance_examples: list[str] = Field(default_factory=list)
    goal: list[str] = Field(default_factory=list)
    required_info: list[str] = Field(default_factory=list)
    slot_filling_policy: dict[str, Any] = Field(default_factory=dict)
    response_rules: list[str] = Field(default_factory=list)
    nodes: list[SkillGraphNode] = Field(default_factory=list)
    edges: list[SkillGraphEdge] = Field(default_factory=list)
    start_node_id: str
    terminal_node_ids: list[str] = Field(default_factory=list)
    interruption_policy: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "SkillCard":
        """写入时校验图结构不变量。

        要求至少一个节点、node_id 唯一、起点与终态节点存在、边两端均存在；
        subflow 节点被归一化为只保留 sub_sop_id，避免父图重复执行子流程工作。
        """
        if not self.nodes:
            raise ValueError("Skill graph requires at least one node.")
        node_ids = [node.node_id for node in self.nodes]
        duplicate_ids = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        if duplicate_ids:
            raise ValueError(f"Skill graph node_id must be unique: {', '.join(duplicate_ids)}")
        node_id_set = set(node_ids)
        if self.start_node_id not in node_id_set:
            raise ValueError("start_node_id must reference an existing node.")
        if not self.terminal_node_ids:
            raise ValueError("terminal_node_ids must contain at least one node id.")
        missing_terminal_ids = [
            node_id for node_id in self.terminal_node_ids if node_id not in node_id_set
        ]
        if missing_terminal_ids:
            raise ValueError(
                f"terminal_node_ids reference missing nodes: {', '.join(missing_terminal_ids)}"
            )
        for edge in self.edges:
            if edge.source_node_id not in node_id_set:
                raise ValueError(
                    f"edge source_node_id references missing node: {edge.source_node_id}"
                )
            if edge.next_node_id not in node_id_set:
                raise ValueError(f"edge next_node_id references missing node: {edge.next_node_id}")
        for node in self.nodes:
            if node.type != "subflow":
                continue
            if not str(node.sub_sop_id or "").strip():
                raise ValueError(f"subflow node must reference sub_sop_id: {node.node_id}")
            # A subflow node is an orchestration boundary, not another executable
            # TaskFrame. Keeping work on the placeholder would make the parent
            # execute it in addition to the child SOP and could expose capabilities
            # that the child did not declare. Normalize legacy drafts on write so
            # the node has exactly one responsibility: enter the referenced SOP.
            node.instruction = ""
            node.expected_user_info = []
            node.allowed_actions = []
            node.knowledge_scope = {}
            node.capability_refs = SkillCapabilityRefs()
            node.retry_policy = {}
        return self


def skill_card_from_persisted(value: Any) -> SkillCard:
    """加载历史持久化的 SOP 数据，同时不放松写入期校验。

    旧版本允许节点存储 required_* 必选能力却没写进对应已选能力；必选能力必然对
    节点可见，故在校验前先把它并入已选列表，再按当前严格 SkillCard 校验。
    """

    if not isinstance(value, dict):
        return SkillCard.model_validate(value)
    content = deepcopy(value)
    nodes = content.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            refs = node.get("capability_refs")
            if not isinstance(refs, dict):
                continue
            for required_field, selected_field in (
                ("required_general_skill_ids", "general_skill_ids"),
                ("required_tool_ids", "tool_ids"),
                ("required_knowledge_base_ids", "knowledge_base_ids"),
            ):
                required = refs.get(required_field)
                if not isinstance(required, list):
                    continue
                selected = refs.get(selected_field)
                selected_values = list(selected) if isinstance(selected, list) else []
                for capability_id in required:
                    if capability_id not in selected_values:
                        selected_values.append(capability_id)
                refs[selected_field] = selected_values
    return SkillCard.model_validate(content)


class ToolSuggestion(BaseModel):
    """技能蒸馏/改写时模型给出的工具建议，含探测结果与是否已存在的匹配信息。"""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = "技能自发现工具"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    sample_arguments: dict[str, Any] = Field(default_factory=dict)
    source_excerpt: Optional[str] = None
    probe_result: Optional[dict[str, Any]] = None
    reason: str = ""
    resolution_status: Literal["existing", "new_candidate", "incomplete"] = "new_candidate"
    matched_tool_id: Optional[str] = None
    matched_tool_name: Optional[str] = None
    matched_tool_display_name: Optional[str] = None
    missing_reason: Optional[str] = None


class SkillCreateRequest(BaseModel):
    """创建技能的请求体：租户、技能卡内容与初始状态。"""

    tenant_id: str
    content: SkillCard
    status: Literal["draft", "published", "archived"] = "draft"


class SkillUpdateRequest(BaseModel):
    """更新技能的请求体：租户、技能卡内容与可选的新状态。"""

    tenant_id: str
    content: SkillCard
    status: Optional[Literal["draft", "published", "archived"]] = None


class SkillRead(BaseModel):
    """技能读取视图：技能卡内容、状态、调用与反馈统计、版本与分支信息。"""

    id: str
    tenant_id: str
    skill_id: str
    version: str
    name: str
    business_domain: Optional[str]
    description: Optional[str]
    content: SkillCard
    status: str
    call_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    positive_rate: float = 0.0
    negative_rate: float = 0.0
    total_call_count: int = 0
    total_positive_feedback_count: int = 0
    total_negative_feedback_count: int = 0
    total_positive_rate: float = 0.0
    total_negative_rate: float = 0.0
    recent_versions: list[str] = Field(default_factory=list)
    recent_call_count: int = 0
    recent_positive_feedback_count: int = 0
    recent_negative_feedback_count: int = 0
    recent_positive_rate: float = 0.0
    recent_negative_rate: float = 0.0
    agent_id: Optional[str] = None
    branch_status: Optional[str] = None
    branch_sync_state: Optional[str] = None
    branch_base_version: Optional[str] = None
    branch_head_version: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SkillVersionRead(BaseModel):
    """技能历史版本读取视图：版本内容、状态、统计指标与分支同步信息。"""

    id: str
    tenant_id: str
    skill_id: str
    version: str
    name: str
    business_domain: Optional[str]
    description: Optional[str]
    content: SkillCard
    status: str
    call_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    positive_rate: float = 0.0
    negative_rate: float = 0.0
    agent_id: Optional[str] = None
    branch_sync_state: Optional[str] = None
    branch_base_version: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SkillDistillRequest(BaseModel):
    """技能蒸馏请求：原始素材、可选模型与可用的工具/技能/知识库清单。"""

    tenant_id: str
    agent_id: Optional[str] = None
    title: str
    raw_content: str
    business_domain: Optional[str] = None
    model_config_id: Optional[str] = None
    available_tools: list[dict[str, Any]] = Field(default_factory=list)
    available_general_skills: list[dict[str, Any]] = Field(default_factory=list)
    available_knowledge_bases: list[dict[str, Any]] = Field(default_factory=list)


class SkillDistillResponse(BaseModel):
    """技能蒸馏结果：草稿技能卡、告警与工具建议。"""

    draft_skill: SkillCard
    warnings: list[str] = Field(default_factory=list)
    tool_suggestions: list[ToolSuggestion] = Field(default_factory=list)


class SkillRewriteRequest(BaseModel):
    """技能改写请求：当前技能卡、改写指令、目标路径与对话上下文。"""

    tenant_id: str
    agent_id: Optional[str] = None
    current_skill: SkillCard
    instruction: str
    model_config_id: Optional[str] = None
    target_path: str = "all"
    target_paths: list[str] = Field(default_factory=list)
    target_label: Optional[str] = None
    conversation: list[dict[str, str]] = Field(default_factory=list)
    available_tools: list[dict[str, Any]] = Field(default_factory=list)
    available_sops: list[dict[str, Any]] = Field(default_factory=list)


class SkillRewriteResponse(BaseModel):
    """技能改写结果：草稿技能卡、助手回复、变更路径、告警与工具建议。"""

    draft_skill: SkillCard
    assistant_message: str
    changed_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tool_suggestions: list[ToolSuggestion] = Field(default_factory=list)


class SkillFileExtractRequest(BaseModel):
    """技能素材文件解析请求：文件名与 base64 编码的文件内容。"""

    filename: str
    content_base64: str


class SkillFileExtractResponse(BaseModel):
    """技能素材文件解析结果：文件名与抽取出的纯文本。"""

    filename: str
    text: str
