from __future__ import annotations

from typing import Any

from app.capabilities.contracts import KnowledgeRuntime
from app.capabilities.local_knowledge import LocalKnowledgeRuntime
from app.capabilities.registry import (
    CapabilityBinding,
    CapabilityRegistry,
    DurableCapabilityBinding,
)
from app.knowledge import KnowledgeService

LOCAL_KNOWLEDGE_DEPLOYMENT = "local-process"
LOCAL_KNOWLEDGE_CONFIG_REVISION = "legacy-local-v1"
LOCAL_KNOWLEDGE_CONTRACT = "knowledge.v1"
LOCAL_KNOWLEDGE_OPERATIONS = {
    "knowledge.scopes": "knowledge.scopes.v1",
    "knowledge.search": "knowledge.search.v1",
    "knowledge.citation": "knowledge.citation.v1",
}


def _model_config_revision(model_config: Any | None) -> str:
    """把模型配置压成 `配置 ID:修订号` 短标识，供能力绑定的修订号比对使用。"""
    if model_config is None:
        return "none"
    config_id = str(getattr(model_config, "id", "inline"))
    revision = str(getattr(model_config, "config_revision", "inline"))
    return f"{config_id}:{revision}"


def build_local_capability_registry(
    db: Any,
    model_config: Any | None = None,
    *,
    service_factory: Any = KnowledgeService,
) -> CapabilityRegistry:
    """构建只含本地知识能力的注册表，显式声明 operation 版本且不触碰 AgentLoop。

    注册、重建回调共用同一个 runtime 与服务工厂；返回前已 seal()，调用方拿到的
    是封闭注册表，不能再追加绑定。
    """

    runtime = LocalKnowledgeRuntime(service_factory, db, model_config)
    registry = CapabilityRegistry()
    config_revision = f"{LOCAL_KNOWLEDGE_CONFIG_REVISION}:{_model_config_revision(model_config)}"
    for capability, operation_version in LOCAL_KNOWLEDGE_OPERATIONS.items():
        registry.register(
            CapabilityBinding(
                capability=capability,
                provider_id=runtime.provider_id,
                provider_deployment_id=LOCAL_KNOWLEDGE_DEPLOYMENT,
                service_contract_version=LOCAL_KNOWLEDGE_CONTRACT,
                provider=runtime,
                operation_versions=((capability, operation_version),),
                config_revision=config_revision,
            )
        )

    def rehydrate(binding: DurableCapabilityBinding) -> KnowledgeRuntime:
        """按持久化绑定重建本地知识运行时，契约或修订号不符时抛 LookupError。"""
        if (
            binding.service_contract_version != LOCAL_KNOWLEDGE_CONTRACT
            or binding.config_revision != config_revision
            or binding.capability not in LOCAL_KNOWLEDGE_OPERATIONS
            or dict(binding.operation_versions)
            != {binding.capability: LOCAL_KNOWLEDGE_OPERATIONS[binding.capability]}
        ):
            raise LookupError("local Knowledge binding revision is retired")
        restored = LocalKnowledgeRuntime(service_factory, db, model_config)
        if restored.provider_id != "local_knowledge":
            raise LookupError("local Knowledge provider identity is unavailable")
        return restored

    registry.register_rehydrator(
        runtime.provider_id,
        LOCAL_KNOWLEDGE_DEPLOYMENT,
        rehydrate,
    )
    registry.seal()
    return registry
