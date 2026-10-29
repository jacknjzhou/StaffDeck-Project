from app.core.conversation_projection import ConversationProjection
from app.db.models import ChatSession, Skill
from app.session.session_schema import StepAgentResult


def test_dedupe_citations_preserves_first_four_and_relabels() -> None:
    """验证引用去重保留前四条、忽略标题大小写与空白差异，并重新编号。"""
    citations = [
        {"title": " Alpha "},
        {"title": "alpha"},
        {"section_path": "Beta"},
        {"summary": "Gamma"},
        {"excerpt": "Delta"},
        {"title": "Epsilon"},
    ]

    assert ConversationProjection.dedupe_knowledge_citations(citations) == [
        {"title": " Alpha ", "label": "[1]"},
        {"section_path": "Beta", "label": "[2]"},
        {"summary": "Gamma", "label": "[3]"},
        {"excerpt": "Delta", "label": "[4]"},
    ]


def test_skill_state_hides_unavailable_active_and_pending_skills() -> None:
    """验证技能状态投影会隐藏不可见技能，只保留可见技能及其待办步骤。"""
    visible = Skill(
        tenant_id="tenant_test",
        skill_id="visible",
        version="1.0.0",
        name="Visible",
    )
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_test",
        active_skill_id="hidden",
        active_step_id="step_hidden",
        pending_tasks_json=[
            {"target_skill_id": "visible", "target_step_id": "step_visible"},
            {"target_skill_id": "hidden"},
        ],
    )

    assert ConversationProjection.skill_state_payload(session, [visible]) == {
        "activeSkillId": None,
        "activeStepId": None,
        "currentSkills": [
            {
                "skillId": "visible",
                "name": "Visible",
                "stepId": "step_visible",
                "state": "pending",
            }
        ],
    }


def test_reply_citation_and_title_projection_preserves_legacy_rules() -> None:
    """验证会话标题兜底、越界引用标签归一化与尾部参考资料剥离保持旧行为。"""
    assert ConversationProjection.fallback_session_title("  请问 A？ ") == "请问 A"
    assert (
        ConversationProjection.normalize_reply_citation_labels("有效[1] 无效[9]", [{}, {}])
        == "有效[1] 无效[2]"
    )
    assert (
        ConversationProjection.strip_trailing_citation_summary("正文\n参考资料：[1] [2]") == "正文"
    )


def test_assistant_metadata_uses_injected_citation_deduper() -> None:
    """验证助手元数据生成时确实调用了注入的引用去重器，并采用其返回值。"""
    calls: list[list[dict[str, object]]] = []
    metadata = ConversationProjection.assistant_message_metadata(
        StepAgentResult(
            knowledge_results=[
                {
                    "query": {"query": "Q"},
                    "chunks": [{"title": "T", "content": "C"}],
                }
            ]
        ),
        citation_deduper=lambda citations: (
            calls.append(citations) or [{"title": "injected", "label": "[1]"}]
        ),
    )

    assert calls
    assert metadata["knowledge_citations"] == [{"title": "injected", "label": "[1]"}]


def test_assistant_metadata_uses_latest_knowledge_result_window() -> None:
    """验证助手元数据只采用最近一次知识检索结果作为问题与引用来源。"""
    metadata = ConversationProjection.assistant_message_metadata(
        StepAgentResult(
            knowledge_results=[
                {
                    "query": {"query": "旧问题"},
                    "chunks": [{"id": "old", "content": "旧答案", "source_ref": "old.md"}],
                },
                {
                    "query": {"query": "新问题"},
                    "chunks": [{"id": "new", "content": "新答案", "source_ref": "new.md"}],
                },
            ]
        )
    )

    assert metadata["knowledge_query"] == {"query": "新问题"}
    assert metadata["knowledge_citations"][0]["source_path"] == "new.md"
