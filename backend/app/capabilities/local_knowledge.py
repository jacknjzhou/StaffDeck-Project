from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.branching import visible_knowledge_base_versions
from app.capabilities.contracts import (
    CapabilityContext,
    CitationDetail,
    KnowledgeHit,
    KnowledgeRuntime,
    KnowledgeScope,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)
from app.capabilities.errors import CapabilityErrorInfo, CapabilityProviderError
from app.db.models import new_id
from app.knowledge.schema import KnowledgeSearchRequest, KnowledgeSearchResponse


class LocalKnowledgeRuntime(KnowledgeRuntime):
    """现有本地知识服务的适配器，不依赖 AgentLoop。"""

    provider_id = "local_knowledge"

    def __init__(
        self,
        service_factory: Callable[[Any], Any],
        db: Any,
        model_config: Any | None = None,
    ) -> None:
        """保存服务工厂、数据库会话与可选模型配置，检索时才按需构造本地服务。"""
        self._service_factory = service_factory
        self._db = db
        self._model_config = model_config

    def list_scopes(self, context: CapabilityContext) -> tuple[KnowledgeScope, ...]:
        """返回当前租户与智能体可见的知识库版本，作为本次会话可检索的范围。"""
        versions = visible_knowledge_base_versions(
            self._db, context.tenant_id, context.agent_id
        )
        return tuple(
            KnowledgeScope(
                scope_id=knowledge_base_id,
                name=version.name,
                version=version.version,
                metadata=dict(version.metadata_json or {}),
            )
            for knowledge_base_id, version in versions.items()
        )

    def search(
        self, context: CapabilityContext, request: KnowledgeSearchQuery
    ) -> KnowledgeSearchResult:
        """把能力层检索请求转成旧版 KnowledgeSearchRequest 并调用本地知识服务。

        仅接受 answer / policy_check / tool_discovery / skill_discovery 四类查询，
        其余抛 KNOWLEDGE_UNSUPPORTED_QUERY_TYPE；返回分块会被转成 KnowledgeHit，
        原有 trace、evidence_pack 等调试信息放入 extensions.local_knowledge。
        """
        allowed_query_types = {"answer", "policy_check", "tool_discovery", "skill_discovery"}
        if request.query_type not in allowed_query_types:
            raise CapabilityProviderError(
                CapabilityErrorInfo(
                    code="KNOWLEDGE_UNSUPPORTED_QUERY_TYPE",
                    message=f"unsupported Knowledge query type: {request.query_type}",
                    retryable=False,
                    request_id=context.request_id,
                    provider_id=self.provider_id,
                )
            )
        legacy_request = KnowledgeSearchRequest(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            query=request.query,
            query_type=request.query_type,
            scope=dict(request.scope),
            max_chunks=request.max_chunks,
            budget_tokens=request.budget_tokens,
            mode="chat",
        )
        response = self._service_factory(self._db).search(legacy_request, self._model_config)
        if not isinstance(response, KnowledgeSearchResponse):
            raise TypeError("local Knowledge provider returned an invalid response")
        items = tuple(
            KnowledgeHit(
                hit_id=str(chunk.id),
                content=chunk.content,
                source_ref=chunk.source_ref,
                metadata=dict(chunk.metadata or {}),
            )
            for chunk in response.chunks
        )
        return KnowledgeSearchResult(
            query_id=new_id("kquery"),
            items=items,
            extensions={
                "local_knowledge": {
                    "request_id": context.request_id,
                    "trace": response.trace,
                    "route_trace": response.route_trace,
                    "selected_documents": response.selected_documents,
                    "selected_concepts": response.selected_concepts,
                    "evidence_pack": response.evidence_pack,
                }
            },
        )

    def resolve_citation(
        self, context: CapabilityContext, provider_citation_ref: str
    ) -> CitationDetail:
        """本地知识引用不支持即时解析，固定抛 KNOWLEDGE_CITATION_NOT_DURABLE。

        引用必须从持久化的检索快照还原，因此这里直接失败而不做降级查询。
        """
        raise CapabilityProviderError(
            CapabilityErrorInfo(
                code="KNOWLEDGE_CITATION_NOT_DURABLE",
                message="Local Knowledge citation must be resolved from a persisted snapshot",
                retryable=False,
                request_id=context.request_id,
                provider_id=self.provider_id,
            )
        )
