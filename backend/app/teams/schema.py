from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TeamRole = Literal["leader", "member"]
ReviewVerdict = Literal["approve", "rework", "escalate"]


class TeamCreateRequest(BaseModel):
    """创建团队的请求体：租户、名称、描述与初始配置。"""

    tenant_id: str
    name: str
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class TeamUpdateRequest(BaseModel):
    """更新团队的请求体：名称、描述、状态与配置均可选，仅传入需要变更的字段。"""

    tenant_id: str
    name: str | None = None
    description: str | None = None
    status: Literal["active", "archived"] | None = None
    config: dict[str, Any] | None = None


class TeamMemberAddRequest(BaseModel):
    """新增团队成员的请求体：agent 及其角色（leader/member）。"""

    tenant_id: str
    agent_id: str
    role: TeamRole = "member"


class TeamLeaderUpdateRequest(BaseModel):
    """变更团队 leader 的请求体：指定接任的 agent。"""

    tenant_id: str
    agent_id: str


class TeamMemberRead(BaseModel):
    """团队成员读取视图：所属团队、agent、角色与加入时间。"""

    id: str
    team_id: str
    agent_id: str
    role: str
    agent_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamRead(BaseModel):
    """团队读取视图：基础信息、状态、配置与成员列表。"""

    id: str
    tenant_id: str
    name: str
    description: str | None = None
    owner_user_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: str
    members: list[TeamMemberRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamTaskEventRead(BaseModel):
    """任务事件读取视图：操作者、事件类型、载荷与发生时间。"""

    id: str
    task_id: str
    team_id: str
    actor_type: str
    actor_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamTaskBidRead(BaseModel):
    """任务投标读取视图：轮次、投标内容及评分与评分理由。"""

    id: str
    task_id: str
    agent_id: str
    agent_name: str | None = None
    round: int
    kind: str
    content: str
    score: float | None = None
    score_rationale: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamTaskRead(BaseModel):
    """团队任务读取视图：状态、依赖、交付报告、评审结论、事件与投标。"""

    id: str
    team_id: str
    tenant_id: str
    team_run_id: str | None = None
    source_turn_id: str | None = None
    parent_task_id: str | None = None
    title: str
    description: str | None = None
    priority: str
    status: str
    created_by_user_id: str | None = None
    created_by_tl: bool
    assignee_agent_id: str | None = None
    session_id: str | None = None
    depends_on_task_ids: list[str] = Field(default_factory=list)
    activation_condition: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    version: int
    events: list[TeamTaskEventRead] = Field(default_factory=list)
    bids: list[TeamTaskBidRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamTaskCreateRequest(BaseModel):
    """手动创建团队任务的请求体：标题、优先级与可选指派人。"""

    tenant_id: str
    title: str
    description: str | None = None
    priority: str = "normal"
    assignee_agent_id: str | None = None


class TeamTLChatRequest(BaseModel):
    """与 team leader 对话的请求体：消息内容与可选会话 id（缺省新建会话）。"""

    tenant_id: str
    message: str
    session_id: str | None = None


class TeamTLChatResponse(BaseModel):
    """team leader 对话响应：回复内容、会话 id 与本次派生的任务。"""

    reply: str
    session_id: str
    created_tasks: list[TeamTaskRead] = Field(default_factory=list)


class TeamTLSessionRequest(BaseModel):
    """获取或创建 team leader 会话的请求体。"""

    tenant_id: str


class TeamTLSessionResponse(BaseModel):
    """team leader 会话响应：会话 id。"""

    session_id: str


class ReviewOverrideRequest(BaseModel):
    """人工覆盖评审结论的请求体：结论（approve/rework/escalate）与可选评论。"""

    tenant_id: str
    verdict: ReviewVerdict
    comment: str | None = None


class TeamTaskResumeRequest(BaseModel):
    """恢复因等待输入而阻塞的任务的请求体：用户回答。"""

    tenant_id: str
    answer: str


class AwardOverrideRequest(BaseModel):
    """人工指定评优 agent 的请求体：目标 agent 与可选评论。"""

    tenant_id: str
    agent_id: str
    comment: str | None = None


class TeamBlackboardEntryCreateRequest(BaseModel):
    """新建团队黑板条目的请求体：内容与标签。"""

    tenant_id: str
    content: str
    tags: list[str] = Field(default_factory=list)


class TeamBlackboardEntryUpdateRequest(BaseModel):
    """更新团队黑板条目的请求体：内容、标签与置顶状态均可选。"""

    tenant_id: str
    content: str | None = None
    tags: list[str] | None = None
    pinned: bool | None = None


class TeamBlackboardEntryArchiveRequest(BaseModel):
    """归档团队黑板条目的请求体。"""

    tenant_id: str


class TeamBlackboardEntryRead(BaseModel):
    """黑板条目读取视图：来源、引用、状态与置顶信息。"""

    id: str
    team_id: str
    tenant_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_type: str
    source_agent_id: str | None = None
    source_task_id: str | None = None
    citation: dict[str, Any] = Field(default_factory=dict)
    status: str
    pinned: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamBlackboardWriteResponse(BaseModel):
    """黑板写入响应：已写入的条目与被跳过的内容。"""

    entries: list[TeamBlackboardEntryRead] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class TeamEventRead(BaseModel):
    """团队级审计事件(全团队 task_events 聚合视图,含任务标题便于展示)。"""

    id: str
    task_id: str
    team_id: str
    task_title: str | None = None
    actor_type: str
    actor_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamThreadRead(BaseModel):
    """跨团队统一线程:TL 对话会话(tl_chat)或任务执行会话(task)。"""

    team_id: str
    team_name: str
    kind: Literal["tl_chat", "task"]
    session_id: str
    task_id: str | None = None
    title: str
    task_status: str | None = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamBlackboardPromoteRequest(BaseModel):
    """把黑板条目提升为知识库文档的请求体。"""

    tenant_id: str


class TeamBlackboardPromoteResponse(BaseModel):
    """黑板提升响应：条目、目标知识库、入库任务 id 与是否已提升过。"""

    entry: TeamBlackboardEntryRead
    knowledge_base_id: str
    ingest_job_id: str
    already_promoted: bool = False


TeamConversationKind = Literal["tl_chat", "member_task", "member_bid", "tl_review"]


class TeamConversationTLRead(BaseModel):
    """团队 TL 信息:session_id 为已有 TL 对话会话,无则 None(前端再调 tl/session 创建)。"""

    agent_id: str
    agent_name: str | None = None
    session_id: str | None = None


class TeamConversationRead(BaseModel):
    """团队维度会话条目:kind 由会话标题前缀判定(见 teams.py 注释),preview 为末条消息截取。"""

    session_id: str
    kind: TeamConversationKind
    agent_id: str | None = None
    agent_name: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    needs_input: bool = False
    pending_question: str | None = None
    title: str
    preview: str = ""
    created_at: datetime
    updated_at: datetime


class TeamConversationsResponse(BaseModel):
    """团队会话聚合响应：团队信息、TL 信息与会话列表。"""

    team_id: str
    team_name: str
    tl: TeamConversationTLRead | None = None
    conversations: list[TeamConversationRead] = Field(default_factory=list)


class TeamConversationMessageRead(BaseModel):
    """团队会话消息读取视图：角色、内容、元数据与所属轮次。"""

    id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    turn_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamConversationStreamRead(BaseModel):
    """会话流式状态视图：运行状态、已生成内容、当前阶段与更新时间。"""

    status: Literal["idle", "running", "completed", "failed"] = "idle"
    content: str = ""
    phase: str | None = None
    updated_at: datetime | None = None
