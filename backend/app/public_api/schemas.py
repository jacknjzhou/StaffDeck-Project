from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class CursorPage(BaseModel):
    """游标分页的统一响应包装：当页数据、下一页游标与请求追踪 id。"""

    data: list[Any]
    next_cursor: str | None = None
    request_id: str | None = None


class APIClientCreate(BaseModel):
    """创建 API client 的入参：名称、描述、初始 scope 与自定义 metadata。"""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    scopes: list[str] = Field(default_factory=lambda: ["agents:read", "runs:*"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class APIClientUpdate(BaseModel):
    """局部更新 API client 的入参；未出现的字段保持原值，status 可取 active 或 archived。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    scopes: list[str] | None = None
    status: Literal["active", "archived"] | None = None
    metadata: dict[str, Any] | None = None


class APIClientRead(BaseModel):
    """API client 的对外视图，含状态、scope 与创建/更新时间，不包含任何密钥。"""

    id: str
    name: str
    description: str | None
    scopes: list[str]
    status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class APICredentialCreate(BaseModel):
    """签发凭据的入参，可限定 scope、绑定 agent_id 以及过期时间。"""

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["runs:create", "runs:read"])
    agent_id: str | None = None
    expires_at: datetime | None = None


class APICredentialRead(BaseModel):
    """凭据的对外视图，只暴露 key_prefix 及状态、使用与吊销时间，不含明文 key。"""

    id: str
    client_id: str
    agent_id: str | None
    name: str
    key_prefix: str
    scopes: list[str]
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class APICredentialCreated(APICredentialRead):
    """新建或轮换凭据的响应：在凭据视图之外唯一一次携带明文 api_key。"""

    api_key: str


class WebhookCreate(BaseModel):
    """注册 webhook 的入参，events 支持 run.* 这类通配订阅。"""

    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["run.*"])


class WebhookRead(BaseModel):
    """webhook 的对外视图；签名 secret 只以掩码返回，避免回显可伪造签名的凭据。"""

    id: str
    name: str
    url: str
    events: list[str]
    status: str
    secret_masked: str
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    """创建数字员工的入参；source_mode 决定空白新建还是从 copy_from_agent_id 复制。"""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    persona_prompt: str | None = None
    source_mode: Literal["copy", "blank"] = "blank"
    copy_from_agent_id: str | None = None
    harness_max_actions: int = Field(default=32, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """局部更新数字员工的入参；status 只允许 active 或 archived，其余字段保持原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    persona_prompt: str | None = None
    status: Literal["active", "archived"] | None = None
    harness_max_actions: int | None = Field(default=None, ge=1, le=100)
    metadata: dict[str, Any] | None = None


class ResourceBindingInput(BaseModel):
    """单个能力资源的绑定项，resource_type 区分技能、通用技能、知识库与工具。"""

    resource_type: Literal["skill", "general_skill", "knowledge_base", "tool"]
    resource_id: str
    status: Literal["active", "inactive"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceBindingsUpdate(BaseModel):
    """整体替换某个 agent 的资源绑定列表的入参。"""

    resources: list[ResourceBindingInput]


class ModelBindingInput(BaseModel):
    """模型角色到模型配置的绑定项，role 覆盖 default、router、step、response 与通用技能。"""

    role: Literal["default", "router", "step", "response", "general_skill"]
    model_config_id: str


class ModelBindingsUpdate(BaseModel):
    """整体替换某个 agent 模型绑定关系的入参。"""

    bindings: list[ModelBindingInput]


class PublicSessionCreate(BaseModel):
    """通过公共 API 创建会话的入参，可携带外部会话/用户标识用于对账与去重。"""

    external_session_id: str | None = Field(default=None, max_length=200)
    external_user_id: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicSessionUpdate(BaseModel):
    """更新公共会话的标题、状态与 metadata 的入参。"""

    title: str | None = Field(default=None, max_length=200)
    status: Literal["active", "closed"] | None = None
    metadata: dict[str, Any] | None = None


class ExternalMessageEvent(BaseModel):
    """外部上报的消息事件；event_id 由调用方给出，用于单条幂等去重。"""

    type: Literal["message"]
    event_id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(max_length=2_000_000)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalToolCallEvent(BaseModel):
    """外部上报的工具调用事件，含调用状态、耗时与标准化错误码。"""

    type: Literal["tool_call"]
    event_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    status: Literal["ok", "error"]
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=100)
    created_at: datetime | None = None


class ExternalUsageEvent(BaseModel):
    """外部上报的 token 用量事件，用于把外部调用成本并入会话统计。"""

    type: Literal["usage"]
    event_id: str = Field(min_length=1, max_length=200)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    created_at: datetime | None = None


class ExternalEventsBatch(BaseModel):
    """一批外部事件；幂等性按单个 event_id 判定，而不是整批。

    重试总是整批重发，若用批级幂等键，只要其中一条已入库就会把重试整体拒掉。
    """

    events: list[ExternalMessageEvent | ExternalToolCallEvent | ExternalUsageEvent] = Field(
        default_factory=list, max_length=200
    )


class ExternalEventsBatchResult(BaseModel):
    """批量上报结果：本次接受的条数与因重复跳过的条数。"""

    accepted: int
    duplicates: int


class AgentRunCreate(BaseModel):
    """发起 agent 运行的入参；session_mode 决定复用会话还是单轮无状态执行。"""

    input: str = Field(min_length=1)
    session_id: str | None = None
    session_mode: Literal["stateful", "stateless"] = "stateful"
    external_session_id: str | None = None
    external_user_id: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl | None = None


class JobRead(BaseModel):
    """异步任务的对外状态视图，含阶段、进度、可重试标记与错误详情。"""

    id: str
    kind: str
    status: str
    stage: str
    progress: float
    agent_id: str | None
    session_id: str | None
    retryable: bool
    error: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class SOPGenerateRequest(BaseModel):
    """由原始文本生成 SOP 草稿的入参，可指定业务域与生成所用模型配置。"""

    title: str = Field(min_length=1, max_length=200)
    raw_content: str = Field(min_length=1)
    business_domain: str | None = None
    model_config_id: str | None = None


class SOPRewritePublicRequest(BaseModel):
    """按自然语言指令改写 SOP 的入参，可限定目标路径并复用既有草稿。"""

    instruction: str = Field(min_length=1)
    target_paths: list[str] = Field(default_factory=list)
    model_config_id: str | None = None
    draft_id: str | None = None


class SOPStructuredCreate(BaseModel):
    """直接以已结构化的内容创建 SOP 的入参，跳过生成阶段。"""

    content: dict[str, Any]


class SOPPublishRequest(BaseModel):
    """发布 SOP 草稿的入参，按 draft_id 定位待发布版本。"""

    draft_id: str


class KnowledgeEntry(BaseModel):
    """单条知识条目的 upsert 入参；external_id 为空则每次调用都新建条目。"""

    external_id: str | None = None
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeEntriesUpsert(BaseModel):
    """批量 upsert 知识条目的入参，单批限 1~100 条以控制单次写入规模。"""

    entries: list[KnowledgeEntry] = Field(min_length=1, max_length=100)


class ScheduledTaskPublicCreate(BaseModel):
    """通过公共 API 创建定时任务的入参，含调度类型、时区与并发/补跑策略。"""

    title: str
    prompt: str
    description: str | None = None
    schedule_type: Literal["once", "daily", "weekly", "monthly"] = "daily"
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    rrule: str | None = None
    status: Literal["active", "paused"] = "active"
    concurrency_policy: Literal["forbid", "allow"] = "forbid"
    misfire_policy: Literal["coalesce", "skip"] = "coalesce"
    max_runs: int | None = None
    end_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
