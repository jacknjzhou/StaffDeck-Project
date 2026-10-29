from app.core.skill_runtime import SkillRuntime
from app.db.models import ChatSession
from app.session.session_schema import PendingTask, RouterDecision


def test_start_new_task_replaces_active_context_without_implicit_stack():
    """验证开启新任务会替换活动技能与步骤并清空槽位，不产生隐藏技能栈。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        slots_json={"asset_id": "EQ-9"},
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="start_new_task",
            target_skill_id="visitor_badge",
            target_step_id="collect_visit_info",
        ),
    )

    assert session.active_skill_id == "visitor_badge"
    assert session.active_step_id == "collect_visit_info"
    assert session.slots_json == {}
    assert session.skill_stack_json == []


def test_complete_task_discards_obsolete_suspended_skill_state():
    """验证完成当前任务会清空活动技能、槽位与已挂起的技能帧。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        skill_stack_json=[
            {
                "skill_id": "visitor_badge",
                "step_id": "collect_visit_info",
                "slots": {"visitor_name": "hm"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(session, RouterDecision(decision="complete_task"))

    assert session.active_skill_id is None
    assert session.active_step_id is None
    assert session.slots_json == {}
    assert session.skill_stack_json == []


def test_start_new_task_discards_obsolete_skill_stack_frames():
    """验证开启新任务时旧技能栈帧被丢弃，不会残留重复任务。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
        skill_stack_json=[
            {
                "skill_id": "visitor_badge",
                "step_id": "collect_visit_info",
                "slots": {"visitor_name": "hm"},
            },
            {
                "skill_id": "repair_ticket",
                "step_id": "collect_repair_info",
                "slots": {"asset_id": "EQ-9"},
            },
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="start_new_task",
            target_skill_id="repair_ticket",
            target_step_id="collect_repair_info",
        ),
    )

    assert session.active_skill_id == "repair_ticket"
    assert session.active_step_id == "collect_repair_info"
    assert session.slots_json == {}
    assert session.skill_stack_json == []


def test_answer_only_preserves_active_task_without_creating_hidden_frames():
    """验证只回答时保留活动任务与步骤，且不生成隐藏的技能帧。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="repair_ticket",
        active_step_id="collect_repair_info",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="answer_only",
        ),
    )
    assert session.active_skill_id == "repair_ticket"
    assert session.active_step_id == "collect_repair_info"
    assert session.resume_after_answer_json is None
    assert session.skill_stack_json == []


def test_answer_only_does_not_switch_to_another_skill():
    """验证只回答不会因路由提示切换技能，原槽位与待恢复状态保持不变。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="purchase",
        active_step_id="collect_user_name",
        slots_json={"product_id": "A1"},
        summary="最近回复：请问姓名和数量",
        last_agent_question="请问姓名和数量？",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="answer_only",
            target_skill_id="price_compare",
            target_step_id="collect_products",
        ),
    )

    assert session.active_skill_id == "purchase"
    assert session.active_step_id == "collect_user_name"
    assert session.slots_json == {"product_id": "A1"}
    assert session.skill_stack_json == []
    assert session.resume_after_answer_json is None


def test_pending_tasks_are_queued_and_selected_explicitly_without_using_skill_stack():
    """验证待办任务入队不占用技能栈，只有显式选择时才切换并消费该任务。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="confirm_refund_order",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="continue_active",
            target_skill_id="refund",
            target_step_id="confirm_refund_order",
            pending_tasks=[
                {
                    "decision": "start_new_task",
                    "target_skill_id": "purchase",
                    "target_step_id": "collect_user_name",
                    "user_intent": "退款完成后购买 A3",
                    "source_message": "退了吧，退完我想买一个a3",
                    "slot_hints": {"product_id": "A3"},
                }
            ],
        ),
    )

    assert session.active_skill_id == "refund"
    assert session.skill_stack_json == []
    assert session.pending_tasks_json[0]["target_skill_id"] == "purchase"

    task_id = session.pending_tasks_json[0]["task_id"]
    runtime.apply_decision(
        session,
        RouterDecision(
            decision="switch_to_pending",
            selected_task_id=task_id,
            target_skill_id="purchase",
            target_step_id="collect_user_name",
        ),
    )

    assert session.active_skill_id == "purchase"
    assert session.slots_json == {"product_id": "A3"}
    assert session.pending_tasks_json == []


def test_continue_active_does_not_regress_existing_step_from_router_hint():
    """验证继续当前任务不会按路由提示回退步骤，已有槽位保持不变。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="meeting_room_book",
        active_step_id="confirm_booking",
        slots_json={"date": "2026-07-22", "employee_id": "123456"},
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="continue_active",
            target_skill_id="meeting_room_book",
            target_step_id="collect_info",
            user_intent="确认会议室预订",
        ),
    )

    assert session.active_skill_id == "meeting_room_book"
    assert session.active_step_id == "confirm_booking"
    assert session.slots_json == {"date": "2026-07-22", "employee_id": "123456"}


def test_start_new_task_clears_previous_awaiting_input():
    """验证开启新任务会清除上一次的等待输入状态与追问记录。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="meeting_room_book",
        active_step_id="confirm_booking",
        awaiting_input_json={
            "skill_id": "meeting_room_book",
            "step_id": "confirm_booking",
            "expected_fields": ["confirmation"],
            "question_summary": "请确认预订",
        },
        last_agent_question="请确认预订",
    )

    SkillRuntime().apply_decision(
        session,
        RouterDecision(
            decision="start_new_task",
            target_skill_id="purchase",
            target_step_id="collect_product",
        ),
    )

    assert session.active_skill_id == "purchase"
    assert session.active_step_id == "collect_product"
    assert session.awaiting_input_json is None
    assert session.last_agent_question is None


def test_runtime_never_persists_router_generated_message_content_slots():
    """验证运行时丢弃模型生成的 message_content 槽位，只保留业务字段。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="confirm_refund_order",
        slots_json={"message_content": "旧的模型改写", "order_id": "ORDER-1"},
        pending_tasks_json=[
            {
                "task_id": "task_purchase_a1",
                "decision": "start_new_task",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "slot_hints": {"message_content": "pending 改写", "product_id": "A1"},
            }
        ],
        skill_stack_json=[
            {
                "task_id": "task_purchase_a3",
                "skill_id": "purchase",
                "step_id": "collect_user_name",
                "slots": {"message_content": "stack 改写", "product_id": "A3"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="switch_to_pending",
            selected_task_id="task_purchase_a1",
            target_skill_id="purchase",
            target_step_id="collect_user_name",
            slot_hints={"message_content": "当前轮改写", "quantity": 1},
        ),
    )

    assert session.active_skill_id == "purchase"
    assert session.slots_json == {"product_id": "A1", "quantity": 1}
    assert session.skill_stack_json == []


def test_runtime_ignores_message_content_only_task_update():
    """验证只含 message_content 的任务更新被忽略，已有任务槽位不被覆盖。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        pending_tasks_json=[
            {
                "task_id": "task_purchase_a1",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "slots": {"product_id": "A1"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="update_pending",
            task_updates=[
                {
                    "task_id": "task_purchase_a1",
                    "slot_hints": {"message_content": "不要覆盖已有任务 slot"},
                }
            ],
        ),
    )

    assert session.pending_tasks_json[0]["slots"] == {"product_id": "A1"}


def test_pending_task_is_not_claimed_without_selected_task_id():
    """验证未显式选择任务 ID 时不认领待办任务，路由槽位仍并入当前任务。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="purchase",
        active_step_id="confirm_purchase",
        slots_json={"product_id": "A1", "quantity": 1},
        pending_tasks_json=[
            {
                "decision": "start_new_task",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "user_intent": "退款完成后购买 A1",
                "source_message": "退完再买 A1",
                "slot_hints": {"user_name": "hm", "product_id": "A1"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="continue_active",
            target_skill_id="purchase",
            target_step_id="confirm_purchase",
            slot_hints={"purchase_confirmed": True},
        ),
    )

    assert len(session.pending_tasks_json) == 1
    assert session.slots_json == {
        "product_id": "A1",
        "quantity": 1,
        "purchase_confirmed": True,
    }


def test_semantic_duplicate_pending_task_is_merged_on_append():
    """验证语义重复的待办任务在追加时合并进已有条目并保留原任务 ID。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        pending_tasks_json=[
            {
                "task_id": "pending_refund_001",
                "status": "pending",
                "target_skill_id": "after_sales_refund",
                "target_step_id": "confirm_refund_order",
                "slot_hints": {"order_id": "ORDER-1", "refund_type": "退款"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="create_pending",
            pending_tasks=[
                PendingTask(
                    task_id="preserved_refund_001",
                    target_skill_id="after_sales_refund",
                    target_step_id="collect_order_info",
                    user_intent="继续当前退款任务",
                    slot_hints={
                        "order_id": "ORDER-1",
                        "refund_type": "退款",
                        "refund_reason": "不需要了",
                    },
                ),
                PendingTask(
                    task_id="purchase_a3_001",
                    target_skill_id="purchase",
                    target_step_id="collect_user_name",
                    user_intent="退款后购买 A3",
                    slot_hints={"product_id": "A3", "quantity": 1},
                ),
            ],
        ),
    )

    assert [frame["task_id"] for frame in session.pending_tasks_json] == [
        "pending_refund_001",
        "purchase_a3_001",
    ]
    refund_frame = session.pending_tasks_json[0]
    assert refund_frame["target_step_id"] == "collect_order_info"
    assert refund_frame["slot_hints"] == {
        "order_id": "ORDER-1",
        "refund_type": "退款",
        "refund_reason": "不需要了",
    }


def test_complete_current_skill_drops_equivalent_pending_frames():
    """验证完成当前技能会丢弃与已完成任务等价的待办帧，其余继续保留。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="after_sales_refund",
        active_step_id="collect_refund_reason",
        slots_json={
            "order_id": "ORDER-1",
            "refund_type": "退款",
            "order_confirmed": True,
            "refund_reason": "不需要了",
        },
        awaiting_input_json={"task_id": "pending_refund_001"},
        pending_tasks_json=[
            {
                "task_id": "preserved_refund_001",
                "status": "pending",
                "target_skill_id": "after_sales_refund",
                "target_step_id": "collect_order_info",
                "slot_hints": {"order_id": "ORDER-1", "refund_type": "退款"},
            },
            {
                "task_id": "purchase_a3_001",
                "status": "pending",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "slot_hints": {"product_id": "A3", "quantity": 1},
            },
        ],
    )
    runtime = SkillRuntime()

    runtime.complete_current_skill(session)

    assert session.active_skill_id is None
    assert session.slots_json == {}
    assert [frame["task_id"] for frame in session.pending_tasks_json] == ["purchase_a3_001"]


def test_selected_pending_task_switch_does_not_suspend_completed_current_skill():
    """验证切换到已选待办任务时不把已完成的当前技能压入技能栈。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="final_reply",
        slots_json={"order_id": "ORDER-1", "refund_reason": "买贵了"},
        pending_tasks_json=[
            {
                "task_id": "task_purchase_a1",
                "decision": "start_new_task",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "user_intent": "退款完成后购买 A1",
                "source_message": "退完再买 A1",
                "slot_hints": {"product_id": "A1"},
            }
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="switch_to_pending",
            selected_task_id="task_purchase_a1",
            target_skill_id="purchase",
            target_step_id="collect_user_name",
            slot_hints={"quantity": 1},
        ),
    )

    assert session.active_skill_id == "purchase"
    assert session.active_step_id == "collect_user_name"
    assert session.slots_json == {"product_id": "A1", "quantity": 1}
    assert session.skill_stack_json == []
    assert session.pending_tasks_json == []


def test_ambiguous_same_skill_pending_tasks_are_not_claimed_by_target_only():
    """验证仅凭目标技能无法区分同技能待办任务时不认领任何一条。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id="refund",
        active_step_id="final_reply",
        pending_tasks_json=[
            {
                "decision": "start_new_task",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "slot_hints": {"product_id": "A1"},
            },
            {
                "decision": "start_new_task",
                "target_skill_id": "purchase",
                "target_step_id": "collect_user_name",
                "slot_hints": {"product_id": "A3"},
            },
        ],
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="start_new_task",
            target_skill_id="purchase",
            target_step_id="collect_user_name",
            slot_hints={"quantity": 1},
        ),
    )

    assert len(session.pending_tasks_json) == 2
    assert session.skill_stack_json == []


def test_continue_active_can_reattach_missing_active_skill():
    """验证活动技能缺失时继续当前任务可重新绑定该技能并沿用步骤。"""
    session = ChatSession(
        id="session_test",
        tenant_id="tenant_demo",
        active_skill_id=None,
        active_step_id="confirm_purchase",
    )
    runtime = SkillRuntime()

    runtime.apply_decision(
        session,
        RouterDecision(
            decision="continue_active",
            target_skill_id="skill_purchase_001",
            target_step_id="confirm_purchase",
            slot_hints={"product_id": "A3"},
        ),
    )

    assert session.active_skill_id == "skill_purchase_001"
    assert session.active_step_id == "confirm_purchase"
    assert session.slots_json == {"product_id": "A3"}
