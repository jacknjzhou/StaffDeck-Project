from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.cancellation import is_chat_turn_cancelled
from app.core.capability_discovery import project_capability_manifest
from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_agent import (
    HarnessExecutionCancelled,
    HarnessExecutionFenced,
    HarnessTaskAgent,
)
from app.core.harness_attachments import (
    materialize_task_attachments,
    validated_task_image_payloads,
)
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.published_deliverables import list_published_deliverables
from app.core.harness_session_lease import (
    HarnessSessionLeaseLost,
    HarnessSessionLeaseStore,
    HarnessSessionLeaseToken,
)
from app.core.harness_session_lock import (
    acquire_harness_session,
    release_harness_session,
)
from app.core.harness_turn_store import HarnessTurnStore
from app.core.slash_commands import (
    SlashCommandError,
    SlashCommandSelection,
    build_slash_turn_plan,
    force_capability_for_requirement,
    parse_slash_command,
    resolve_capability,
    slash_command_message,
)
from app.core.slot_hydration_policy import SlotHydrationPolicy
from app.core.task_frame_store import (
    TaskFrameClaimConflict,
    TaskFrameStore,
    planned_frame_from_record,
)
from app.core.task_request_compiler import (
    TaskExecutionResult,
    TaskRequestCompiler,
)
from app.core.turn_planner import TurnPlanner, turn_plan_router_decision
from app.db.models import (
    ChatSession,
    ExternalBusinessTask,
    HarnessRunRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    Message,
    Skill,
    Team,
)
from app.knowledge.citations import compact_knowledge_citation_labels
from app.memory.service import memory_read
from app.session.helpers import public_session
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    StepAgentResult,
    TurnPlan,
)
from app.skills.nesting import discoverable_sops, expand_visible_sops
from app.tools.external_tasks import update_external_task_checkpoint


def _turn_skill_projection(
    source_skills: list[Skill],
    *,
    interaction_mode: str,
) -> tuple[list[Skill], list[Skill]]:
    """为当前交互模式投影出可执行 SOP 与可路由 SOP 两份列表。

    团队 TL 会话已独占一个 ChatSession，隐藏 leader 的 SOP 只会禁掉合法工作，
    对状态隔离并无必要。
    """

    _ = interaction_mode
    skills = expand_visible_sops(source_skills)
    return skills, discoverable_sops(skills)


def _turn_planner_message(
    request: ChatTurnRequest,
) -> str:
    """只把用户消息交给 Planner，避免服务端执行上下文混入任务需求。"""

    return request.message


def _apply_forced_sop_snapshot(
    source_skills: list[Skill],
    forced_sop_id: str | None,
    snapshot: dict[str, Any] | None,
) -> list[Skill]:
    """用定时任务保存的不可变快照替换当前可访问的指定 SOP。

    快照与指定 SOP 不一致或内容非法时抛 SlashCommandError；SOP 已不可见时原样返回
    列表，让后续解析照常报能力不可用，历史快照不得复活已解绑的 SOP。
    """

    target = str(forced_sop_id or "").strip()
    if not target or not snapshot:
        return source_skills
    if str(snapshot.get("skill_id") or "").strip() != target:
        raise SlashCommandError(
            "FORCED_SOP_SNAPSHOT_INVALID",
            "定时任务保存的 SOP 快照与指定 SOP 不一致。",
        )
    content = snapshot.get("content_json")
    if not isinstance(content, dict):
        raise SlashCommandError(
            "FORCED_SOP_SNAPSHOT_INVALID",
            "定时任务保存的 SOP 快照内容无效。",
        )
    current = next((skill for skill in source_skills if skill.skill_id == target), None)
    if current is None:
        # Keep the normal capability-access error from resolve_sop. A historical
        # snapshot must never resurrect an SOP that is no longer bound/visible.
        return source_skills
    pinned = Skill(
        id=current.id,
        tenant_id=current.tenant_id,
        skill_id=current.skill_id,
        version=str(snapshot.get("version") or current.version),
        name=str(snapshot.get("name") or current.name),
        business_domain=(
            str(snapshot.get("business_domain"))
            if snapshot.get("business_domain") is not None
            else current.business_domain
        ),
        description=(
            str(snapshot.get("description"))
            if snapshot.get("description") is not None
            else current.description
        ),
        content_json=deepcopy(content),
        status="published",
        created_at=current.created_at,
        updated_at=current.updated_at,
    )
    if hasattr(current, "agent_branch_meta"):
        object.__setattr__(pinned, "agent_branch_meta", getattr(current, "agent_branch_meta"))
    return [pinned if skill.skill_id == target else skill for skill in source_skills]


def _turn_slash_selection(request: ChatTurnRequest) -> SlashCommandSelection | None:
    """统一解析用户斜杠指令与服务端固定的定时 SOP 选择。

    两者同时出现、或定时任务文本里夹带斜杠指令时抛 SlashCommandError。
    """

    selection = parse_slash_command(request.message)
    forced_sop_id = str(request.forced_sop_id or "").strip()
    if forced_sop_id:
        if selection is not None:
            raise SlashCommandError(
                "FORCED_SOP_COMMAND_CONFLICT",
                "内部指定的 SOP 不能与用户斜杠指令同时使用。",
            )
        return SlashCommandSelection(
            kind="sop",
            target=forced_sop_id,
            prompt=request.message,
            raw=f"/sop {forced_sop_id}",
        )
    if selection and request.interaction_mode == "scheduled_task":
        raise SlashCommandError(
            "SLASH_COMMAND_MODE_CONFLICT",
            "定时任务执行不能从任务文本解析斜杠指令，请使用结构化 SOP 选择。",
        )
    return selection


class HarnessV2Engine:
    """外层 Planner + 持久化 TaskFrame 调度器 + 隔离的 Harness 执行。"""

    def __init__(self, owner: Any) -> None:
        """保存所属会话运行时，并初始化 Planner、编译器与各持久化存储。"""
        self.owner = owner
        self.db = owner.db
        self.events = owner.events
        self.planner = TurnPlanner()
        self.compiler = TaskRequestCompiler()
        self.manifests = CapabilityManifestBuilder(self.db)
        self.task_agent = HarnessTaskAgent()
        self.store = TaskFrameStore(self.db)
        self.turn_store = HarnessTurnStore(self.db)
        self.session_leases = HarnessSessionLeaseStore(self.db)
        self.turn_record: HarnessTurnRecord | None = None
        self.session_lease: HarnessSessionLeaseToken | None = None
        self.user_message_id: str | None = None
        self.current_source_turn_id: str | None = None
        self.session: ChatSession | None = None
        self.active_frame_id: str | None = None
        self.active_frame_lease_owner: str | None = None
        self.active_frame_attempt_no: int | None = None
        self.active_run_id: str | None = None
        self.slash_command: SlashCommandSelection | None = None
        self._session_lock: Any | None = None
        self._session_lock_id: str | None = None

    def run(self, request: ChatTurnRequest) -> ChatTurnResponse:
        """执行一轮 Harness 对话：规划、落盘 TaskFrame、逐帧执行并合成回复。

        先用 client_turn_id 做幂等认领，命中重放直接返回上次结果；随后完成斜杠指令
        解析、能力投影、规划落盘、帧执行与引用/产物归并，最后写回用户可见回复。
        会话租约丢失抛 HarnessExecutionFenced，用户取消抛 HarnessExecutionCancelled。
        """
        session_request = _with_recoverable_first_session(request)
        if session_request.session_id:
            self._session_lock_id = session_request.session_id
            self._session_lock = acquire_harness_session(
                session_request.session_id
            )
        session = self._get_or_create_session(session_request)
        self.session = session
        if self._session_lock is None:
            self._session_lock_id = session.id
            self._session_lock = acquire_harness_session(session.id)
        self.session_lease = self.session_leases.acquire(session)
        turn_claim = self.turn_store.claim(session, request)
        self.turn_record = turn_claim.record
        if turn_claim.replay is not None:
            return turn_claim.replay
        self.owner._mark_session_running(session)
        user_message = self.owner._append_message(
            request.tenant_id,
            session.id,
            "user",
            request.message,
            metadata=self.owner._user_message_metadata(request),
        )
        self.user_message_id = user_message.id
        self.current_source_turn_id = user_message.id
        self.turn_store.bind_user_message(
            self.turn_record,
            user_message.id,
        )
        bind_turn = getattr(self.events, "bind_turn", None)
        if callable(bind_turn):
            bind_turn(user_message.id, request.client_turn_id)
        self.events.record(
            request.tenant_id,
            session.id,
            "user_message_received",
            {
                "message_id": user_message.id,
                "turn_id": user_message.id,
                "client_turn_id": request.client_turn_id,
                "message": request.message,
                "channel": request.channel,
                "user_id": request.user_id,
                "execution_engine": "harness_v2",
                **(
                    {"message_visibility": request.message_visibility}
                    if request.message_visibility != "visible"
                    else {}
                ),
            },
        )

        self.slash_command = _turn_slash_selection(request)
        execution_message = (
            slash_command_message(self.slash_command)
            if self.slash_command
            else request.message
        )
        if request.context_injection:
            execution_message = f"{request.context_injection.rstrip()}\n\n{execution_message}"
        execution_request = request.model_copy(
            update={"message": execution_message, "context_injection": None}
        )

        model_config = self.owner._get_request_model(request, session.agent_id)
        if model_config is None:
            raise RuntimeError("没有默认模型配置。")
        source_skills = self.owner._list_published_skills(
            request.tenant_id, session.agent_id
        )
        source_skills = _apply_forced_sop_snapshot(
            source_skills,
            request.forced_sop_id,
            request.forced_sop_snapshot,
        )
        # Team TL conversations use a dedicated ChatSession, so the leader can
        # safely execute their own SOPs without mutating a personal chat. Keep
        # the same published/discoverable SOP boundary in every interaction
        # mode; team orchestration remains an additional conversation concern,
        # not a reason to hide the leader's executable workflow.
        skills, routing_skills = _turn_skill_projection(
            source_skills,
            interaction_mode=request.interaction_mode,
        )
        self.owner._drop_unavailable_skill_state(
            request.tenant_id, session, skills
        )
        memory_context = [
            memory_read(row)
            for row in self.owner.memory.context_memories(
                request.tenant_id,
                request.user_id,
                agent_id=session.agent_id,
            )
        ]
        if memory_context:
            self.events.record(
                request.tenant_id,
                session.id,
                "memory_recalled",
                {
                    "memories": memory_context,
                    "execution_engine": "harness_v2",
                },
            )
        self.db.commit()
        self.db.refresh(session)
        self._raise_if_cancelled(request, session)
        conversation_context = self.owner._conversation_context(
            session, model_config=model_config
        )

        self._renew_session_lease()
        planner_state = self.store.planner_state(session)
        team_context = request.team_context
        team: Team | None = None
        if request.interaction_mode == "team_tl" and session.team_id:
            team = self.db.get(Team, session.team_id)
            if team is not None and team_context is None:
                from app.teams.wakeup import build_team_planner_context

                team_context = build_team_planner_context(self.db, team)
        if self.slash_command:
            if self.slash_command.kind in {"skill", "tool"}:
                direct_manifest = self.manifests.build(
                    request.tenant_id,
                    session.agent_id,
                    None,
                    None,
                )
                resolve_capability(self.slash_command, direct_manifest)
            plan = build_slash_turn_plan(
                self.slash_command,
                execution_request.message,
                session,
                routing_skills,
                planner_state,
            )
        else:
            plan = self.planner.plan(
                _turn_planner_message(request),
                session,
                routing_skills,
                model_config,
                deepcopy(conversation_context),
                memory_context,
                planner_state,
                interaction_mode=request.interaction_mode,
                team_context=team_context,
            )
        ready_frame = next(
            (
                item
                for item in self.store.planner_state(session)
                if item.get("status") == "ready_to_resume"
                and item.get("kind") == "sop"
            ),
            None,
        )
        if ready_frame is not None and plan.decision not in {
            "complete_task",
            "handoff_human",
        }:
            resume = planned_frame_from_record(
                self.db.exec(
                    select(HarnessTaskFrameRecord).where(
                        HarnessTaskFrameRecord.session_id == session.id,
                        HarnessTaskFrameRecord.task_id == ready_frame["task_id"],
                    )
                ).one()
            )
            plan = plan.model_copy(
                update={
                    "decision": "switch_to_pending",
                    "selected_task_id": resume.task_id,
                    "target_skill_id": resume.target_skill_id,
                    "target_step_id": resume.target_step_id,
                    "task_frames": [resume],
                    "task_updates": [],
                }
            )
        self._renew_session_lease()
        self._raise_if_cancelled(request, session)
        slot_hydration = SlotHydrationPolicy.hydrate_plan(
            session,
            plan,
            skills,
            memory_context,
        )
        if slot_hydration:
            self.events.record(
                request.tenant_id,
                session.id,
                "slots_hydrated",
                {
                    **slot_hydration,
                    "source": "memory",
                    "turn_id": user_message.id,
                    "execution_engine": "harness_v2",
                },
            )
        router_decision = turn_plan_router_decision(plan)
        self.events.record(
            request.tenant_id,
            session.id,
            "turn_plan_created",
            {
                **plan.model_dump(mode="json"),
                "turn_id": user_message.id,
                "execution_engine": "harness_v2",
            },
        )
        # Keep the old public trace event while making the new planner explicit.
        self.events.record(
            request.tenant_id,
            session.id,
            "router_decision_created",
            {
                **router_decision.model_dump(mode="json"),
                "turn_id": user_message.id,
                "execution_engine": "harness_v2",
            },
        )
        team_publish_result = None
        remote_frames = [
            frame for frame in plan.task_frames if frame.execution_target == "team_member"
        ]
        if remote_frames:
            if request.interaction_mode != "team_tl" or team is None:
                raise RuntimeError("团队成员 TaskFrame 缺少可信团队上下文。")
            from app.teams.wakeup import publish_team_planner_frames

            team_publish_result = publish_team_planner_frames(
                self.db,
                team=team,
                session=session,
                source_turn_id=user_message.id,
                created_by_user_id=request.user_id,
                frames=remote_frames,
            )
            if team_publish_result is None:
                raise RuntimeError("团队成员 TaskFrame 未能持久化，已停止本轮虚假分发。")
            plan = plan.model_copy(
                update={
                    "task_frames": [
                        frame
                        for frame in plan.task_frames
                        if frame.execution_target != "team_member"
                    ]
                }
            )
        if plan.decision == "complete_task":
            active_task_frame_id = self.store.active_task_frame_id(session)
            self.store.complete_active_frame(
                session,
                reason=plan.reason or "用户结束了当前任务。",
                task_id=plan.selected_task_id,
            )
            if (
                session.active_skill_id
                and (
                    not plan.selected_task_id
                    or plan.selected_task_id == active_task_frame_id
                )
            ):
                self.owner.runtime.complete_current_skill(session)
            self.events.record(
                request.tenant_id,
                session.id,
                "task_frame_completed",
                {
                    "selected_task_id": plan.selected_task_id,
                    "reason": plan.reason,
                    "execution_engine": "harness_v2",
                },
            )

        pre_turn_state = _session_state(session)
        records = self.store.persist_plan(session, user_message.id, plan)
        known_record_ids = {row.task_id for row in records}
        records.extend(
            self.store.ready_dependency_frames(
                session,
                exclude_task_ids=known_record_ids,
            )
        )
        records = _dependency_order(records)
        known_record_ids = {row.task_id for row in records}
        self.db.commit()
        self.db.refresh(session)

        remaining_turn_actions = max(
            0,
            int(
                self.owner._get_agent_loop_max_actions(
                    request.tenant_id, session.agent_id
                )
            ),
        )
        execution_payloads: list[dict[str, object]] = []
        execution_results: list[TaskExecutionResult] = []
        last_step_result = StepAgentResult()
        last_skill: Skill | None = None

        for record_index, row in enumerate(records):
            self._renew_session_lease()
            if remaining_turn_actions <= 0:
                deferred_rows = records[record_index:]
                self.store.defer_for_action_budget(deferred_rows)
                self.events.record(
                    request.tenant_id,
                    session.id,
                    "turn_action_budget_exhausted",
                    {
                        "deferred_task_frame_ids": [
                            item.task_id for item in deferred_rows
                        ],
                        "execution_engine": "harness_v2",
                    },
                )
                self.db.commit()
                break
            self._raise_if_cancelled(request, session)
            if not self.store.dependencies_satisfied(row, records):
                self.store.defer_for_dependencies(row)
                waiting = TaskExecutionResult(
                    task_frame_id=row.task_id,
                    status="blocked",
                    reply_fragment="前置任务完成后将自动继续该任务。",
                    task_summary="TaskFrame 正在等待前置任务完成。",
                    error={"code": "DEPENDENCY_WAITING"},
                )
                self.events.record(
                    request.tenant_id,
                    session.id,
                    "task_frame_dependency_waiting",
                    {
                        "task_frame_id": row.task_id,
                        "depends_on_task_ids": list(row.depends_on_json or []),
                        "execution_engine": "harness_v2",
                    },
                )
                execution_results.append(waiting)
                execution_payloads.append(
                    _response_task_payload(
                        row,
                        waiting,
                        None,
                        StepAgentResult(reply=waiting.reply_fragment),
                    )
                )
                continue

            frame = planned_frame_from_record(row)
            active_skill = self._activate_frame(session, row, skills)
            if frame.kind == "sop" and active_skill is None:
                failed = TaskExecutionResult(
                    task_frame_id=row.task_id,
                    status="failed",
                    reply_fragment="对应的 SOP 当前不可用。",
                    task_summary="SOP 在执行前已下线或解绑。",
                    error={"code": "SOP_NOT_AVAILABLE"},
                )
                self.store.finish_frame(
                    row,
                    status="failed",
                    step_id=row.step_id,
                    slots=dict(row.slots_json or {}),
                    result=failed.model_dump(mode="json"),
                )
                execution_results.append(failed)
                execution_payloads.append(
                    _response_task_payload(
                        row,
                        failed,
                        None,
                        StepAgentResult(reply=failed.reply_fragment),
                    )
                )
                continue

            last_skill = active_skill or last_skill
            combined, step_result = self._run_frame(
                execution_request,
                session,
                row,
                frame,
                active_skill,
                model_config,
                memory_context,
                [
                    *self.store.dependency_results(row),
                    *self.store.referenced_session_results(row),
                ],
                remaining_turn_actions,
            )
            remaining_turn_actions = max(
                0,
                remaining_turn_actions - max(1, combined.action_count),
            )
            last_step_result = step_result
            execution_results.append(combined)
            execution_payloads.append(
                _response_task_payload(
                    row,
                    combined,
                    active_skill,
                    step_result,
                )
            )
            if row.status == "completed":
                released = self.store.ready_dependency_frames(
                    session,
                    exclude_task_ids=known_record_ids,
                )
                if released:
                    records.extend(released)
                    known_record_ids.update(
                        item.task_id for item in released
                    )
                    self.events.record(
                        request.tenant_id,
                        session.id,
                        "task_frame_dependencies_released",
                        {
                            "completed_task_frame_id": row.task_id,
                            "released_task_frame_ids": [
                                item.task_id for item in released
                            ],
                            "execution_engine": "harness_v2",
                        },
                    )

        self._restore_visible_active_frame(
            session,
            records,
            pre_turn_state,
        )
        self.store.project_session(session)
        self.db.commit()
        self.db.refresh(session)
        self._raise_if_cancelled(request, session)
        citations = _globalize_citations(execution_results)
        for payload, result in zip(
            execution_payloads,
            execution_results,
            strict=False,
        ):
            payload["knowledge_citations"] = list(result.citations)
        _inject_handoff_context(self.db, session, execution_payloads, execution_results, request)

        # ``last_skill`` is the execution-expanded parent graph. Prefer it so a
        # nested SOP's response rules remain available after the child graph
        # reaches a terminal node. Falling back to the stored row is only
        # needed for turns that did not execute a TaskFrame.
        response_skill = last_skill or self.owner._get_active_skill(
            request.tenant_id, session.active_skill_id, session.agent_id
        )
        self._renew_session_lease()
        reply = _single_task_reply(execution_results)
        if team_publish_result is not None and not execution_results:
            task_count = len(team_publish_result.task_ids)
            reply = (
                f"已完成 {task_count} 个团队任务的拆分与派发。"
            )
        is_handoff = any(result.status == "handoff" for result in execution_results)
        if is_handoff:
            # Handoff is now queued for a human; do not let the execution model
            # describe the missing native channel as if the request failed.
            reply = "已为你提交人工处理请求，请稍候，工作人员会尽快回复。"
            if self.owner.stream_sink is not None:
                for chunk in self.owner.response_generator.chunk_text(reply):
                    self.owner.stream_sink.on_delta(chunk)
        elif reply is None:
            response_args = (
                execution_request.message,
                session,
                response_skill,
                router_decision,
                last_step_result,
                None,
                model_config,
                self.owner._get_persona_prompt(request.tenant_id, session.agent_id),
                memory_context,
                conversation_context,
                execution_payloads,
            )
            if self.owner.stream_sink is not None:
                parts: list[str] = []
                for chunk in self.owner.response_generator.generate_stream(*response_args):
                    parts.append(chunk)
                    self.owner.stream_sink.on_delta(chunk)
                reply = "".join(parts).strip()
            else:
                reply = self.owner.response_generator.generate(*response_args)
        elif self.owner.stream_sink is not None:
            for chunk in self.owner.response_generator.chunk_text(reply):
                self.owner.stream_sink.on_delta(chunk)
        self._renew_session_lease()
        reply, citations = compact_knowledge_citation_labels(reply, citations)
        artifacts = _aggregate_artifacts(execution_results)
        assistant_metadata: dict[str, Any] = {
            "execution_engine": "harness_v2",
            "task_frame_ids": [row.task_id for row in records],
        }
        if team_publish_result is not None:
            assistant_metadata["team_run_id"] = team_publish_result.run_id
            assistant_metadata["team_task_ids"] = list(team_publish_result.task_ids)
            assistant_metadata["team_progress"] = {
                "phase": "collecting",
                "completed_tasks": 0,
                "total_tasks": len(team_publish_result.task_ids),
                "status_text": "正在等待成员回复",
            }
        if request.client_turn_id:
            assistant_metadata["client_turn_id"] = request.client_turn_id
        if request.message_visibility != "visible":
            assistant_metadata["message_visibility"] = request.message_visibility
        if citations:
            assistant_metadata["knowledge_citations"] = citations
        if artifacts:
            assistant_metadata["harness_artifacts"] = artifacts
        if self.slash_command:
            assistant_metadata["slash_command"] = self.slash_command.model_dump(
                mode="json"
            )
        # Cancellation and normal projection compete for this durable receipt.
        # Only the winner may append a terminal assistant message.
        self._raise_if_cancelled(request, session)
        if self.owner.stream_sink is not None:
            self.owner.stream_delivery_succeeded = self.owner.stream_sink.finish()
        self.turn_store.begin_completion(self.turn_record)
        reply = self.owner._finalize_turn(
            session,
            request.tenant_id,
            reply,
            last_step_result,
            request.message,
            user_message_id=user_message.id,
            assistant_metadata_override=assistant_metadata,
        )
        self.db.commit()
        self.db.refresh(session)
        if request.message_visibility == "visible":
            self.owner._enqueue_memory_capture(
                request,
                session,
                last_step_result,
                None,
                model_config,
            )
        response = ChatTurnResponse(
            reply=reply,
            session_id=session.id,
            router_decision=router_decision,
            step_result=last_step_result,
            tool_result=None,
            session_state=public_session(session),
        )
        self.turn_store.complete(self.turn_record, response)
        return response

    def close(self) -> None:
        """释放本轮会话租约并归还进程内会话锁，由 turn 收尾路径调用。"""
        session_id = str(self._session_lock_id or "")
        try:
            self.session_leases.release(self.session_lease)
        finally:
            self.session_lease = None
            release_harness_session(session_id, self._session_lock)
            self._session_lock = None
            self._session_lock_id = None

    def _get_or_create_session(
        self,
        request: ChatTurnRequest,
    ) -> ChatSession:
        """取出或创建本轮的 ChatSession，具体创建逻辑委托给所属运行时。"""
        return get_or_create_harness_session(self.owner, request)

    def _renew_session_lease(self) -> None:
        """续期会话租约与 turn 记录并提交，避免长回合被判定为租约过期。"""
        self.session_leases.renew(self.session_lease)
        self.turn_store.renew(self.turn_record)
        self.db.commit()

    def _renew_execution_leases(
        self,
        row: HarnessTaskFrameRecord,
    ) -> None:
        """续期会话、turn 与正在执行的 TaskFrame 三类租约。

        缺少帧租约信息，或续期时发现租约丢失/被抢占，则抛 HarnessExecutionFenced。
        """
        lease_owner = str(self.active_frame_lease_owner or "")
        attempt_no = self.active_frame_attempt_no
        if not lease_owner or attempt_no is None:
            raise HarnessExecutionFenced(
                "Harness TaskFrame lease token is missing."
            )
        try:
            self.session_leases.renew(self.session_lease)
            self.turn_store.renew(self.turn_record)
            self.store.renew_running_lease(
                row,
                lease_owner=lease_owner,
                attempt_no=attempt_no,
            )
        except (HarnessSessionLeaseLost, TaskFrameClaimConflict) as exc:
            raise HarnessExecutionFenced(str(exc)) from exc
        self.db.commit()

    def _run_frame(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
        row: HarnessTaskFrameRecord,
        frame: Any,
        active_skill: Skill | None,
        model_config: Any,
        memory_context: list[dict[str, object]],
        prior_frame_results: list[dict[str, Any]],
        max_actions: int,
    ) -> tuple[TaskExecutionResult, StepAgentResult]:
        """在独立 Harness run 内推进单个 TaskFrame，直到步骤终态或动作预算耗尽。

        SOP 帧一轮可跨多个节点：每步重新编译需求、续租、写 trace 并保存 AgentLoop
        检查点；若下一步失败但已有提交过的节点结果，则降级为 action_budget 并把帧
        重新排队。返回合并后的执行结果与最后一步的 StepAgentResult。
        """
        self.store.mark_running(row)
        agent_loop = self.store.ensure_agent_loop(row)
        loop_checkpoint = dict(agent_loop.checkpoint_json or {})
        self.active_frame_id = row.id
        self.active_frame_lease_owner = row.lease_owner
        self.active_frame_attempt_no = row.attempt_no
        attachment_descriptors = materialize_task_attachments(
            request.attachments,
            tenant_id=request.tenant_id,
            session_id=session.id,
            task_frame_id=row.task_id,
            user_id=request.user_id or "",
            db=self.db,
        )
        image_payloads = validated_task_image_payloads(request.attachments)
        published_deliverables = list_published_deliverables(
            self.db,
            tenant_id=request.tenant_id,
            session_id=session.id,
            exclude_task_frame_id=row.task_id,
        )
        step_timeout_seconds = (
            _skill_step_timeout_seconds(active_skill)
            if frame.kind == "sop"
            else None
        )
        self.events.record(
            request.tenant_id,
            session.id,
            "task_frame_started",
            {
                "task_frame_id": row.task_id,
                "kind": row.kind,
                "skill_id": row.skill_id,
                "skill_name": active_skill.name if active_skill is not None else None,
                "step_id": row.step_id,
                "step_timeout_seconds": step_timeout_seconds,
                "harness_max_actions": max_actions,
                "agent_loop_id": agent_loop.id,
                "agent_loop_kind": agent_loop.kind,
                "execution_engine": "harness_v2",
            },
        )
        remaining_actions = max_actions
        results: list[TaskExecutionResult] = []
        last_step_result = StepAgentResult()
        run: HarnessRunRecord | None = None

        while remaining_actions > 0:
            self._raise_if_cancelled(request, session)
            step_deadline_monotonic = (
                time.monotonic() + step_timeout_seconds
                if step_timeout_seconds is not None
                else None
            )
            frame.target_step_id = row.step_id or session.active_step_id
            manifest = self.manifests.build(
                request.tenant_id,
                session.agent_id,
                active_skill,
                frame.target_step_id,
            )
            # Keep the complete frozen manifest server-side for authorization,
            # while compiling the TaskRequirement only from the safe model
            # projection. capability_describe can activate schemas later.
            model_manifest = project_capability_manifest(manifest)
            requirement = self.compiler.compile(
                frame,
                session,
                active_skill,
                model_manifest,
                memory_context,
                [
                    *prior_frame_results,
                    *[_prior_result(item) for item in results],
                ],
                attachment_descriptors,
                published_deliverables,
                # 驱动本次执行的是当前这条用户消息。长驻 SOP 帧跨回合恢复
                # 时若改用创建该帧的老消息，会顶掉用户的最新回复（真实案例：
                # 「提交」被首句需求顶掉，恢复的 agent 看不到而重复提问）。
                # 原始意图仍在 user_intent 与 slots 里，不丢。
                source_user_message=(
                    request.message.strip() or _source_user_message(self.db, row)
                ),
                out_of_scope_task_intents=_sibling_task_intents(self.db, row),
                client_timezone=request.client_timezone,
            )
            if (
                self.slash_command
                and self.slash_command.kind in {"skill", "tool"}
                and frame.kind == "conversation"
                and row.source_turn_id == self.user_message_id
            ):
                forced = resolve_capability(self.slash_command, manifest)
                force_capability_for_requirement(
                    requirement,
                    model_manifest,
                    forced,
                )
            self.store.save_requirement(
                row,
                requirement.model_dump(mode="json"),
                lease_owner=self.active_frame_lease_owner,
                attempt_no=self.active_frame_attempt_no,
            )
            if run is None:
                run = self.store.start_run(
                    row,
                    requirement=requirement.model_dump(mode="json"),
                    capability_snapshot=manifest.model_dump(mode="json"),
                    lease_owner=self.active_frame_lease_owner,
                    attempt_no=self.active_frame_attempt_no,
                )
                self.store.save_agent_loop_checkpoint(
                    agent_loop,
                    loop_checkpoint,
                    status="active",
                    last_run_id=run.id,
                )
            else:
                self.store.update_run_context(
                    run,
                    requirement=requirement.model_dump(mode="json"),
                    capability_snapshot=manifest.model_dump(mode="json"),
                )
            self.active_run_id = run.id
            self.db.commit()

            def trace(event_type: str, payload: dict[str, Any]) -> None:
                """记录带 TaskFrame/run 上下文的执行事件并立即提交。

                立即提交是为了让流式中继实时暴露正在运行的 TaskFrame。
                """
                self.events.record(
                    request.tenant_id,
                    session.id,
                    event_type,
                    {
                        **payload,
                        "task_frame_id": row.task_id,
                        "harness_run_id": run.id,
                        "agent_loop_id": agent_loop.id,
                        "execution_engine": "harness_v2",
                    },
                )
                # Harness runs execute outside the response generator. Commit
                # each trace checkpoint so the stream relay can expose the
                # running TaskFrame instead of revealing it only at the end.
                self.db.commit()

            invoker = HarnessCapabilityInvoker(
                self.db,
                tenant_id=request.tenant_id,
                session=session,
                task_frame_id=row.task_id,
                model_config=model_config,
                manifest=manifest,
                active_skill=active_skill,
                active_step_id=frame.target_step_id,
                agent_id=session.agent_id,
                run_id=run.id,
                initially_activated_names=(
                    requirement.capability_manifest.allowed_names()
                ),
                is_cancelled=lambda: self._is_cancelled(request, session),
                ensure_execution_lease=lambda: self._renew_execution_leases(
                    row
                ),
                trace_sink=trace,
                step_deadline_monotonic=step_deadline_monotonic,
            )

            result = self.task_agent.run(
                requirement,
                model_config,
                invoker.invoke,
                max_actions=remaining_actions,
                trace_sink=trace,
                is_cancelled=lambda: self._is_cancelled(request, session),
                image_payloads=image_payloads,
                step_deadline_monotonic=step_deadline_monotonic,
                step_timeout_seconds=step_timeout_seconds,
                checkpoint=loop_checkpoint,
            )
            if request.channel == "human_handoff_resume" and result.status == "handoff":
                # The human reply is already the handoff completion signal. Do not
                # re-enter the same terminal handoff node during the resume turn.
                result.status = "completed"
            if result.status == "waiting_external_task":
                next_step = (
                    self.owner._default_next_step(active_skill, frame.target_step_id)
                    if active_skill is not None
                    else None
                )
                resume_step_id = (
                    str(
                        (next_step or {}).get("step_id")
                        or (next_step or {}).get("node_id")
                        or ""
                    ).strip()
                    or None
                )
                external_task = _external_task_for_result(self.db, row, result)
                if external_task is not None:
                    external_task.resume_step_id = resume_step_id
                    self.db.add(external_task)
                result.next_step_id = resume_step_id
                self.db.commit()
            deferred_continuation = False
            if frame.kind == "sop":
                deferred_result = _defer_failed_step_after_completed_checkpoint(
                    result,
                    results,
                )
                if deferred_result is not result:
                    deferred_continuation = True
                    trace(
                        "harness_step_continuation_deferred",
                        {
                            "failed_step_id": frame.target_step_id,
                            "error": result.error,
                            "checkpoint_step_id": row.step_id,
                            "reason": "previous_step_already_produced_user_result",
                        },
                    )
                    result = deferred_result
            loop_checkpoint = dict(result.loop_checkpoint or {})
            _merge_discovered_artifacts(result, invoker.discover_artifacts())
            loop_checkpoint["artifacts"] = list(result.artifacts)
            self.store.save_agent_loop_checkpoint(
                agent_loop,
                loop_checkpoint,
                status="active",
                last_run_id=run.id,
            )
            results.append(result)
            remaining_actions -= max(1, result.action_count)

            if deferred_continuation:
                # The previous SOP step was already committed and moved the
                # session to this step.  Keep that durable checkpoint queued
                # for a later turn instead of applying a transient failure to
                # the session and replacing the useful answer already found.
                last_step_result = _step_result(result)
                break

            if frame.kind == "conversation":
                if (
                    frame.decision == "handoff_human"
                    or result.status == "handoff"
                ):
                    result.status = "handoff"
                    last_step_result = _step_result(result)
                    handoff = self.owner._create_human_handoff_request(
                        request.tenant_id,
                        session,
                        None,
                        last_step_result,
                    )
                    result.artifacts.append(
                        {
                            "type": "human_handoff",
                            "handoff_id": getattr(handoff, "id", None),
                        }
                    )
                else:
                    last_step_result = _step_result(result)
                break

            result = _enforce_required_slots(result, requirement, session)
            results[-1] = result
            if (
                result.status == "completed"
                and not result.next_step_id
                and active_skill is not None
            ):
                default_next = self.owner._default_next_step(
                    active_skill, session.active_step_id
                )
                if default_next:
                    result.next_step_id = str(
                        default_next.get("step_id")
                        or default_next.get("node_id")
                        or ""
                    ).strip() or None
            last_step_result = _step_result(result)
            previous_step_id = session.active_step_id
            self.owner._apply_step_result(
                request.tenant_id,
                session,
                last_step_result,
                active_skill,
            )
            finalize_state = self.owner._finalize_execution_after_reply(
                request.tenant_id,
                session,
                active_skill,
                turn_plan_router_decision(
                    TurnPlan(
                        decision=frame.decision,
                        task_frames=[frame],
                        user_intent=frame.user_intent,
                    )
                ),
                last_step_result,
                None,
            )
            row.step_id = session.active_step_id
            row.slots_json = dict(session.slots_json or {})
            continue_frame = True
            if finalize_state == "handoff":
                result.status = "handoff"
                _append_session_handoff_artifact(result, session)
                continue_frame = False
            elif result.status == "handoff":
                result.status = "failed"
                result.error = {
                    "code": "HANDOFF_NOT_ALLOWED",
                    "message": "当前 SOP 步骤未声明转人工能力。",
                }
                continue_frame = False
            elif finalize_state == "completed":
                result.status = "completed"
                continue_frame = False
            elif result.status != "completed":
                continue_frame = False
            elif (
                remaining_actions <= 0
                or not session.active_skill_id
                or session.active_step_id == previous_step_id
            ):
                result.status = "action_budget"
                continue_frame = False
            else:
                frame.target_step_id = session.active_step_id
            if not continue_frame:
                break

        combined = _combine_results(row.task_id, results)
        if combined.status == "waiting_external_task":
            from app.tools.external_tasks import PERSISTED_TERMINAL_STATUSES

            external_task = _external_task_for_result(self.db, row, combined)
            if external_task is not None and external_task.status in PERSISTED_TERMINAL_STATUSES:
                combined.status = (
                    "ready_to_resume"
                    if row.kind == "sop"
                    else (
                        "completed"
                        if external_task.status == "completed"
                        else "failed"
                    )
                )
                if (
                    row.kind == "sop"
                    and external_task.status == "completed"
                    and external_task.resume_step_id
                ):
                    row.step_id = external_task.resume_step_id
                row.slots_json = {
                    **dict(row.slots_json or {}),
                    **dict(external_task.result_json or {}),
                }
                update_external_task_checkpoint(self.db, row, external_task)
                loop_checkpoint = dict(agent_loop.checkpoint_json or loop_checkpoint)
                combined.structured_result = {
                    "external_task_id": external_task.external_task_id,
                    "external_task_status": external_task.status,
                    "external_task_result": dict(external_task.result_json or {}),
                }
        if run is not None:
            self.store.finish_run(
                run,
                status=combined.status,
                action_count=combined.action_count,
                result=combined.model_dump(mode="json"),
                lease_owner=self.active_frame_lease_owner,
                attempt_no=self.active_frame_attempt_no,
            )
            self.active_run_id = None
        row_status = combined.status
        preserve_agent_loop = _is_recoverable_action_protocol_failure(combined)
        if row_status == "action_budget" or preserve_agent_loop:
            row_status = "queued"
        self.store.finish_frame(
            row,
            status=row_status,
            step_id=row.step_id,
            slots=dict(row.slots_json or {}),
            result=combined.model_dump(mode="json"),
            lease_owner=self.active_frame_lease_owner,
            attempt_no=self.active_frame_attempt_no,
        )
        self.store.finish_agent_loop_for_frame(
            row,
            result_status=("queued" if preserve_agent_loop else combined.status),
            checkpoint=loop_checkpoint,
            last_run_id=run.id if run is not None else None,
        )
        self.events.record(
            request.tenant_id,
            session.id,
            "task_frame_finished",
            {
                "task_frame_id": row.task_id,
                "kind": row.kind,
                "skill_id": row.skill_id,
                "skill_name": active_skill.name if active_skill is not None else None,
                "status": combined.status,
                "step_id": row.step_id,
                "action_count": combined.action_count,
                "error": combined.error,
                "agent_loop_id": agent_loop.id,
                "agent_loop_status": agent_loop.status,
                "execution_engine": "harness_v2",
            },
        )
        self.db.commit()
        self.active_frame_id = None
        self.active_frame_lease_owner = None
        self.active_frame_attempt_no = None
        return combined, last_step_result

    def mark_cancelled(self) -> None:
        """收尾被用户取消的执行：回滚未提交改动并终结 run、TaskFrame 与 turn。"""

        self.db.rollback()
        source_turn_id = str(self.current_source_turn_id or "").strip()
        if self.session is not None and source_turn_id:
            self.store.cancel_source_turn(self.session, source_turn_id)
        else:
            if self.active_run_id:
                run = self.db.get(HarnessRunRecord, self.active_run_id)
                if run is not None and run.status == "running":
                    self.store.finish_run(
                        run,
                        status="cancelled",
                        action_count=run.action_count,
                        result={"status": "cancelled"},
                    )
            if self.active_frame_id:
                row = self.db.get(HarnessTaskFrameRecord, self.active_frame_id)
                if (
                    row is not None
                    and row.status == "running"
                    and row.lease_owner == self.active_frame_lease_owner
                    and row.attempt_no == self.active_frame_attempt_no
                ):
                    self.store.finish_frame(
                        row,
                        status="cancelled",
                        step_id=row.step_id,
                        slots=dict(row.slots_json or {}),
                        result={
                            "task_frame_id": row.task_id,
                            "status": "cancelled",
                            "task_summary": "用户取消了当前 Harness 执行。",
                        },
                    )
                    self.store.finish_agent_loop_for_frame(
                        row,
                        result_status="cancelled",
                        checkpoint=self.store.agent_loop_checkpoint(row),
                        last_run_id=self.active_run_id,
                    )
        self.db.commit()
        turn_store = getattr(self, "turn_store", None)
        if turn_store is not None:
            turn_store.finish_with_error(
                getattr(self, "turn_record", None),
                status="cancelled",
                code="CANCELLED",
                message="用户取消了当前 Harness 执行。",
            )
        self.active_run_id = None
        self.active_frame_id = None
        self.active_frame_lease_owner = None
        self.active_frame_attempt_no = None

    def mark_interrupted(self, code: str, message: str) -> None:
        """异常崩溃后把本轮 run 标记失败，并将对应 TaskFrame 重新排队。

        仅在仍持有帧租约时收尾运行中的记录，避免覆盖已被其他进程接管的状态。
        """

        self.db.rollback()
        source_turn_id = str(self.current_source_turn_id or "").strip()
        if (
            self.session is not None
            and source_turn_id
            and self.active_frame_lease_owner
            and self.active_frame_attempt_no is not None
        ):
            self.store.finish_source_turn_running_runs(
                self.session.id,
                source_turn_id,
                status="failed",
                result={
                    "status": "failed",
                    "error": {"code": code, "message": message},
                },
                lease_owner=self.active_frame_lease_owner,
                attempt_no=self.active_frame_attempt_no,
            )
        elif self.active_run_id:
            run = self.db.get(HarnessRunRecord, self.active_run_id)
            if run is not None and run.status == "running":
                self.store.finish_run(
                    run,
                    status="failed",
                    action_count=run.action_count,
                    result={
                        "status": "failed",
                        "error": {"code": code, "message": message},
                    },
                )
        if self.active_frame_id:
            row = self.db.get(HarnessTaskFrameRecord, self.active_frame_id)
            if (
                row is not None
                and row.status == "running"
                and row.lease_owner == self.active_frame_lease_owner
                and row.attempt_no == self.active_frame_attempt_no
            ):
                self.store.finish_frame(
                    row,
                    status="queued",
                    step_id=row.step_id,
                    slots=dict(row.slots_json or {}),
                    result={
                        "task_frame_id": row.task_id,
                        "status": "failed",
                        "error": {"code": code, "message": message},
                        "task_summary": "Harness 执行中断，TaskFrame 已重新排队。",
                    },
                )
                self.store.finish_agent_loop_for_frame(
                    row,
                    result_status="action_budget",
                    checkpoint=self.store.agent_loop_checkpoint(row),
                    last_run_id=self.active_run_id,
                )
        self.db.commit()
        turn_store = getattr(self, "turn_store", None)
        if turn_store is not None:
            turn_store.finish_with_error(
                getattr(self, "turn_record", None),
                status="failed",
                code=code,
                message=message,
            )
        self.active_run_id = None
        self.active_frame_id = None
        self.active_frame_lease_owner = None
        self.active_frame_attempt_no = None

    def _is_cancelled(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
    ) -> bool:
        """按用户消息 id 与 client_turn_id 两种身份判断本轮是否已被取消。"""
        user_message_id = str(self.user_message_id or "").strip()
        client_turn_id = str(request.client_turn_id or "").strip()
        return bool(
            user_message_id
            and is_chat_turn_cancelled(
                session.id,
                user_message_id,
                db=self.db,
                identity_kind="message",
            )
        ) or bool(
            client_turn_id
            and is_chat_turn_cancelled(
                session.id,
                client_turn_id,
                db=self.db,
                identity_kind="client",
            )
        )

    def _raise_if_cancelled(
        self,
        request: ChatTurnRequest,
        session: ChatSession,
    ) -> None:
        """本轮已被取消时抛 HarnessExecutionCancelled，供执行循环的检查点调用。"""
        if self._is_cancelled(request, session):
            raise HarnessExecutionCancelled(
                "Harness execution was cancelled by the user."
            )

    def _activate_frame(
        self,
        session: ChatSession,
        row: HarnessTaskFrameRecord,
        skills: list[Skill],
    ) -> Skill | None:
        """激活 SOP 帧对应的技能并恢复其步骤与会话槽位。

        非 SOP 帧、或技能已下线/解绑时返回 None，由调用方按 SOP 不可用处理。
        """
        if row.kind != "sop":
            return None
        active_skill = next(
            (skill for skill in skills if skill.skill_id == row.skill_id),
            None,
        )
        if active_skill is None:
            return None
        self._record_skill_activation_event(session, row, active_skill)
        self.owner.runtime.restore_task_frame(
            session,
            {
                "task_id": row.task_id,
                "skill_id": row.skill_id,
                "step_id": row.step_id,
                "slots": dict(row.slots_json or {}),
                "awaiting_input": {},
            },
        )
        return active_skill

    def _record_skill_activation_event(
        self,
        session: ChatSession,
        row: HarnessTaskFrameRecord,
        skill: Skill,
    ) -> None:
        """新发起/恢复 SOP 的 TaskFrame 落一条运行时事件。

        SOP 调用次数统计(api/skills._skill_stats)只认 skill_started /
        skill_resumed 事件;legacy 运行时移除后这两个事件不再产生,导致
        管理端调用次数永远是 0。这里在新任务(start_new_task)与恢复
        挂起任务(switch_to_pending)被调度时补记,payload 保持旧结构
        (to_skill_id/to_skill_version/from_skill_id/from_step_id),
        继续执行的 continue_active 不计新调用。
        """
        if row.decision == "start_new_task":
            event_type = "skill_started"
        elif row.decision == "switch_to_pending":
            event_type = "skill_resumed"
        else:
            return
        payload = {
            "decision": row.decision,
            "from_skill_id": session.active_skill_id,
            "to_skill_id": skill.skill_id,
            "from_skill_version": None,
            "to_skill_version": skill.version,
            "from_step_id": session.active_step_id,
            "to_step_id": row.step_id,
            "execution_engine": "harness_v2",
            "task_frame_id": row.task_id,
        }
        self.events.record(
            session.tenant_id,
            session.id,
            event_type,
            payload,
        )

    def _restore_visible_active_frame(
        self,
        session: ChatSession,
        records: list[HarnessTaskFrameRecord],
        pre_turn_state: dict[str, Any],
    ) -> None:
        """一轮执行结束后，把会话投影回用户应看到的活跃任务帧。

        优先级为 handoff 帧、等待用户回答的 conversation 帧、本轮 SOP 候选帧；已无
        候选时按 pre_turn_state 还原会话的技能状态。
        """
        if session.status == "handoff":
            handoff_row = next(
                (row for row in records if row.status == "handoff"),
                None,
            )
            if handoff_row is not None:
                self.store.set_active_task_frame(session, handoff_row)
            return
        awaiting_conversation = self.store.latest_awaiting_conversation(
            session
        )
        if awaiting_conversation is not None:
            result = awaiting_conversation.result_json or {}
            reply = str(result.get("reply_fragment") or "").strip()
            requirement = awaiting_conversation.task_requirement_json or {}
            session.active_skill_id = None
            session.active_step_id = None
            session.slots_json = {}
            session.resume_after_answer_json = None
            session.awaiting_input_json = {
                "task_id": awaiting_conversation.task_id,
                "kind": "conversation",
                "expected_fields": list(
                    requirement.get("required_slots") or []
                ),
                "requirements": list(
                    awaiting_conversation.requirements_json or []
                ),
                "question_summary": reply or None,
            }
            session.last_agent_question = reply or None
            self.store.set_active_task_frame(
                session,
                awaiting_conversation,
            )
            return
        original_skill_id = str(pre_turn_state.get("active_skill_id") or "")
        original_records = [
            row for row in records if row.skill_id == original_skill_id
        ]
        if original_skill_id and not original_records:
            _restore_session_state(session, pre_turn_state)
            return
        candidates = [
            row
            for row in records
            if row.kind == "sop"
            and row.status
            not in {"completed", "cancelled", "failed"}
        ]
        if original_records:
            candidates.sort(
                key=lambda row: (
                    0 if row.skill_id == original_skill_id else 1,
                    row.sequence,
                )
            )
        else:
            candidates.sort(key=lambda row: row.sequence)
        if not candidates:
            conversation_candidates = [
                row
                for row in records
                if row.kind == "conversation"
                and row.status not in {"completed", "cancelled", "failed"}
            ]
            session.active_skill_id = None
            session.active_step_id = None
            session.slots_json = {}
            session.awaiting_input_json = None
            session.last_agent_question = None
            self.store.set_active_task_frame(
                session,
                (
                    sorted(
                        conversation_candidates,
                        key=lambda item: item.sequence,
                    )[0]
                    if conversation_candidates
                    else None
                ),
            )
            return
        row = candidates[0]
        result = row.result_json or {}
        reply = str(result.get("reply_fragment") or "").strip()
        self.owner.runtime.restore_task_frame(
            session,
            {
                "task_id": row.task_id,
                "skill_id": row.skill_id,
                "step_id": row.step_id,
                "slots": dict(row.slots_json or {}),
                "awaiting_input": (
                    {
                        "task_id": row.task_id,
                        "skill_id": row.skill_id,
                        "step_id": row.step_id,
                        "expected_fields": (
                            row.task_requirement_json.get("required_slots") or []
                        ),
                        "question_summary": reply or None,
                    }
                    if row.status == "awaiting_user"
                    else {}
                ),
            },
        )
        self.store.set_active_task_frame(session, row)


def _step_result(result: TaskExecutionResult) -> StepAgentResult:
    """把 TaskExecutionResult 的状态映射为对外的 StepAgentResult 动作与回复。"""
    action = {
        "completed": "advance",
        "awaiting_user": "ask_user",
        "handoff": "handoff",
        "failed": "reply",
        "blocked": "reply",
        "action_budget": "reply",
    }.get(result.status, "reply")
    return StepAgentResult(
        action=action,  # type: ignore[arg-type]
        reply=result.reply_fragment,
        slot_updates=dict(result.slot_updates),
        knowledge_results=list(result.evidence_results),
        next_step_id=result.next_step_id,
        is_step_completed=result.status == "completed",
        handoff=result.status == "handoff",
        structured_result=result.structured_result,
    )


def _is_recoverable_action_protocol_failure(result: TaskExecutionResult) -> bool:
    """判断失败是否只源于模型动作协议非法，此时 SOP AgentLoop 仍可恢复。"""

    error = result.error if isinstance(result.error, dict) else {}
    return result.status == "failed" and str(error.get("code") or "") == (
        "HARNESS_ACTION_INVALID"
    )


def _defer_failed_step_after_completed_checkpoint(
    result: TaskExecutionResult,
    completed_results: list[TaskExecutionResult],
) -> TaskExecutionResult:
    """已提交节点结果后，把后续步骤的失败降级为可继续的排队状态。

    SOP TaskFrame 可能一次请求推进多个节点，节点完成即已落盘转场与槽位；此时
    启动下一节点的模型或协议失败不能抹掉用户已看到的结果。因此改写为
    action_budget，让帧留在已持久化的下一步并保持可恢复。
    """

    if result.status != "failed":
        return result
    checkpoint = next(
        (
            item
            for item in reversed(completed_results)
            if item.status == "completed" and item.reply_fragment.strip()
        ),
        None,
    )
    if checkpoint is None:
        return result
    failure_summary = str(result.task_summary or "").strip()
    summaries = [
        summary
        for summary in (checkpoint.task_summary.strip(), failure_summary)
        if summary
    ]
    # 上一节点的回复可能带有"接下来将…"式的前瞻表述；后续步骤实际已
    # 暂停排队、要等下一条用户消息才继续，必须向用户说清（真实案例：
    # 用户看到"将进入正式提交步骤"以为会自动提交，实际什么都没发生）。
    reply = checkpoint.reply_fragment.rstrip()
    suffix = "（本轮执行到此暂停，剩余步骤已排队；回复任意消息即可继续。）"
    if suffix not in reply:
        reply = f"{reply}\n\n{suffix}"
    return result.model_copy(
        update={
            "status": "action_budget",
            "reply_fragment": reply,
            "next_step_id": None,
            "task_summary": "；".join(dict.fromkeys(summaries)),
        }
    )


def _enforce_required_slots(
    result: TaskExecutionResult,
    requirement: Any,
    session: ChatSession,
) -> TaskExecutionResult:
    """完成态结果若缺必填槽位，则改为等待用户补充并清空下一步。"""
    if result.status != "completed" or not requirement.required_slots:
        return result
    merged = {
        **dict(session.slots_json or {}),
        **dict(result.slot_updates or {}),
    }
    missing = [
        field
        for field in requirement.required_slots
        if merged.get(field) in (None, "", [], {})
    ]
    if not missing:
        return result
    result.status = "awaiting_user"
    if not result.reply_fragment:
        result.reply_fragment = "还需要您补充：" + "、".join(missing) + "。"
    result.next_step_id = None
    return result


def _external_task_for_result(db, frame, result):
    """取出与该结果对应的外部任务回执。

    优先按 structured_result 里的 task_id 精确匹配，避免误用同一 SOP 帧中更早
    完成的外部任务。
    """
    data = result.structured_result if isinstance(result.structured_result, dict) else {}
    task_id = str(data.get("task_id") or "").strip()
    query = select(ExternalBusinessTask).where(
        ExternalBusinessTask.tenant_id == frame.tenant_id,
        ExternalBusinessTask.session_id == frame.session_id,
        ExternalBusinessTask.task_frame_id == frame.task_id,
    )
    if task_id:
        query = query.where(ExternalBusinessTask.id == task_id)
    return db.exec(query.order_by(ExternalBusinessTask.created_at.desc(), ExternalBusinessTask.id.desc())).first()


def _combine_results(
    task_frame_id: str,
    results: list[TaskExecutionResult],
) -> TaskExecutionResult:
    """把一个 TaskFrame 内多步结果合并为一条对用户可见的执行结果。

    回复只取最后一步非空的 finish 文本，槽位、引用、证据、产物与动作数累加；
    没有任何结果时返回 EMPTY_TASK_RESULT 失败结果。
    """
    if not results:
        return TaskExecutionResult(
            task_frame_id=task_frame_id,
            status="failed",
            reply_fragment="当前 TaskFrame 未产生执行结果。",
            error={"code": "EMPTY_TASK_RESULT"},
        )
    last = results[-1]
    terminal_reply = next(
        (
            item.reply_fragment.strip()
            for item in reversed(results)
            if item.reply_fragment.strip()
        ),
        "",
    )
    return TaskExecutionResult(
        task_frame_id=task_frame_id,
        status=last.status,
        # A SOP TaskFrame can advance through several steps in one turn.  Each
        # step produces a finish reply, but only the terminal step is the
        # user-visible answer for that TaskFrame.  Keep intermediate summaries
        # and structured results below without concatenating their transitional
        # replies into a duplicated final response.
        reply_fragment=terminal_reply,
        slot_updates={
            key: value
            for item in results
            for key, value in item.slot_updates.items()
        },
        next_step_id=last.next_step_id,
        citations=[
            citation
            for item in results
            for citation in item.citations
        ],
        evidence_results=[
            evidence
            for item in results
            for evidence in item.evidence_results
        ],
        capability_results=[
            capability_result
            for item in results
            for capability_result in item.capability_results
        ],
        structured_result=last.structured_result,
        artifacts=[
            artifact
            for item in results
            for artifact in item.artifacts
        ],
        task_summary="；".join(
            dict.fromkeys(
                item.task_summary.strip()
                for item in results
                if item.task_summary.strip()
            )
        ),
        action_count=sum(item.action_count for item in results),
        error=last.error,
    )


def _single_task_reply(results: list[TaskExecutionResult]) -> str | None:
    """单帧且回复完整时直接复用其终稿，省去一次模型合成。

    finish 已产出面向用户的回复，多帧并存时才需要合成；回复为空或疑似不完整
    的 JSON 投影时返回 None，回落到 ResponseGenerator。
    """

    if len(results) != 1:
        return None
    result = results[0]
    reply = str(result.reply_fragment or "").strip()
    if _structured_reply_requires_synthesis(reply, result.structured_result):
        return None
    return reply or None


def _structured_reply_requires_synthesis(reply: str, structured_result: Any) -> bool:
    """回复是明显不完整的 JSON 投影时要求走合成，而非直接透出给用户。"""

    if structured_result is None or not reply:
        return False
    looks_like_json = reply.startswith("{") or (
        reply.startswith("[")
        and (len(reply) == 1 or reply[1:2] in {"{", "[", '"'})
    )
    if not looks_like_json:
        return False
    try:
        projected = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        return True
    return projected != structured_result


def _response_task_payload(
    row: HarnessTaskFrameRecord,
    result: TaskExecutionResult,
    skill: Skill | None,
    step_result: StepAgentResult,
) -> dict[str, object]:
    """组装供回复生成器使用的单帧任务负载，含技能内容、槽位与能力结果。"""
    projected_step_result = step_result.model_dump(mode="json")
    projected_step_result["reply"] = result.reply_fragment
    projected_step_result["knowledge_results"] = list(result.evidence_results)
    return {
        "task": row.user_intent or "当前任务",
        "task_frame_id": row.task_id,
        "status": result.status,
        "skill_content": dict(skill.content_json or {}) if skill else {},
        "current_step_id": row.step_id,
        "slots": dict(row.slots_json or {}),
        "step_result": projected_step_result,
        "tool_result": (
            {
                "success": all(
                    bool(item.get("success"))
                    for item in result.capability_results
                ),
                "capability_results": list(result.capability_results),
            }
            if result.capability_results
            else None
        ),
        "task_summary": result.task_summary,
        "structured_result": result.structured_result,
        "artifacts": list(result.artifacts),
    }


def _inject_handoff_context(
    db: object,
    session: ChatSession,
    payloads: list[dict[str, object]],
    results: list[TaskExecutionResult],
    request: ChatTurnRequest | None = None,
) -> None:
    """在 resume turn 执行期间注入 handoff_info(含 human_reply),供 response_generator 转述。

    判定 resume turn:request.channel == "human_handoff_resume"(由 _resume_human_handoff_worker
    传入)。此时 handoff 已 answered 且 human_reply 已落库,直接注入即可——不再依赖
    resume_finished_at 标记(原标记在 worker 写入时机晚于 turn 执行,导致注入永远 miss)。
    handoff 状态的 task(本轮新触发转人工)也注入 handoff_info(无 human_reply),
    用于告知用户已转交。
    """
    from app.db.models import HumanHandoffRequest

    handoff = db.exec(
        select(HumanHandoffRequest)
        .where(HumanHandoffRequest.session_id == session.id)
        .order_by(HumanHandoffRequest.created_at.desc())
    ).first()
    if not handoff:
        return
    notify_message_id = handoff.notify_message_id or ""
    human_reply = (handoff.human_reply or "").strip()
    is_answered = handoff.status in ("answered", "resolved") and bool(human_reply)
    # resume turn:由 worker 显式传入 channel 标识判定,时序可靠。
    is_resume_turn = (
        bool(request and getattr(request, "channel", "") == "human_handoff_resume")
        and is_answered
    )
    for payload, result in zip(payloads, results, strict=False):
        if is_resume_turn or payload.get("status") == "handoff":
            pass
        else:
            continue
        handoff_info: dict[str, Any] = {
            "handoff_id": handoff.id,
            "notified_via_feishu": bool(notify_message_id),
        }
        if is_answered:
            handoff_info["human_reply"] = human_reply
        payload["handoff_info"] = handoff_info


def _globalize_citations(
    results: list[TaskExecutionResult],
) -> list[dict[str, Any]]:
    """把各帧引用合并为全局编号，最多保留 8 条并回写各帧的引用标签。"""
    citations: list[dict[str, Any]] = []
    labels_by_identity: dict[str, str] = {}
    for result in results:
        relabeled: list[dict[str, Any]] = []
        for citation in result.citations:
            identity = _citation_identity(citation)
            if not identity:
                continue
            label = labels_by_identity.get(identity)
            if label is None:
                if len(citations) >= 8:
                    continue
                label = f"[{len(citations) + 1}]"
                labels_by_identity[identity] = label
                citations.append({**citation, "label": label})
            relabeled.append({**citation, "label": label})
        result.citations = relabeled
    return citations


def _citation_identity(citation: dict[str, Any]) -> str:
    """生成引用的去重标识：优先 concept_id/chunk_id，否则拼接来源字段。"""
    for field in ("concept_id", "chunk_id"):
        value = str(citation.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    components = [
        str(citation.get(field) or "").strip()
        for field in ("source_path", "section_path", "title", "excerpt")
    ]
    normalized = "|".join(component for component in components if component)
    return normalized[:2_000]


def _aggregate_artifacts(
    results: list[TaskExecutionResult],
) -> list[dict[str, Any]]:
    """跨帧汇总产物，按类型/帧/路径去重，最多返回 20 条。"""
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        for artifact in result.artifacts:
            identity = "|".join(
                str(artifact.get(field) or "")
                for field in ("type", "task_frame_id", "path", "handoff_id")
            )
            if not identity.strip("|") or identity in seen:
                continue
            seen.add(identity)
            artifacts.append(dict(artifact))
            if len(artifacts) >= 20:
                return artifacts
    return artifacts


def _merge_discovered_artifacts(
    result: TaskExecutionResult,
    discovered: list[dict[str, Any]],
) -> None:
    """把调用方新发现的产物并入结果，按路径去重且总量不超过 20 条。"""
    known_paths = {
        str(item.get("path") or "")
        for item in result.artifacts
        if isinstance(item, dict) and item.get("path")
    }
    for item in discovered:
        path = str(item.get("path") or "")
        if not path or path in known_paths:
            continue
        result.artifacts.append(dict(item))
        known_paths.add(path)
        if len(result.artifacts) >= 20:
            break


def _append_session_handoff_artifact(
    result: TaskExecutionResult,
    session: ChatSession,
) -> None:
    """会话已进入人工接管时，给执行结果补一条 human_handoff 产物。"""
    awaiting = (
        session.awaiting_input_json
        if isinstance(session.awaiting_input_json, dict)
        else {}
    )
    handoff_id = str(awaiting.get("handoff_id") or "").strip()
    if not handoff_id:
        return
    result.artifacts.append(
        {
            "type": "human_handoff",
            "handoff_id": handoff_id,
        }
    )


def _with_recoverable_first_session(
    request: ChatTurnRequest,
) -> ChatTurnRequest:
    """首轮未带 session_id 时，由租户、用户与 client_turn_id 派生稳定的会话 id。

    同一 client_turn_id 重试会命中同一会话，避免并发创建重复的首轮会话。
    """

    if request.session_id or not str(request.client_turn_id or "").strip():
        return request
    identity = "\x1f".join(
        (
            request.tenant_id.strip(),
            str(request.user_id or "").strip(),
            str(request.client_turn_id or "").strip(),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return request.model_copy(
        update={"session_id": f"session_{digest[:16]}"}
    )


def get_or_create_harness_session(
    owner: Any,
    request: ChatTurnRequest,
) -> ChatSession:
    """创建或恢复单个 Harness 会话，并处理并发主键冲突。

    两个 worker 抢建同一确定性首轮会话时，落败方复用胜者已提交的行；随后校验
    租户、用户与 agent 归属，不匹配则抛 HarnessExecutionFenced。
    """

    db = owner.db
    try:
        session = owner._get_or_create_session(request)
        db.commit()
    except IntegrityError:
        # Two workers may race to create the same deterministic first-turn
        # session. The loser reuses the row committed by the winner.
        db.rollback()
        session = (
            db.get(ChatSession, request.session_id)
            if request.session_id
            else None
        )
        if session is None:
            raise

    if session.tenant_id != request.tenant_id:
        raise HarnessExecutionFenced(
            "Harness session tenant does not match the request."
        )
    if request.user_id is not None and session.user_id != request.user_id:
        raise HarnessExecutionFenced(
            "Harness session user does not match the request."
        )
    if (
        request.agent_id is not None
        and session.agent_id not in {None, request.agent_id}
    ):
        raise HarnessExecutionFenced(
            "Harness session agent does not match the request."
        )
    if request.agent_id and not session.agent_id:
        session.agent_id = request.agent_id
        db.add(session)
        db.commit()
    db.refresh(session)
    return session


def _session_state(session: ChatSession) -> dict[str, Any]:
    """快照会话的技能、步骤、槽位与总结字段，供本轮结束后比对和还原。"""
    return {
        "active_skill_id": session.active_skill_id,
        "active_step_id": session.active_step_id,
        "slots_json": deepcopy(session.slots_json or {}),
        "awaiting_input_json": deepcopy(session.awaiting_input_json),
        "context_state_json": deepcopy(session.context_state_json or {}),
        "summary": session.summary,
        "last_agent_question": session.last_agent_question,
    }


def _restore_session_state(
    session: ChatSession, state: dict[str, Any]
) -> None:
    """用 _session_state 的快照覆盖写回会话字段，深拷贝以避免共享引用。"""
    session.active_skill_id = state.get("active_skill_id")
    session.active_step_id = state.get("active_step_id")
    session.slots_json = deepcopy(state.get("slots_json") or {})
    session.awaiting_input_json = deepcopy(state.get("awaiting_input_json"))
    session.context_state_json = deepcopy(state.get("context_state_json") or {})
    session.summary = state.get("summary")
    session.last_agent_question = state.get("last_agent_question")


def _prior_result(result: TaskExecutionResult) -> dict[str, Any]:
    """裁剪前一帧的执行结果，仅保留下游编译任务需求所需的摘要字段。"""
    return {
        "task_frame_id": result.task_frame_id,
        "status": result.status,
        "task_summary": result.task_summary,
        "slot_updates": result.slot_updates,
        "capability_results": result.capability_results,
        "artifacts": result.artifacts,
        "structured_result": result.structured_result,
    }


def _source_user_message(db: Any, row: HarnessTaskFrameRecord) -> str:
    """读取触发该 TaskFrame 的用户消息原文；消息缺失或非用户角色时返回空串。"""
    message = db.get(Message, row.source_turn_id)
    if message is None or message.role != "user":
        return ""
    return str(message.content or "").strip()


def _sibling_task_intents(
    db: Any,
    row: HarnessTaskFrameRecord,
) -> list[str]:
    """返回同一轮用户消息中属于其他 TaskFrame 的任务意图，用于声明任务边界。"""

    siblings = db.exec(
        select(HarnessTaskFrameRecord).where(
            HarnessTaskFrameRecord.session_id == row.session_id,
            HarnessTaskFrameRecord.source_turn_id == row.source_turn_id,
            HarnessTaskFrameRecord.id != row.id,
        )
    ).all()
    return [
        str(item.user_intent or "").strip()
        for item in siblings
        if str(item.user_intent or "").strip()
    ]


def _skill_step_timeout_seconds(skill: Skill | None) -> int | None:
    """读取 SOP 配置的单步超时秒数；缺失或非法返回 None，有效值截断到 1-3600。"""
    if skill is None or not isinstance(skill.content_json, dict):
        return None
    raw = skill.content_json.get("step_timeout_seconds")
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(1, min(value, 3600))


def _dependency_order(
    records: list[HarnessTaskFrameRecord],
) -> list[HarnessTaskFrameRecord]:
    """按 depends_on_json 对帧列表做拓扑排序。

    依赖不在本批次内的帧视为已满足；出现循环依赖时按原顺序追加剩余帧，避免死循环。
    """
    by_id = {row.task_id: row for row in records}
    remaining = list(records)
    ordered: list[HarnessTaskFrameRecord] = []
    resolved: set[str] = set()
    while remaining:
        ready = [
            row
            for row in remaining
            if all(
                dependency_id in resolved
                or dependency_id not in by_id
                for dependency_id in row.depends_on_json or []
            )
        ]
        if not ready:
            ordered.extend(remaining)
            break
        for row in ready:
            ordered.append(row)
            resolved.add(row.task_id)
            remaining.remove(row)
    return ordered
