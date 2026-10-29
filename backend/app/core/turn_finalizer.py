from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from app.db.models import ChatSession, Skill
from app.session.session_schema import RouterDecision, StepAgentResult
from app.tools.tool_schema import ToolResult

ExecutionFinalizeState = Literal["handoff", "completed", "continued"]


class TurnFinalizer:
    """在 Harness v2 回复结束后统一落地终止决策，并保持事件写入顺序一致。"""

    @staticmethod
    def finalize(
        tenant_id: str,
        chat_session: ChatSession,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        *,
        current_step_allows_handoff: Callable[[Skill | None, str | None], bool],
        route_to_handoff_node: Callable[[ChatSession, Skill | None], bool],
        create_handoff: Callable[[str, ChatSession, Skill | None, StepAgentResult], Any],
        record_event: Callable[[str, str, str, dict[str, Any]], None],
        should_complete: Callable[
            [Skill | None, ChatSession, StepAgentResult, ToolResult | None], bool
        ],
        complete_skill: Callable[[str, ChatSession, Skill | None, str], None],
    ) -> ExecutionFinalizeState:
        """按 v2 规则决定本轮以转人工、完结技能还是续接状态收尾。

        转人工请求只有当前步骤声明允许时才创建 handoff，否则补记
        human_handoff_ignored 事件后继续判定是否完结；外部副作用全部经注入的回调完成。
        """
        requested_handoff = router_decision.decision == "handoff_human" or step_result.handoff
        if requested_handoff:
            if not current_step_allows_handoff(active_skill, chat_session.active_step_id):
                route_to_handoff_node(chat_session, active_skill)
            if current_step_allows_handoff(active_skill, chat_session.active_step_id):
                create_handoff(tenant_id, chat_session, active_skill, step_result)
                return "handoff"
            record_event(
                tenant_id,
                chat_session.id,
                "human_handoff_ignored",
                {
                    "reason": "current_step_does_not_declare_handoff",
                    "active_skill_id": chat_session.active_skill_id,
                    "active_step_id": chat_session.active_step_id,
                    "router_decision": router_decision.decision,
                    "step_handoff": step_result.handoff,
                },
            )
        if should_complete(active_skill, chat_session, step_result, tool_result):
            complete_skill(tenant_id, chat_session, active_skill, "step_completed")
            return "completed"
        return "continued"
