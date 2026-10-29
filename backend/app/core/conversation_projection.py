from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.db.models import ChatSession, Message, Skill
from app.knowledge.citations import knowledge_citations_from_results
from app.session.attachments import (
    message_content_with_attachment_context,
    message_images_from_metadata,
)
from app.session.session_schema import ChatTurnRequest, RouterDecision, StepAgentResult


class ConversationProjection:
    """把会话数据投影成前端与运行时共用的结构：消息、引用、技能状态与标题。"""

    @staticmethod
    def message_context_entry(row: Message, *, content: str | None = None) -> dict[str, Any]:
        """构造单条消息的上下文条目，拼接附件上下文，并为用户消息附加图片。"""
        entry: dict[str, Any] = {
            "id": row.id,
            "role": row.role,
            "content": message_content_with_attachment_context(
                row.content if content is None else content,
                row.metadata_json,
            ),
            "created_at": row.created_at,
        }
        images = message_images_from_metadata(row.metadata_json)
        if images and row.role == "user":
            entry["images"] = images
        return entry

    @staticmethod
    def context_compacted_now(context: dict[str, object] | None) -> bool:
        """判断运行时 context 的 metadata 中是否标记了 compacted_now。"""
        if not isinstance(context, dict):
            return False
        metadata = context.get("metadata")
        return isinstance(metadata, dict) and metadata.get("compacted_now") is True

    @classmethod
    def assistant_message_metadata(
        cls,
        step_result: StepAgentResult | None,
        *,
        citation_deduper: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """从步骤结果提取知识引用，生成助手消息元数据；没有引用时返回空字典。

        引用先经 citation_deduper（默认按内容身份去重）处理，并记录最近一次检索 query。
        """
        knowledge_results = list(step_result.knowledge_results or []) if step_result else []
        dedupe = citation_deduper or cls.dedupe_knowledge_citations
        citations = dedupe(knowledge_citations_from_results(knowledge_results))
        if not citations:
            return {}
        latest_query = next(
            (
                item.get("query")
                for item in reversed(knowledge_results)
                if isinstance(item.get("query"), dict)
            ),
            None,
        )
        return {
            "knowledge_citations": citations,
            "knowledge_query": latest_query or {},
        }

    @staticmethod
    def dedupe_knowledge_citations(
        citations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按标题/路径等身份键去重引用并重新编号，最多保留 4 条。"""
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            identity = str(
                citation.get("title")
                or citation.get("section_path")
                or citation.get("summary")
                or citation.get("excerpt")
                or citation.get("source_path")
                or citation.get("concept_id")
                or citation.get("id")
                or ""
            )
            key = re.sub(r"\s+", " ", identity).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append({**citation, "label": f"[{len(result) + 1}]"})
            if len(result) >= 4:
                break
        return result

    @staticmethod
    def user_message_metadata(request: ChatTurnRequest) -> dict[str, Any]:
        """按请求中的非默认字段生成用户消息元数据，默认值一律省略。"""
        metadata: dict[str, Any] = {}
        if request.client_turn_id:
            metadata["client_turn_id"] = request.client_turn_id
        if request.interaction_mode != "normal":
            metadata["interaction_mode"] = request.interaction_mode
        if request.message_visibility != "visible":
            metadata["message_visibility"] = request.message_visibility
        if request.model_config_id:
            metadata["model_config_id"] = request.model_config_id
        if request.attachments:
            metadata["attachments"] = [item.model_dump(mode="json") for item in request.attachments]
        return metadata

    @staticmethod
    def runtime_stream_context(
        decision: RouterDecision,
        before_skill: str | None,
        before_step: str | None,
        chat_session: ChatSession,
    ) -> dict[str, object]:
        """生成流式事件中的运行时上下文：路由决策、来源与目标 Skill/步骤。"""
        return {
            "runtimeDecision": decision.decision,
            "fromSkillId": before_skill,
            "fromStepId": before_step,
            "toSkillId": chat_session.active_skill_id,
            "toStepId": chat_session.active_step_id,
        }

    @staticmethod
    def skill_state_payload(
        chat_session: ChatSession,
        skills: list[Skill],
        runtime_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """汇总激活 Skill 与待办任务，投影成前端使用的技能状态负载。

        过滤掉不在 skills 列表中的 Skill；activeStepId 仅在存在激活 Skill 时给出。
        """
        skill_names = {skill.skill_id: skill.name for skill in skills}
        visible_skill_ids = set(skill_names)
        current_skills: list[dict[str, object]] = []
        active_skill_id = (
            chat_session.active_skill_id
            if chat_session.active_skill_id in visible_skill_ids
            else None
        )
        if active_skill_id:
            current_skills.append(
                {
                    "skillId": active_skill_id,
                    "name": skill_names.get(active_skill_id, active_skill_id),
                    "stepId": chat_session.active_step_id,
                    "state": "active",
                }
            )
        for task in chat_session.pending_tasks_json or []:
            if not isinstance(task, dict):
                continue
            skill_id = str(task.get("target_skill_id") or task.get("skill_id") or "").strip()
            if not skill_id or skill_id not in visible_skill_ids:
                continue
            current_skills.append(
                {
                    "skillId": skill_id,
                    "name": skill_names.get(skill_id, skill_id),
                    "stepId": task.get("target_step_id") or task.get("step_id"),
                    "state": task.get("status") or "pending",
                }
            )
        return {
            "activeSkillId": active_skill_id,
            "activeStepId": chat_session.active_step_id if active_skill_id else None,
            "currentSkills": current_skills,
            **(runtime_context or {}),
        }

    @staticmethod
    def fallback_session_title(message: str) -> str:
        """把首条消息压缩空白后截取前 28 个字符，作为会话兜底标题。"""
        title = re.sub(r"\s+", " ", message).strip().strip("。！？!?")
        return title[:28] if title else ""

    @staticmethod
    def normalize_reply_citation_labels(reply: str, citations: object) -> str:
        """把回答中越界的引用标号收敛到有效范围，避免引用不存在的条目。

        仅在 citations 为非空列表时改写；范围内的合法标号原样保留。
        """
        if not isinstance(citations, list) or not citations:
            return reply
        max_label = len(citations)

        def replace(match: re.Match[str]) -> str:
            """重写单个 [n] 引用标号：合法则保留，越界则指向最大有效标号。"""
            try:
                value = int(match.group(1))
            except ValueError:
                return match.group(0)
            if 1 <= value <= max_label:
                return match.group(0)
            return f"[{max(1, max_label)}]"

        return re.sub(r"\[(\d+)\]", replace, reply)

    @staticmethod
    def strip_trailing_citation_summary(reply: str) -> str:
        """去掉回答末尾自动附带的“参考资料：[n]”一类引用汇总行。"""
        return re.sub(
            r"(?:\n|\s){0,3}(?:参考资料|引用来源|资料来源)\s*[:：]\s*(?:\[\d+\]\s*)+$",
            "",
            reply.rstrip(),
        ).rstrip()
