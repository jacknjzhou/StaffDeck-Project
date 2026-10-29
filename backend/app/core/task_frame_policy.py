from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.models import ChatSession, Skill
from app.session.session_schema import PendingTask, RouterDecision, StepAgentResult
from app.tools.tool_schema import ToolResult


@dataclass
class QueuedTaskContinuation:
    """排队任务续跑时要发出的一轮回复及其执行现场。"""

    reply: str
    task_results: list[dict[str, object]]
    active_skill: Skill | None
    router_decision: RouterDecision
    step_result: StepAgentResult
    tool_result: ToolResult | None


class TaskFramePolicy:
    """待办 TaskFrame 的读写规则：续跑组装、frame 查找与决策还原。"""

    @staticmethod
    def queued_continuation(
        replies: list[str],
        task_results: list[dict[str, object]],
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
    ) -> QueuedTaskContinuation | None:
        """本轮有回复或任务结果时组装续跑载荷，两者都为空则返回 None。"""
        if not replies and not task_results:
            return None
        return QueuedTaskContinuation(
            reply="\n\n".join(replies).strip(),
            task_results=task_results,
            active_skill=active_skill,
            router_decision=router_decision,
            step_result=step_result,
            tool_result=tool_result,
        )

    @staticmethod
    def merge_reply_segment(
        replies: list[str], segment: str
    ) -> tuple[list[str], bool]:
        """把一段回复并入回复列表并返回 (replies, False)；纯空白段直接忽略。"""
        clean_segment = str(segment or "").strip()
        if not clean_segment:
            return replies, False
        return [*replies, clean_segment], False

    @staticmethod
    def find(chat_session: ChatSession, task_id: str) -> dict[str, Any] | None:
        """按 task_id 在会话的 pending_tasks_json 中取出原始 frame，找不到返回 None。"""
        for frame in chat_session.pending_tasks_json or []:
            if isinstance(frame, dict) and str(frame.get("task_id") or "") == str(task_id):
                return frame
        return None

    @classmethod
    def decision_from_pending(
        cls,
        chat_session: ChatSession,
        task_id: str,
        order_reason: str | None = None,
    ) -> RouterDecision | None:
        """以会话中定位到的待办 frame 生成 switch_to_pending 决策。"""
        frame = cls.find(chat_session, task_id)
        if not frame:
            return None
        return cls.decision_from_frame(frame, task_id, order_reason)

    @staticmethod
    def decision_from_frame(
        frame: dict[str, Any],
        task_id: str,
        order_reason: str | None = None,
    ) -> RouterDecision | None:
        """把持久化的待办 frame 还原成 switch_to_pending 决策。

        skill 标识兼容 skill_id 与 target_skill_id，槽位兼容 slots 与 slot_hints；
        缺少 skill 标识时返回 None。
        """
        skill_id = frame.get("skill_id") or frame.get("target_skill_id")
        if not skill_id:
            return None
        slot_hints = {}
        if isinstance(frame.get("slots"), dict):
            slot_hints = dict(frame["slots"])
        elif isinstance(frame.get("slot_hints"), dict):
            slot_hints = dict(frame["slot_hints"])
        return RouterDecision(
            decision="switch_to_pending",
            selected_task_id=str(task_id),
            target_skill_id=str(skill_id),
            target_step_id=frame.get("step_id") or frame.get("target_step_id"),
            confidence=float(frame.get("confidence") or 0.0),
            user_intent=frame.get("intent_summary") or frame.get("user_intent"),
            reason=order_reason or frame.get("reason"),
            source_message=frame.get("source_message"),
            slot_hints=slot_hints,
        )

    @staticmethod
    def turn_followup_frames(router_decision: RouterDecision) -> list[PendingTask]:
        """列出本轮 task_frames 中仍需跟进的帧；首帧与主决策目标一致时跳过它。"""
        frames = list(router_decision.task_frames or [])
        if not frames:
            return []
        first = frames[0]
        return frames[1:] if first.target_skill_id == router_decision.target_skill_id else frames

    @staticmethod
    def decision_from_turn_frame(frame: PendingTask) -> RouterDecision:
        """把本轮单个 PendingTask 转成 start_new_task 决策，供逐帧执行使用。"""
        return RouterDecision(
            decision="start_new_task",
            target_skill_id=frame.target_skill_id,
            target_step_id=frame.target_step_id,
            confidence=frame.confidence,
            user_intent=frame.user_intent,
            reason=frame.reason or "按 Router 本轮 task_frames 顺序继续执行。",
            source_message=frame.source_message,
            slot_hints=dict(frame.slot_hints or {}),
            task_frames=[frame],
        )

    @staticmethod
    def next_pending_task_id(chat_session: ChatSession) -> str | None:
        """返回会话中第一个仍为 pending 且带 skill 标识的任务 id，没有则返回 None。"""
        for frame in chat_session.pending_tasks_json or []:
            if not isinstance(frame, dict):
                continue
            if str(frame.get("status") or "pending") != "pending":
                continue
            if not (frame.get("skill_id") or frame.get("target_skill_id")):
                continue
            task_id = str(frame.get("task_id") or "").strip()
            if task_id:
                return task_id
        return None
