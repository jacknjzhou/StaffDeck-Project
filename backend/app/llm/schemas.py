from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigCreateRequest(BaseModel):
    """新建模型配置的入参：连接信息、协议与生成参数，api_key 回显时打码。"""
    tenant_id: str
    name: str
    provider: Optional[str] = None
    api_protocol: Optional[str] = None
    base_url: Optional[str] = None
    api_key: str = Field(default="", repr=False)
    model: str
    temperature: float = 0.2
    max_output_tokens: int = 8192
    extra_body: dict[str, Any] = Field(default_factory=dict)
    protocol_options: Optional[dict[str, Any]] = None
    is_default: bool = False
    enabled: bool = True


class ModelConfigUpdateRequest(BaseModel):
    """局部更新模型配置的入参，字段全部可选，只有显式传入的项才会被覆盖。"""
    tenant_id: str
    name: Optional[str] = None
    provider: Optional[str] = None
    api_protocol: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, repr=False)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    extra_body: Optional[dict[str, Any]] = None
    protocol_options: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None


class ModelConfigRead(BaseModel):
    """模型配置的对外视图：api_key 仅以掩码形式出现，并附带信任状态与配置版本号。"""
    id: str
    tenant_id: str
    name: str
    provider: str
    api_protocol: str
    base_url: Optional[str]
    api_key_masked: str
    model: str
    temperature: float
    max_output_tokens: int
    extra_body: dict[str, Any]
    protocol_options: dict[str, Any]
    legacy_unmapped_options: dict[str, Any]
    trust_status: str
    verification_attempt_status: str
    config_revision: int
    security_revision: int
    is_default: bool
    enabled: bool
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class ModelCapabilityTestResult(BaseModel):
    """单项模型能力的连通性测试结果，失败时带错误码便于前端分类提示。"""
    id: str
    success: bool
    error_code: Optional[str] = None


class ModelProviderErrorDetail(BaseModel):
    """上游模型服务返回的错误细节，保留 provider 原始编码与是否可重试的判定。"""
    code: str
    message: str
    upstream_status: Optional[int] = None
    provider_code: Optional[str] = None
    provider_message: Optional[str] = None
    upstream_body: Optional[str] = None
    request_id: Optional[str] = None
    retryable: bool = False


class ModelConfigTestResponse(BaseModel):
    """模型配置测试接口的响应：测试结论、激活状态、样例输出与失败详情。"""
    success: bool
    message: str
    activated: bool = False
    model: Optional[ModelConfigRead] = None
    output: Optional[str] = None
    attempt_id: Optional[str] = None
    trust_status: Optional[str] = None
    attempt_status: Optional[str] = None
    capabilities: list[ModelCapabilityTestResult] = Field(default_factory=list)
    error: Optional[ModelProviderErrorDetail] = None
