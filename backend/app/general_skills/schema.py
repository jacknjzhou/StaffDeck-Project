from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.capability_scope import CapabilityScope


class GeneralSkillFile(BaseModel):
    """技能包内的单个文件条目：相对路径、文本内容与可选的字节数和 MIME 类型。"""

    path: str
    content: str
    size: Optional[int] = None
    mime_type: Optional[str] = None


class GeneralSkillImportRequest(BaseModel):
    """导入技能的请求体：可直接给 markdown，也可携带文件与目录清单。"""

    tenant_id: str
    agent_id: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    markdown: Optional[str] = None
    files: list[GeneralSkillFile] = Field(default_factory=list)
    directories: Optional[list[str]] = None
    status: str = "published"
    capability_scope: Optional[CapabilityScope] = None
    original_slug: Optional[str] = None


class GeneralSkillClawHubImportRequest(BaseModel):
    """从 ClawHub 导入技能的请求体：来源地址、落库元信息与能力作用域。"""

    tenant_id: str
    agent_id: Optional[str] = None
    source: str
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    status: str = "published"
    capability_scope: CapabilityScope = "general"


class GeneralSkillPackageUploadRequest(BaseModel):
    """以 base64 上传技能压缩包的请求体，由服务端解包后落库为技能。"""

    tenant_id: str
    agent_id: Optional[str] = None
    filename: str
    content_base64: str
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    status: str = "published"
    capability_scope: CapabilityScope = "general"


class GeneralSkillRead(BaseModel):
    """技能读取视图：元信息、正文、文件与目录清单、权限及运行时配置。"""

    id: str
    tenant_id: str
    slug: str
    name: str
    description: Optional[str] = None
    homepage: Optional[str] = None
    skill_markdown: str
    skill_files: list[GeneralSkillFile] = Field(default_factory=list)
    skill_directories: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    capability_scope: CapabilityScope
    permissions: dict[str, Any] = Field(default_factory=dict)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class GeneralSkillRunRequest(BaseModel):
    """运行技能的请求：查询语句、read/execute 操作、可选会话与模型及最大尝试次数。"""

    tenant_id: str
    agent_id: Optional[str] = None
    user_id: str = ""
    query: str
    operation: Literal["read", "execute"] = "execute"
    session_id: Optional[str] = None
    model_config_id: Optional[str] = None
    max_attempts: int = Field(default=10, ge=1, le=10)


class GeneralSkillRunResponse(BaseModel):
    """技能运行结果：执行轨迹、生成代码、标准输出/错误、结构化产物与回复文本。"""

    skill_slug: str
    operation: Literal["read", "execute"] = "execute"
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    generated_code: str = ""
    stdout: str = ""
    stderr: str = ""
    structured_result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    reply: str


class GeneralSkillSelection(BaseModel):
    """技能选择结果：是否启用技能、选中的 slug、操作类型、知识查询与置信度。"""

    use_general_skill: bool = False
    selected_slug: Optional[str] = None
    operation: Literal["read", "execute"] = "execute"
    use_knowledge: bool = False
    knowledge_query: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None


class GeneralSkillExecutionPlan(BaseModel):
    """一次技能执行的计划：待运行代码、运行时类型与预期产出说明。"""

    code: str
    runtime: str = "python"
    rationale: Optional[str] = None
    expected_output: Optional[str] = None


class GeneralSkillExecutionReview(BaseModel):
    """执行结果评审：是否充分、是否需要重试、是否终止及修复提示。"""

    result_sufficient: bool = False
    needs_retry: bool = False
    terminal: bool = False
    reason: str = ""
    repair_hint: Optional[str] = None


class GeneralSkillReply(BaseModel):
    """技能链路的最终回复文本。"""

    reply: str
