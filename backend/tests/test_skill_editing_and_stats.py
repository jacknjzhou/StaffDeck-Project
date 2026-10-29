import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.chat import _active_skill_context_for_assistant_message, _active_skill_for_assistant_message
from app.api.skills import (
    _extract_uploaded_skill_file,
    _skill_stats,
    _validate_handoff_assignees,
    create_skill,
    draft_skill,
    distill_skill,
    list_skill_versions,
    list_skills,
    publish_skill,
    rollback_skill_version,
    skill_read,
    update_skill,
)
from app.agents.branching import ensure_open_gallery_binding, visible_published_skills
from app.db.models import AgentEvent, AgentProfile, ChannelBinding, ChannelIdentity, Message, Skill, SkillFeedback, SkillVersion, Tenant, Tool, User
from app.db.models import ModelConfig
from app.skills.skill_distiller import SkillDistiller
from app.skills.skill_editor import SkillEditor
from app.skills.nesting import SopNestingError
from app.skills.skill_reflection import PROMPT_PATH as SKILL_REFLECTION_PROMPT_PATH
from app.skills.skill_reflection import RUBRIC_LABELS
from app.skills.skill_schema import (
    SkillCard,
    SkillCreateRequest,
    SkillDistillRequest,
    SkillDistillResponse,
    SkillRewriteRequest,
    SkillUpdateRequest,
    skill_card_from_persisted,
)
from app.security.encryption import encrypt_secret


def _admin_user() -> User:
    """构造管理员用户，作为需要管理员权限的技能接口调用者。"""
    return User(id="user_admin", tenant_id="tenant_demo", username="admin", role="admin", password_hash="test")


def _owner_user() -> User:
    """构造普通员工用户，用于验证个人员工资源的创建者归属。"""
    return User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="test")


def test_skill_editor_only_merges_selected_step() -> None:
    """验证只选中单个步骤时，仅合并该步骤改写，基础信息与其他步骤保持不变。"""
    current = _skill_card()
    candidate = _skill_card()
    candidate.name = "不应修改基础信息"
    candidate.nodes[0].instruction = "新的收集说明"
    candidate.nodes[1].instruction = "不应修改其他步骤"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写步骤。",
            "draft_skill": candidate.model_dump(),
            "changed_paths": ["nodes.collect_info"],
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="只优化第一步",
            target_path="nodes.collect_info",
            target_label="步骤 1",
        ),
    )

    assert response.draft_skill.name == current.name
    assert response.draft_skill.nodes[0].instruction == "新的收集说明"
    assert response.draft_skill.nodes[1].instruction == current.nodes[1].instruction


def test_skill_editor_merges_multiple_selected_targets() -> None:
    """验证同时选中基础信息与步骤时两处都合并，未选中的步骤保持原样。"""
    current = _skill_card()
    candidate = _skill_card()
    candidate.description = "新的描述"
    candidate.nodes[0].instruction = "新的收集说明"
    candidate.nodes[1].instruction = "不应修改第二步"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写多个区域。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="优化基础信息和第一步",
            target_path="basic",
            target_paths=["basic", "nodes.collect_info"],
            target_label="基础信息、步骤 1",
        ),
    )

    assert response.draft_skill.description == "新的描述"
    assert response.draft_skill.nodes[0].instruction == "新的收集说明"
    assert response.draft_skill.nodes[1].instruction == current.nodes[1].instruction


def test_skill_editor_can_target_node_by_index() -> None:
    """验证以 nodes[1] 下标选中节点时只替换该下标节点，其他节点不被改写。"""
    current = _skill_card()
    candidate = _skill_card()
    candidate.nodes[0].instruction = "不应修改第一步"
    candidate.nodes[1].instruction = "只修改第二个节点"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已改写指定下标节点。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="只改第二个节点",
            target_path="nodes[1]",
            target_paths=["nodes[1]"],
            target_label="步骤 2",
        ),
    )

    assert response.draft_skill.nodes[0].instruction == current.nodes[0].instruction
    assert response.draft_skill.nodes[1].instruction == "只修改第二个节点"


def test_skill_editor_allows_selected_step_deletion() -> None:
    """验证允许删除被选中的步骤，删除后草稿只保留剩余节点。"""
    current = _skill_card()
    candidate_data = current.model_dump(mode="json")
    candidate_data["nodes"] = candidate_data["nodes"][:1]
    candidate_data["edges"] = []
    candidate_data["terminal_node_ids"] = ["collect_info"]
    candidate = SkillCard.model_validate(candidate_data)

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已删除反馈步骤。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="删除第二步",
            target_path="nodes[1]",
            target_paths=["nodes[1]"],
            target_label="步骤 2",
        ),
    )

    assert [step.node_id for step in response.draft_skill.nodes] == ["collect_info"]


def test_skill_editor_allows_selected_step_insertion() -> None:
    """验证允许在被选中步骤后插入新步骤，且模型未申报的基础信息不被覆盖。"""
    current = _skill_card()
    candidate_data = current.model_dump(mode="json")
    candidate_data["nodes"].insert(
        1,
        {
            "node_id": "confirm_purchase",
            "name": "确认购买信息",
            "instruction": "向用户确认商品和数量。",
            "expected_user_info": ["purchase_confirmed"],
            "allowed_actions": ["ask_user", "continue_flow"],
        },
    )
    candidate = SkillCard.model_validate(candidate_data)

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已新增确认步骤。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="在第一步后新增确认步骤",
            target_path="nodes[0]",
            target_paths=["nodes[0]"],
            target_label="步骤 1",
        ),
    )

    assert [step.node_id for step in response.draft_skill.nodes] == [
        "collect_info",
        "confirm_purchase",
        "reply_result",
    ]
    assert response.draft_skill.name == current.name


def test_skill_editor_merges_selected_step_id_change() -> None:
    """验证选中节点修改 node_id 时只合并该节点，其他节点 ID 保持不变。"""
    current = _skill_card()
    current.nodes[1].node_id = "create_order"
    current.edges[0].next_node_id = "create_order"
    current.terminal_node_ids = ["create_order"]
    candidate = _skill_card()
    candidate.nodes[1].node_id = "feedback_order_result"
    candidate.edges[0].next_node_id = "feedback_order_result"
    candidate.terminal_node_ids = ["feedback_order_result"]

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已修正步骤 ID。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="把反馈订单结果的 step_id 从 create_order 改成 feedback_order_result",
            target_path="nodes[1]",
            target_paths=["nodes[1]"],
            target_label="步骤 2：反馈结果",
        ),
    )

    assert response.draft_skill.nodes[0].node_id == current.nodes[0].node_id
    assert response.draft_skill.nodes[1].node_id == "feedback_order_result"


def test_skill_editor_applies_step_id_corrections_to_final_draft() -> None:
    """验证节点 ID 修正会落到最终草稿，并写入 changed_paths 且不产生警告。"""
    current = _skill_card()
    candidate = _skill_card()
    candidate.nodes[1].node_id = "feedback_order_result"
    candidate.edges[0].next_node_id = "feedback_order_result"
    candidate.terminal_node_ids = ["feedback_order_result"]

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已修正步骤 ID。",
            "draft_skill": candidate.model_dump(),
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="把第二个节点 ID 改成 feedback_order_result",
            target_path="nodes[1]",
            target_paths=["nodes[1]"],
            target_label="步骤 2",
        ),
    )

    assert response.draft_skill.nodes[0].node_id == "collect_info"
    assert response.draft_skill.nodes[1].node_id == "feedback_order_result"
    assert "nodes[1]" in response.changed_paths
    assert not response.warnings


def test_skill_editor_applies_patch_response_without_full_draft() -> None:
    """验证模型只返回 patches 而不返回完整草稿时，补丁仍能落到目标字段。"""
    current = _skill_card()
    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "assistant_message": "已精简回复规则。",
            "patches": [
                {
                    "path": "response_rules",
                    "value": ["信息不足时追问；工具成功后给出明确结果，不编造事实。"],
                }
            ],
            "changed_paths": ["basic"],
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="回复规则太长了，精简一下",
            target_path="basic",
            target_paths=["basic", "nodes[0]", "nodes[1]"],
            target_label="全部区域",
        ),
    )

    assert response.draft_skill.response_rules == ["信息不足时追问；工具成功后给出明确结果，不编造事实。"]
    assert response.draft_skill.nodes[0].instruction == current.nodes[0].instruction


def test_skill_editor_patches_graph_topology_and_capability_refs() -> None:
    """验证补丁可更新连线拓扑与节点能力引用，并把 graph 记入变更路径。"""
    current = _skill_card()
    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "patches": [
                {
                    "path": "edges",
                    "value": [
                        {
                            "source_node_id": "collect_info",
                            "next_node_id": "reply_result",
                            "condition": "product_id 已填写",
                            "priority": 1,
                            "label": "信息完整",
                        }
                    ],
                },
                {
                    "path": "nodes[0].capability_refs",
                    "value": {
                        "tool_ids": ["product.price_query"],
                        "required_tool_ids": ["product.price_query"],
                    },
                },
            ]
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="商品信息完整后强制查询价格再继续",
            target_paths=["nodes[0]"],
        ),
    )

    assert response.draft_skill.edges[0].condition == "product_id 已填写"
    assert response.draft_skill.nodes[0].capability_refs.required_tool_ids == [
        "product.price_query"
    ]
    assert "graph" in response.changed_paths


def test_skill_editor_does_not_apply_unreported_graph_changes() -> None:
    """验证模型未申报的图形变更（连线标签）不会被合并进最终草稿。"""
    current = _skill_card()
    candidate = current.model_copy(deep=True)
    candidate.nodes[0].instruction = "只修改节点说明"
    candidate.edges[0].label = "模型顺手改了连线"

    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {
            "draft_skill": candidate.model_dump(mode="json"),
            "changed_paths": ["nodes[0]"],
        },
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="只修改节点说明",
            target_paths=["nodes[0]"],
        ),
    )

    assert response.draft_skill.nodes[0].instruction == "只修改节点说明"
    assert response.draft_skill.edges[0].label == "默认推进"
    assert "graph" not in response.changed_paths


def test_skill_editor_validates_and_normalizes_nested_sop() -> None:
    """验证节点改成子 SOP 时先校验嵌套关系，并清空父节点残留的指令与能力。"""
    current = _skill_card()
    candidate = current.model_dump(mode="json")
    candidate["nodes"][0].update(
        {
            "type": "subflow",
            "sub_sop_id": "child_purchase",
            "instruction": "不应在父节点继续执行",
            "allowed_actions": ["ask_user"],
        }
    )
    response = SkillEditor()._normalize_response(  # noqa: SLF001
        {"draft_skill": candidate},
        SkillRewriteRequest(
            tenant_id="tenant_demo",
            current_skill=current,
            instruction="第一步改为调用子 SOP",
            target_paths=["nodes[0]"],
            available_sops=[_available_sop("child_purchase")],
        ),
    )

    node = response.draft_skill.nodes[0]
    assert node.type == "subflow"
    assert node.sub_sop_id == "child_purchase"
    assert node.instruction == ""
    assert node.allowed_actions == []
    assert node.capability_refs.tool_ids == []


def test_skill_editor_rejects_unknown_or_cyclic_nested_sop() -> None:
    """验证引用未知或成环的子 SOP 时抛出 SopNestingError 拒绝改写。"""
    current = _skill_card()
    candidate = current.model_dump(mode="json")
    candidate["nodes"][0].update(
        {
            "type": "subflow",
            "sub_sop_id": "child_purchase",
        }
    )
    request = SkillRewriteRequest(
        tenant_id="tenant_demo",
        current_skill=current,
        instruction="第一步改为调用子 SOP",
        target_paths=["nodes[0]"],
    )

    with pytest.raises(SopNestingError, match="missing or unpublished"):
        SkillEditor()._normalize_response({"draft_skill": candidate}, request)  # noqa: SLF001

    cyclic_child = _available_sop("child_purchase", nested_sop_ids=[current.skill_id])
    with pytest.raises(SopNestingError, match="cycle"):
        SkillEditor()._normalize_response(  # noqa: SLF001
            {"draft_skill": candidate},
            request.model_copy(update={"available_sops": [cyclic_child]}),
        )


def test_skill_editor_payload_exposes_compact_sop_catalog() -> None:
    """验证提供给模型的可用 SOP 目录只含精简字段，不暴露完整 content。"""
    child = _available_sop("child_purchase")
    request = SkillRewriteRequest(
        tenant_id="tenant_demo",
        current_skill=_skill_card(),
        instruction="调用子 SOP",
        available_sops=[child],
    )

    payload = SkillEditor()._payload(request)  # noqa: SLF001

    assert payload["available_sops"] == [
        {
            "skill_id": "child_purchase",
            "name": "child_purchase",
            "capability_scope": "sop_specific",
            "status": "published",
            "nested_sop_ids": [],
            "selectable": True,
        }
    ]
    assert "content" not in payload["available_sops"][0]


def test_skill_editor_stream_repairs_invalid_json_once(monkeypatch) -> None:
    """验证模型输出非法 JSON 时只重试修复一次，并依次推送各阶段状态。"""
    def fake_stream(self, _system_prompt: str, _payload: dict):  # noqa: ANN001
        """伪造流式输出：先返回被截断的 JSON，触发后续修复流程。"""
        assert self.max_output_tokens == 8192
        yield '{"assistant_message": "截断的输出", "patches": ['

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造非流式输出：修复请求返回合法补丁，反思请求返回全部通过。"""
        assert self.max_output_tokens == 8192
        if payload.get("reflection_round"):
            return _reflection_passes_json()
        assert "previous_error" in payload
        return json.dumps(
            {
                "assistant_message": "已精简回复规则。",
                "patches": [
                    {
                        "path": "response_rules",
                        "value": ["信息不足时追问；工具成功后给出明确结果，不编造事实。"],
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.skills.skill_editor.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_editor.LLMClient.generate_text", fake_text)

    events = list(
        SkillEditor().stream_text(
            SkillRewriteRequest(
                tenant_id="tenant_demo",
                current_skill=_skill_card(),
                instruction="回复规则太长了，精简一下",
                target_path="basic",
                target_paths=["basic", "nodes[0]", "nodes[1]"],
                target_label="全部区域",
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]
    complete = next(event for event in events if event["event"] == "complete")

    assert "模型输出需要修复，正在重试一次" in status_texts
    assert "正在校验改写范围与工具接入" in status_texts
    assert any(text.startswith("正在校验技能结果") for text in status_texts)
    assert "正在整理校验后的改写结果" in status_texts
    assert complete["data"]["draft_skill"]["response_rules"] == [
        "信息不足时追问；工具成功后给出明确结果，不编造事实。"
    ]


def test_skill_stats_counts_skill_entry_and_feedback() -> None:
    """验证技能调用次数与正负反馈分别按技能、技能@版本两个维度统计。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        content = _skill_card()
        db.add(
            Skill(
                tenant_id="tenant_demo",
                skill_id="purchase",
                version="1.5.0",
                name="购买商品",
                content_json=content.model_dump(),
                status="published",
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": "purchase", "to_skill_version": "1.5.0"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_2",
                event_type="skill_started",
                payload_json={"to_skill_id": "purchase", "to_skill_version": "1.5.0"},
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id="purchase",
                skill_version="1.5.0",
                session_id="session_1",
                message_id="msg_1",
                user_id="user_1",
                rating="up",
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id="purchase",
                skill_version="1.5.0",
                session_id="session_2",
                message_id="msg_2",
                user_id="user_2",
                rating="down",
            )
        )
        db.commit()

        stats = _skill_stats(db, "tenant_demo")

    assert stats["purchase"]["call_count"] == 2
    assert stats["purchase"]["positive_feedback_count"] == 1
    assert stats["purchase"]["negative_feedback_count"] == 1
    assert stats["purchase"]["positive_rate"] == 0.5
    assert stats["purchase"]["negative_rate"] == 0.5
    assert stats["purchase@1.5.0"]["call_count"] == 2
    assert stats["purchase@1.5.0"]["positive_feedback_count"] == 1
    assert stats["purchase@1.5.0"]["negative_feedback_count"] == 1


def test_skill_stats_count_one_negative_feedback_per_flow() -> None:
    """验证同一会话内多条负反馈只计一次，避免重复拉低负反馈率。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        content = _skill_card()
        db.add(
            Skill(
                tenant_id="tenant_demo",
                skill_id="purchase",
                version="1.5.0",
                name="购买商品",
                content_json=content.model_dump(),
                status="published",
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": "purchase", "to_skill_version": "1.5.0"},
            )
        )
        for message_id in ["msg_1", "msg_2"]:
            db.add(
                SkillFeedback(
                    tenant_id="tenant_demo",
                    skill_id="purchase",
                    skill_version="1.5.0",
                    session_id="session_1",
                    message_id=message_id,
                    user_id="user_1",
                    rating="down",
                )
            )
        db.commit()

        stats = _skill_stats(db, "tenant_demo")

    assert stats["purchase"]["call_count"] == 1
    assert stats["purchase"]["negative_feedback_count"] == 1
    assert stats["purchase"]["negative_rate"] == 1.0
    assert stats["purchase@1.5.0"]["negative_feedback_count"] == 1


def test_skill_versions_are_snapshotted_with_version_stats() -> None:
    """验证版本列表返回的是版本快照，并带上该版本自身的调用统计。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="开放广场", is_overall=True))
        content = _skill_card()
        row = Skill(
            tenant_id="tenant_demo",
            skill_id=content.skill_id,
            version="1.5.0",
            name=content.name,
            content_json=content.model_dump(),
            status="draft",
        )
        db.add(row)
        db.flush()
        ensure_open_gallery_binding(db, "tenant_demo", "skill", row.id, "active")
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": content.skill_id, "to_skill_version": "1.5.0"},
            )
        )
        db.commit()

        versions = list_skill_versions(content.skill_id, "tenant_demo", db)

    assert versions[0].version == "1.5.0"
    assert versions[0].call_count == 1


def test_skill_id_cannot_be_modified_after_create() -> None:
    """验证创建后修改 skill_id 会以 400 拒绝并返回固定错误信息。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        content = _skill_card()
        db.add(
            Skill(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                version=content.version,
                name=content.name,
                content_json=content.model_dump(),
                status="published",
            )
        )
        db.commit()

        edited_content = content.model_copy(deep=True)
        edited_content.skill_id = "purchase_v2"

        with pytest.raises(HTTPException) as exc_info:
            update_skill(
                content.skill_id,
                SkillUpdateRequest(tenant_id="tenant_demo", content=edited_content, status="published"),
                db=db,
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "SOP skill_id cannot be modified"


def test_skill_can_return_to_draft_without_leaving_runtime_list() -> None:
    """验证技能退回草稿后管理列表仍可见，运行时列表则只保留已发布技能。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="开放广场", is_overall=True))
        content = _skill_card()
        row = Skill(
            tenant_id="tenant_demo",
            skill_id=content.skill_id,
            version=content.version,
            name=content.name,
            content_json=content.model_dump(),
            status="published",
        )
        db.add(row)
        db.commit()
        ensure_open_gallery_binding(db, "tenant_demo", "skill", row.id, "active")
        db.commit()

        drafted = draft_skill(content.skill_id, tenant_id="tenant_demo", db=db, current_user=_admin_user())

        assert drafted.status == "draft"
        assert [item.skill_id for item in list_skills("tenant_demo", db)] == [content.skill_id]
        assert visible_published_skills(db, "tenant_demo") == []

        published = publish_skill(content.skill_id, tenant_id="tenant_demo", db=db, current_user=_admin_user())

        assert published.status == "published"
        assert [item.skill_id for item in visible_published_skills(db, "tenant_demo")] == [content.skill_id]


def test_personal_created_skill_uses_agent_owner_as_creator() -> None:
    """验证个人员工下创建的技能，创建者信息取自该员工的所有者。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        agent = AgentProfile(
            id="agent_owner",
            tenant_id="tenant_demo",
            name="个人员工",
            is_overall=False,
            metadata_json={
                "owner_user_id": "user_owner",
                "owner_username": "owner",
                "owner_display_name": "Owner",
                "created_by_user_id": "user_owner",
                "created_by_username": "owner",
            },
        )
        db.add(agent)
        db.commit()

        created = create_skill(
            SkillCreateRequest(tenant_id="tenant_demo", content=_skill_card(), status="published"),
            agent_id=agent.id,
            db=db,
            current_user=_owner_user(),
        )
        listed = list_skills("tenant_demo", db, agent_id=agent.id)

        assert created.metadata["creator_name"] == "owner"
        assert created.metadata["created_by_username"] == "owner"
        assert listed[0].metadata["creator_name"] == "owner"
        assert listed[0].metadata["created_by_username"] == "owner"


def test_personal_created_skill_binds_explicit_tools_to_its_skill_id() -> None:
    """验证个人技能显式调用的工具会被追加到该工具的可用技能列表。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        agent = AgentProfile(
            id="agent_tool_owner",
            tenant_id="tenant_demo",
            name="个人员工",
            is_overall=False,
            metadata_json={
                "owner_user_id": "user_owner",
                "owner_username": "owner",
            },
        )
        tool = Tool(
            tenant_id="tenant_demo",
            name="product.price_query",
            method="POST",
            url="http://localhost/api/mock/product/price-query",
            allowed_skills_json=["skill_price_compare_001"],
            enabled=True,
        )
        db.add(agent)
        db.add(tool)
        db.commit()

        content = _skill_card().model_copy(deep=True)
        content.skill_id = "price_compare_copy"
        content.nodes[0].allowed_actions = ["call_tool:product.price_query"]
        create_skill(
            SkillCreateRequest(
                tenant_id="tenant_demo",
                content=content,
                status="published",
            ),
            agent_id=agent.id,
            db=db,
            current_user=_owner_user(),
        )

        db.refresh(tool)
        assert tool.allowed_skills_json == [
            "skill_price_compare_001",
            "price_compare_copy",
        ]


def test_personal_created_skill_uses_current_admin_when_owner_missing() -> None:
    """验证员工缺少所有者信息时，创建者信息回落为当前管理员。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        current_user = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            role="admin",
            display_name="Admin",
            password_hash="test",
        )
        db.add(current_user)
        agent = AgentProfile(
            id="agent_legacy",
            tenant_id="tenant_demo",
            name="旧员工",
            is_overall=False,
            metadata_json={},
        )
        db.add(agent)
        db.commit()

        created = create_skill(
            SkillCreateRequest(tenant_id="tenant_demo", content=_skill_card(), status="published"),
            agent_id=agent.id,
            db=db,
            current_user=current_user,
        )
        listed = list_skills("tenant_demo", db, agent_id=agent.id)

        assert created.metadata["creator_name"] == "admin"
        assert created.metadata["created_by_username"] == "admin"
        assert listed[0].metadata["creator_name"] == "admin"
        assert listed[0].metadata["created_by_username"] == "admin"


def test_unversioned_stats_remain_aggregate_without_guessing_a_version() -> None:
    """验证无版本号的调用与反馈只计入聚合统计，不臆测归属到某个版本。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="开放广场", is_overall=True))
        content = _skill_card()
        old_content = content.model_copy(update={"version": "1.0.0"})
        new_content = content.model_copy(update={"version": "1.1.0"})
        row = Skill(
            tenant_id="tenant_demo",
            skill_id=content.skill_id,
            version="1.1.0",
            name=content.name,
            content_json=new_content.model_dump(),
            status="published",
        )
        db.add(row)
        db.flush()
        ensure_open_gallery_binding(db, "tenant_demo", "skill", row.id, "active")
        db.add(
            SkillVersion(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                version="1.0.0",
                name=content.name,
                content_json=old_content.model_dump(),
                status="published",
            )
        )
        db.add(
            SkillVersion(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                version="1.1.0",
                name=content.name,
                content_json=new_content.model_dump(),
                status="published",
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_legacy",
                event_type="skill_started",
                payload_json={"to_skill_id": content.skill_id},
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                session_id="session_legacy",
                message_id="msg_legacy",
                user_id="user_legacy",
                rating="down",
            )
        )
        db.commit()

        versions = list_skill_versions(content.skill_id, "tenant_demo", db)
        stats = _skill_stats(db, "tenant_demo")
        versions_by_version = {version.version: version for version in versions}
        current_skill = db.exec(
            select(Skill).where(Skill.tenant_id == "tenant_demo", Skill.skill_id == content.skill_id)
        ).one()
        payload = skill_read(current_skill, stats)

    assert stats[content.skill_id]["call_count"] == 1
    assert stats[content.skill_id]["negative_feedback_count"] == 1
    assert versions_by_version["1.0.0"].call_count == 0
    assert versions_by_version["1.0.0"].negative_feedback_count == 0
    assert versions_by_version["1.0.0"].negative_rate == 0.0
    assert versions_by_version["1.1.0"].call_count == 0
    assert versions_by_version["1.1.0"].negative_feedback_count == 0
    assert versions_by_version["1.1.0"].negative_rate == 0.0
    assert payload.call_count == 0
    assert payload.negative_feedback_count == 0
    assert payload.negative_rate == 0.0


def test_unversioned_stats_do_not_fall_back_to_current_version() -> None:
    """验证无版本号统计不会回落到当前版本，技能读接口返回零值。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        content = _skill_card()
        db.add(
            Skill(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                version="1.0.0",
                name=content.name,
                content_json=content.model_dump(),
                status="published",
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_legacy",
                event_type="skill_started",
                payload_json={"to_skill_id": content.skill_id},
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                session_id="session_legacy",
                message_id="msg_legacy",
                user_id="user_legacy",
                rating="down",
            )
        )
        db.commit()

        current_skill = db.exec(
            select(Skill).where(Skill.tenant_id == "tenant_demo", Skill.skill_id == content.skill_id)
        ).one()
        stats = _skill_stats(db, "tenant_demo")
        payload = skill_read(current_skill, stats)

    assert stats[content.skill_id]["call_count"] == 1
    assert stats[content.skill_id]["negative_feedback_count"] == 1
    assert payload.call_count == 0
    assert payload.negative_feedback_count == 0
    assert payload.negative_rate == 0.0


def test_rollback_skill_version_restores_content_without_copying_stats() -> None:
    """验证回滚只恢复目标版本的内容，不把统计数字一并复制。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        old_content = _skill_card()
        old_content.version = "1.0.0"
        old_content.name = "旧版购买"
        new_content = _skill_card()
        new_content.version = "1.1.0"
        new_content.name = "新版购买"
        db.add(
            Skill(
                tenant_id="tenant_demo",
                skill_id=old_content.skill_id,
                version="1.1.0",
                name=new_content.name,
                content_json=new_content.model_dump(),
                status="published",
            )
        )
        db.add(
            SkillVersion(
                tenant_id="tenant_demo",
                skill_id=old_content.skill_id,
                version="1.0.0",
                name=old_content.name,
                content_json=old_content.model_dump(),
                status="published",
            )
        )
        db.add(
            SkillVersion(
                tenant_id="tenant_demo",
                skill_id=old_content.skill_id,
                version="1.1.0",
                name=new_content.name,
                content_json=new_content.model_dump(),
                status="published",
            )
        )
        db.commit()

        payload = rollback_skill_version(
            old_content.skill_id,
            "1.0.0",
            "tenant_demo",
            db,
            current_user=_admin_user(),
        )
        row = db.exec(
            select(Skill).where(
                Skill.tenant_id == "tenant_demo",
                Skill.skill_id == old_content.skill_id,
            )
        ).one()

    assert payload.version == "1.0.0"
    assert payload.name == "旧版购买"
    assert payload.status == "published"
    assert row.content_json["version"] == "1.0.0"
    assert row.content_json["name"] == "旧版购买"


def test_skill_read_uses_current_version_stats_for_skill_list() -> None:
    """验证技能读接口在列表场景取当前版本维度的统计而非聚合统计。"""
    content = _skill_card()
    row = Skill(
        tenant_id="tenant_demo",
        skill_id="purchase",
        version="1.5.0",
        name="购买商品",
        content_json=content.model_dump(),
        status="published",
    )
    payload = skill_read(
        row,
        {
            "purchase": {
                "call_count": 3,
                "positive_feedback_count": 2,
                "negative_feedback_count": 1,
                "positive_rate": 0.6667,
                "negative_rate": 0.3333,
            },
            "purchase@1.5.0": {
                "call_count": 1,
                "positive_feedback_count": 0,
                "negative_feedback_count": 0,
                "positive_rate": 0.0,
                "negative_rate": 0.0,
            },
        },
    )

    assert payload.call_count == 1
    assert payload.positive_feedback_count == 0
    assert payload.negative_feedback_count == 0


def test_skill_read_includes_total_and_recent_version_ranking_stats() -> None:
    """验证列表返回总量统计，以及最近若干版本的排行统计。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_overall", tenant_id="tenant_demo", name="整体智能体", is_overall=True))
        content = _skill_card()
        current = content.model_copy(update={"version": "1.3.0"})
        skill = Skill(
            tenant_id="tenant_demo",
            skill_id=content.skill_id,
            version="1.3.0",
            name=content.name,
            content_json=current.model_dump(),
            status="published",
        )
        db.add(skill)
        db.flush()
        ensure_open_gallery_binding(db, "tenant_demo", "skill", skill.id, "active")
        for version in ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]:
            version_content = content.model_copy(update={"version": version})
            db.add(
                SkillVersion(
                    tenant_id="tenant_demo",
                    skill_id=content.skill_id,
                    version=version,
                    name=content.name,
                    content_json=version_content.model_dump(),
                    status="published",
                )
            )
            db.add(
                AgentEvent(
                    tenant_id="tenant_demo",
                    session_id=f"session_{version}",
                    event_type="skill_started",
                    payload_json={"to_skill_id": content.skill_id, "to_skill_version": version},
                )
            )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                skill_version="1.0.0",
                session_id="session_1.0.0",
                message_id="msg_old",
                user_id="user_old",
                rating="down",
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                skill_version="1.2.0",
                session_id="session_1.2.0",
                message_id="msg_recent_up",
                user_id="user_recent_up",
                rating="up",
            )
        )
        db.add(
            SkillFeedback(
                tenant_id="tenant_demo",
                skill_id=content.skill_id,
                skill_version="1.3.0",
                session_id="session_1.3.0",
                message_id="msg_recent_down",
                user_id="user_recent_down",
                rating="down",
            )
        )
        db.commit()

        rows = list_skills("tenant_demo", db)

    payload = rows[0]
    assert payload.call_count == 1
    assert payload.total_call_count == 4
    assert payload.total_negative_feedback_count == 2
    assert payload.recent_versions == ["1.3.0", "1.2.0", "1.1.0"]
    assert payload.recent_call_count == 3
    assert payload.recent_positive_feedback_count == 1
    assert payload.recent_negative_feedback_count == 1
    assert payload.recent_positive_rate == 0.3333
    assert payload.recent_negative_rate == 0.3333


def test_message_feedback_attribution_uses_turn_active_skill() -> None:
    """验证反馈归因取该轮对话中生效的技能与步骤，而非会话首个技能。"""
    with _test_session() as db:
        db.add(Message(id="msg_user", tenant_id="tenant_demo", session_id="session_1", role="user", content="我要退款"))
        assistant = Message(
            id="msg_assistant",
            tenant_id="tenant_demo",
            session_id="session_1",
            role="assistant",
            content="请提供订单号。",
        )
        db.add(assistant)
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="user_message_received",
                payload_json={"message_id": "msg_user", "message": "我要退款"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_started",
                payload_json={"to_skill_id": "refund", "to_step_id": "collect_order"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="assistant_message_created",
                payload_json={"message_id": "msg_assistant", "reply": "请提供订单号。"},
            )
        )
        db.commit()

        skill_id = _active_skill_for_assistant_message(db, "tenant_demo", assistant)
        context = _active_skill_context_for_assistant_message(db, "tenant_demo", assistant)

    assert skill_id == "refund"
    assert context == {"skill_id": "refund", "skill_version": None, "node_id": "collect_order"}


def test_message_feedback_attribution_uses_router_skill_hint_for_legacy_step_event() -> None:
    """验证旧版只记录步骤事件时，用路由决策里的技能提示补全归因。"""
    with _test_session() as db:
        db.add(Message(id="msg_user", tenant_id="tenant_demo", session_id="session_1", role="user", content="继续下单"))
        assistant = Message(
            id="msg_assistant",
            tenant_id="tenant_demo",
            session_id="session_1",
            role="assistant",
            content="已为您下单。",
        )
        db.add(assistant)
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="user_message_received",
                payload_json={"message_id": "msg_user", "message": "继续下单"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="router_decision_created",
                payload_json={
                    "decision": "continue_active",
                    "target_skill_id": "purchase",
                    "target_node_id": "create_order",
                },
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="skill_step_changed",
                payload_json={"from_step_id": "confirm_purchase", "to_step_id": "create_order"},
            )
        )
        db.add(
            AgentEvent(
                tenant_id="tenant_demo",
                session_id="session_1",
                event_type="assistant_message_created",
                payload_json={"message_id": "msg_assistant", "reply": "已为您下单。"},
            )
        )
        db.commit()

        skill_id = _active_skill_for_assistant_message(db, "tenant_demo", assistant)
        context = _active_skill_context_for_assistant_message(db, "tenant_demo", assistant)

    assert skill_id == "purchase"
    assert context == {"skill_id": "purchase", "skill_version": None, "node_id": "create_order"}


def test_skill_read_preserves_graph_node_ids() -> None:
    """验证技能读接口返回的内容保留图的节点 ID 与顺序。"""
    content = _skill_card()
    row = Skill(
        tenant_id="tenant_demo",
        skill_id=content.skill_id,
        name=content.name,
        content_json=content.model_dump(),
        status="draft",
    )

    payload = skill_read(row)
    node_ids = [node.node_id for node in payload.content.nodes]

    assert node_ids == ["collect_info", "reply_result"]


def test_persisted_skill_promotes_required_capabilities_without_weakening_schema() -> None:
    """验证持久化技能恢复时把必选能力提升为可用能力，同时不放松 schema 校验。"""
    content = _skill_card().model_dump(mode="json")
    content["nodes"][0]["capability_refs"] = {
        "required_tool_ids": ["order.query"],
    }

    with pytest.raises(ValueError, match="required_tool_ids must be a subset"):
        SkillCard.model_validate(content)

    restored = skill_card_from_persisted(content)

    assert restored.nodes[0].capability_refs.tool_ids == ["order.query"]
    assert restored.nodes[0].capability_refs.required_tool_ids == ["order.query"]


def test_skill_distiller_stream_uses_generation_status(monkeypatch) -> None:
    """验证蒸馏流程推送生成阶段状态，且提示词传递原始文本而不是 JSON。"""
    def fake_stream(self, _system_prompt: str, _payload: str):  # noqa: ANN001
        """伪造蒸馏流式输出：返回一份完整的技能草稿 JSON 文本。"""
        assert self.max_output_tokens == 8192
        assert isinstance(_payload, str)
        assert _payload.startswith("技能标题：商品比价\n原始流程：")
        assert not _payload.lstrip().startswith("{")
        yield """
        {
          "draft_skill": {
            "skill_id": "skill_compare_price",
            "name": "商品比价",
            "version": "1.0.0",
            "business_domain": "ecommerce",
            "description": "比较两个商品价格。",
            "trigger_intents": ["compare_price"],
            "user_utterance_examples": ["比较 A 和 B"],
            "goal": ["收集两个商品名称", "反馈比价结果"],
            "required_info": ["product_name_1", "product_name_2"],
            "slot_filling_policy": {
              "enabled": true,
              "multi_slot_per_turn": true,
              "extract_scope": "all_skill_expected_user_info",
              "skip_satisfied_steps": true,
              "target_info": ["product_name_1", "product_name_2"]
            },
            "nodes": [
              {
                "node_id": "collect_names",
                "name": "收集商品名称",
                "instruction": "收集两个商品名称。",
                "expected_user_info": ["product_name_1", "product_name_2"],
                "allowed_actions": ["ask_user"]
              },
              {
                "node_id": "reply_result",
                "name": "反馈结果",
                "instruction": "反馈明确结果。",
                "expected_user_info": [],
                "allowed_actions": ["answer_user"]
              }
            ],
            "interruption_policy": {},
            "response_rules": []
          },
            "warnings": []
        }
        """

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造反思调用：断言处于首轮反思并返回全部通过的结果。"""
        assert self.max_output_tokens == 8192
        assert payload.get("reflection_round") == 1
        return _reflection_passes_json()

    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text", fake_text)
    events = list(
        SkillDistiller().stream_text(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="商品比价",
                raw_content="用户提供两个商品的名称，系统根据商品价格进行比价",
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]

    assert "正在改写技能" not in status_texts
    assert "模型正在规划技能结构" in status_texts
    assert "正在校验模型输出结构" in status_texts
    assert "正在校验步骤闭环与工具接入" in status_texts
    assert any(text.startswith("正在校验技能结果") for text in status_texts)
    assert "正在整理校验后的技能草稿" in status_texts
    assert "校验完成，已完成 Skill Card 结构化" in status_texts


def test_skill_distiller_stream_reflects_and_repairs_generated_skill(monkeypatch) -> None:
    """验证反思发现缺少闭环步骤时应用修正，并重置此前已推送的片段。"""
    def fake_stream(self, _system_prompt: str, _payload: dict):  # noqa: ANN001
        """伪造蒸馏流式输出：返回缺少闭环回复步骤的技能草稿。"""
        assert self.max_output_tokens == 8192
        yield json.dumps(
            {
                "draft_skill": {
                    "skill_id": "skill_purchase",
                    "name": "购买流程",
                    "version": "1.0.0",
                    "business_domain": "ecommerce",
                    "description": "收集商品并反馈。",
                    "trigger_intents": ["buy_product"],
                    "user_utterance_examples": ["我要买 A1"],
                    "goal": ["收集商品", "创建订单"],
                    "required_info": ["product_id"],
                    "slot_filling_policy": {"enabled": True},
                    "response_rules": [],
                    "nodes": [
                        {
                            "node_id": "collect_product",
                            "name": "收集商品",
                            "instruction": "收集商品。",
                            "expected_user_info": ["product_id"],
                            "allowed_actions": ["ask_user"],
                        }
                    ],
                    "interruption_policy": {},
                },
                "warnings": [],
            },
            ensure_ascii=False,
        )

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造反思调用：首轮补上闭环步骤，第二轮返回通过结果。"""
        assert self.max_output_tokens == 8192
        if payload.get("reflection_round") == 1:
            revised = dict(payload["candidate_skill"])
            revised["nodes"] = [
                *revised["nodes"],
                {
                    "node_id": "reply_result",
                    "name": "反馈结果",
                    "instruction": "给用户明确最终回复。",
                    "expected_user_info": [],
                    "allowed_actions": ["answer_user"],
                },
            ]
            return json.dumps(
                {
                    "passed": False,
                    "summary": "缺少闭环回复步骤，已补充。",
                    "rubric_results": [
                        {
                            "name": "closed_loop",
                            "passed": False,
                            "finding": "没有最终回复步骤",
                            "origin": "generated_skill",
                        }
                    ],
                    "warnings": [],
                    "source_warnings": [],
                    "draft_skill": revised,
                    "tool_mentions": [],
                },
                ensure_ascii=False,
            )
        if payload.get("reflection_round") == 2:
            return _reflection_passes_json()
        raise AssertionError(f"unexpected payload: {payload}")

    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text", fake_text)

    events = list(
        SkillDistiller().stream_text(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="购买流程",
                raw_content="用户购买商品后需要得到明确订单结果",
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]
    complete = next(event for event in events if event["event"] == "complete")

    assert any("校验发现：闭环能力" in text for text in status_texts)
    assert any("校验未通过，正在应用第 1 轮修正" in text for text in status_texts)
    assert any("校验通过" in text for text in status_texts)
    assert any(event["event"] == "chunk_reset" for event in events)
    assert [step["node_id"] for step in complete["data"]["draft_skill"]["nodes"]][-1] == "reply_result"


def test_skill_distiller_reflection_checks_tool_call_format_without_rule_fallback(monkeypatch) -> None:
    """验证反思按 rubric 检查工具调用格式并修正裸 call_tool，不用规则兜底。"""
    def fake_stream(self, _system_prompt: str, _payload: dict):  # noqa: ANN001
        """伪造蒸馏流式输出：返回含裸 call_tool 动作的技能草稿。"""
        assert self.max_output_tokens == 8192
        yield json.dumps(
            {
                "draft_skill": {
                    "skill_id": "skill_price_compare",
                    "name": "商品比价",
                    "version": "1.0.0",
                    "business_domain": "ecommerce",
                    "description": "查询商品价格并比较。",
                    "trigger_intents": ["compare_price"],
                    "user_utterance_examples": ["比较 A1 和 A3"],
                    "goal": ["查询价格", "反馈比价结果"],
                    "required_info": ["product_name_1", "product_name_2"],
                    "slot_filling_policy": {"enabled": True},
                    "response_rules": [],
                    "nodes": [
                        {
                            "node_id": "query_price",
                            "name": "查询价格",
                            "instruction": "当商品名已满足时调用 product.price_query 工具查询价格。",
                            "expected_user_info": ["product_name_1", "product_name_2"],
                            "allowed_actions": ["call_tool", "continue_flow"],
                        },
                        {
                            "node_id": "reply_result",
                            "name": "反馈结果",
                            "instruction": "基于工具结果反馈比价结果。",
                            "expected_user_info": [],
                            "allowed_actions": ["answer_user"],
                        },
                    ],
                    "interruption_policy": {},
                },
                "warnings": [],
                "tool_mentions": [],
            },
            ensure_ascii=False,
        )

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造反思调用：断言 rubric 含工具调用格式项，首轮修正动作后次轮通过。"""
        assert self.max_output_tokens == 8192
        if payload.get("reflection_round") == 1:
            rubric_names = {item["name"] for item in payload["rubrics"]}
            assert "tool_call_format" in rubric_names
            assert payload["candidate_skill"]["nodes"][0]["allowed_actions"] == ["call_tool", "continue_flow"]
            revised = dict(payload["candidate_skill"])
            revised["nodes"] = [dict(step) for step in revised["nodes"]]
            revised["nodes"][0]["allowed_actions"] = ["call_tool:product.price_query", "continue_flow"]
            revised["nodes"][0]["instruction"] = (
                "当商品名已满足时调用 product.price_query 工具查询价格；"
                "工具成功后基于返回价格继续组织最终回复。"
            )
            return json.dumps(
                {
                    "passed": False,
                    "summary": "工具调用动作缺少具体工具名，已修正。",
                    "rubric_results": [
                        {
                            "name": "tool_call_format",
                            "passed": False,
                            "finding": "allowed_actions 中出现裸 call_tool，必须写成 call_tool:<tool_name>。",
                            "origin": "generated_skill",
                        }
                    ],
                    "warnings": [],
                    "source_warnings": [],
                    "draft_skill": revised,
                    "tool_mentions": [],
                },
                ensure_ascii=False,
            )
        if payload.get("reflection_round") == 2:
            return _reflection_passes_json()
        raise AssertionError(f"unexpected payload: {payload}")

    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text", fake_text)

    events = list(
        SkillDistiller().stream_text(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="商品比价",
                raw_content="用商品价格查询工具 product.price_query 查询两个商品价格后反馈比价结果。",
                available_tools=[{"name": "product.price_query", "input_schema": {"required": ["product_name"]}}],
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]
    complete = next(event for event in events if event["event"] == "complete")

    assert any("校验发现：工具调用格式" in text for text in status_texts)
    assert complete["data"]["draft_skill"]["nodes"][0]["allowed_actions"] == [
        "call_tool:product.price_query",
        "continue_flow",
    ]


def test_skill_reflection_prompt_keeps_new_candidate_tool_actions() -> None:
    """验证反思提示词保留新候选工具动作，并包含各 rubric 的中文标签。"""
    prompt = SKILL_REFLECTION_PROMPT_PATH.read_text(encoding="utf-8")

    assert RUBRIC_LABELS["tool_grounding"] == "工具依据"
    assert "resolution_status 为 existing 或 new_candidate" in prompt
    assert "保留该 action" in prompt
    assert "不得仅因不在 available_tools" in prompt
    assert "tool_suggestions(existing/new_candidate)" in prompt
    assert RUBRIC_LABELS["graph_integrity"] == "图结构完整性"
    assert RUBRIC_LABELS["nested_sop_grounding"] == "子 SOP 依据"
    assert RUBRIC_LABELS["capability_policy"] == "能力可见性与强制执行"
    assert "available_sops 中 selectable=true" in prompt


def test_skill_distiller_stream_repairs_invalid_json_with_model(monkeypatch) -> None:
    """验证蒸馏遇到非法 JSON 时由模型修复一次，并重置片段后继续。"""
    def fake_stream(self, _system_prompt: str, _payload: dict):  # noqa: ANN001
        """伪造流式输出：返回被截断的技能草稿 JSON。"""
        assert self.max_output_tokens == 8192
        yield '{"draft_skill": {"name": "截断"'

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造修复调用：断言修复轮次，并返回完整的技能草稿。"""
        assert self.max_output_tokens == 8192
        if payload.get("reflection_round"):
            return _reflection_passes_json()
        assert payload["repair_attempt"] == 1
        return json.dumps(
            {
                "draft_skill": {
                    "skill_id": "skill_compare_price",
                    "name": "商品比价",
                    "version": "1.0.0",
                    "business_domain": "ecommerce",
                    "description": "比较两个商品价格。",
                    "trigger_intents": ["compare_price"],
                    "user_utterance_examples": ["比较 A 和 B"],
                    "goal": ["收集两个商品名称", "反馈比价结果"],
                    "required_info": ["product_name_1", "product_name_2"],
                    "slot_filling_policy": {
                        "enabled": True,
                        "multi_slot_per_turn": True,
                        "extract_scope": "all_skill_expected_user_info",
                        "skip_satisfied_steps": True,
                        "target_info": ["product_name_1", "product_name_2"],
                    },
                    "response_rules": ["不要编造价格。"],
                    "nodes": [
                        {
                            "node_id": "collect_names",
                            "name": "收集商品名称",
                            "instruction": "收集两个商品名称。",
                            "expected_user_info": ["product_name_1", "product_name_2"],
                            "allowed_actions": ["ask_user"],
                        },
                        {
                            "node_id": "reply_result",
                            "name": "反馈结果",
                            "instruction": "反馈明确结果。",
                            "expected_user_info": [],
                            "allowed_actions": ["answer_user"],
                        },
                    ],
                    "interruption_policy": {},
                },
                "warnings": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text", fake_text)

    events = list(
        SkillDistiller().stream_text(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="商品比价",
                raw_content="用户提供两个商品的名称，系统根据商品价格进行比价",
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]
    complete = next(event for event in events if event["event"] == "complete")

    assert "模型输出需要修复，正在重试" in status_texts
    assert any(event["event"] == "chunk_reset" for event in events)
    assert complete["data"]["draft_skill"]["name"] == "商品比价"


def test_skill_distiller_stream_uses_staged_generation_after_repair_failure(monkeypatch) -> None:
    """验证模型修复失败后改用分段生成，逐节点扩写后再做最终评审。"""
    def fake_stream(self, _system_prompt: str, _payload: dict):  # noqa: ANN001
        """伪造流式输出：返回被截断的 JSON，迫使流程进入修复阶段。"""
        assert self.max_output_tokens == 8192
        yield '{"draft_skill": {"name": "截断"'

    def fake_text(self, _system_prompt: str, payload: dict):  # noqa: ANN001
        """伪造修复与分段调用：修复失败，分段生成逐节点扩写并完成评审。"""
        assert self.max_output_tokens == 8192
        if payload.get("reflection_round"):
            return _reflection_passes_json()
        if "repair_instruction" in payload:
            return "still invalid"
        mode = payload.get("generation_mode")
        if mode == "outline_only":
            return json.dumps(
                {
                    "draft_skill": {
                        "skill_id": "skill_compare_price",
                        "name": "商品比价",
                        "version": "1.0.0",
                        "business_domain": "ecommerce",
                        "description": "比较两个商品价格。",
                        "trigger_intents": ["compare_price"],
                        "user_utterance_examples": ["比较 A 和 B"],
                        "goal": ["收集两个商品名称", "反馈比价结果"],
                        "required_info": ["product_name_1", "product_name_2"],
                        "slot_filling_policy": {
                            "enabled": True,
                            "multi_slot_per_turn": True,
                            "extract_scope": "all_skill_expected_user_info",
                            "skip_satisfied_steps": True,
                            "target_info": ["product_name_1", "product_name_2"],
                        },
                        "response_rules": ["不要编造价格。"],
                        "nodes": [
                            {
                                "node_id": "collect_names",
                                "name": "收集商品名称",
                                "instruction": "收集两个商品名称。",
                                "expected_user_info": ["product_name_1", "product_name_2"],
                                "allowed_actions": ["ask_user"],
                            },
                            {
                                "node_id": "reply_result",
                                "name": "反馈结果",
                                "instruction": "反馈结果。",
                                "expected_user_info": [],
                                "allowed_actions": ["answer_user"],
                            },
                        ],
                        "interruption_policy": {},
                    },
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        if mode == "expand_node":
            node = dict(payload["target_node"])
            node["instruction"] = f"扩写步骤 {payload['target_node_index'] + 1}，支持自适应推进。"
            return json.dumps({"node": node, "warnings": [], "tool_suggestions": []}, ensure_ascii=False)
        if mode == "final_review":
            return json.dumps({"draft_skill": payload["current_draft"], "warnings": []}, ensure_ascii=False)
        raise AssertionError(f"unexpected payload: {payload}")

    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text_stream", fake_stream)
    monkeypatch.setattr("app.skills.skill_distiller.LLMClient.generate_text", fake_text)

    events = list(
        SkillDistiller().stream_text(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="商品比价",
                raw_content="用户提供两个商品的名称，系统根据商品价格进行比价",
            ),
            _model_config(),
        )
    )
    status_texts = [event["data"]["text"] for event in events if event["event"] == "status"]
    complete = next(event for event in events if event["event"] == "complete")
    instructions = [node["instruction"] for node in complete["data"]["draft_skill"]["nodes"]]

    assert "模型修复失败，改用分段生成" in status_texts
    assert any("扩写步骤 1" in instruction for instruction in instructions)
    assert any("扩写步骤 2" in instruction for instruction in instructions)


def test_distill_skill_uses_selected_model_config(monkeypatch) -> None:
    """验证蒸馏接口使用请求指定的模型配置，而不是租户默认模型。"""
    captured: dict[str, str] = {}

    def fake_distill(self, request, model_config):  # noqa: ANN001
        """替换真实蒸馏调用，记录实际使用的模型 ID 以便断言。"""
        captured["model_id"] = model_config.id
        return SkillDistillResponse(draft_skill=_skill_card())

    monkeypatch.setattr("app.api.skills.SkillDistiller.distill", fake_distill)

    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            ModelConfig(
                id="model_default",
                tenant_id="tenant_demo",
                name="默认模型",
                api_key_encrypted="",
                model="default-model",
                is_default=True,
            )
        )
        db.add(
            ModelConfig(
                id="model_selected",
                tenant_id="tenant_demo",
                name="选择模型",
                api_key_encrypted="",
                model="selected-model",
            )
        )
        db.commit()

        distill_skill(
            SkillDistillRequest(
                tenant_id="tenant_demo",
                title="测试 SOP",
                raw_content="用户说 hello 时回复 hi",
                model_config_id="model_selected",
            ),
            db=db,
            current_user=_admin_user(),
        )

    assert captured["model_id"] == "model_selected"


def test_extract_uploaded_skill_file_reads_docx_text() -> None:
    """验证上传的 docx 技能文件能被解析出正文文本。"""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>标题：商品比价</w:t></w:r></w:p>
                <w:p><w:r><w:t>用户提供两个商品名称后进行比价。</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        )

    text = _extract_uploaded_skill_file("skill.docx", buffer.getvalue())

    assert "标题：商品比价" in text
    assert "用户提供两个商品名称后进行比价。" in text


def _skill_card() -> SkillCard:
    """构造覆盖两个节点、一条连线的基准技能卡，供各改写用例复用与对比。"""
    return SkillCard(
        skill_id="purchase",
        name="购买商品",
        version="1.0.0",
        business_domain="commerce",
        description="购买流程",
        trigger_intents=["购买"],
        user_utterance_examples=["我要买 A1"],
        goal=["完成下单"],
        required_info=["product_id"],
        nodes=[
            {
                "node_id": "collect_info",
                "name": "收集信息",
                "instruction": "收集商品信息",
                "expected_user_info": ["product_id"],
                "allowed_actions": ["ask_user", "continue_flow"],
            },
            {
                "node_id": "reply_result",
                "name": "反馈结果",
                "instruction": "反馈订单结果",
                "expected_user_info": [],
                "allowed_actions": ["answer_user"],
            },
        ],
        edges=[
            {
                "source_node_id": "collect_info",
                "next_node_id": "reply_result",
                "priority": 0,
                "label": "默认推进",
            }
        ],
        start_node_id="collect_info",
        terminal_node_ids=["reply_result"],
        interruption_policy={},
        response_rules=[],
    )


def _available_sop(
    skill_id: str,
    *,
    nested_sop_ids: list[str] | None = None,
) -> dict[str, object]:
    """构造可用 SOP 目录项，可带上子 SOP 列表以模拟嵌套关系。"""
    child_ids = nested_sop_ids or []
    return {
        "skill_id": skill_id,
        "name": skill_id,
        "capability_scope": "sop_specific",
        "status": "published",
        "nested_sop_ids": child_ids,
        "selectable": True,
        "content": {
            "capability_scope": "sop_specific",
            "nodes": [
                {
                    "type": "subflow",
                    "sub_sop_id": child_id,
                }
                for child_id in child_ids
            ],
        },
    }


def _reflection_passes_json() -> str:
    """构造反思全部通过的 JSON 响应，供蒸馏与改写流程的 mock 使用。"""
    return json.dumps(
        {
            "passed": True,
            "summary": "通过",
            "rubric_results": [
                {"name": "source_alignment", "passed": True, "finding": "", "origin": "generated_skill"},
                {"name": "closed_loop", "passed": True, "finding": "", "origin": "generated_skill"},
                {"name": "adaptive_progression", "passed": True, "finding": "", "origin": "generated_skill"},
                {"name": "tool_grounding", "passed": True, "finding": "", "origin": "generated_skill"},
                {"name": "side_effect_confirmation", "passed": True, "finding": "", "origin": "generated_skill"},
                {"name": "interruption_and_recovery", "passed": True, "finding": "", "origin": "generated_skill"},
            ],
            "warnings": [],
            "source_warnings": [],
            "tool_mentions": [],
        },
        ensure_ascii=False,
    )


def _model_config() -> ModelConfig:
    """构造已加密密钥的模型配置，供流式改写与蒸馏调用。"""
    return ModelConfig(
        tenant_id="tenant_demo",
        name="mock",
        api_key_encrypted=encrypt_secret("mock"),
        model="mock-model",
    )


def _test_session():
    """创建内存 SQLite 会话，隔离各用例的数据库状态。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _handoff_skill_card(
    assignee_user_id: str | None,
    assignee_notify_channel: str | None = None,
) -> SkillCard:
    """把基准技能卡的第二个节点改造成转接节点，便于校验转接待办人。"""
    card = _skill_card().model_copy(deep=True)
    card.nodes[1].type = "handoff"
    card.nodes[1].assignee_user_id = assignee_user_id
    card.nodes[1].assignee_notify_channel = assignee_notify_channel
    return card


def _scope_binding(
    *,
    binding_id: str = "binding_feishu",
    channel: str = "feishu",
    scope: str = "",
) -> ChannelBinding:
    """构造启用中的渠道绑定，用于验证渠道身份的可达性判定。"""
    return ChannelBinding(
        id=binding_id,
        tenant_id="tenant_demo",
        agent_id="agent_1",
        channel=channel,
        status="active",
        identity_scope_key=scope,
    )


def test_validate_handoff_assignees_accepts_internal_and_bound_channel_variants() -> None:
    """验证站内通知与已绑定渠道的转接人校验都能通过。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        # 渠道可达性要求:active 员工绑定 + 该 binding scope 下的非群聊身份
        db.add(_scope_binding(scope="app:cli_a:tenant:t_a"))
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="feishu",
                external_account_scope="app:cli_a:tenant:t_a",
                external_user_id="ou_owner",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        _validate_handoff_assignees(db, _handoff_skill_card("user_owner"), "tenant_demo")
        _validate_handoff_assignees(db, _handoff_skill_card("user_owner", "web"), "tenant_demo")
        _validate_handoff_assignees(db, _handoff_skill_card("user_owner", "feishu"), "tenant_demo")


def test_validate_handoff_assignees_rejects_unbound_channel_variant() -> None:
    """验证只有群聊虚拟身份时不算有效渠道绑定，渠道转接会被拒绝。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        db.add(_scope_binding(scope="app:cli_a:tenant:t_a"))
        # 仅群聊虚拟身份不算有效渠道绑定
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="feishu",
                external_account_scope="app:cli_a:tenant:t_a",
                external_user_id="group:chat_1",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _validate_handoff_assignees(
                db, _handoff_skill_card("user_owner", "feishu"), "tenant_demo"
            )
        assert exc_info.value.status_code == 400


def test_validate_handoff_assignees_scope_level_reachability() -> None:
    """scope 级可达性:身份挂在其他企业 binding 的 scope 下时不可达,应拒绝。

    用户可能绑定过另一个飞书企业(不同 binding scope),但租户内当前
    active 绑定的作用域无法触达该身份,渠道转接会静默失败,校验必须拦下。
    """
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        # 租户内唯一 active 飞书绑定,scope 是本企业 A
        db.add(_scope_binding(scope="app:cli_a:tenant:t_a"))
        # 但用户身份绑定在另一个企业 B 的 scope 下(该企业无 active 绑定)
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="feishu",
                external_account_scope="app:cli_b:tenant:t_b",
                external_user_id="ou_other_org",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _validate_handoff_assignees(
                db, _handoff_skill_card("user_owner", "feishu"), "tenant_demo"
            )
        assert exc_info.value.status_code == 400
        assert "未绑定渠道身份" in exc_info.value.detail

        # 多绑定任一可达即可:再挂一个企业 B 的 active 绑定后通过
        db.add(_scope_binding(binding_id="binding_feishu_b", scope="app:cli_b:tenant:t_b"))
        db.commit()
        _validate_handoff_assignees(db, _handoff_skill_card("user_owner", "feishu"), "tenant_demo")


def test_validate_handoff_assignees_ignores_team_and_inactive_bindings() -> None:
    """团队绑定与停用绑定的 scope 不参与可达性计算。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        team_binding = _scope_binding(binding_id="binding_team", scope="app:cli_team:tenant:t")
        team_binding.team_id = "team_1"
        disabled = _scope_binding(binding_id="binding_disabled", scope="app:cli_a:tenant:t_a")
        disabled.status = "disabled"
        db.add(team_binding)
        db.add(disabled)
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="feishu",
                external_account_scope="app:cli_team:tenant:t",
                external_user_id="ou_team",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _validate_handoff_assignees(
                db, _handoff_skill_card("user_owner", "feishu"), "tenant_demo"
            )
        assert exc_info.value.status_code == 400


def test_validate_handoff_assignees_accepts_wecom_scope_reachability() -> None:
    """渠道已通用化:企微渠道按同样 scope 规则校验可达。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        db.add(_scope_binding(binding_id="binding_wecom", channel="wecom", scope="corp_1"))
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="corp_1",
                external_user_id="staff_owner",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        _validate_handoff_assignees(db, _handoff_skill_card("user_owner", "wecom"), "tenant_demo")


def test_validate_handoff_assignees_rejects_channel_customer() -> None:
    """验证渠道客户账号不能作为转接待办人。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            User(
                id="user_channel",
                tenant_id="tenant_demo",
                username="feishu_customer",
                source="feishu",
                password_hash="x",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _validate_handoff_assignees(db, _handoff_skill_card("user_channel"), "tenant_demo")
        assert exc_info.value.status_code == 400


def test_validate_handoff_assignees_rejects_unsupported_private_message_channel() -> None:
    """钉钉/微信不支持主动私聊通知:即使身份已绑定也拒绝。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x"))
        db.add(_scope_binding(binding_id="binding_ding", channel="dingtalk", scope="ding_scope"))
        db.add(
            ChannelIdentity(
                tenant_id="tenant_demo",
                channel="dingtalk",
                external_account_scope="ding_scope",
                external_user_id="staff_owner",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _validate_handoff_assignees(
                db, _handoff_skill_card("user_owner", "dingtalk"), "tenant_demo"
            )
        assert exc_info.value.status_code == 400
        assert "暂不支持私聊通知" in exc_info.value.detail
