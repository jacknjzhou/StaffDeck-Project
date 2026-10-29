from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EvolutionResourceType = Literal["sop", "general_skill"]


class EvolutionAnalyzeRequest(BaseModel):
    """演化分析请求：指定待分析资源与反馈集合，可附补充指令引导分析方向。"""

    tenant_id: str
    resource_type: EvolutionResourceType | None = None
    resource_id: str | None = None
    feedback_ids: list[str] = Field(default_factory=list)
    instruction: str | None = None


class EvolutionRejectRequest(BaseModel):
    """驳回演化提案的请求体；reason 缺省为空串，表示未注明驳回原因。"""

    tenant_id: str
    reason: str = ""


class EvolutionActionRequest(BaseModel):
    """演化提案通用操作（发布、回滚等）的请求体，仅需携带租户标识。"""

    tenant_id: str


class EvolutionProposalRead(BaseModel):
    """演化提案的对外读取模型：提案元数据、候选内容、差异与评估结论。"""

    id: str
    tenant_id: str
    agent_id: str
    resource_type: EvolutionResourceType
    resource_id: str
    resource_key: str
    resource_name: str
    base_version: str | None = None
    status: str
    trigger_type: str
    risk_level: str
    hypothesis: str
    rationale: str
    expected_outcome: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    candidate: dict = Field(default_factory=dict)
    diff: list[dict] = Field(default_factory=list)
    evaluation: dict = Field(default_factory=dict)
    error: str | None = None
    created_by_user_id: str
    reviewed_by_user_id: str | None = None
    created_at: str
    updated_at: str
    reviewed_at: str | None = None
    published_at: str | None = None
    rolled_back_at: str | None = None
