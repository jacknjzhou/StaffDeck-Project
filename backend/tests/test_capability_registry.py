from dataclasses import dataclass

import pytest

from app.capabilities.contracts import (
    CapabilityContext,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SceneSkillDefinition,
)
from app.capabilities.registry import CapabilityBinding, CapabilityRegistry


@dataclass
class FakeKnowledge:
    """测试用的知识检索 provider,记录 provider_id 并按请求 ID 返回空结果。"""
    provider_id: str = "local_knowledge"

    def search(self, context: CapabilityContext, request: KnowledgeSearchQuery) -> KnowledgeSearchResult:
        """按请求上下文返回服务自有结构的知识检索结果,供注册表用例作为桩实现。"""
        return KnowledgeSearchResult(query_id=context.request_id)


def test_snapshot_freezes_provider_selection_for_a_turn() -> None:
    """验证快照冻结本轮 provider 选择,后续重复注册被拒也不影响已取快照。"""
    registry = CapabilityRegistry()
    first = FakeKnowledge()
    registry.register(
        CapabilityBinding("knowledge.search", "local_knowledge", "local-dev", "knowledge.v1", first)
    )

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.require("knowledge.search").provider is first

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            CapabilityBinding("knowledge.search", "remote_knowledge", "remote-prod", "knowledge.v1", FakeKnowledge("remote_knowledge"))
        )

    assert snapshot.require("knowledge.search").provider is first
    assert snapshot.snapshot_id


def test_sealed_registry_rejects_runtime_provider_mutation() -> None:
    """验证注册表封存后新增绑定与注册 rehydrator 都抛 RuntimeError。"""
    registry = CapabilityRegistry()
    registry.register(
        CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v1", object())
    )
    registry.seal()

    with pytest.raises(RuntimeError, match="sealed"):
        registry.register(
            CapabilityBinding("scene_skill.catalog", "local", "local-dev", "scene.v1", object())
        )
    with pytest.raises(RuntimeError, match="sealed"):
        registry.register_rehydrator("local", "local-dev", lambda _: object())


def test_capability_context_rejects_missing_provider_identity() -> None:
    """验证能力上下文缺少 agent_id 时构造失败,避免无归属的调用。"""
    with pytest.raises(ValueError, match="agent_id"):
        CapabilityContext(
            request_id="req-1",
            tenant_id="tenant-1",
            agent_id="",
            user_id="user-1",
            session_id="session-1",
            turn_id="turn-1",
            channel="web",
        )


def test_snapshot_only_contains_requested_capabilities() -> None:
    """验证快照只包含本轮请求的能力,未请求的能力既查不到也 require 失败。"""
    registry = CapabilityRegistry()
    registry.register(CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v1", object()))
    registry.register(CapabilityBinding("scene_skill.catalog", "local", "local-dev", "scene.v1", object()))

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.get("knowledge.search") is not None
    assert snapshot.get("scene_skill.catalog") is None
    with pytest.raises(LookupError, match="scene_skill.catalog"):
        snapshot.require("scene_skill.catalog")


def test_snapshot_rejects_contracts_outside_consumer_matrix() -> None:
    """验证快照拒绝消费方矩阵未声明的契约版本,防止越界使用能力。"""
    registry = CapabilityRegistry()
    registry.register(CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v2", object()))

    with pytest.raises(ValueError, match="unsupported capability contract"):
        registry.snapshot(
            {"knowledge.search"},
            supported_contracts={"knowledge.search": {"knowledge.v1"}},
        )


def test_rehydrate_uses_saved_deployment_identity_without_reresolving() -> None:
    """验证按快照保存的部署身份重水化绑定,不会重新解析 provider 选择。"""
    registry = CapabilityRegistry()
    provider = object()
    binding = CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v1", provider)
    registry.register(binding)
    snapshot = registry.snapshot({"knowledge.search"})
    restored = object()
    registry.register_rehydrator("local", "local-dev", lambda durable: restored)

    assert registry.rehydrate(snapshot.durable_bindings[0]) is restored


def test_knowledge_result_has_service_owned_shape_and_extensions() -> None:
    """验证知识检索结果保留服务自有字段,并可透传厂商扩展数据。"""
    result = KnowledgeSearchResult(
        query_id="q1",
        outcome="partial",
        extensions={"vendor_x": {"rerank_score": 0.8}},
    )
    assert result.outcome == "partial"
    assert result.extensions["vendor_x"]["rerank_score"] == 0.8


def test_scene_definition_preserves_existing_content_json_graph() -> None:
    """验证场景技能定义原样保留既有 content_json 图结构,不做 nodes/steps 重写。"""
    content_json = {
        "start_node_id": "collect_amount",
        "nodes": [
            {"id": "collect_amount", "type": "input"},
            {"id": "complete", "type": "terminal"},
        ],
        "edges": [{"from": "collect_amount", "to": "complete"}],
        "terminal_node_ids": ["complete"],
    }
    definition = SceneSkillDefinition(
        skill_id="expense_approval",
        version="1.0.0",
        definition_hash="sha256:definition",
        content_json=content_json,
        extensions={"vendor_x": {"revision": 1}},
    )

    assert definition.content_json == content_json
    assert "nodes" in definition.content_json
    assert "steps" not in definition.content_json
    assert definition.extensions["vendor_x"]["revision"] == 1
