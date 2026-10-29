from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]
ExtensionMap: TypeAlias = Mapping[str, JsonObject]


@dataclass(frozen=True)
class CapabilityContext:
    """由 Core 注入、贯穿 Provider 适配器的可信调用上下文。

    session/turn/user/agent/channel 构成 Provider 稳定的业务上下文；tenant 与
    传输层元数据由 Core 提供，不属于第二套对外身份模型。
    """

    request_id: str
    tenant_id: str
    agent_id: str
    user_id: str
    session_id: str
    turn_id: str
    channel: str
    trace_id: str | None = None
    deadline_at: datetime | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        """校验必填字段为非空白字符串、attempt 为正整数（拒绝 bool），否则抛 ValueError。"""
        required = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "channel": self.channel,
        }
        missing = [
            name
            for name, value in required.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise ValueError(
                "CapabilityContext requires non-empty fields: " + ", ".join(missing)
            )
        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
        ):
            raise ValueError("CapabilityContext attempt must be a positive integer")


@dataclass(frozen=True)
class GeneralSkillResourceRef:
    """指向已固定版本与摘要的目录资源；Agent 运行期间禁止按 slug 重新解析。"""

    catalog_binding_id: str
    package_id: str
    version: str
    digest: str
    package_contract_version: str


@dataclass(frozen=True)
class KnowledgeSearchQuery:
    """一次知识检索请求：查询文本、类型、作用域、分块上限与预算，并支持游标翻页。"""

    query: str
    query_type: str = "answer"
    scope: Mapping[str, JsonValue] = field(default_factory=dict)
    max_chunks: int = 8
    budget_tokens: int = 4000
    cursor: str | None = None


@dataclass(frozen=True)
class KnowledgeScope:
    """知识库中可检索的作用域，带显示名称、可选版本与附加元数据。"""

    scope_id: str
    name: str
    version: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeHit:
    """单条检索命中：正文内容、可选相关度分数与来源引用。"""

    hit_id: str
    content: str
    score: float | None = None
    source_ref: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeSearchResult:
    """知识库返回的检索结果，不携带 Skill 执行相关字段。

    outcome 标记结果是否完整，next_cursor 供调用方续取剩余命中。
    """

    query_id: str
    items: tuple[KnowledgeHit, ...] = ()
    outcome: Literal["complete", "partial"] = "complete"
    warnings: tuple[str, ...] = ()
    next_cursor: str | None = None
    service_version: str | None = None
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class CitationDetail:
    """引用详情：标题、正文与来源引用，用于回答中的引用溯源展示。"""

    citation_id: str
    title: str | None
    content: str | None
    source_ref: str | None = None
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class SceneSkillSummary:
    """场景 Skill 的列表项摘要：标识、版本、名称与定义哈希。"""

    skill_id: str
    version: str
    name: str
    definition_hash: str
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class SceneSkillDefinition:
    """已有 SOP 图的版本化快照，不做语义转换。"""

    skill_id: str
    version: str
    definition_hash: str
    content_json: Mapping[str, JsonValue]
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class GeneralSkillSummary:
    """通用 Skill 的列表项摘要：slug、版本、名称、包 id 与内容摘要。"""

    slug: str
    version: str
    name: str
    package_id: str
    digest: str
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class GeneralSkillFile:
    """通用 Skill 包内的单个文件：相对路径、文本内容与可选大小、MIME 类型。"""

    path: str
    content: str
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class GeneralSkillPackage:
    """不可变的内容快照，由 StaffDeck 加载并在其本地 Runner 中执行。"""

    package_id: str
    slug: str
    version: str
    digest: str
    package_contract_version: str
    skill_markdown: str
    files: tuple[GeneralSkillFile, ...]
    entrypoint: str
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    output_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    extensions: ExtensionMap = field(default_factory=dict)


class KnowledgeRuntime(Protocol):
    """检索型知识 Provider 的协议：列作用域、执行检索与解析引用。"""

    provider_id: str

    def list_scopes(self, context: CapabilityContext) -> Sequence[KnowledgeScope]:
        """列出当前上下文可访问的知识作用域。"""

    def search(
        self, context: CapabilityContext, request: KnowledgeSearchQuery
    ) -> KnowledgeSearchResult:
        """按查询请求检索知识，返回命中集合与续取游标。"""

    def resolve_citation(
        self, context: CapabilityContext, provider_citation_ref: str
    ) -> CitationDetail:
        """把 Provider 侧引用标识解析为可展示的引用详情。"""


class SceneSkillCatalog(Protocol):
    """已发布场景 Skill 的只读目录协议。"""

    provider_id: str

    def list_published(self, context: CapabilityContext) -> Sequence[SceneSkillSummary]:
        """列出当前上下文可见的已发布场景 Skill。"""

    def get_definition(
        self, context: CapabilityContext, skill_id: str, version: str | None = None
    ) -> SceneSkillDefinition | None:
        """取指定 Skill（可指定版本）的定义快照；不存在或不可见时返回 None。"""


class GeneralSkillCatalog(Protocol):
    """通用 Skill 内容加载协议；执行始终留在 StaffDeck 内。"""

    provider_id: str

    def list_published(self, context: CapabilityContext) -> Sequence[GeneralSkillSummary]:
        """列出当前上下文可见的已发布通用 Skill。"""

    def get_package(
        self,
        context: CapabilityContext,
        resource_ref: GeneralSkillResourceRef,
    ) -> GeneralSkillPackage | None:
        """按固定的资源引用加载完整包内容；不可用或不存在时返回 None。"""
