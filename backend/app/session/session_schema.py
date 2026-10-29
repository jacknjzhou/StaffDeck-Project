from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.tool_schema import ToolCall, ToolResult


RouterDecisionValue = Literal[
    "continue_active",
    "switch_to_pending",
    "create_pending",
    "update_pending",
    "complete_task",
    "start_new_task",
    "answer_only",
    "handoff_human",
    "clarify",
]
TaskFrameKind = Literal["sop", "conversation"]
TaskFrameExecutionTarget = Literal["self", "team_member"]
TaskFrameRunStatus = Literal[
    "queued",
    "running",
    "waiting_external_task",
    "ready_to_resume",
    "awaiting_user",
    "blocked",
    "completed",
    "handoff",
    "failed",
    "cancelled",
]
MessageFeedbackValue = Literal["up", "down"]


class TaskFrame(BaseModel):
    """会话内任务帧的快照：绑定的 SOP 技能、当前步骤、槽位与恢复策略。"""

    task_id: Optional[str] = None
    status: str = "pending"
    skill_id: Optional[str] = None
    step_id: Optional[str] = None
    slots: dict[str, Any] = Field(default_factory=dict)
    intent_summary: Optional[str] = None
    source_turn_id: Optional[str] = None
    source_message: Optional[str] = None
    parent_task_id: Optional[str] = None
    resume_policy: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PlannedTaskFrame(BaseModel):
    """规划器为本轮产出的任务帧计划，随 TurnPlan 落库后才编译成 Harness 任务。"""

    task_id: Optional[str] = None
    kind: TaskFrameKind = "conversation"
    status: TaskFrameRunStatus = "queued"
    decision: RouterDecisionValue = "answer_only"
    target_skill_id: Optional[str] = None
    target_step_id: Optional[str] = None
    user_intent: Optional[str] = None
    requirements: list[str] = Field(default_factory=list)
    slot_hints: dict[str, Any] = Field(default_factory=dict)
    depends_on_task_ids: list[str] = Field(default_factory=list)
    execution_target: TaskFrameExecutionTarget = "self"
    assignee_agent_id: Optional[str] = None
    activation_condition: dict[str, Any] = Field(default_factory=dict)
    source_message: Optional[str] = None

    @field_validator("slot_hints", mode="before")
    @classmethod
    def _default_null_slot_hints(cls, value: Any) -> Any:
        """把 None 的 slot_hints 归一为空字典，兼容模型返回的 null。"""
        return {} if value is None else value

    @field_validator("activation_condition", mode="before")
    @classmethod
    def _default_null_activation_condition(cls, value: Any) -> Any:
        """把 None 的 activation_condition 归一为空字典，避免下游解包报错。"""
        return {} if value is None else value

    @field_validator("requirements", "depends_on_task_ids", mode="before")
    @classmethod
    def _default_null_lists(cls, value: Any) -> Any:
        """把 requirements 与 depends_on_task_ids 的 None 归一为空列表。"""
        return [] if value is None else value


class PendingTask(BaseModel):
    """路由阶段识别出的待办任务，用于前端展示并作为后续轮次的上下文。"""

    task_id: Optional[str] = None
    status: str = "pending"
    decision: RouterDecisionValue = "start_new_task"
    target_skill_id: Optional[str] = None
    target_step_id: Optional[str] = None
    confidence: float = 0.0
    user_intent: Optional[str] = None
    reason: Optional[str] = None
    source_message: Optional[str] = None
    slot_hints: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    """对既有任务的部分更新；remove 为 True 表示删除该任务。"""

    task_id: str
    status: Optional[str] = None
    target_skill_id: Optional[str] = None
    target_step_id: Optional[str] = None
    user_intent: Optional[str] = None
    reason: Optional[str] = None
    source_message: Optional[str] = None
    slot_hints: dict[str, Any] = Field(default_factory=dict)
    remove: bool = False

    @field_validator("slot_hints", mode="before")
    @classmethod
    def _default_null_slot_hints(cls, value: Any) -> Any:
        """把 None 的 slot_hints 归一为空字典，兼容模型返回的 null。"""
        return {} if value is None else value


class TurnPlan(BaseModel):
    """用户单轮对话唯一产出的场景/SOP 意图决策。

    能力选择刻意不写进这个契约：每个计划帧在计划落库之后才编译成 Harness 任务。
    """

    decision: RouterDecisionValue = "answer_only"
    selected_task_id: Optional[str] = None
    confidence: float = 0.0
    user_intent: Optional[str] = None
    reason: Optional[str] = None
    clarification_question: Optional[str] = None
    task_frames: list[PlannedTaskFrame] = Field(default_factory=list)
    task_updates: list[TaskUpdate] = Field(default_factory=list)

    @field_validator("task_frames", "task_updates", mode="before")
    @classmethod
    def _default_null_lists(cls, value: Any) -> Any:
        """把 task_frames 与 task_updates 的 None 归一为空列表。"""
        return [] if value is None else value


class AwaitingInput(BaseModel):
    """记录当前任务在等用户补充哪些字段，用于生成追问话术。"""

    task_id: Optional[str] = None
    skill_id: Optional[str] = None
    step_id: Optional[str] = None
    expected_fields: list[str] = Field(default_factory=list)
    question_summary: Optional[str] = None
    turn_id: Optional[str] = None


class RouterDecision(BaseModel):
    """路由决策结果：任务选择与创建、槽位提示、澄清问题及待办任务列表。"""

    decision: RouterDecisionValue
    selected_task_id: Optional[str] = None
    target_skill_id: Optional[str] = None
    target_step_id: Optional[str] = None
    confidence: float = 0.0
    user_intent: Optional[str] = None
    general_intent: Optional[str] = None
    reason: Optional[str] = None
    source_message: Optional[str] = None
    clarification_question: Optional[str] = None
    slot_hints: dict[str, Any] = Field(default_factory=dict)
    task_frames: list[PendingTask] = Field(default_factory=list)
    pending_tasks: list[PendingTask] = Field(default_factory=list)
    task_updates: list[TaskUpdate] = Field(default_factory=list)
    created_tasks: list[PendingTask] = Field(default_factory=list)
    awaiting_input: Optional[AwaitingInput] = None


class KnowledgeQuery(BaseModel):
    """发给知识库的检索请求：查询文本、检索范围、条数上限与检索类型。"""

    query: str
    reason: Optional[str] = None
    scope: dict[str, Any] = Field(default_factory=dict)
    max_chunks: int = 6
    query_type: Literal["answer", "policy_check", "tool_discovery", "skill_discovery"] = "answer"
    desired_evidence: Optional[str] = None
    max_depth: int = 2


class StepAgentResult(BaseModel):
    """单步智能体的执行结果：动作、回复、槽位更新、工具调用与交接标记。"""

    action: Optional[
        Literal[
            "ask_user",
            "clarify",
            "reply",
            "advance",
            "call_tool",
            "query_knowledge",
            "handoff",
        ]
    ] = None
    reply: Optional[str] = None
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    tool_call: Optional[ToolCall] = None
    knowledge_query: Optional[KnowledgeQuery] = None
    knowledge_results: list[dict[str, Any]] = Field(default_factory=list)
    next_step_id: Optional[str] = None
    is_step_completed: bool = False
    handoff: bool = False
    structured_result: Any | None = None


class SessionPublic(BaseModel):
    """对外暴露的会话状态快照：活动技能与步骤、槽位、待办任务与摘要。"""

    session_id: str
    tenant_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    title: Optional[str] = None
    active_skill_id: Optional[str] = None
    active_step_id: Optional[str] = None
    slots: dict[str, Any] = Field(default_factory=dict)
    pending_tasks: list[dict[str, Any]] = Field(default_factory=list)
    awaiting_input: Optional[dict[str, Any]] = None
    knowledge_context: list[dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None
    last_agent_question: Optional[str] = None
    status: str = "active"


class ChatTurnRequest(BaseModel):
    """一次对话轮次的入参：消息、渠道、会话标识以及服务端专用的注入字段。"""

    tenant_id: str
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    model_config_id: Optional[str] = None
    client_turn_id: Optional[str] = None
    user_id: Optional[str] = None
    message: str
    attachments: list["ChatAttachmentRead"] = Field(default_factory=list)
    channel: str = "web"
    interaction_mode: Literal["normal", "scheduled_task", "team_task", "team_tl"] = "normal"
    team_context: Optional["TeamPlannerContext"] = Field(default=None, exclude=True)
    # Server-only prompt prefix. It is consumed by the runtime but never persisted as
    # the user's visible message or serialized into background-job payloads.
    context_injection: Optional[str] = Field(default=None, exclude=True)
    # Internal retry turns remain auditable in storage while staying out of the
    # user-facing conversation and subsequent conversational context.
    message_visibility: Literal["visible", "internal"] = Field(default="visible", exclude=True)
    # Internal callers such as scheduled tasks may pin one published SOP.  This
    # is deliberately separate from the visible message so execution does not
    # depend on the planner rediscovering the same SOP on every wake-up.
    forced_sop_id: Optional[str] = Field(default=None, exclude=True)
    # Scheduled tasks may freeze the selected SOP at save time. The snapshot is
    # server-only and is applied only after the current employee binding has
    # been verified, so pinning a version never bypasses capability access.
    forced_sop_snapshot: Optional[dict[str, Any]] = Field(default=None, exclude=True)
    client_timezone: Optional[str] = None
    debug: bool = False


class TeamPlannerMember(BaseModel):
    """团队规划上下文中的成员信息：agent_id、名称、角色与能力清单。"""

    agent_id: str
    name: str
    role: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)


class TeamPlannerContext(BaseModel):
    """团队任务规划所需的团队上下文：团队 id、队长 agent 与成员清单。"""

    team_id: str
    leader_agent_id: str
    members: list[TeamPlannerMember] = Field(default_factory=list)


class ChatAttachmentRead(BaseModel):
    """对话附件的元数据与预解析结果：文本、预览、数据地址与解析错误。"""

    id: str
    filename: str
    content_type: str
    size: int
    kind: Literal["text", "pdf", "image", "binary"] = "binary"
    text: Optional[str] = None
    preview: Optional[str] = None
    data_url: Optional[str] = None
    sandbox_path: Optional[str] = None
    sha256: Optional[str] = None
    python_summary: Optional[str] = None
    error: Optional[str] = None


class ChatTurnResponse(BaseModel):
    """一次对话轮次的响应体：回复文本、路由/步骤/工具结果与会话状态。"""

    reply: str
    session_id: str
    runtime_error_code: Optional[str] = None
    router_decision: Optional[RouterDecision] = None
    step_result: Optional[StepAgentResult] = None
    tool_result: Optional[ToolResult] = None
    session_state: SessionPublic


class ChatSessionCreateRequest(BaseModel):
    """创建会话的请求体：租户、所属用户、绑定智能体与标题。"""

    tenant_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    title: Optional[str] = None


class ChatSessionUpdateRequest(BaseModel):
    """更新会话的请求体，目前只允许修改标题。"""

    tenant_id: str
    user_id: Optional[str] = None
    title: str


class ChatSessionRead(BaseModel):
    """会话列表与详情接口返回的会话信息，可直接由 ORM 对象转换。"""

    id: str
    tenant_id: str
    user_id: Optional[str]
    agent_id: Optional[str] = None
    title: Optional[str]
    active_skill_id: Optional[str]
    active_step_id: Optional[str]
    status: str
    summary: Optional[str]
    last_agent_question: Optional[str]
    is_scheduled: bool = False
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class MessageRead(BaseModel):
    """消息接口返回的消息正文、元数据与用户反馈评分。"""

    id: str
    tenant_id: str
    session_id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    turn_id: Optional[str] = None
    created_at: str
    feedback_rating: Optional[MessageFeedbackValue] = None

    model_config = ConfigDict(from_attributes=True)


class MessageFeedbackRequest(BaseModel):
    """提交消息反馈的请求体：租户与点赞/点踩取值。"""

    tenant_id: str
    rating: MessageFeedbackValue
