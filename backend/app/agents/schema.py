from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AgentResourceType = Literal["skill", "general_skill", "knowledge_base", "tool"]
AgentWorkRecordEventKind = Literal["chat", "task", "sop", "tool", "knowledge", "skill"]
AgentWorkRecordEventPhase = Literal["reply", "last_run", "next_run", "assigned"]


class AgentProfileCreateRequest(BaseModel):
    """创建智能体档案的请求体;source_mode 决定从既有档案复制还是空白新建。"""

    tenant_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    persona_prompt: Optional[str] = None
    is_overall: bool = False
    source_mode: Literal["copy", "blank"] = "copy"
    copy_from_agent_id: Optional[str] = None
    harness_max_actions: int = Field(default=32, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentProfileUpdateRequest(BaseModel):
    """更新智能体档案的请求体,未提交或为 None 的字段表示保持原值。"""

    tenant_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    persona_prompt: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None
    harness_max_actions: Optional[int] = Field(default=None, ge=1, le=100)
    metadata: Optional[dict[str, Any]] = None


class AgentResourceBindingRead(BaseModel):
    """智能体与资源(技能、知识库、工具)绑定关系的读模型。"""

    id: str
    tenant_id: str
    agent_id: str
    resource_type: AgentResourceType
    resource_id: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class AgentProfileRead(BaseModel):
    """智能体档案读模型,含基础配置与已绑定资源列表。"""

    id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    persona_prompt: Optional[str] = None
    is_overall: bool
    status: str
    harness_max_actions: int = 32
    metadata: dict[str, Any] = Field(default_factory=dict)
    resources: list[AgentResourceBindingRead] = Field(default_factory=list)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class AgentScopeRead(BaseModel):
    """某个租户下可见智能体范围的响应体。"""

    tenant_id: str
    agents: list[AgentProfileRead] = Field(default_factory=list)


class AgentWorkRecordReplyStatsRead(BaseModel):
    """工作记录中的回复统计:累计数、今日数与按天分布。"""

    total: int = 0
    today: int = 0
    by_day: dict[str, int] = Field(default_factory=dict)


class AgentWorkRecordEventRead(BaseModel):
    """工作记录时间线上的一条事件,由 kind 与 phase 定位、label 供前端直接展示。"""

    id: str
    kind: AgentWorkRecordEventKind
    phase: AgentWorkRecordEventPhase
    timestamp: str
    label: str = ""


class AgentWorkRecordRead(BaseModel):
    """智能体工作记录响应体:指定时区下的回复统计与事件时间线。"""

    agent_id: str
    timezone: str
    generated_at: str
    reply_stats: AgentWorkRecordReplyStatsRead
    events: list[AgentWorkRecordEventRead] = Field(default_factory=list)


class AgentResourceBindingInput(BaseModel):
    """更新资源绑定时提交的单条资源项。"""

    resource_type: AgentResourceType
    resource_id: str
    status: Literal["active", "inactive"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResourcesUpdateRequest(BaseModel):
    """整体替换某个智能体资源绑定的请求体。"""

    tenant_id: str
    resources: list[AgentResourceBindingInput] = Field(default_factory=list)


class AgentResourceImportRequest(BaseModel):
    """把源智能体指定类型的资源批量导入目标智能体的请求体。"""

    tenant_id: str
    source_agent_id: str
    resource_type: AgentResourceType
    resource_ids: list[str] = Field(default_factory=list)


class AgentModelBindingInput(BaseModel):
    """智能体单个模型角色的绑定输入:role 与 model_config_id。"""

    role: Literal["default", "router", "step", "response", "general_skill"]
    model_config_id: str


class AgentModelsUpdateRequest(BaseModel):
    """整体替换某个智能体模型绑定的请求体。"""

    tenant_id: str
    bindings: list[AgentModelBindingInput] = Field(default_factory=list)


class AgentModelBindingRead(BaseModel):
    """智能体模型绑定的读模型,effective 表示该角色当前是否真正生效。"""

    role: str
    model_config_id: str
    effective: bool = False


class AgentSkillRollbackRequest(BaseModel):
    """把智能体技能回滚到指定版本的请求体。"""

    tenant_id: str
    version: str


class AgentAPICredentialCreateRequest(BaseModel):
    """为智能体创建运行时 API 凭证的请求体,expires_at 为空表示长期有效。"""

    tenant_id: str
    name: str = Field(min_length=1, max_length=120)
    access: Literal["runtime"] = "runtime"
    expires_at: datetime | None = None


class AgentAPICredentialRead(BaseModel):
    """智能体 API 凭证的读模型,只暴露 key_prefix,不含明文密钥。"""

    id: str
    agent_id: str
    name: str
    access: Literal["runtime", "full_access"]
    key_prefix: str
    scopes: list[str] = Field(default_factory=list)
    status: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class AgentAPICredentialCreated(AgentAPICredentialRead):
    """新建凭证的响应体,api_key 明文仅在此次创建响应中返回一次。"""

    api_key: str
