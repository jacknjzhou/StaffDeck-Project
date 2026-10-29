from __future__ import annotations

from pydantic import BaseModel

from app import paths
from app.core.context_projection import (
    compact_conversation_context,
    compact_current_step,
    compact_router_decision,
    compact_step_result,
)
from app.db.models import ChatSession, ModelConfig, Skill, Tool
from app.llm import LLMClient, LLMError
from app.llm.stage_protocol import (
    REFLECTION_OUTPUT_SCHEMA,
    stage_payload,
    unified_system_prompt,
)
from app.observability.spans import llm_operation
from app.session.session_schema import RouterDecision, StepAgentResult
from app.tools.tool_schema import ToolResult


PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "reflection_prompt.md"


class ReflectionDecision(BaseModel):
    """反思判定结果：是否放行、是否需要重试，以及重试的目标技能/步骤/工具。"""

    action: str = "pass"
    needs_retry: bool = False
    reason: str | None = None
    target_skill_id: str | None = None
    target_step_id: str | None = None
    target_tool_name: str | None = None


class ReflectionAgent:
    """在关键步骤后审查执行结果，判断是放行、重试还是改走其它技能或工具。"""

    def review(
        self,
        message: str,
        session: ChatSession,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        available_skills: list[Skill],
        available_tools: list[Tool],
        model_config: ModelConfig,
        conversation_context: dict[str, object] | None = None,
        memory_context: list[dict[str, object]] | None = None,
    ) -> ReflectionDecision:
        """审查本轮路由决策、步骤结果与工具结果，产出是否需要重试的判定。

        先用 action_needs_reflection 过滤掉无需反思的常规推进；需要反思时组装
        当前步骤、槽位、工具结果等上下文调用 LLM，并校验返回的 JSON 结构。
        任何非 LLMError 的异常都包装成 LLMError 抛出，避免上层收到半成品判定。
        """
        if not action_needs_reflection(router_decision, step_result, tool_result):
            return ReflectionDecision()

        stage_data = {
            "current_step": compact_current_step(
                active_skill.content_json if active_skill else None,
                session.active_step_id,
            ),
            "rules": {
                "response_rules": active_skill.content_json.get("response_rules", [])
                if active_skill
                else [],
            },
            "slots": session.slots_json or {},
            "router_decision": compact_router_decision(
                router_decision.model_dump(mode="json")
            ),
            "step_result": compact_step_result(step_result.model_dump(mode="json")),
            "tool_result": tool_result.model_dump() if tool_result else None,
        }
        payload = stage_payload(
            phase="Reflection",
            user_message=message,
            conversation_context=compact_conversation_context(conversation_context),
            memory_context=memory_context,
            instructions=PROMPT_PATH.read_text(encoding="utf-8"),
            stage_data=stage_data,
            output_contract=REFLECTION_OUTPUT_SCHEMA,
        )
        try:
            with llm_operation("reflection.review"):
                raw = LLMClient(model_config).generate_json(
                    unified_system_prompt(), payload
                )
            return ReflectionDecision.model_validate(raw)
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"Reflection agent returned invalid JSON schema: {exc}") from exc

def action_needs_reflection(
    router_decision: RouterDecision,
    step_result: StepAgentResult,
    tool_result: ToolResult | None,
) -> bool:
    """判断本轮是否值得触发反思：外部动作、知识查询、转人工或整体完成才需要。

    clarify/answer_only 场景只有存在工具调用或工具结果时才反思；若步骤已确定
    下一节点，则属于技能图的正常推进，仅当步骤整体结束时才反思。
    """
    if router_decision.decision in {"clarify", "answer_only"}:
        return bool(tool_result or step_result.tool_call or step_result.knowledge_query)
    if (
        tool_result
        or step_result.tool_call
        or step_result.knowledge_query
        or step_result.knowledge_results
        or step_result.handoff
    ):
        return True
    # Advancing to a decided next node is normal skill graph progress.
    # Reflection is reserved for external actions or the overall skill completion.
    if step_result.next_step_id:
        return False
    return bool(step_result.is_step_completed)


def tool_result_needs_reflection(tool_result: ToolResult | None) -> bool:
    """判断工具结果是否需要反思：只有失败结果需要，成功或没有结果都不需要。"""
    if tool_result is None:
        return False
    return not tool_result.success


def _data_indicates_unexpected_result(value: object) -> bool:
    """判断工具返回的数据是否暗示结果异常，供重试判定使用。

    空值、空列表、found/success 为 False、带 miss_reason 或 error 类字段，
    以及 results/items/data 为空数组时都算异常；其余情况一律视为正常。
    """
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    if not isinstance(value, dict):
        return False

    if value.get("found") is False or value.get("success") is False:
        return True
    for key in ("miss_reason", "not_found", "empty", "error", "error_code"):
        if value.get(key):
            return True
    for key in ("results", "items", "data"):
        nested = value.get(key)
        if isinstance(nested, list) and len(nested) == 0:
            return True
    return False
