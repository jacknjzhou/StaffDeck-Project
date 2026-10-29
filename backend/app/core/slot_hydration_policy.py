from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.db.models import ChatSession, Skill
from app.session.session_schema import RouterDecision, TurnPlan


class SlotHydrationPolicy:
    """SOP 槽位注水策略：只用带稳定 key 的结构化记忆补全槽位，不做自然语言推测。"""
    ALLOWED_MEMORY_KINDS = frozenset({"profile", "preference", "fact"})
    SLOT_MEMORY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
        "user_name": ("preferred_name",),
    }

    @classmethod
    def hydrate_plan(
        cls,
        chat_session: ChatSession,
        plan: TurnPlan,
        skills: list[Skill],
        memory_context: list[dict[str, object]],
        patcher: Callable[
            [Skill | None, dict[str, Any], list[dict[str, object]]], dict[str, Any]
        ]
        | None = None,
    ) -> dict[str, Any]:
        """在 SOP 任务帧落库或执行前，用长期记忆补全槽位。

        槽位补全过去只是 RouterDecision 的工具函数，Harness v2 并未调用，导致步骤重复
        追问记忆里已有的信息。这里就地改写唯一的 TurnPlan，让持久化、执行与对外路由
        投影共用同一份槽位快照；返回已补全任务的摘要，无补全时返回空字典。
        """

        patch_slots = patcher or cls.patch
        skills_by_id = {skill.skill_id: skill for skill in skills}
        hydrated_tasks: list[dict[str, Any]] = []
        for frame in plan.task_frames:
            if frame.kind != "sop":
                continue
            target_skill_id = str(
                frame.target_skill_id or chat_session.active_skill_id or ""
            ).strip()
            target_skill = skills_by_id.get(target_skill_id)
            continues_active = bool(
                frame.decision == "continue_active"
                and target_skill_id == chat_session.active_skill_id
            )
            base_slots = (
                dict(chat_session.slots_json or {}) if continues_active else {}
            )
            base_slots.update(dict(frame.slot_hints or {}))
            patch = patch_slots(target_skill, base_slots, memory_context)
            if not patch:
                continue
            frame.slot_hints = {**dict(frame.slot_hints or {}), **patch}
            hydrated_tasks.append(
                {
                    "task_id": frame.task_id,
                    "target_skill_id": target_skill_id,
                    "slots": patch,
                }
            )
        return {"tasks": hydrated_tasks} if hydrated_tasks else {}

    @classmethod
    def hydrate(
        cls,
        chat_session: ChatSession,
        router_decision: RouterDecision,
        skills: list[Skill],
        memory_context: list[dict[str, object]],
        patcher: Callable[
            [Skill | None, dict[str, Any], list[dict[str, object]]], dict[str, Any]
        ]
        | None = None,
        awaiting_trimmer: Callable[
            [RouterDecision, dict[str, Any]], list[str] | None
        ]
        | None = None,
    ) -> dict[str, Any]:
        """就地补全 RouterDecision 的主槽位与各任务帧槽位，并裁剪已被槽位满足的待补字段。

        返回 {"primary": 主槽位补丁, "tasks": 任务补丁列表,
        "awaiting_input_expected_fields": 剩余待补字段} 的子集，无变化时为空字典。
        """
        patch_slots = patcher or cls.patch
        trim_awaiting = awaiting_trimmer or cls.trim_satisfied_awaiting_fields
        skills_by_id = {skill.skill_id: skill for skill in skills}
        hydrated: dict[str, Any] = {}
        target_skill = skills_by_id.get(
            router_decision.target_skill_id or chat_session.active_skill_id or ""
        )
        base_slots = dict(chat_session.slots_json or {})
        base_slots.update(dict(router_decision.slot_hints or {}))
        patch = patch_slots(target_skill, base_slots, memory_context)
        if patch:
            router_decision.slot_hints = {**dict(router_decision.slot_hints or {}), **patch}
            hydrated["primary"] = patch
        remaining_awaiting = trim_awaiting(router_decision, {**base_slots, **patch})
        if remaining_awaiting is not None:
            hydrated["awaiting_input_expected_fields"] = remaining_awaiting

        task_patches: list[dict[str, Any]] = []
        for task in [
            *router_decision.task_frames,
            *router_decision.pending_tasks,
            *router_decision.created_tasks,
        ]:
            task_skill = skills_by_id.get(task.target_skill_id or "")
            task_slots = dict(task.slot_hints or {})
            task_patch = patch_slots(task_skill, task_slots, memory_context)
            if task_patch:
                task.slot_hints = {**task_slots, **task_patch}
                task_patches.append(
                    {
                        "task_id": task.task_id,
                        "target_skill_id": task.target_skill_id,
                        "slots": task_patch,
                    }
                )
        if task_patches:
            hydrated["tasks"] = task_patches
        return hydrated

    @classmethod
    def patch(
        cls,
        skill: Skill | None,
        slots: dict[str, Any],
        memory_context: list[dict[str, object]],
    ) -> dict[str, Any]:
        """计算需要补的槽位：记忆 key 命中即直接补，声明字段再按别名映射补一次。

        只补当前 slots 里尚无有效值（见 slot_has_value）的字段，不覆盖已有值。
        """
        expected_fields = cls.skill_expected_fields(skill) if skill else set()
        memory_values = cls.memory_values_by_key(memory_context)
        patch: dict[str, Any] = {
            key: value
            for key, value in memory_values.items()
            if not cls.slot_has_value(slots, key)
        }
        for field in expected_fields:
            if cls.slot_has_value(slots, field) or cls.slot_has_value(patch, field):
                continue
            for memory_key in cls.memory_keys_for_slot(field):
                if memory_key not in memory_values:
                    continue
                patch[field] = memory_values[memory_key]
                break
        return patch

    @classmethod
    def trim_satisfied_awaiting_fields(
        cls, router_decision: RouterDecision, slots: dict[str, Any]
    ) -> list[str] | None:
        """移除 awaiting_input 中已被槽位满足的字段，返回剩余字段。

        原本没有 awaiting_input、或剩余字段与原始一致时返回 None；全部满足则整体置空。
        """
        if not router_decision.awaiting_input:
            return None
        original = list(router_decision.awaiting_input.expected_fields)
        remaining = [
            field
            for field in router_decision.awaiting_input.expected_fields
            if not cls.slot_has_value(slots, field)
        ]
        if remaining == original:
            return None
        if remaining:
            router_decision.awaiting_input.expected_fields = remaining
        else:
            router_decision.awaiting_input = None
        return remaining

    @staticmethod
    def slot_has_value(slots: dict[str, Any], field: str) -> bool:
        """判断槽位是否已有有效值：非 None、非空串、非空列表即视为已填。"""
        value = slots.get(field)
        return value is not None and value != "" and value != []

    @staticmethod
    def skill_expected_fields(skill: Skill) -> set[str]:
        """收集 SOP 声明的期望信息字段：顶层 required_info 加各节点的 expected_user_info。"""
        content = skill.content_json or {}
        fields: set[str] = set()
        required_info = content.get("required_info")
        if isinstance(required_info, list):
            fields.update(str(item) for item in required_info if str(item).strip())
        nodes = content.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                expected = node.get("expected_user_info")
                if isinstance(expected, list):
                    fields.update(str(item) for item in expected if str(item).strip())
        return fields

    @classmethod
    def memory_values_by_key(
        cls, memory_context: list[dict[str, object]]
    ) -> dict[str, str]:
        """把可用的结构化记忆按稳定 key 建索引，不从散文内容推断。

    MemoryService.context_memories 返回新记录在前，故用 setdefault 保留最新值；
    没有显式 key 的记忆会被刻意忽略，因为文本相似度不足以作为填槽依据。
    """

        values: dict[str, str] = {}
        for memory in memory_context:
            if str(memory.get("kind") or "") not in cls.ALLOWED_MEMORY_KINDS:
                continue
            metadata = memory.get("metadata")
            raw_key = metadata.get("key") if isinstance(metadata, dict) else None
            key = cls.normalize_key(raw_key)
            content = str(memory.get("content") or "").strip()
            if key and content:
                values.setdefault(key, content)
        return values

    @classmethod
    def memory_keys_for_slot(cls, field: str) -> tuple[str, ...]:
        """返回槽位字段可匹配的记忆 key：规范化字段名在前，再附 SLOT_MEMORY_KEY_ALIASES 别名。"""
        normalized = cls.normalize_key(field)
        if not normalized:
            return ()
        aliases = cls.SLOT_MEMORY_KEY_ALIASES.get(normalized, ())
        return (normalized, *aliases)

    @staticmethod
    def normalize_key(value: object) -> str:
        """把输入规范成小写下划线 key：非字母数字统一替换为 _，并去掉首尾下划线。"""
        if not isinstance(value, str):
            return ""
        return re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
