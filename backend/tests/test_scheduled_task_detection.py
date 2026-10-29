from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, Tenant
from app.scheduled_tasks import service as scheduled_service


def test_model_failure_does_not_create_keyword_based_draft(monkeypatch) -> None:
    """验证模型识别失败时不会退化为按关键词创建定时任务草稿。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
        db.commit()
        monkeypatch.setattr(scheduled_service, "_detect_with_llm", lambda *args, **kwargs: None)

        draft = scheduled_service.detect_scheduled_task_draft(
            db,
            "tenant_demo",
            "agent_demo",
            "user_demo",
            "每周五18点复盘差评对话",
            "session_demo",
        )

        assert draft is None


def test_llm_draft_is_used_without_confidence_fallback(monkeypatch) -> None:
    """验证模型草稿即使置信度很低也直接采用，不触发置信度兜底逻辑。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
        db.commit()

        monkeypatch.setattr(
            scheduled_service,
            "_detect_with_llm",
            lambda *args, **kwargs: scheduled_service._LLMScheduledTaskDraft(
                should_create=True,
                title="模型解析的一次性任务",
                prompt="到点后检查 A1 价格并按条件购买",
                schedule_type="once",
                schedule={"run_at": "2026-06-22T14:10:00+08:00"},
                timezone="Asia/Shanghai",
                confidence=0.1,
                reason="模型已给出完整结构",
            ),
        )
        draft = scheduled_service.detect_scheduled_task_draft(
            db,
            "tenant_demo",
            "agent_demo",
            "user_demo",
            "下午2点10分帮我看下A1价格",
            "session_demo",
        )

        assert draft is not None
        assert draft.schedule_type == "once"
        assert draft.schedule["run_at"] == "2026-06-22T14:10:00+08:00"
        assert draft.confidence == 0.1


def test_llm_draft_defaults_to_requested_timezone(monkeypatch) -> None:
    """验证模型未给出时区时，草稿时区回退为调用方请求的时区。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
        db.commit()

        monkeypatch.setattr(
            scheduled_service,
            "_detect_with_llm",
            lambda *args, **kwargs: scheduled_service._LLMScheduledTaskDraft(
                should_create=True,
                title="模型解析的周期任务",
                prompt="每天提醒喝水",
                schedule_type="daily",
                schedule={"time": "09:00"},
                confidence=0.8,
                reason="模型已给出完整结构",
            ),
        )

        draft = scheduled_service.detect_scheduled_task_draft(
            db,
            "tenant_demo",
            "agent_demo",
            "user_demo",
            "每天9点提醒我喝水",
            "session_demo",
            "America/Los_Angeles",
        )

        assert draft is not None
        assert draft.timezone == "America/Los_Angeles"
        assert draft.schedule == {"time": "09:00"}


def test_llm_negative_result_does_not_fallback(monkeypatch) -> None:
    """验证模型判定不该创建任务时直接返回 None，不回退关键词解析。"""
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
        db.commit()

        monkeypatch.setattr(
            scheduled_service,
            "_detect_with_llm",
            lambda *args, **kwargs: scheduled_service._LLMScheduledTaskDraft(
                should_create=False,
                confidence=0.9,
                reason="不是自动任务",
            ),
        )
        draft = scheduled_service.detect_scheduled_task_draft(
            db,
            "tenant_demo",
            "agent_demo",
            "user_demo",
            "只是普通问题",
            "session_demo",
        )

        assert draft is None


def _test_session() -> Session:
    """创建内存 SQLite 会话并建表，让每个用例拥有彼此隔离的数据。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)
