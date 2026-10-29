from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import ChatSession, Message, MessageFeedback, ModelConfig, Tenant, User
from app.feedback.service import FeedbackAnalysisService, feedback_summary
from app.llm.client import LLMClient


def test_feedback_analysis_uses_model_bucket(monkeypatch) -> None:
    """验证存在默认模型时按模型返回的分桶结果写入分析字段。"""
    def fake_init(self, model_config):  # noqa: ANN001
        """替换 LLMClient 初始化，避免用例连接真实模型服务。"""
        return

    def fake_generate_json(self, system_prompt, payload):  # noqa: ANN001
        """断言分析请求携带差评与目标消息，并返回预设的分桶结果。"""
        assert payload["feedback"]["rating"] == "down"
        assert payload["target_message"]["content"] == "请稍候"
        return {
            "bucket": "skill_issue",
            "confidence": 0.82,
            "reason": "技能步骤停在请稍候，没有闭环。",
            "summary": "下单流程缺少完成回复。",
            "evidence": ["助手回复没有最终结果"],
            "suggested_action": "补充最终反馈步骤。",
        }

    monkeypatch.setattr(LLMClient, "__init__", fake_init)
    monkeypatch.setattr(LLMClient, "generate_json", fake_generate_json)

    with _test_session() as db:
        feedback = _seed_feedback(db, with_model=True)
        analyzed = FeedbackAnalysisService(db).analyze_feedback(feedback.id)

    assert analyzed is not None
    assert analyzed.analysis_status == "analyzed"
    assert analyzed.analysis_bucket == "skill_issue"
    assert analyzed.analysis_confidence == 0.82
    assert "下单流程" in (analyzed.analysis_summary or "")


def test_feedback_analysis_without_model_marks_needs_model() -> None:
    """验证租户缺少默认模型时把反馈标记为待模型分析状态。"""
    with _test_session() as db:
        feedback = _seed_feedback(db, with_model=False)
        analyzed = FeedbackAnalysisService(db).analyze_feedback(feedback.id)

    assert analyzed is not None
    assert analyzed.analysis_status == "needs_model"
    assert analyzed.analysis_bucket == "needs_model_analysis"


def test_feedback_summary_buckets_downvotes() -> None:
    """验证反馈汇总按差评分桶计数并生成中文摘要。"""
    rows = [
        MessageFeedback(
            tenant_id="tenant_demo",
            session_id="session_1",
            message_id="msg_1",
            user_id="user_demo",
            rating="down",
            analysis_bucket="model_issue",
            analysis_summary="回复理解错了。",
        ),
        MessageFeedback(
            tenant_id="tenant_demo",
            session_id="session_2",
            message_id="msg_2",
            user_id="user_demo",
            rating="down",
            analysis_bucket="model_issue",
        ),
        MessageFeedback(
            tenant_id="tenant_demo",
            session_id="session_3",
            message_id="msg_3",
            user_id="user_demo",
            rating="up",
            analysis_bucket="positive_or_resolved",
        ),
    ]

    summary = feedback_summary(rows)

    assert summary["down_count"] == 2
    assert summary["up_count"] == 1
    assert summary["bucket_counts"][0]["bucket"] == "model_issue"
    assert summary["bucket_counts"][0]["count"] == 2
    assert "模型问题" in summary["summary"]


def _seed_feedback(db: Session, with_model: bool) -> MessageFeedback:
    """写入租户、用户、会话、消息和一条差评反馈，并按需补建默认模型。"""
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="demo",
        display_name="Demo",
        password_hash="hash",
    )
    db.add(user)
    db.add(ChatSession(id="session_1", tenant_id="tenant_demo", user_id=user.id, title="测试会话"))
    db.add(Message(id="msg_user", tenant_id="tenant_demo", session_id="session_1", role="user", content="我要买东西"))
    db.add(Message(id="msg_assistant", tenant_id="tenant_demo", session_id="session_1", role="assistant", content="请稍候"))
    if with_model:
        db.add(
            ModelConfig(
                tenant_id="tenant_demo",
                name="demo",
                api_key_encrypted="mock",
                model="mock",
                is_default=True,
                enabled=True,
            )
        )
    feedback = MessageFeedback(
        tenant_id="tenant_demo",
        session_id="session_1",
        message_id="msg_assistant",
        user_id=user.id,
        rating="down",
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


def _test_session() -> Session:
    """创建内存 SQLite 会话并建表，为分析用例提供独立的数据库环境。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
