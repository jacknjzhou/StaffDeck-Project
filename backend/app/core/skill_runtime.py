from __future__ import annotations

from typing import Any

from app.db.models import ChatSession, new_id, utc_now
from app.session.session_schema import PendingTask, RouterDecision, TaskUpdate
from app.session.slot_policy import strip_router_generated_message_slots


TASK_IDENTITY_FIELDS = {
    "document_id",
    "knowledge_base_id",
    "order_id",
    "product_id",
    "product_name",
    "product_name_1",
    "product_name_2",
    "refund_type",
    "sku_id",
    "tool_name",
}


class SkillRuntime:
    """把路由决策落到会话状态：维护活跃技能、待办任务帧与槽位，供 Harness v2 读取。

    任务帧统一存在 pending_tasks_json；awaiting_input 里的 task_id 标记当前活跃任务。
    """

    def apply_decision(self, session: ChatSession, decision: RouterDecision) -> ChatSession:
        """应用一次路由决策并返回更新后的会话（调用方负责提交）。

        先清洗槽位与任务帧，再按 decision 类型激活/移除任务；不做数据库读写。
        """
        _sanitize_decision_slots(decision)
        session.slots_json = strip_router_generated_message_slots(session.slots_json)
        session.pending_tasks_json = _sanitize_task_frames(session.pending_tasks_json)
        session.skill_stack_json = []
        self._apply_task_updates(session, decision.task_updates)
        self._append_pending_tasks(session, decision.pending_tasks)
        session.resume_after_answer_json = None

        selected_frame = self._take_task_frame(session, decision.selected_task_id)
        selected_frame = _frame_with_slot_hints(selected_frame, decision.slot_hints)

        decision_name = decision.decision
        if decision_name in {"create_pending", "update_pending", "answer_only", "clarify"}:
            pass
        elif decision_name == "handoff_human":
            session.status = "handoff"
        elif decision_name in {"start_new_task", "continue_active", "switch_to_pending"}:
            self._activate_decision_target(session, decision, selected_frame)
        elif decision_name == "complete_task":
            if decision.selected_task_id:
                self._remove_task_frame(session, decision.selected_task_id)
            else:
                self.complete_current_skill(session)

        if decision.awaiting_input:
            awaiting_input = decision.awaiting_input.model_dump(mode="json")
            active_task_id = _active_task_id(session)
            if active_task_id and not awaiting_input.get("task_id"):
                awaiting_input["task_id"] = active_task_id
            session.awaiting_input_json = awaiting_input
        if decision.slot_hints and decision_name != "complete_task":
            session.slots_json = strip_router_generated_message_slots(
                {**(session.slots_json or {}), **dict(decision.slot_hints)}
            )

        session.updated_at = utc_now()
        return session

    def complete_current_skill(self, session: ChatSession) -> ChatSession:
        """结束当前技能：移除其任务帧与等价帧，并清空技能栈、槽位和待输入状态。"""
        active_task_id = _active_task_id(session)
        completed_frame = _current_frame(session, status="completed")
        if active_task_id:
            self._remove_task_frame(session, active_task_id)
        if completed_frame:
            session.pending_tasks_json = _without_equivalent_task_frames(
                session.pending_tasks_json,
                completed_frame,
                exclude_task_id=active_task_id,
            )
        session.skill_stack_json = []
        session.active_skill_id = None
        session.active_step_id = None
        session.slots_json = {}
        session.awaiting_input_json = None
        session.resume_after_answer_json = None
        session.updated_at = utc_now()
        return session

    def suspend_current_skill(
        self, session: ChatSession, *, enqueue: bool = False
    ) -> dict[str, Any] | None:
        """把当前技能挂起成待恢复的任务帧，返回该帧；无活跃技能时返回 None。

        enqueue 为真时同时压入待办队列；挂起会清空活跃技能、步骤、槽位与提问缓存。
        """
        frame = _current_frame(session, status="pending", resume_policy="resume_after_turn_tasks")
        if not frame:
            return None
        frame["awaiting_input"] = dict(session.awaiting_input_json or {})
        if enqueue:
            self.enqueue_task_frame(session, frame)
        session.active_skill_id = None
        session.active_step_id = None
        session.slots_json = {}
        session.awaiting_input_json = None
        session.summary = None
        session.last_agent_question = None
        session.resume_after_answer_json = None
        session.updated_at = utc_now()
        return frame

    def restore_task_frame(self, session: ChatSession, frame: dict[str, Any]) -> ChatSession:
        """把已保存的任务帧重新激活为会话当前状态，并恢复其中的待输入上下文。"""
        _activate_frame(session, frame)
        awaiting_input = frame.get("awaiting_input")
        if isinstance(awaiting_input, dict):
            session.awaiting_input_json = dict(awaiting_input)
        session.updated_at = utc_now()
        return session

    def enqueue_task_frame(self, session: ChatSession, frame: dict[str, Any]) -> None:
        """把任务帧标记为 pending 后追加到待办队列；同 task_id 已存在时忽略，避免重复。"""
        next_frame = dict(frame)
        next_frame["status"] = "pending"
        frames = list(session.pending_tasks_json or [])
        task_id = str(next_frame.get("task_id") or "").strip()
        if task_id and any(
            isinstance(item, dict) and str(item.get("task_id") or "") == task_id
            for item in frames
        ):
            return
        frames.append(next_frame)
        session.pending_tasks_json = frames

    def enqueue_pending_task(self, session: ChatSession, task: PendingTask) -> None:
        """把路由器给出的 PendingTask 转成任务帧并入队，重复帧按等价规则合并。"""
        self._append_pending_tasks(session, [task])

    def _activate_decision_target(
        self,
        session: ChatSession,
        decision: RouterDecision,
        selected_frame: dict[str, Any] | None,
    ) -> None:
        """按决策类型切换会话的活跃技能与步骤；优先复用已取出的任务帧。

        continue_active 命中同一技能时保留当前步骤，避免路由把用户拉回旧步骤。
        """
        if selected_frame:
            _activate_frame(session, selected_frame)
            return
        if not decision.target_skill_id:
            return
        if decision.decision == "switch_to_pending":
            session.active_skill_id = decision.target_skill_id
            session.active_step_id = decision.target_step_id
            session.slots_json = strip_router_generated_message_slots(decision.slot_hints)
            session.awaiting_input_json = None
            session.last_agent_question = None
            return
        if not session.active_skill_id and decision.target_skill_id:
            session.active_skill_id = decision.target_skill_id
            session.slots_json = strip_router_generated_message_slots(decision.slot_hints)
            _set_active_task_id(session, None)
        if decision.target_skill_id and decision.decision == "start_new_task":
            session.active_skill_id = decision.target_skill_id
            session.slots_json = strip_router_generated_message_slots(decision.slot_hints)
            session.awaiting_input_json = None
            session.last_agent_question = None
        preserve_active_step = (
            decision.decision == "continue_active"
            and bool(session.active_step_id)
            and decision.target_skill_id == session.active_skill_id
        )
        if decision.target_step_id and not preserve_active_step:
            session.active_step_id = decision.target_step_id

    def _append_pending_tasks(self, session: ChatSession, tasks: list[PendingTask]) -> None:
        """批量并入待办任务：同 task_id 去重，等价帧合并字段而不是新增一条。"""
        if not tasks:
            return
        frames = list(session.pending_tasks_json or [])
        existing_ids = {
            str(frame.get("task_id"))
            for frame in frames
            if isinstance(frame, dict) and frame.get("task_id")
        }
        for task in tasks:
            frame = _task_frame_from_pending(task)
            task_id = str(frame.get("task_id") or "")
            if not task_id or task_id in existing_ids:
                continue
            existing_index = _find_equivalent_task_frame_index(frames, frame)
            if existing_index is not None:
                frames[existing_index] = _merge_task_frames(frames[existing_index], frame)
                continue
            frames.append(frame)
            existing_ids.add(task_id)
        session.pending_tasks_json = frames

    def _apply_task_updates(self, session: ChatSession, updates: list[TaskUpdate]) -> None:
        """按 task_id 更新待办任务帧；标记移除或已完成的状态会直接丢弃该帧。"""
        if not updates:
            return
        pending = list(session.pending_tasks_json or [])
        for update in updates:
            if update.remove or update.status in {"removed", "completed", "cancelled"}:
                pending = _without_task_or_skill(pending, task_id=update.task_id)
                continue
            patch = {
                key: value
                for key, value in {
                    "status": update.status,
                    "skill_id": update.target_skill_id,
                    "target_skill_id": update.target_skill_id,
                    "step_id": update.target_step_id,
                    "target_step_id": update.target_step_id,
                    "intent_summary": update.user_intent,
                    "user_intent": update.user_intent,
                    "reason": update.reason,
                    "source_message": update.source_message,
                    "updated_at": utc_now().isoformat(),
                }.items()
                if value is not None
            }
            if update.slot_hints:
                slot_hints = strip_router_generated_message_slots(update.slot_hints)
                if slot_hints:
                    patch["slots"] = slot_hints
                    patch["slot_hints"] = slot_hints
            pending = _patch_task_frame(pending, update.task_id, patch)
        session.pending_tasks_json = pending

    def _take_task_frame(self, session: ChatSession, task_id: str | None) -> dict[str, Any] | None:
        """从待办队列取出并移除指定任务帧；task_id 为空或未命中时返回 None。"""
        if not task_id:
            return None
        frame, pending = _pop_task_frame(session.pending_tasks_json, task_id)
        if frame:
            session.pending_tasks_json = pending
            return frame
        return None

    def _remove_task_frame(self, session: ChatSession, task_id: str | None) -> None:
        """从待办队列剔除指定任务帧；task_id 为空时不做任何事。"""
        if not task_id:
            return
        session.pending_tasks_json = _without_task_or_skill(session.pending_tasks_json, task_id=task_id)


def _sanitize_decision_slots(decision: RouterDecision) -> None:
    """就地清掉决策里所有槽位中的路由生成话术字段，避免污染业务槽位。"""
    decision.slot_hints = strip_router_generated_message_slots(decision.slot_hints)
    for task in [*decision.task_frames, *decision.pending_tasks, *decision.created_tasks]:
        task.slot_hints = strip_router_generated_message_slots(task.slot_hints)
    for update in decision.task_updates:
        update.slot_hints = strip_router_generated_message_slots(update.slot_hints)


def _sanitize_task_frames(frames_json: list[dict] | None) -> list[dict]:
    """清洗已有任务帧的槽位：非字典帧丢弃，无有效槽位时移除 slots/slot_hints。"""
    frames: list[dict] = []
    for frame in list(frames_json or []):
        if not isinstance(frame, dict):
            continue
        next_frame = dict(frame)
        raw_slots = next_frame.get("slots") if isinstance(next_frame.get("slots"), dict) else {}
        raw_hints = next_frame.get("slot_hints") if isinstance(next_frame.get("slot_hints"), dict) else {}
        slots = strip_router_generated_message_slots({**raw_hints, **raw_slots})
        if slots:
            next_frame["slots"] = slots
            next_frame["slot_hints"] = slots
        else:
            next_frame.pop("slots", None)
            next_frame.pop("slot_hints", None)
        frames.append(next_frame)
    return frames


def _task_frame_from_pending(task: PendingTask) -> dict[str, Any]:
    """把 PendingTask 转成标准任务帧字典；缺 task_id 时自动生成一个。

    skill_id/step_id 同时写平铺字段与 target_* 别名，兼容新旧读取方。
    """
    now = utc_now().isoformat()
    task_id = task.task_id or new_id("task")
    skill_id = task.target_skill_id
    step_id = task.target_step_id
    slots = strip_router_generated_message_slots(task.slot_hints)
    return {
        "task_id": task_id,
        "status": task.status or "pending",
        "skill_id": skill_id,
        "target_skill_id": skill_id,
        "step_id": step_id,
        "target_step_id": step_id,
        "slots": slots,
        "slot_hints": slots,
        "intent_summary": task.user_intent,
        "user_intent": task.user_intent,
        "source_turn_id": None,
        "source_message": task.source_message,
        "parent_task_id": None,
        "resume_policy": None,
        "reason": task.reason,
        "confidence": task.confidence,
        "created_at": now,
        "updated_at": now,
    }


def _current_frame(
    session: ChatSession,
    status: str,
    resume_policy: str | None = None,
) -> dict[str, Any] | None:
    """按会话当前技能快照出一个任务帧；无活跃技能时返回 None。"""
    if not session.active_skill_id:
        return None
    now = utc_now().isoformat()
    task_id = _active_task_id(session) or new_id("task")
    return {
        "task_id": task_id,
        "status": status,
        "skill_id": session.active_skill_id,
        "target_skill_id": session.active_skill_id,
        "step_id": session.active_step_id,
        "target_step_id": session.active_step_id,
        "slots": strip_router_generated_message_slots(session.slots_json),
        "slot_hints": strip_router_generated_message_slots(session.slots_json),
        "intent_summary": None,
        "source_turn_id": None,
        "source_message": None,
        "parent_task_id": None,
        "resume_policy": resume_policy,
        "summary": session.summary,
        "last_agent_question": session.last_agent_question,
        "created_at": now,
        "updated_at": now,
    }


def _active_task_id(session: ChatSession) -> str | None:
    """从 awaiting_input 元数据里读当前活跃任务 id；没有则返回 None。"""
    metadata = session.awaiting_input_json if isinstance(session.awaiting_input_json, dict) else {}
    task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
    return str(task_id) if task_id else None


def _set_active_task_id(session: ChatSession, task_id: str | None) -> None:
    """写入或清除 awaiting_input 里的活跃 task_id；清空后无其他字段则置 None。"""
    if not task_id:
        if isinstance(session.awaiting_input_json, dict) and "task_id" in session.awaiting_input_json:
            data = dict(session.awaiting_input_json)
            data.pop("task_id", None)
            session.awaiting_input_json = data or None
        return
    data = dict(session.awaiting_input_json or {})
    data["task_id"] = task_id
    session.awaiting_input_json = data


def _activate_frame(session: ChatSession, frame: dict[str, Any]) -> None:
    """把任务帧的内容写回会话：技能、步骤、槽位、摘要、待输入与活跃任务 id。"""
    session.active_skill_id = frame.get("skill_id") or frame.get("target_skill_id")
    session.active_step_id = frame.get("step_id") or frame.get("target_step_id")
    slots = frame.get("slots") if isinstance(frame.get("slots"), dict) else frame.get("slot_hints")
    session.slots_json = strip_router_generated_message_slots(slots)
    session.summary = frame.get("summary")
    session.last_agent_question = frame.get("last_agent_question")
    awaiting_input = frame.get("awaiting_input")
    session.awaiting_input_json = dict(awaiting_input) if isinstance(awaiting_input, dict) else None
    _set_active_task_id(session, str(frame.get("task_id") or ""))


def _pop_last_skill_frame(
    stack_json: list[dict] | None,
    skill_id: str | None,
) -> tuple[dict | None, list[dict]]:
    """从技能栈自顶向下找该技能的帧并弹出，返回 (命中的帧, 剩余栈)；无 skill_id 时原样返回。"""
    stack = list(stack_json or [])
    if not skill_id:
        return None, stack
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].get("skill_id") == skill_id or stack[index].get("target_skill_id") == skill_id:
            frame = stack.pop(index)
            return frame, stack
    return None, stack


def _pop_task_frame(frames_json: list[dict] | None, task_id: str) -> tuple[dict | None, list[dict]]:
    """按 task_id 取出任务帧并返回剩余列表；未命中时返回 (None, 原列表副本)。"""
    frames = list(frames_json or [])
    for index, frame in enumerate(frames):
        if isinstance(frame, dict) and str(frame.get("task_id") or "") == task_id:
            return frame, frames[:index] + frames[index + 1 :]
    return None, frames


def _without_task_or_skill(
    frames_json: list[dict] | None,
    task_id: str | None = None,
    skill_id: str | None = None,
) -> list[dict]:
    """过滤掉匹配 task_id 或 skill_id 的任务帧，返回新列表；两个条件可单独或同时生效。"""
    frames = []
    for frame in list(frames_json or []):
        if task_id and str(frame.get("task_id") or "") == task_id:
            continue
        if skill_id and (frame.get("skill_id") == skill_id or frame.get("target_skill_id") == skill_id):
            continue
        frames.append(frame)
    return frames


def _find_equivalent_task_frame_index(frames: list[dict], target: dict[str, Any]) -> int | None:
    """在任务帧列表里找与 target 等价的第一帧下标，找不到返回 None。"""
    for index, frame in enumerate(frames):
        if isinstance(frame, dict) and _task_frames_equivalent(frame, target):
            return index
    return None


def _without_equivalent_task_frames(
    frames_json: list[dict] | None,
    completed_frame: dict[str, Any],
    exclude_task_id: str | None = None,
) -> list[dict]:
    """移除与已完成帧等价的任务帧（可额外排除指定 task_id），避免重复冒出同一任务。"""
    frames: list[dict] = []
    for frame in list(frames_json or []):
        if not isinstance(frame, dict):
            continue
        if exclude_task_id and str(frame.get("task_id") or "") == exclude_task_id:
            continue
        if _task_frames_equivalent(frame, completed_frame):
            continue
        frames.append(frame)
    return frames


def _task_frames_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """判断两帧是否同一任务：技能相同且身份类槽位重叠部分取值一致。

    任一帧没有身份槽位、或身份键完全不重叠时视为不同任务，避免误合并。
    """
    if _frame_skill_id(left) != _frame_skill_id(right):
        return False
    left_identity = _task_identity_slots(left)
    right_identity = _task_identity_slots(right)
    if not left_identity or not right_identity:
        return False
    common_keys = set(left_identity) & set(right_identity)
    if not common_keys:
        return False
    return all(left_identity[key] == right_identity[key] for key in common_keys)


def _merge_task_frames(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """把等价的新帧合并进已有帧：槽位取并集，非空字段覆盖，保留原 task_id 与创建时间。"""
    merged = dict(existing)
    existing_slots = strip_router_generated_message_slots(
        existing.get("slots") if isinstance(existing.get("slots"), dict) else {}
    )
    incoming_slots = strip_router_generated_message_slots(
        incoming.get("slots") if isinstance(incoming.get("slots"), dict) else {}
    )
    slots = {**existing_slots, **incoming_slots}
    merged.update(
        {
            key: value
            for key, value in incoming.items()
            if key not in {"task_id", "created_at", "slots", "slot_hints"} and value not in {None, ""}
        }
    )
    if slots:
        merged["slots"] = slots
        merged["slot_hints"] = slots
    else:
        merged.pop("slots", None)
        merged.pop("slot_hints", None)
    merged["task_id"] = existing.get("task_id") or incoming.get("task_id")
    merged["created_at"] = existing.get("created_at") or incoming.get("created_at")
    merged["updated_at"] = utc_now().isoformat()
    return merged


def _frame_skill_id(frame: dict[str, Any]) -> str | None:
    """读取任务帧的技能 id，兼容 skill_id 与 target_skill_id 两种写法。"""
    value = frame.get("skill_id") or frame.get("target_skill_id")
    return str(value) if value else None


def _task_identity_slots(frame: dict[str, Any]) -> dict[str, str]:
    """抽取用于判定任务同一性的槽位：只取 TASK_IDENTITY_FIELDS，值统一转小写去空白。"""
    slots = frame.get("slots") if isinstance(frame.get("slots"), dict) else frame.get("slot_hints")
    if not isinstance(slots, dict):
        return {}
    slots = strip_router_generated_message_slots(slots)
    identity: dict[str, str] = {}
    for key in TASK_IDENTITY_FIELDS:
        value = slots.get(key)
        if value is None or value == "":
            continue
        identity[key] = str(value).strip().lower()
    return identity


def _patch_task_frame(frames_json: list[dict] | None, task_id: str, patch: dict[str, Any]) -> list[dict]:
    """对匹配 task_id 的帧浅合并 patch，其余帧原样保留；未命中时列表不变。"""
    frames = []
    for frame in list(frames_json or []):
        if isinstance(frame, dict) and str(frame.get("task_id") or "") == task_id:
            frames.append({**frame, **patch})
        else:
            frames.append(frame)
    return frames


def _upsert_frame(frames_json: list[dict] | None, frame: dict[str, Any]) -> list[dict]:
    """同 task_id 的帧原地替换，否则追加到末尾，返回新列表。"""
    task_id = str(frame.get("task_id") or "")
    frames = []
    replaced = False
    for current in list(frames_json or []):
        current_task_id = str(current.get("task_id") or "")
        if task_id and current_task_id == task_id:
            frames.append(frame)
            replaced = True
        else:
            frames.append(current)
    if not replaced:
        frames.append(frame)
    return frames


def _frame_with_slot_hints(frame: dict[str, Any] | None, slot_hints: dict | None) -> dict[str, Any] | None:
    """把决策的槽位提示并入任务帧槽位并刷新 updated_at；帧或提示为空时原样返回。"""
    if not frame or not slot_hints:
        return frame
    next_frame = dict(frame)
    current_slots = strip_router_generated_message_slots(
        next_frame.get("slots") if isinstance(next_frame.get("slots"), dict) else {}
    )
    current_hints = strip_router_generated_message_slots(
        next_frame.get("slot_hints") if isinstance(next_frame.get("slot_hints"), dict) else {}
    )
    incoming_hints = strip_router_generated_message_slots(slot_hints)
    merged_slots = strip_router_generated_message_slots({**current_hints, **current_slots, **incoming_hints})
    next_frame["slots"] = merged_slots
    next_frame["slot_hints"] = merged_slots
    next_frame["updated_at"] = utc_now().isoformat()
    return next_frame
