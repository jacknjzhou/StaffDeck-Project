from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.capability_scope import CapabilityScope


class ToolExecutionPolicy(BaseModel):
    """工具执行策略：超时、同步/异步模式，以及 Provider 托管任务的轮询配置。"""
    timeout_seconds: float = Field(ge=1, le=3600)
    execution_mode: Literal["sync", "detached"] = "sync"
    async_strategy: Literal["staffdeck_worker", "provider_task"] = "staffdeck_worker"
    status_url: Optional[str] = None
    poll_interval_seconds: float = Field(default=5, ge=1, le=3600)
    task_id_field: str = "taskId"
    status_field: str = "status"
    result_field: str = "result"
    status_mapping: dict[str, str] = Field(default_factory=dict)
    max_tracking_seconds: int = Field(default=86400, ge=1, le=2592000)

    @model_validator(mode="after")
    def validate_provider_tracking(self) -> "ToolExecutionPolicy":
        """校验 Provider 托管的 detached 工具必须提供 status_url，否则抛 ValueError。"""
        if (
            self.execution_mode == "detached"
            and self.async_strategy == "provider_task"
            and not str(self.status_url or "").strip()
        ):
            raise ValueError("Provider-managed detached tools require status_url")
        return self


class ToolCreateRequest(BaseModel):
    """创建工具的请求体，覆盖 HTTP/A2A/MCP 三类工具的连接、schema 与执行配置。"""
    tenant_id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = "未分桶"
    tool_type: Literal["http", "a2a", "mcp"] = "http"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, Any] = Field(default_factory=dict)
    mcp_config: dict[str, Any] = Field(default_factory=dict)
    execution_policy: Optional[ToolExecutionPolicy] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_skills: list[str] = Field(default_factory=list)
    capability_scope: CapabilityScope = "general"
    enabled: bool = True


class ToolUpdateRequest(ToolCreateRequest):
    """更新工具的请求体；capability_scope 为空表示沿用原值。"""
    capability_scope: Optional[CapabilityScope] = None


class ToolRead(BaseModel):
    """工具详情的响应体，支持从 ORM 行读取。"""
    id: str
    tenant_id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str
    tool_type: str
    method: str
    url: str
    headers: dict[str, Any]
    auth: dict[str, Any]
    mcp_config: dict[str, Any]
    execution_policy: Optional[ToolExecutionPolicy] = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_skills: list[str]
    mcp_server_id: Optional[str] = None
    capability_scope: CapabilityScope
    enabled: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class ToolBucketRead(BaseModel):
    """工具分桶统计：总数、启用/禁用数量与桶内工具 id。"""
    bucket: str
    total: int
    enabled_count: int
    disabled_count: int
    tool_ids: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    """一次工具调用的名字与参数。"""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    """工具调用失败的错误码与消息。"""
    code: str
    message: str


class MCPAppDescriptor(BaseModel):
    """下发给 MCP App 的会话描述，用于渲染内嵌应用并回填调用上下文。"""
    server_id: str
    resource_uri: str
    tool_name: str
    visibility: list[str] = Field(default_factory=lambda: ["model", "app"])
    mime_type: str = "text/html;profile=mcp-app"
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    active_skill_id: Optional[str] = None
    initial_result: Optional[Any] = None
    initial_meta: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果：成功数据、错误、MCP App 描述与附加元数据。"""
    tool_name: str
    success: bool
    data: Optional[Any] = None
    error: Optional[ToolError] = None
    mcp_app: Optional[MCPAppDescriptor] = None
    mcp_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolTestRequest(BaseModel):
    """工具试调用请求；client_request_id 供调用方做幂等重试。"""
    client_request_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    tenant_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolProbeRequest(BaseModel):
    """工具落库前的探测请求，附带用于试跑的样例参数。"""
    tenant_id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = "技能自发现工具"
    tool_type: Literal["http", "a2a", "mcp"] = "http"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, Any] = Field(default_factory=dict)
    mcp_config: dict[str, Any] = Field(default_factory=dict)
    execution_policy: Optional[ToolExecutionPolicy] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    sample_arguments: dict[str, Any] = Field(default_factory=dict)


class ToolProbeResponse(BaseModel):
    """探测结果：状态码、数据预览与推断出的输出 schema。"""
    success: bool
    status_code: Optional[int] = None
    data_preview: Optional[Any] = None
    inferred_output_schema: dict[str, Any] = Field(default_factory=dict)
    error: Optional[ToolError] = None


MCPTransport = Literal["stdio", "streamable_http", "sse", "builtin"]
MCPAppsMode = Literal["disabled", "auto"]


class MCPServerConnection(BaseModel):
    """MCP Server 连接配置（对齐标准 MCP Client 的连接语义）。"""

    transport: MCPTransport = "streamable_http"
    url: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None


class MCPServerCreateRequest(BaseModel):
    """创建 MCP Server 的请求体，包含传输连接配置与 MCP App 模式。"""
    tenant_id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = "MCP 工具"
    connection: MCPServerConnection = Field(default_factory=MCPServerConnection)
    apps_mode: MCPAppsMode = "disabled"
    capability_scope: CapabilityScope = "general"
    enabled: bool = True


class MCPServerUpdateRequest(MCPServerCreateRequest):
    """更新 MCP Server 的请求体；capability_scope 为空表示沿用原值。"""
    capability_scope: Optional[CapabilityScope] = None


class MCPDiscoveredTool(BaseModel):
    """MCP 服务端发现到的单个工具，并携带本地导入与启用状态。"""
    name: str
    title: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    app: Optional[dict[str, Any]] = None
    # 该工具是否已同步为 Tool 行
    imported: bool = False
    tool_id: Optional[str] = None
    enabled: Optional[bool] = None
    capability_scope: Optional[CapabilityScope] = None


class MCPServerRead(BaseModel):
    """MCP Server 详情响应体，含协商结果、同步时间与工具数量。"""
    id: str
    tenant_id: str
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str
    connection: MCPServerConnection
    apps_mode: MCPAppsMode = "disabled"
    apps_negotiated: bool = False
    negotiated_capabilities: dict[str, Any] = Field(default_factory=dict)
    capability_scope: CapabilityScope
    enabled: bool
    last_synced_at: Optional[str] = None
    tool_count: int = 0
    created_at: str
    updated_at: str


class MCPDiscoverRequest(BaseModel):
    """探测 MCP Server 的请求：可直接给连接配置，也可只给已保存的 server_id。"""
    tenant_id: str
    # 未保存前用连接配置直接探测；已保存则可只传 server_id
    connection: Optional[MCPServerConnection] = None
    apps_mode: MCPAppsMode = "disabled"


class MCPDiscoverResponse(BaseModel):
    """MCP 工具发现结果，含工具列表、服务端能力与服务端信息。"""
    success: bool
    tools: list[MCPDiscoveredTool] = Field(default_factory=list)
    server_capabilities: dict[str, Any] = Field(default_factory=dict)
    server_info: dict[str, Any] = Field(default_factory=dict)
    error: Optional[ToolError] = None


class MCPAppResourceRead(BaseModel):
    """读取 MCP App 资源的响应：URI、mime_type、正文与元数据。"""
    server_id: str
    uri: str
    mime_type: str
    text: str
    meta: dict[str, Any] = Field(default_factory=dict)


class MCPAppToolCallRequest(BaseModel):
    """由 MCP App 前端发起的工具调用请求，可声明已确认副作用。"""
    tenant_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    active_skill_id: Optional[str] = None
    confirm_side_effect: bool = False


class MCPAppToolCallResponse(BaseModel):
    """MCP App 工具调用结果，失败或需确认副作用时带相应标记。"""
    success: bool
    result: Optional[ToolResult] = None
    requires_confirmation: bool = False
    error: Optional[ToolError] = None


class MCPSyncRequest(BaseModel):
    """同步 MCP 工具的请求；tool_names 为空表示导入全部已发现工具。"""
    tenant_id: str
    # 需要导入/更新的工具名；为空表示导入全部发现到的工具
    tool_names: Optional[list[str]] = None
    capability_scope_overrides: dict[str, CapabilityScope] = Field(default_factory=dict)


class MCPSyncResponse(BaseModel):
    """同步结果：成功导入、更新与移除的工具名。"""
    success: bool
    imported: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    error: Optional[ToolError] = None
