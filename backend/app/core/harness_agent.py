from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import is_dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app import paths
from app.core.harness_attachments import (
    ValidatedTaskImagePayload,
    isolated_attachment_context,
)
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    TaskExecutionResult,
    TaskRequirement,
)
from app.db.models import ModelConfig
from app.llm import LLMClient, LLMError
from app.observability.spans import llm_operation
from app.session.slot_policy import strip_router_generated_message_slots

PROMPT_PATH = paths.resource_dir() / "app" / "llm" / "prompts" / "harness_agent_prompt.md"
MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK = 2
ToolInvoker = Callable[[str, dict[str, Any]], dict[str, Any]]
TraceSink = Callable[[str, dict[str, Any]], None]
CancellationCheck = Callable[[], bool]


class HarnessExecutionCancelled(RuntimeError):
    """调用方已取消本次 Harness 执行时抛出，用于中断内层动作循环。"""


class HarnessExecutionFenced(RuntimeError):
    """会话租约丢失或被其他执行者抢占时抛出，调用方必须立即停止写入。"""


class HarnessAction(BaseModel):
    """模型单步输出的动作协议：调用一个已授权能力，或结束当前任务。"""
    action: Literal["tool", "finish"]
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["completed", "awaiting_user", "handoff", "failed"] | None = None
    reply_fragment: str = ""
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    next_step_id: str | None = None
    task_summary: str = ""
    structured_result: Any | None = None


class HarnessTaskAgent:
    """执行单个 TaskRequirement 的隔离运行体，不读取外层会话消息。"""

    def run(
        self,
        requirement: TaskRequirement,
        model_config: ModelConfig,
        invoke_tool: ToolInvoker,
        *,
        max_actions: int = 6,
        trace_sink: TraceSink | None = None,
        is_cancelled: CancellationCheck | None = None,
        image_payloads: list[ValidatedTaskImagePayload] | None = None,
        step_deadline_monotonic: float | None = None,
        step_timeout_seconds: int | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> TaskExecutionResult:
        """执行本轮任务直到产出结论，返回 TaskExecutionResult。

        每轮把压缩后的对话记录、冻结能力清单与知识检索预算交给模型，解析出的动作
        依次执行；达到动作上限、单步超时或取消时返回对应状态，并通过
        loop_checkpoint 保存可恢复的上下文。
        """
        max_actions = max(1, min(int(max_actions), 100))
        checkpoint = dict(checkpoint or {})
        current_step_id = _requirement_step_id(requirement)
        same_frame = (
            str(checkpoint.get("task_frame_id") or "") == requirement.task_frame_id
        )
        same_step = (
            same_frame
            and str(checkpoint.get("step_id") or "") == current_step_id
        )
        transcript = _dict_items(checkpoint.get("transcript")) if same_frame else []
        # 从暂停恢复时，把用户的最新回复显式写进内层对话记录：仅靠
        # requirement.source_user_message 字段太不显眼，实测模型会忽略它
        # 而重复上一轮的提问（真实案例：确认暂停点反复索要确认）。
        latest_user_message = str(requirement.source_user_message or "").strip()
        if same_frame and transcript and latest_user_message:
            previous_user_entry = next(
                (
                    str(item.get("content") or "")
                    for item in reversed(transcript)
                    if item.get("role") == "user"
                ),
                None,
            )
            if previous_user_entry != latest_user_message:
                transcript.append({"role": "user", "content": latest_user_message})
        citations = _dict_items(checkpoint.get("citations")) if same_frame else []
        evidence_results = (
            _dict_items(checkpoint.get("evidence_results")) if same_frame else []
        )
        capability_results = (
            _dict_items(checkpoint.get("capability_results")) if same_step else []
        )
        satisfied_required_knowledge_ids = set(
            _string_list(checkpoint.get("satisfied_required_knowledge_ids"))
            if same_step
            else []
        )
        successful_knowledge_searches = (
            int(checkpoint.get("successful_knowledge_searches") or 0)
            if same_step
            else 0
        )
        artifacts = _dict_items(checkpoint.get("artifacts")) if same_frame else []
        loaded_general_skill_names = (
            _string_list(checkpoint.get("loaded_general_skill_names"))
            if same_frame
            else []
        )
        recent_task_summaries = _string_list(
            checkpoint.get("recent_task_summaries")
        )[-8:]
        # A non-retryable failure only blocks an identical call inside this
        # invocation of the AgentLoop.  Persisting the signature in the
        # checkpoint made a later user turn inherit an obsolete failure even
        # after its inputs or external state had changed.
        non_retryable_action_signatures: set[str] = set()
        # 强制能力从未尝试就 finish(failed) 只拦一次，避免与固执模型互相死锁。
        failed_finish_without_attempt_blocked = False
        allowed_names = requirement.capability_manifest.allowed_names()
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
        pending_actions: list[HarnessAction] = []

        def finish(result: TaskExecutionResult) -> TaskExecutionResult:
            """补齐任务摘要，并按当前 TaskFrame 与步骤写入 loop_checkpoint 后返回。"""
            summary = " ".join(
                str(result.task_summary or result.reply_fragment or "").split()
            ).strip()
            if (
                result.status != "action_budget"
                and summary
                and (
                    not recent_task_summaries
                    or recent_task_summaries[-1] != summary
                )
            ):
                recent_task_summaries.append(summary[:1_000])
            result.loop_checkpoint = {
                "version": 1,
                "task_frame_id": requirement.task_frame_id,
                "step_id": current_step_id,
                "transcript": _transcript_for_model(transcript),
                "citations": citations[-20:],
                "evidence_results": evidence_results[-10:],
                "capability_results": capability_results[-20:],
                "satisfied_required_knowledge_ids": sorted(
                    satisfied_required_knowledge_ids
                ),
                "successful_knowledge_searches": successful_knowledge_searches,
                "artifacts": artifacts[-20:],
                "loaded_general_skill_names": loaded_general_skill_names[-20:],
                "recent_task_summaries": recent_task_summaries[-8:],
            }
            return result

        for iteration in range(1, max_actions + 1):
            _raise_if_cancelled(is_cancelled)
            if _deadline_expired(step_deadline_monotonic):
                return finish(_step_timeout_result(
                    requirement,
                    action_count=iteration - 1,
                    timeout_seconds=step_timeout_seconds,
                    capability_results=capability_results,
                    citations=citations,
                    evidence_results=evidence_results,
                    artifacts=artifacts,
                    trace_sink=trace_sink,
                ))
            requirement_payload = requirement.model_dump(mode="json")
            attachment_descriptors, attachment_context = isolated_attachment_context(
                requirement.attachments,
                image_payloads,
            )
            requirement_payload["attachments"] = attachment_descriptors
            payload = {
                "task_requirement": requirement_payload,
                "harness_transcript": _transcript_for_model(transcript),
                "iteration": iteration,
                "remaining_actions": max_actions - iteration + 1,
                "knowledge_search_budget": {
                    "maximum_successful_calls": MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK,
                    "successful_calls": successful_knowledge_searches,
                    "remaining_successful_calls": max(
                        0,
                        MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK
                        - successful_knowledge_searches,
                    ),
                },
            }
            if recent_task_summaries:
                payload["agent_loop_memory"] = {
                    "recent_task_summaries": list(recent_task_summaries),
                }
            if attachment_context is not None:
                payload["conversation_context"] = attachment_context
            try:
                action: HarnessAction | None = None
                validation_error: Exception | None = None
                if pending_actions:
                    action = pending_actions.pop(0)
                else:
                    for protocol_attempt in range(2):
                        # Persist a stable link between this LLM span and the Harness
                        # iteration that consumes it.  Timing projections must not
                        # infer this relationship from overlapping wall-clock windows.
                        with llm_operation(
                            "harness.task_action",
                            task_frame_id=requirement.task_frame_id,
                            iteration=iteration,
                            protocol_attempt=protocol_attempt + 1,
                        ):
                            client = _deadline_llm_client(
                                model_config,
                                step_deadline_monotonic,
                            )
                            raw = _generate_harness_action_json(
                                client,
                                system_prompt,
                                payload,
                            )
                        try:
                            actions = _harness_actions_from_raw(raw)
                            action = actions[0]
                            pending_actions.extend(actions[1:])
                            if len(actions) > 1 and trace_sink:
                                trace_sink(
                                    "harness_action_sequence_accepted",
                                    {
                                        "iteration": iteration,
                                        "action_count": len(actions),
                                        "actions": [item.action for item in actions],
                                    },
                                )
                        except (ValidationError, ValueError) as exc:
                            action = _adapt_general_skill_structured_result(
                                raw,
                                loaded_general_skill_names=loaded_general_skill_names,
                            )
                            if action is not None:
                                if trace_sink:
                                    trace_sink(
                                        "harness_structured_result_adapted",
                                        {
                                            "iteration": iteration,
                                            "source": loaded_general_skill_names[-1],
                                            "result_type": type(raw).__name__,
                                        },
                                    )
                                break
                            validation_error = exc
                            if protocol_attempt == 0:
                                payload = {
                                    **payload,
                                    "protocol_repair": {
                                        "message": (
                                            "上一次输出不符合 HarnessAction Schema。请只修正动作"
                                            "协议，不要改变任务意图。"
                                        ),
                                        "invalid_output": raw,
                                        "validation_error": str(exc),
                                        "required_tool_envelope": {
                                            "action": "tool",
                                            "tool_name": (
                                                "capability_manifest 中的已授权能力名称"
                                            ),
                                            "arguments": {},
                                        },
                                        "required_finish_envelope": {
                                            "action": "finish",
                                            "status": "completed | awaiting_user | handoff | failed",
                                        },
                                    },
                                }
                                if trace_sink:
                                    trace_sink(
                                        "harness_action_repair_requested",
                                        {
                                            "iteration": iteration,
                                            "error": str(exc),
                                        },
                                    )
                                continue
                            raise
                        break
                if action is None:
                    if validation_error is not None:
                        raise validation_error
                    raise RuntimeError("Harness action generation returned no action.")
            except (ValidationError, ValueError, LLMError) as exc:
                if _deadline_expired(step_deadline_monotonic):
                    return finish(_step_timeout_result(
                        requirement,
                        action_count=iteration - 1,
                        timeout_seconds=step_timeout_seconds,
                        capability_results=capability_results,
                        citations=citations,
                        evidence_results=evidence_results,
                        artifacts=artifacts,
                        trace_sink=trace_sink,
                    ))
                if trace_sink:
                    trace_sink(
                        "harness_action_failed",
                        {
                            "iteration": iteration,
                            "error": str(exc),
                        },
                    )
                return finish(TaskExecutionResult(
                    task_frame_id=requirement.task_frame_id,
                    status="failed",
                    reply_fragment="当前任务的执行模型没有返回有效动作。",
                    task_summary="Harness 动作解析失败。",
                    capability_results=capability_results,
                    action_count=iteration,
                    error={"code": "HARNESS_ACTION_INVALID", "message": str(exc)},
                ))
            _raise_if_cancelled(is_cancelled)
            if _deadline_expired(step_deadline_monotonic):
                return finish(_step_timeout_result(
                    requirement,
                    action_count=iteration,
                    timeout_seconds=step_timeout_seconds,
                    capability_results=capability_results,
                    citations=citations,
                    evidence_results=evidence_results,
                    artifacts=artifacts,
                    trace_sink=trace_sink,
                ))

            if trace_sink:
                trace_sink(
                    "harness_action_created",
                    {
                        "iteration": iteration,
                        "action": action.action,
                        "tool_name": action.tool_name,
                    },
                )
            if action.action == "finish":
                missing_capabilities = _missing_required_capabilities(
                    requirement,
                    capability_results,
                    satisfied_required_knowledge_ids,
                )
                if action.status in {None, "completed"} and missing_capabilities:
                    transcript.extend(
                        [
                            {
                                "role": "assistant",
                                "action": "finish",
                                "status": action.status or "completed",
                            },
                            {
                                "role": "tool",
                                "tool_name": "harness_requirement_check",
                                "result": {
                                    "success": False,
                                    "error": {
                                        "code": "REQUIRED_CAPABILITY_NOT_INVOKED",
                                        "message": (
                                            "当前 SOP 节点尚未成功执行强制能力："
                                            + "、".join(missing_capabilities)
                                        ),
                                    },
                                },
                            },
                        ]
                    )
                    if trace_sink:
                        trace_sink(
                            "harness_completion_blocked",
                            {
                                "iteration": iteration,
                                "reason": "required_capability_not_invoked",
                                "missing_capabilities": missing_capabilities,
                            },
                        )
                    continue
                # 真实案例：提交节点的模型带着上一步骤的工具报错直接
                # finish(failed)，全程没在本节点尝试过强制能力。失败结论
                # 必须建立在真实尝试之上，先打回一次要求实际调用。
                if (
                    action.status == "failed"
                    and missing_capabilities
                    and not failed_finish_without_attempt_blocked
                ):
                    attempted = {
                        str(item.get("tool_name") or "")
                        for item in capability_results
                        if isinstance(item, dict)
                    }
                    unattempted = [
                        name
                        for name in missing_capabilities
                        if not name.startswith("knowledge_search:")
                        and name not in attempted
                    ]
                    if unattempted:
                        failed_finish_without_attempt_blocked = True
                        transcript.extend(
                            [
                                {
                                    "role": "assistant",
                                    "action": "finish",
                                    "status": "failed",
                                },
                                {
                                    "role": "tool",
                                    "tool_name": "harness_requirement_check",
                                    "result": {
                                        "success": False,
                                        "error": {
                                            "code": (
                                                "REQUIRED_CAPABILITY_NOT_ATTEMPTED"
                                            ),
                                            "message": (
                                                "当前 SOP 节点的强制能力在本节点内"
                                                "尚未尝试调用，不能直接宣告失败："
                                                + "、".join(unattempted)
                                                + "。请先实际调用，再依据其真实"
                                                "结果决定完成或失败。"
                                            ),
                                        },
                                    },
                                },
                            ]
                        )
                        if trace_sink:
                            trace_sink(
                                "harness_completion_blocked",
                                {
                                    "iteration": iteration,
                                    "reason": "required_capability_not_attempted",
                                    "missing_capabilities": unattempted,
                                },
                            )
                        continue
                return finish(_finish_result(
                    requirement,
                    action,
                    citations,
                    evidence_results,
                    capability_results,
                    artifacts,
                    action_count=iteration,
                ))

            tool_name = str(action.tool_name or "").strip()
            if not tool_name or tool_name not in allowed_names:
                transcript.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "result": {
                            "success": False,
                            "error": {
                                "code": "TOOL_NOT_AVAILABLE",
                                "message": "该能力不在当前 TaskFrame 的冻结清单中。",
                            },
                        },
                    }
                )
                continue

            if (
                tool_name == "knowledge_search"
                and successful_knowledge_searches
                >= MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK
            ):
                result = {
                    "success": False,
                    "error": {
                        "code": "KNOWLEDGE_SEARCH_BUDGET_EXHAUSTED",
                        "message": (
                            "当前 TaskFrame 已完成两次有效知识检索。请使用已有证据完成"
                            "原始需求；不要扩展相邻主题或继续改写同义查询。"
                        ),
                    },
                }
            else:
                action_signature = _action_signature(
                    tool_name,
                    dict(action.arguments or {}),
                )
                if action_signature in non_retryable_action_signatures:
                    result = {
                        "success": False,
                        "error": {
                            "code": "NON_RETRYABLE_ACTION_REPEATED",
                            "message": (
                                "相同工具与参数此前已失败且不可重试，本次未再次执行。"
                                "请根据前一次错误更换工具或参数，或结束当前任务。"
                            ),
                            "retryable": False,
                        },
                    }
                    if trace_sink:
                        trace_sink(
                            "harness_action_failed",
                            {
                                "iteration": iteration,
                                "tool_name": tool_name,
                                "error": {
                                    "code": "NON_RETRYABLE_ACTION_REPEATED",
                                    "message": (
                                        "模型重复提交了已标记为不可重试的相同工具调用；"
                                        "调用未执行，AgentLoop 将继续重新规划。"
                                    ),
                                    "retryable": False,
                                },
                            },
                        )
                else:
                    try:
                        _raise_if_cancelled(is_cancelled)
                        result = invoke_tool(tool_name, dict(action.arguments or {}))
                        _raise_if_cancelled(is_cancelled)
                    except (HarnessExecutionCancelled, HarnessExecutionFenced):
                        raise
                    except Exception as exc:
                        result = {
                            "success": False,
                            "error": {
                                "code": "HARNESS_TOOL_ERROR",
                                "message": str(exc),
                            },
                        }
                    if _is_non_retryable_failure(result):
                        non_retryable_action_signatures.add(action_signature)
            bounded_result = _bounded_capability_result(tool_name, result)
            result_data = result.get("data")
            if (
                result.get("success") is True
                and isinstance(result_data, dict)
                and result_data.get("detached") is True
            ):
                capability_results.append(bounded_result)
                transcript.extend(
                    [
                        {
                            "role": "assistant",
                            "action": "tool",
                            "tool_name": tool_name,
                            "arguments": action.arguments,
                        },
                        {
                            "role": "tool",
                            "tool_name": tool_name,
                            "result": bounded_result,
                        },
                    ]
                )
                reply = str(result_data.get("user_reply") or "").strip()
                return finish(TaskExecutionResult(
                    task_frame_id=requirement.task_frame_id,
                    status="waiting_external_task",
                    reply_fragment=reply,
                    capability_results=capability_results,
                    action_count=iteration,
                    task_summary=(
                        "异步业务任务已受理，等待完成后恢复 SOP。"
                        if requirement.kind == "sop"
                        else "异步业务任务已受理，可通过任务号查询进度。"
                    ),
                    structured_result={
                        "task_id": result_data.get("task_id"),
                        "status": result_data.get("status"),
                    },
                ))
            if _is_loaded_general_skill_result(tool_name, result):
                loaded_general_skill_names.append(tool_name)
            transcript.extend(
                [
                    {
                        "role": "assistant",
                        "action": "tool",
                        "tool_name": tool_name,
                        "arguments": action.arguments,
                    },
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "result": bounded_result,
                    },
                ]
            )
            if tool_name not in {"capability_search", "capability_describe"}:
                capability_results.append(bounded_result)
            if _deadline_expired(step_deadline_monotonic):
                return finish(_step_timeout_result(
                    requirement,
                    action_count=iteration,
                    timeout_seconds=step_timeout_seconds,
                    capability_results=capability_results,
                    citations=citations,
                    evidence_results=evidence_results,
                    artifacts=artifacts,
                    trace_sink=trace_sink,
                ))
            activated_names = _activate_described_capabilities(
                requirement,
                tool_name,
                result,
            )
            allowed_names.update(activated_names)
            _extend_dict_list(artifacts, result.get("artifacts"))
            if tool_name == "knowledge_search" and bool(result.get("success")):
                if _has_usable_knowledge_evidence(result):
                    successful_knowledge_searches += 1
                requested_knowledge_ids = _string_list(
                    (action.arguments or {}).get("knowledge_base_ids")
                )
                satisfied_required_knowledge_ids.update(
                    requested_knowledge_ids or requirement.required_knowledge_base_ids
                )
            if (
                tool_name == "knowledge_search"
                and bool(result.get("success"))
                and isinstance(result.get("data"), dict)
            ):
                _extend_dict_list(citations, result.get("citations"))
                evidence_results.append(dict(result["data"]))
            else:
                _extend_dict_list(citations, result.get("citations"))
            if trace_sink:
                trace_sink(
                    "harness_tool_completed",
                    {
                        "iteration": iteration,
                        "tool_name": tool_name,
                        "success": bool(result.get("success")),
                        "error": result.get("error"),
                        "result": _trace_capability_result(
                            tool_name,
                            result,
                        ),
                    },
                )
        return finish(TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="action_budget",
            reply_fragment="当前任务已达到本轮自动执行上限，需要下一轮继续。",
            citations=citations,
            evidence_results=evidence_results,
            capability_results=capability_results,
            artifacts=artifacts,
            task_summary="Harness 达到 action budget。",
            action_count=max_actions,
            error={"code": "ACTION_BUDGET_EXHAUSTED"},
        ))


def _requirement_step_id(requirement: TaskRequirement) -> str:
    """提取 SOP 上下文里的步骤标识，依次尝试 step_id、node_id、id。"""
    step = requirement.sop_context.get("step")
    if not isinstance(step, dict):
        return ""
    for key in ("step_id", "node_id", "id"):
        value = str(step.get(key) or "").strip()
        if value:
            return value
    return ""


def _dict_items(value: object) -> list[dict[str, Any]]:
    """把任意值过滤为字典列表，非列表与非字典元素一律丢弃。"""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _activate_described_capabilities(
    requirement: TaskRequirement,
    tool_name: str,
    result: dict[str, Any],
) -> set[str]:
    """把 capability_describe 返回的新能力并入冻结清单，返回本次激活的名称。

    快照版本不一致、能力不可用或 kind 为 internal 的条目会被忽略，避免模型借
    描述动作扩大自身可调用的能力范围。
    """
    if tool_name != "capability_describe" or result.get("success") is not True:
        return set()
    data = result.get("data")
    if (
        not isinstance(data, dict)
        or str(data.get("snapshot_revision") or "")
        != requirement.capability_manifest.snapshot_revision
    ):
        return set()
    raw_descriptors = data.get("activated_capabilities")
    if not isinstance(raw_descriptors, list):
        return set()
    existing = {item.name: item for item in requirement.capability_manifest.available}
    activated: set[str] = set()
    for raw in raw_descriptors:
        if not isinstance(raw, dict):
            continue
        try:
            descriptor = CapabilityDescriptor.model_validate(raw)
        except ValidationError:
            continue
        if not descriptor.available or descriptor.kind == "internal":
            continue
        existing[descriptor.name] = descriptor
        activated.add(descriptor.name)
    requirement.capability_manifest.available = list(existing.values())
    return activated


def _is_loaded_general_skill_result(
    tool_name: str,
    result: dict[str, Any],
) -> bool:
    """判断工具结果是否为 General Skill 说明的成功加载（operation=read）。"""
    data = result.get("data")
    return (
        tool_name.startswith("general_skill.")
        and result.get("success") is True
        and isinstance(data, dict)
        and data.get("kind") == "general_skill"
        and data.get("operation") == "read"
    )


def _action_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """生成工具名与参数的稳定签名，用于识别重复的不可重试调用。"""
    return json.dumps(
        {"tool_name": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _is_non_retryable_failure(result: object) -> bool:
    """判断工具结果是否为明确标记 retryable=false 的失败。"""
    if not isinstance(result, dict) or result.get("success") is not False:
        return False
    error = result.get("error")
    return isinstance(error, dict) and error.get("retryable") is False


def _generate_harness_action_json(
    client: LLMClient,
    system_prompt: str,
    payload: dict[str, Any],
) -> Any:
    """优先调用支持动作序列的生成接口，老客户端回退到单动作生成。"""

    sequence_generator = getattr(client, "generate_json_sequence", None)
    if callable(sequence_generator):
        return sequence_generator(system_prompt, payload)
    # Test doubles and older compatible clients still expose the original method.
    return client.generate_json(system_prompt, payload)


def _harness_actions_from_raw(raw: object) -> list[HarnessAction]:
    """把模型原始输出归一化为有序动作列表。

    兼容单个动作、{"actions": [...]} 包装与裸数组；序列为空或 finish 不在末尾时
    抛 ValueError，由上层触发一次协议修复。
    """

    items: object = raw
    if isinstance(raw, dict) and set(raw) == {"actions"}:
        items = raw.get("actions")
    if not isinstance(items, list):
        return [HarnessAction.model_validate(items)]
    if not items:
        raise ValueError("Harness action sequence must not be empty.")
    actions = [HarnessAction.model_validate(item) for item in items]
    if any(action.action == "finish" for action in actions[:-1]):
        raise ValueError("A finish action must be the final item in an action sequence.")
    return actions


def _adapt_general_skill_structured_result(
    raw: object,
    *,
    loaded_general_skill_names: list[str],
) -> HarnessAction | None:
    """把仅含说明的 Skill 直接返回的业务 JSON 适配成安全的 finish 动作。

    适配只在成功加载过 General Skill 时生效，且拒绝任何自带 action/actions 字段
    的输入，因此 RFC 或 MCP 形态的对象只会作为数据返回，不会被当作工具调用执行。
    """

    if not loaded_general_skill_names or not isinstance(raw, (dict, list)):
        return None
    if isinstance(raw, dict) and ({"action", "actions"} & set(raw)):
        return None
    if isinstance(raw, list) and any(
        isinstance(item, dict) and "action" in item for item in raw
    ):
        return None
    reply_fragment = json.dumps(
        raw,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return HarnessAction(
        action="finish",
        status="completed",
        reply_fragment=reply_fragment,
        task_summary=(
            f"{loaded_general_skill_names[-1]} 已生成结构化业务结果。"
        ),
        structured_result=raw,
    )


def _missing_required_capabilities(
    requirement: TaskRequirement,
    capability_results: list[dict[str, Any]],
    satisfied_required_knowledge_ids: set[str],
) -> list[str]:
    """列出尚未成功执行的强制能力，知识库需求以 knowledge_search:<id> 形式表示。"""
    succeeded = {
        str(item.get("tool_name") or "")
        for item in capability_results
        if isinstance(item, dict) and item.get("success") is True
    }
    missing = [name for name in requirement.required_capability_names if name not in succeeded]
    for knowledge_base_id in requirement.required_knowledge_base_ids:
        if knowledge_base_id not in satisfied_required_knowledge_ids:
            missing.append(f"knowledge_search:{knowledge_base_id}")
    return missing


def _has_usable_knowledge_evidence(result: dict[str, Any]) -> bool:
    """判断知识检索结果是否带有可用证据（引用列表或 evidence_pack）。"""
    citations = result.get("citations")
    if isinstance(citations, list) and any(isinstance(item, dict) for item in citations):
        return True
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    evidence = data.get("evidence_pack")
    return isinstance(evidence, list) and any(isinstance(item, dict) for item in evidence)


def _finish_result(
    requirement: TaskRequirement,
    action: HarnessAction,
    citations: list[dict[str, Any]],
    evidence_results: list[dict[str, Any]],
    capability_results: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    *,
    action_count: int,
) -> TaskExecutionResult:
    """把模型 finish 动作转换为 TaskExecutionResult。

    next_step_id 必须落在允许的迁移目标内，否则清空；处于 handoff 节点且未指定
    下一步时，状态归一化为 handoff。
    """
    status = action.status or "completed"
    step = requirement.sop_context.get("step") if requirement.sop_context else None
    step_type = str(step.get("type") or "").strip() if isinstance(step, dict) else ""
    allowed_next_steps = {
        str(item.get("next_node_id") or "").strip()
        for item in requirement.allowed_transitions
        if isinstance(item, dict) and item.get("next_node_id")
    }
    next_step_id = str(action.next_step_id or "").strip() or None
    if next_step_id and next_step_id not in allowed_next_steps:
        next_step_id = None
    # Merely allowing the optional handoff_human action must not turn an otherwise
    # successful SOP step into a handoff. A dedicated terminal handoff node owns
    # the routing decision, so normalize model failure/completion wording to the
    # durable handoff state instead of telling the user that routing is unavailable.
    if step_type == "handoff" and next_step_id is None:
        status = "handoff"
    return TaskExecutionResult(
        task_frame_id=requirement.task_frame_id,
        status=status,
        reply_fragment=action.reply_fragment.strip(),
        slot_updates=strip_router_generated_message_slots(action.slot_updates),
        next_step_id=next_step_id,
        citations=citations,
        evidence_results=evidence_results,
        capability_results=capability_results,
        artifacts=artifacts,
        task_summary=action.task_summary.strip(),
        action_count=action_count,
        structured_result=action.structured_result,
    )


def _extend_dict_list(target: list[dict[str, Any]], value: object) -> None:
    """把 value 中的字典元素追加到目标列表，其他类型静默忽略。"""
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            target.append(item)


def _raise_if_cancelled(check: CancellationCheck | None) -> None:
    """取消检查命中时抛 HarnessExecutionCancelled，中断当前执行。"""
    if check is not None and check():
        raise HarnessExecutionCancelled("Harness execution was cancelled.")


def _deadline_expired(deadline_monotonic: float | None) -> bool:
    """判断单调时钟是否已到达单步截止时刻，未设置截止时恒为 False。"""
    return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic


def _deadline_llm_client(
    model_config: ModelConfig,
    deadline_monotonic: float | None,
) -> LLMClient:
    """构造带剩余时长约束的 LLM 客户端，避免单次调用越过单步截止时刻。"""
    if deadline_monotonic is None:
        return LLMClient(model_config)
    remaining = max(deadline_monotonic - time.monotonic(), 0.1)
    configured = getattr(model_config, "timeout_seconds", None)
    timeout_seconds = min(float(configured), remaining) if configured else remaining
    if is_dataclass(model_config):
        limited_config = replace(model_config, timeout_seconds=timeout_seconds)
    else:
        limited_config = model_config.model_copy(
            update={"timeout_seconds": timeout_seconds}
        )
    return LLMClient(limited_config)


def _step_timeout_result(
    requirement: TaskRequirement,
    *,
    action_count: int,
    timeout_seconds: int | None,
    capability_results: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    evidence_results: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    trace_sink: TraceSink | None,
) -> TaskExecutionResult:
    """构造单步超时的失败结果，并上报 harness_step_timeout 追踪事件。"""
    limit_text = f"{timeout_seconds} 秒" if timeout_seconds else "配置的时间"
    error = {
        "code": "SOP_STEP_TIMEOUT",
        "message": f"当前 SOP 单步运行超过 {limit_text}，已停止继续执行。",
        "timeout_seconds": timeout_seconds,
    }
    if trace_sink:
        trace_sink(
            "harness_step_timeout",
            {
                "timeout_seconds": timeout_seconds,
                "action_count": max(0, action_count),
                "error": error,
            },
        )
    return TaskExecutionResult(
        task_frame_id=requirement.task_frame_id,
        status="failed",
        reply_fragment=error["message"],
        citations=citations,
        evidence_results=evidence_results,
        capability_results=capability_results,
        artifacts=artifacts,
        task_summary="SOP 单步运行超时。",
        action_count=max(0, action_count),
        error=error,
    )


def _bounded_capability_result(
    tool_name: str,
    result: dict[str, Any],
    *,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """构造写入对话与检查点的能力结果，超长时截断为预览并标记 truncated。"""
    payload = {
        "tool_name": tool_name,
        "success": bool(result.get("success")),
        "data": result.get("data"),
        "error": result.get("error"),
    }
    if isinstance(result.get("mcp_app"), dict):
        payload["mcp_app"] = result["mcp_app"]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    if len(serialized) <= max_chars:
        return payload
    truncated = {
        "tool_name": tool_name,
        "success": bool(result.get("success")),
        "truncated": True,
        "preview": serialized[:max_chars],
        "error": result.get("error"),
    }
    if isinstance(result.get("mcp_app"), dict):
        truncated["mcp_app"] = result["mcp_app"]
    return truncated


def _transcript_for_model(
    transcript: list[dict[str, Any]],
    *,
    keep_recent_entries: int = 6,
) -> list[dict[str, Any]]:
    """把持久的工具历史投影成有界的模型上下文。

    旧工具结果压缩为回执，只保留最新若干条交互；每个 General Skill 的最新说明
    条目始终保留，因为它定义了当前 AgentLoop 正在遵循的流程。
    """

    cutoff = max(0, len(transcript) - keep_recent_entries)
    latest_skill_instruction_index: dict[str, int] = {}
    for index, entry in enumerate(transcript):
        if _is_general_skill_instruction_entry(entry):
            latest_skill_instruction_index[str(entry.get("tool_name") or "")] = index

    projected: list[tuple[int, dict[str, Any]]] = []
    for index, entry in enumerate(transcript):
        copied = dict(entry)
        skill_name = str(copied.get("tool_name") or "")
        is_latest_skill_instruction = (
            _is_general_skill_instruction_entry(copied)
            and latest_skill_instruction_index.get(skill_name) == index
        )
        if _is_general_skill_instruction_entry(copied) and not is_latest_skill_instruction:
            continue
        if (
            copied.get("role") == "assistant"
            and skill_name.startswith("general_skill.")
            and latest_skill_instruction_index.get(skill_name) != index + 1
        ):
            continue
        if index >= cutoff or is_latest_skill_instruction:
            projected.append((index, copied))
            continue
        if copied.get("role") == "tool" and isinstance(copied.get("result"), dict):
            result = dict(copied["result"])
            receipt: dict[str, Any] = {
                key: result.get(key)
                for key in ("tool_name", "success", "error", "truncated")
                if key in result
            }
            data = result.get("data")
            if isinstance(data, dict):
                receipt["data"] = {
                    key: data.get(key)
                    for key in (
                        "path",
                        "source_path",
                        "destination_path",
                        "sha256",
                        "size",
                        "offset",
                        "next_offset",
                        "continuation_token",
                        "eof",
                        "status",
                        "exit_code",
                    )
                    if data.get(key) is not None
                }
            receipt["history_receipt"] = _history_receipt(result)
            copied["result"] = receipt
        elif copied.get("role") == "assistant":
            arguments = copied.get("arguments")
            if isinstance(arguments, dict):
                copied["arguments"] = _compact_old_arguments(arguments)
        projected.append((index, copied))

    if len(projected) <= 40:
        return [entry for _index, entry in projected]

    # A long-lived non-SOP AgentLoop may exceed the transcript entry cap. Keep
    # the latest loaded instruction for every Skill, then spend the remaining
    # budget on the newest ordinary interactions.
    essential_indexes = {
        index
        for index, entry in projected
        if _is_general_skill_instruction_entry(entry)
    }
    remaining = max(0, 40 - len(essential_indexes))
    ordinary_indexes = [
        index for index, _entry in projected if index not in essential_indexes
    ][-remaining:]
    selected_indexes = essential_indexes | set(ordinary_indexes)
    return [
        entry for index, entry in projected if index in selected_indexes
    ]


def _is_general_skill_instruction_entry(entry: dict[str, Any]) -> bool:
    """判断条目是否为一条成功加载的 General Skill 说明（tool 角色且带结果）。"""
    if entry.get("role") != "tool" or not str(entry.get("tool_name") or "").startswith(
        "general_skill."
    ):
        return False
    result = entry.get("result")
    return isinstance(result, dict) and bool(result.get("success"))


def _history_receipt(value: Any) -> dict[str, Any]:
    """为被省略的历史内容生成回执：原始字符数与内容摘要，便于模型确认已裁剪。"""
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "omitted_chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _compact_old_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """参数较短时原样返回，超过 1000 字符则替换为历史回执以压缩上下文。"""
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) <= 1_000:
        return dict(arguments)
    return {"history_receipt": _history_receipt(arguments)}


def _string_list(value: object) -> list[str]:
    """把任意值规整为非空去空白的字符串列表，非列表输入返回空列表。"""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _trace_capability_result(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """裁剪 Trace 中的能力结果，General Skill 只保留少量关键字段。"""
    trace_result = dict(result)
    data = trace_result.get("data")
    if tool_name.startswith("general_skill.") and isinstance(data, dict):
        trace_result["data"] = {
            key: data.get(key)
            for key in (
                "kind",
                "slug",
                "operation",
                "reply",
                "structured_result",
            )
            if data.get(key) not in (None, "", [], {})
        }
    return _bounded_capability_result(
        tool_name,
        trace_result,
        max_chars=4_000,
    )
