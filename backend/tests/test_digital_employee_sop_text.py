from __future__ import annotations

from app.digital_employees.sop_text import effective_hint, render_sop_content


def _card(**overrides):
    card = {
        "skill_id": "expense_over_limit_approval",
        "name": "报销超标审批",
        "description": "金额超标准时走审批",
        "goal": ["判断是否超标", "超标时升级审批"],
        "required_info": ["报销金额", "费用类型"],
        "response_rules": ["必须给出制度条款"],
        "nodes": [
            {
                "node_id": "check_amount",
                "type": "collect_info",
                "name": "确认金额",
                "instruction": "收取报销金额与费用类型。",
                "expected_user_info": ["报销金额", "费用类型"],
            },
            {
                "node_id": "escalate",
                "type": "handoff",
                "name": "升级审批",
                "instruction": "把超标单据交财务负责人。",
            },
        ],
        "edges": [
            {
                "source_node_id": "check_amount",
                "next_node_id": "escalate",
                "condition": "amount_over_limit",
                "label": "超标",
            }
        ],
        "start_node_id": "check_amount",
        "terminal_node_ids": ["escalate"],
    }
    card.update(overrides)
    return card


def test_render_includes_goal_steps_and_rules():
    text = render_sop_content(_card())
    assert "判断是否超标" in text
    assert "1. 确认金额" in text
    assert "收取报销金额与费用类型。" in text
    assert "必须给出制度条款" in text
    assert "超标" in text


def test_render_survives_legacy_card_without_optional_fields():
    text = render_sop_content(
        {
            "skill_id": "legacy",
            "name": "旧 SOP",
            "nodes": [{"node_id": "only", "name": "唯一节点"}],
            "start_node_id": "only",
            "terminal_node_ids": ["only"],
        }
    )
    assert "旧 SOP" not in text  # 标题由 payload 层单独给，正文不重复
    assert "1. 唯一节点" in text


def test_render_never_raises_on_garbage():
    assert render_sop_content({}) == "（SOP 结构缺失，无法渲染）"
    assert render_sop_content({"nodes": "not-a-list"}) == "（SOP 结构缺失，无法渲染）"


def test_effective_hint_prefers_explicit_override():
    assert effective_hint("hybrid", "remote") == "remote"
    assert effective_hint("hybrid", "local") == "local"
    assert effective_hint("local", None) == "local"
    assert effective_hint("remote", None) == "remote"
    # hybrid 未标注时默认本地：远端执行是稀缺资源，默认不该外派
    assert effective_hint("hybrid", None) == "local"
    # 未知值降级为本地，避免坏配置把员工整体锁死
    assert effective_hint("hybrid", "bogus") == "local"
    assert effective_hint("bogus", None) == "local"
