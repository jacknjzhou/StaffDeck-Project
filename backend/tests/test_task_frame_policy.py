from app.core.task_frame_policy import TaskFramePolicy
from app.db.models import ChatSession
from app.session.session_schema import PendingTask, RouterDecision


def test_pending_decision_prefers_persisted_slots_and_identity() -> None:
    """验证从持久化待办恢复决策时优先采用已存槽位与任务标识。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_test",
        pending_tasks_json=[
            {
                "task_id": "task-1",
                "skill_id": "purchase",
                "step_id": "collect",
                "slots": {"product_id": "A1"},
                "slot_hints": {"product_id": "ignored"},
                "intent_summary": "购买 A1",
            }
        ],
    )

    decision = TaskFramePolicy.decision_from_pending(session, "task-1")

    assert decision is not None
    assert decision.selected_task_id == "task-1"
    assert decision.target_skill_id == "purchase"
    assert decision.slot_hints == {"product_id": "A1"}


def test_turn_followup_frames_preserves_router_order() -> None:
    """验证回调帧按路由顺序保留，且单个帧可直接构造出决策。"""
    first = PendingTask(task_id="task-1", target_skill_id="purchase")
    second = PendingTask(task_id="task-2", target_skill_id="refund")
    decision = RouterDecision(
        decision="start_new_task",
        target_skill_id="purchase",
        task_frames=[first, second],
    )

    assert TaskFramePolicy.turn_followup_frames(decision) == [second]
    assert TaskFramePolicy.decision_from_turn_frame(second).task_frames == [second]


def test_next_pending_task_skips_non_pending_and_invalid_frames() -> None:
    """验证取下一个待办时跳过已完成与缺少技能标识的无效帧。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_test",
        pending_tasks_json=[
            {"task_id": "done", "skill_id": "a", "status": "completed"},
            {"task_id": "missing-skill"},
            {"task_id": "next", "target_skill_id": "b"},
        ],
    )

    assert TaskFramePolicy.next_pending_task_id(session) == "next"


def test_reply_merge_and_empty_continuation_preserve_legacy_shape() -> None:
    """验证回复片段合并会去除首尾空白，纯空白续写不产生新片段。"""
    assert TaskFramePolicy.merge_reply_segment(["one"], " two ") == (
        ["one", "two"],
        False,
    )
    assert TaskFramePolicy.merge_reply_segment(["one"], " ") == (["one"], False)
