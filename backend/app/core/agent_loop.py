from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from time import sleep
from typing import Any, Literal

from sqlmodel import Session, select

from app.agents.branching import (
    model_for_agent,
    visible_published_skills,
    visible_skill,
)
from app.channels.service_outbox import stage_channel_delivery
from app.core.agent_identity_prompt import AgentIdentityPrompt
from app.core.cancellation import clear_chat_turn_cancelled
from app.core.conversation_context import (
    ConversationContextSettings,
    build_conversation_context,
)
from app.core.conversation_projection import ConversationProjection
from app.core.graph_rules import GraphRules
from app.core.harness_agent import HarnessExecutionCancelled
from app.core.harness_session_lock import HarnessSessionBusy
from app.core.harness_turn_store import HarnessTurnConflict
from app.core.harness_v2_engine import (
    HarnessV2Engine,
    _with_recoverable_first_session,
    get_or_create_harness_session,
)
from app.core.human_handoff_service import HumanHandoffService
from app.core.response_generator import (
    ResponseGenerator,
    format_runtime_failure_reply,
    model_failure_suggestion,
)
from app.core.skill_runtime import SkillRuntime
from app.core.slash_commands import SlashCommandError
from app.core.turn_finalizer import TurnFinalizer
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChatSession,
    HarnessTurnRecord,
    HumanHandoffRequest,
    Message,
    ModelConfig,
    PersonaConfig,
    Skill,
    UIConfig,
    new_id,
    utc_now,
)
from app.knowledge.citations import (
    compact_knowledge_citation_labels,
    restore_truncated_atomic_references,
)
from app.llm import LLMClient, LLMError
from app.llm.model_config_resolver import (
    resolve_model_config_for_runtime,
)
from app.llm.stage_protocol import stage_payload, unified_system_prompt
from app.memory.jobs import enqueue_memory_capture
from app.memory.service import MemoryService
from app.observability import EventLog
from app.observability.spans import llm_operation
from app.session.helpers import public_session
from app.session.message_visibility import visible_message_content, visible_message_rows
from app.session.origin import PILOTDECK_GROUP_CHAT_CHANNEL
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    RouterDecision,
    StepAgentResult,
)
from app.tools.tool_schema import ToolResult

logger = logging.getLogger(__name__)

STREAM_CHUNK_INTERVAL_SECONDS = 0.045
MAX_TOOL_ACTIONS_PER_TURN = 32
MAX_TOOL_ACTIONS_PER_TURN_LIMIT = 100
GRAPH_PENDING_STEPS_SLOT = "_graph_pending_steps"
CANCELLED_ASSISTANT_REPLY = "已停止生成"
ExecutionFinalizeState = Literal["continued", "completed", "handoff"]


def _knowledge_scope_ids(
    scope: dict[str, Any],
    plural_key: str,
    singular_key: str,
) -> list[str]:
    """解析知识范围 ID:优先读取复数键,缺失时回退单数键,去重并保持首次出现顺序。"""
    values = scope.get(plural_key)
    if not isinstance(values, list):
        singular = scope.get(singular_key)
        values = [singular] if singular else []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _find_handoff_node_id_in_skill(
    skill: Skill, active_step_id: str | None = None
) -> str | None:
    """查找 SOP 中从当前节点可达的 handoff 节点。

    使用 GraphRules.find_handoff_node_id 做基于 edges 的 BFS,
    优先返回从 active_step_id 可达的 handoff 节点,而非数组顺序的第一个。
    """
    content = skill.content_json or {}
    return GraphRules.find_handoff_node_id(content, active_step_id)


def _agent_identity_prompt(agent: AgentProfile) -> str:
    """用 AgentIdentityPrompt 渲染 agent 的身份提示词,供人设与阶段提示复用。"""
    return AgentIdentityPrompt.render(
        agent,
        single_line=_single_line_text,
        metadata_formatter=_metadata_prompt_text,
    )


def _metadata_prompt_text(value: object) -> str:
    """把元数据值渲染成提示词内的单行文本;列表用顿号连接,不支持的类型返回空串。"""
    if isinstance(value, str):
        return _single_line_text(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        items = [_single_line_text(item) for item in value]
        return "、".join(item for item in items if item)
    return ""


def _single_line_text(value: object) -> str:
    """复用 AgentIdentityPrompt.single_line 把任意值压成单行,避免提示词里出现换行。"""
    return AgentIdentityPrompt.single_line(value)


class AgentLoopPreconditionError(Exception):
    """Agent 循环的前置条件校验失败,携带机器可读的 code 与面向用户的 message。"""

    def __init__(self, code: str, message: str):
        """保存错误码与提示文案,并把文案作为异常消息交给 Exception。"""
        super().__init__(message)
        self.code = code
        self.message = message


class AgentLoop:
    """对话回合编排器:驱动 Harness v2 引擎,并统一处理技能状态、人工转接与消息落库。

    一个实例绑定一个数据库 Session,覆盖单轮对话及其取消、失败等分支。
    """

    def __init__(
        self,
        db: Session,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        stream_sink: Any | None = None,
    ) -> None:
        """装配事件日志、技能运行时、回复生成器与记忆服务,并保存可选的流式与事件回调。"""
        self.db = db
        self.events = EventLog(db, event_sink=event_sink)
        self.stream_sink = stream_sink
        self.stream_delivery_succeeded = False
        self.runtime = SkillRuntime()
        self.response_generator = ResponseGenerator()
        self.memory = MemoryService(db)

    def _turn_payload(self, payload: dict[str, Any], user_message_id: str | None) -> dict[str, Any]:
        """复制事件载荷,并在拿到用户消息 ID 时补齐 turn_id 与 user_message_id。"""
        data = dict(payload)
        if user_message_id:
            data.setdefault("user_message_id", user_message_id)
            data.setdefault("turn_id", user_message_id)
        return data

    def handle_turn(self, request: ChatTurnRequest) -> ChatTurnResponse:
        """同步执行一轮对话:委托 Harness v2 引擎,并统一收尾后返回回合响应。

        并发/重复请求、取消、前置条件、模型失败与未知异常分别转成可展示的回复;
        回复统一经 _finalize_turn 落库,所有分支结束时都会关闭引擎。
        """
        engine = HarnessV2Engine(self)
        chat_session: ChatSession | None = None
        user_message_id: str | None = None
        step_result = StepAgentResult(action="reply")
        try:
            return engine.run(request)
        except (HarnessTurnConflict, HarnessSessionBusy) as exc:
            chat_session = engine.session
            self.db.rollback()
            chat_session = chat_session or self._get_or_create_session(request)
            error_code = (
                "HARNESS_SESSION_BUSY"
                if isinstance(exc, HarnessSessionBusy)
                else "HARNESS_TURN_CONFLICT"
            )
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "turn_rejected",
                {
                    "code": error_code,
                    "message": str(exc),
                    "client_turn_id": request.client_turn_id,
                },
            )
            self.db.commit()
            return ChatTurnResponse(
                reply=format_runtime_failure_reply(
                    "Harness 并发或重复请求已阻止",
                    exc,
                    error_code,
                    "请等待原请求完成，或为新请求使用新的 client_turn_id。",
                ),
                session_id=chat_session.id,
                runtime_error_code=error_code,
                step_result=step_result,
                session_state=public_session(chat_session),
            )
        except HarnessExecutionCancelled:
            chat_session = engine.session
            user_message_id = engine.user_message_id
            engine.mark_cancelled()
            chat_session = chat_session or self._get_or_create_session(request)
            if user_message_id:
                self._persist_cancelled_assistant_message(
                    request.tenant_id,
                    chat_session,
                    user_message_id,
                    request.client_turn_id,
                )
            self.db.commit()
            for turn_id in (user_message_id, request.client_turn_id):
                if turn_id:
                    clear_chat_turn_cancelled(chat_session.id, turn_id)
            return ChatTurnResponse(
                reply=CANCELLED_ASSISTANT_REPLY,
                session_id=chat_session.id,
                step_result=step_result,
                session_state=public_session(chat_session),
            )
        except (AgentLoopPreconditionError, SlashCommandError) as exc:
            chat_session = engine.session
            engine.mark_interrupted(exc.code, exc.message)
            chat_session = chat_session or self._get_or_create_session(request)
            return self._finish_with_error(chat_session, exc.code, exc.message)
        except LLMError as exc:
            chat_session = engine.session
            user_message_id = engine.user_message_id
            engine.mark_interrupted("LLM_ERROR", str(exc))
            chat_session = chat_session or self._get_or_create_session(request)
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "error_occurred",
                {"code": "LLM_ERROR", "message": str(exc)},
            )
            reply = format_runtime_failure_reply(
                "模型调用失败", exc, "LLM_ERROR", model_failure_suggestion(exc)
            )
        except Exception as exc:
            chat_session = engine.session
            user_message_id = engine.user_message_id
            engine.mark_interrupted("HARNESS_V2_ERROR", str(exc))
            chat_session = chat_session or self._get_or_create_session(request)
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "error_occurred",
                {"code": "HARNESS_V2_ERROR", "message": str(exc)},
            )
            reply = format_runtime_failure_reply(
                "Harness v2 执行出错",
                exc,
                "HARNESS_V2_ERROR",
                "请查看执行记录或服务日志定位具体原因。",
            )
        finally:
            terminal_record = getattr(engine, "turn_record", None)
            terminal_session = getattr(engine, "session", None)
            if (
                terminal_record is not None
                and terminal_session is not None
                and terminal_record.status in {"completed", "failed", "cancelled"}
            ):
                for turn_id in (engine.user_message_id, request.client_turn_id):
                    if turn_id:
                        clear_chat_turn_cancelled(terminal_session.id, turn_id)
            engine.close()

        reply = self._finalize_turn(
            chat_session,
            request.tenant_id,
            reply,
            step_result,
            request.message,
            user_message_id=user_message_id,
            assistant_metadata_override=(
                {"message_visibility": request.message_visibility}
                if request.message_visibility != "visible"
                else None
            ),
        )
        self.db.commit()
        self.db.refresh(chat_session)
        return ChatTurnResponse(
            reply=reply,
            session_id=chat_session.id,
            step_result=step_result,
            session_state=public_session(chat_session),
        )

    def handle_turn_stream(self, request: ChatTurnRequest) -> Iterator[dict[str, object]]:
        """把流式回合委托给 Harness v2 实现,保持对外统一的迭代器接口。"""
        yield from self._handle_turn_stream_v2(request)

    def _handle_turn_stream_v2(self, request: ChatTurnRequest) -> Iterator[dict[str, object]]:
        """产出 Harness v2 的流式事件:会话创建、状态、增量文本、结束与最终响应。

        先同步跑完 handle_turn,再按回复逐块发出 stream_delta;取消与运行时错误分别以
        stream_cancelled 与 error 事件结束本轮,不再发送后续内容。
        """
        session_request = _with_recoverable_first_session(request)
        existing_session = (
            self.db.get(ChatSession, session_request.session_id)
            if session_request.session_id
            else None
        )
        chat_session = get_or_create_harness_session(
            self,
            session_request,
        )
        created_session = existing_session is None
        scoped_request = request.model_copy(update={"session_id": chat_session.id})
        initial_turn_id = str(request.client_turn_id or "").strip() or None
        if created_session:
            yield self._stream_event(
                "session_created",
                chat_session,
                {
                    "sessionId": chat_session.id,
                    "turn_id": initial_turn_id,
                    "client_turn_id": request.client_turn_id,
                    "execution_engine": "harness_v2",
                },
            )
        yield self._stream_event(
            "user_message_received",
            chat_session,
            self._turn_payload(
                {
                    "sessionId": chat_session.id,
                    "client_turn_id": request.client_turn_id,
                    "execution_engine": "harness_v2",
                },
                initial_turn_id,
            ),
        )
        yield self._stream_status(
            chat_session,
            "planning",
            "正在规划本轮任务",
            {"execution_engine": "harness_v2"},
            user_message_id=initial_turn_id,
        )
        response = self.handle_turn(scoped_request)
        chat_session = self.db.get(ChatSession, response.session_id)
        if chat_session is None:
            return
        user_message = None
        client_turn_id = str(request.client_turn_id or "").strip()
        if client_turn_id:
            receipt = self.db.exec(
                select(HarnessTurnRecord).where(
                    HarnessTurnRecord.tenant_id == request.tenant_id,
                    HarnessTurnRecord.session_id == response.session_id,
                    HarnessTurnRecord.client_turn_id == client_turn_id,
                )
            ).first()
            if receipt is not None and receipt.user_message_id:
                candidate = self.db.get(Message, receipt.user_message_id)
                if (
                    candidate is not None
                    and candidate.tenant_id == request.tenant_id
                    and candidate.session_id == response.session_id
                    and candidate.role == "user"
                ):
                    user_message = candidate
        if user_message is None and not client_turn_id:
            user_message = self.db.exec(
                select(Message)
                .where(
                    Message.tenant_id == request.tenant_id,
                    Message.session_id == response.session_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
            ).first()
        user_message_id = user_message.id if user_message else None
        if response.reply == CANCELLED_ASSISTANT_REPLY:
            yield self._stream_event(
                "stream_cancelled",
                chat_session,
                self._turn_payload(
                    {
                        "phase": "cancelled",
                        "text": CANCELLED_ASSISTANT_REPLY,
                        "client_turn_id": request.client_turn_id,
                        "execution_engine": "harness_v2",
                    },
                    user_message_id or initial_turn_id,
                ),
            )
            return
        resolved_turn_id = user_message_id or initial_turn_id
        if response.runtime_error_code:
            yield self._stream_event(
                "error",
                chat_session,
                self._turn_payload(
                    {
                        "code": response.runtime_error_code,
                        "message": response.reply,
                        "client_turn_id": request.client_turn_id,
                        "execution_engine": "harness_v2",
                    },
                    resolved_turn_id,
                ),
            )
            return
        for chunk in self.response_generator.chunk_text(response.reply):
            event = self._stream_event(
                "stream_delta",
                chat_session,
                self._turn_payload(
                    {
                        "content": chunk,
                        "execution_engine": "harness_v2",
                    },
                    resolved_turn_id,
                ),
            )
            self.db.commit()
            yield event
        end_event = self._stream_event(
            "stream_end",
            chat_session,
            self._turn_payload({"execution_engine": "harness_v2"}, resolved_turn_id),
        )
        self.db.commit()
        yield end_event
        yield self._stream_event(
            "complete",
            chat_session,
            self._turn_payload(
                {
                    **response.model_dump(mode="json"),
                    "execution_engine": "harness_v2",
                },
                resolved_turn_id,
            ),
        )

    def _stream_status(
        self,
        chat_session: ChatSession,
        phase: str,
        text: str,
        extra: dict[str, object] | None = None,
        user_message_id: str | None = None,
    ) -> dict[str, object]:
        """构造 status 事件;带 turn_id 且阶段不是 received 时同时写入事件日志并提交。"""
        payload: dict[str, object] = {"phase": phase, "text": text, **(extra or {})}
        if user_message_id:
            payload = self._turn_payload(payload, user_message_id)
            if phase != "received":
                self.events.record(
                    chat_session.tenant_id, chat_session.id, "stream_status", payload
                )
                self.db.commit()
        return self._stream_event(
            "status",
            chat_session,
            payload,
        )

    def _stream_event(
        self,
        kind: str,
        chat_session: ChatSession,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """组装统一格式的流式事件,并按白名单把关键事件持久化到事件日志。"""
        persisted_stream_events = {
            "agent_loop_completed",
            "agent_loop_continued",
            "general_skill_run_finished",
            "general_skill_trace",
            "knowledge_result",
            "reflection_decision",
            "skill_state",
            "step_result",
            "stream_delta",
            "stream_replace",
            "stream_end",
            "tool_result",
        }
        if kind in persisted_stream_events and (
            payload.get("turn_id") or payload.get("user_message_id")
        ):
            self.events.record(chat_session.tenant_id, chat_session.id, kind, payload)
            self.db.commit()
        data = {
            "kind": kind,
            "sessionId": chat_session.id,
            "timestamp": utc_now().isoformat(),
            "provider": "skill",
            **payload,
        }
        return {"event": kind, "data": data}

    def _pace_stream(self) -> None:
        """按固定间隔休眠,控制流式增量的输出节奏,避免一次性刷屏。"""
        sleep(STREAM_CHUNK_INTERVAL_SECONDS)

    def _current_step_allows_human_handoff(
        self, skill: Skill | None, active_step_id: str | None
    ) -> bool:
        """判断当前步骤是否声明了人工转接,即 handoff 节点或含 handoff_human 动作。"""
        if not skill:
            return False
        current_step = self._current_skill_step(skill, active_step_id)
        return bool(current_step and self._step_declares_human_handoff(current_step))

    def _maybe_route_to_handoff_node(
        self, chat_session: ChatSession, active_skill: Skill | None
    ) -> bool:
        """当 step_result.handoff=True 但当前 step 不声明 handoff 时,
        查找 SOP 中的 handoff 节点并路由到它。这使得后续的
        _create_human_handoff_request 能从 handoff 节点读取 assignee_user_id。

        返回 True 表示已路由到 handoff 节点。
        """
        if not active_skill or not chat_session.active_skill_id:
            return False
        current_step = self._current_skill_step(
            active_skill, chat_session.active_step_id
        )
        if current_step and self._step_declares_human_handoff(current_step):
            return False
        handoff_step_id = _find_handoff_node_id_in_skill(
            active_skill, chat_session.active_step_id
        )
        if not handoff_step_id:
            return False
        self._change_active_step(
            chat_session.tenant_id,
            chat_session,
            handoff_step_id,
            reason="handoff_node_routed_by_step_result",
        )
        return True

    def _step_declares_human_handoff(self, step: dict[str, Any]) -> bool:
        """判断节点是否为 handoff 类型,或其允许动作中包含 handoff_human。"""
        node_type = str(step.get("type") or "").strip()
        return node_type == "handoff" or "handoff_human" in self._step_actions(step)

    def _human_handoff_assignee_user_id(
        self, tenant_id: str, agent_id: str | None, fallback_user_id: str | None
    ) -> str | None:
        """按 agent 元数据中的负责人、租户管理员、传入兜底人的顺序解析站内人工处理人。"""
        return HumanHandoffService(self.db, getattr(self, "events", None)).assignee_user_id(
            tenant_id,
            agent_id,
            fallback_user_id,
            tenant_admin_resolver=self._human_handoff_tenant_admin_user_id,
        )

    def _human_handoff_tenant_admin_user_id(self, tenant_id: str) -> str | None:
        """取租户内最早创建的管理员用户 ID,作为人工转接的兜底处理人。"""
        return HumanHandoffService(self.db, getattr(self, "events", None)).tenant_admin_user_id(
            tenant_id
        )

    def _human_handoff_context_summary(self, chat_session: ChatSession) -> str:
        """取会话最近两条消息拼成转接上下文摘要,单条截断到 240 字。"""
        return HumanHandoffService(self.db, getattr(self, "events", None)).context_summary(
            chat_session
        )

    def _human_handoff_pending_question(
        self, current_step: dict[str, Any] | None, step_result: StepAgentResult
    ) -> str:
        """按回复、节点 handoff_question、question、name 的优先级提取待处理问题。"""
        return HumanHandoffService.pending_question(current_step, step_result)

    def _step_actions(self, step: dict[str, Any]) -> list[str]:
        """返回步骤声明的允许动作列表,由 GraphRules 统一归一化。"""
        return GraphRules.step_actions(step)

    def _finish_stale_completed_skill(
        self, tenant_id: str, chat_session: ChatSession, skills: list[Skill]
    ) -> None:
        """清掉失效的技能栈与恢复标记,并在活动技能处于终态时将它结束。"""
        if chat_session.skill_stack_json or chat_session.resume_after_answer_json:
            chat_session.skill_stack_json = []
            chat_session.resume_after_answer_json = None
            chat_session.updated_at = utc_now()
        active_skill = next(
            (skill for skill in skills if skill.skill_id == chat_session.active_skill_id), None
        )
        if active_skill and self._is_terminal_skill_state(active_skill, chat_session):
            self._complete_active_skill(
                tenant_id, chat_session, active_skill, "stale_terminal_state"
            )

    def _should_complete_skill(
        self,
        skill: Skill | None,
        chat_session: ChatSession,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
    ) -> bool:
        """判断本轮是否应结束当前 SOP 技能,图谱拓扑优先于回复/工具完成的捷径。

        当前节点仍有出边或存在待处理分支时返回 False;工具成功且步骤可收尾时返回 True。
        """
        if not skill or not step_result.is_step_completed:
            return False
        if tool_result and not tool_result.success:
            return False
        # Graph topology is authoritative for SOP completion. A non-terminal
        # node may allow an interim reply and all global slots may already be
        # filled, but an outgoing edge still means the workflow has work left.
        # Check this before the reply/tool completion shortcuts so transitioning
        # into an intermediate node cannot finish the entire SOP.
        if self._graph_flow_has_unfinished_work(skill, chat_session, step_result):
            return False
        if (
            tool_result
            and tool_result.success
            and self._current_step_can_finish_after_tool(skill, chat_session)
        ):
            return True
        if self._graph_pending_steps(chat_session):
            return False
        if self._is_answer_ready_skill_state(skill, chat_session):
            return True
        if self._is_terminal_skill_state(skill, chat_session):
            return True
        if not step_result.next_step_id and not step_result.tool_call:
            return True
        return self._is_terminal_skill_state(skill, chat_session)

    def _is_terminal_skill_state(self, skill: Skill, chat_session: ChatSession) -> bool:
        """判断会话当前步骤是否处于技能终态。"""
        return self._is_terminal_skill_position(
            skill, chat_session.active_step_id, chat_session.slots_json or {}
        )

    def _is_answer_ready_skill_state(self, skill: Skill, chat_session: ChatSession) -> bool:
        """判断当前步骤是否已允许最终回复,且技能声明的必填信息全部集齐。"""
        step = self._current_skill_step(skill, chat_session.active_step_id)
        if not step:
            return False
        actions = self._step_actions(step)
        if not self._actions_allow_final_reply(actions):
            return False
        required = [str(field) for field in (skill.content_json or {}).get("required_info", [])]
        return all(
            self._skill_slot_satisfied(chat_session.slots_json or {}, field) for field in required
        )

    def _graph_flow_has_unfinished_work(
        self,
        skill: Skill | None,
        chat_session: ChatSession,
        step_result: StepAgentResult | None = None,
    ) -> bool:
        """判断技能图谱是否还有未完成的工作:待处理分支、指回自身的边或当前节点的出边。"""
        if not skill or chat_session.active_skill_id != skill.skill_id:
            return False
        if self._graph_pending_steps(chat_session):
            return True
        if (
            step_result
            and step_result.next_step_id
            and str(step_result.next_step_id) == str(chat_session.active_step_id)
        ):
            return True
        if not chat_session.active_step_id:
            return False
        return bool(self._graph_outgoing_edges(skill).get(chat_session.active_step_id))

    def _is_terminal_skill_position(
        self, skill: Skill, active_step_id: str | None, slots: dict[str, Any]
    ) -> bool:
        """判断指定步骤是否属于终态节点,并结合槽位与动作确认终态位置条件成立。"""
        if not active_step_id:
            return False
        content = skill.content_json or {}
        terminal_node_ids = {str(node_id) for node_id in content.get("terminal_node_ids", [])}
        if active_step_id not in terminal_node_ids:
            return False
        return GraphRules.terminal_position_from_step(
            content,
            active_step_id,
            slots,
            self._current_skill_step(skill, active_step_id),
            self._skill_slot_satisfied,
            self._step_actions,
        )

    def _current_step_can_finish_after_tool(self, skill: Skill, chat_session: ChatSession) -> bool:
        """工具成功后判断当前步骤能否收尾:动作允许最终回复且期望收集的信息已齐备。"""
        step = self._current_skill_step(skill, chat_session.active_step_id)
        if not step:
            return False
        actions = self._step_actions(step)
        if not self._actions_allow_final_reply(actions):
            return False
        expected = [str(field) for field in step.get("expected_user_info", [])]
        return all(
            self._skill_slot_satisfied(chat_session.slots_json or {}, field) for field in expected
        )

    def _actions_allow_final_reply(self, actions: list[str]) -> bool:
        """判断动作列表是否包含 answer_user,即允许直接给出最终回复。"""
        return GraphRules.actions_allow_final_reply(actions)

    def _complete_active_skill(
        self, tenant_id: str, chat_session: ChatSession, skill: Skill, reason: str
    ) -> None:
        """结束当前活动技能并记录 skill_completed 事件,附带恢复后的技能与步骤位置。"""
        before_skill = chat_session.active_skill_id
        before_step = chat_session.active_step_id
        self.runtime.complete_current_skill(chat_session)
        self.events.record(
            tenant_id,
            chat_session.id,
            "skill_completed",
            {
                "skill_id": before_skill or skill.skill_id,
                "step_id": before_step,
                "reason": reason,
                "resumed_skill_id": chat_session.active_skill_id,
                "resumed_step_id": chat_session.active_step_id,
            },
        )

    def _finalize_execution_after_reply(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
    ) -> ExecutionFinalizeState:
        """回复之后决定执行走向:继续、完成或转人工,具体规则委托给 TurnFinalizer。

        返回 ExecutionFinalizeState 之一,调用方据此决定是否继续推进或结束本轮。
        """
        return TurnFinalizer.finalize(
            tenant_id,
            chat_session,
            active_skill,
            router_decision,
            step_result,
            tool_result,
            current_step_allows_handoff=self._current_step_allows_human_handoff,
            route_to_handoff_node=self._maybe_route_to_handoff_node,
            create_handoff=self._create_human_handoff_request,
            record_event=self.events.record,
            should_complete=self._should_complete_skill,
            complete_skill=self._complete_active_skill,
        )

    def _create_human_handoff_request(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        active_skill: Skill | None,
        step_result: StepAgentResult,
    ) -> HumanHandoffRequest:
        """创建人工转接工单:解析处理人与通知渠道,并给处理人发送渠道通知。

        处理人优先级为 step.assignee_user_id、binding 默认人、服务层兜底;
        通知失败只记日志,网页收件箱仍可兜底。
        """
        # SOP 节点指定的处理人:从当前 step 的 assignee_user_id 字段读取
        # (handoff 类型节点或 allowed_actions 含 handoff_human 的节点可配置)。
        # assignee_notify_channel 指定投递渠道:None=默认;"web"=仅网页端;绑定渠道=按渠道转接。
        step_assignee_user_id: str | None = None
        step_notify_channel: str | None = None
        current_step = (
            self._current_skill_step(active_skill, chat_session.active_step_id)
            if active_skill
            else None
        )
        if isinstance(current_step, dict):
            step_assignee_user_id = (
                str(current_step.get("assignee_user_id") or "").strip() or None
            )
            step_notify_channel = (
                str(current_step.get("assignee_notify_channel") or "").strip() or None
            )
        # 当前渠道默认处理人:从会话所属 binding 的 config_json 读取。
        binding_default_assignee_user_id, binding_default_notify_channel = (
            self._binding_default_handoff_assignee(tenant_id, chat_session)
        )
        handoff = HumanHandoffService(self.db, self.events).create(
            tenant_id,
            chat_session,
            step_result,
            current_step_resolver=lambda: current_step,
            assignee_resolver=self._human_handoff_assignee_user_id,
            context_summary=self._human_handoff_context_summary,
            pending_question=self._human_handoff_pending_question,
            step_assignee_user_id=step_assignee_user_id,
            binding_default_assignee_user_id=binding_default_assignee_user_id,
            step_notify_channel=step_notify_channel,
            binding_default_notify_channel=binding_default_notify_channel,
        )
        # 给 assignee 发渠道私聊通知。失败仅记日志,不影响 handoff 主流程
        # (网页收件箱仍可兜底)。
        self._maybe_notify_handoff_assignee(tenant_id, chat_session, handoff)
        return handoff

    def _binding_default_handoff_assignee(
        self,
        tenant_id: str,
        chat_session: ChatSession,
    ) -> tuple[str | None, str | None]:
        """会话所属渠道绑定配置的默认人工处理人及其通知渠道。

        从 ChatSession.channel_binding_id 反查 binding(而非 agent 挂载列表取首个),
        读取 config_json.default_handoff_assignee_user_id 与
        default_handoff_assignee_channel。无 binding 或未配置返回 (None, None)。
        """
        if not chat_session.channel_binding_id:
            return None, None
        binding = self.db.get(ChannelBinding, chat_session.channel_binding_id)
        if not binding or binding.tenant_id != tenant_id:
            return None, None
        config = binding.config_json if isinstance(binding.config_json, dict) else {}
        value = str(config.get("default_handoff_assignee_user_id") or "").strip()
        if not value:
            return None, None
        channel = str(config.get("default_handoff_assignee_channel") or "").strip()
        return value, (channel or None)

    def _maybe_notify_handoff_assignee(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        handoff: HumanHandoffRequest,
    ) -> None:
        """按通知渠道偏好解析投递 binding,给 assignee 登记渠道私聊通知。

        绑定解析规则:
        - 偏好为具体渠道(如 feishu)时:优先会话所属 binding(渠道匹配且 active);
          会话无 binding 或渠道不匹配时,在租户内找该渠道的任一 active 员工绑定。
        - 偏好为 None(默认)时:用会话所属 binding(渠道支持私聊通知即可达)。
        - 偏好为 "web" 时:仅网页收件箱,直接返回。

        无可用 binding(含日志说明)或 assignee 在该 binding scope 无非群聊身份时,
        由 notify_handoff_assignee 内部跳过,网页收件箱兜底。
        """
        from app.channels.service_outbox import (
            HANDOFF_NOTIFY_CHANNELS,
            notify_handoff_assignee,
            resolve_handoff_notify_binding,
        )

        metadata = handoff.metadata_json if isinstance(handoff.metadata_json, dict) else {}
        notify_channel = str(metadata.get("assignee_notify_channel") or "").strip()
        if notify_channel == "web":
            return
        binding: ChannelBinding | None = None
        if notify_channel:
            # 指定渠道:优先会话所属 binding,渠道不匹配时回退租户内该渠道任一 binding。
            if chat_session.channel_binding_id:
                session_binding = self.db.get(ChannelBinding, chat_session.channel_binding_id)
                if (
                    session_binding
                    and session_binding.tenant_id == tenant_id
                    and session_binding.channel == notify_channel
                    and session_binding.status == "active"
                ):
                    binding = session_binding
            binding = binding or resolve_handoff_notify_binding(self.db, tenant_id, notify_channel)
            if binding is None:
                logger.warning(
                    "handoff 通知跳过:租户无可用的 %s 绑定 handoff=%s", notify_channel, handoff.id
                )
                return
        else:
            # 默认投递:用会话所属 binding,渠道支持私聊通知即可达。
            if not chat_session.channel_binding_id:
                return
            session_binding = self.db.get(ChannelBinding, chat_session.channel_binding_id)
            if (
                not session_binding
                or session_binding.tenant_id != tenant_id
                or session_binding.status != "active"
            ):
                return
            if session_binding.channel not in HANDOFF_NOTIFY_CHANNELS:
                return
            binding = session_binding
        notify_handoff_assignee(
            self.db,
            binding,
            handoff,
            handoff.pending_question or "",
            handoff.context_summary or "",
        )

    def _apply_step_result(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        step_result: StepAgentResult,
        active_skill: Skill | None = None,
    ) -> None:
        """把步骤结果写回会话:合并槽位、同步等待输入,并按图谱校验和切换活动步骤。

        非法的 next_step_id 会被丢弃并把步骤标记为未完成;存在待处理分支时先推进队列,
        而不是直接跳到步骤声明的下一节点。
        """
        source_skill_id = chat_session.active_skill_id
        source_step_id = chat_session.active_step_id
        if step_result.slot_updates:
            chat_session.slots_json = {
                **(chat_session.slots_json or {}),
                **step_result.slot_updates,
            }
            self.events.record(
                tenant_id,
                chat_session.id,
                "slot_updated",
                {"slot_updates": step_result.slot_updates, "slots": chat_session.slots_json},
            )

        active_skill_matches = bool(
            active_skill and active_skill.skill_id == chat_session.active_skill_id
        )
        invalid_next_step = False
        if active_skill_matches and step_result.next_step_id:
            next_step_id = str(step_result.next_step_id).strip()
            if not self._skill_has_step(active_skill, next_step_id):
                self.events.record(
                    tenant_id,
                    chat_session.id,
                    "step_agent_result_repaired",
                    {
                        "mode": "invalid_next_step_ignored",
                        "active_skill_id": chat_session.active_skill_id,
                        "active_step_id": chat_session.active_step_id,
                        "invalid_next_step_id": step_result.next_step_id,
                    },
                )
                step_result.next_step_id = None
                step_result.is_step_completed = False
                invalid_next_step = True

        self._sync_awaiting_input_from_step_result(
            chat_session,
            step_result,
            active_skill,
            source_skill_id=source_skill_id,
            source_step_id=source_step_id,
        )

        if not chat_session.active_skill_id:
            return
        if invalid_next_step:
            return
        if active_skill_matches and step_result.next_step_id:
            next_step_id = str(step_result.next_step_id).strip()
            source_step_id = chat_session.active_step_id
            pending_steps = self._graph_pending_steps(chat_session)
            if pending_steps:
                if next_step_id in pending_steps:
                    pending_steps = [item for item in pending_steps if item != next_step_id]
                    self._store_graph_pending_steps(tenant_id, chat_session, pending_steps)
                    self._change_active_step(
                        tenant_id,
                        chat_session,
                        next_step_id,
                        reason="graph_merge_step",
                    )
                    return

                if next_step_id not in pending_steps:
                    pending_steps.append(next_step_id)
                    self._store_graph_pending_steps(tenant_id, chat_session, pending_steps)
                if self._activate_next_pending_graph_step(
                    tenant_id,
                    chat_session,
                    active_skill,
                    reason="graph_sibling_step",
                ):
                    step_result.next_step_id = chat_session.active_step_id
                return

            self._queue_graph_sibling_steps(
                tenant_id,
                chat_session,
                active_skill,
                source_step_id,
                next_step_id,
            )

        if step_result.next_step_id:
            self._change_active_step(tenant_id, chat_session, str(step_result.next_step_id).strip())
            return

        if active_skill_matches and step_result.is_step_completed:
            if self._activate_next_pending_graph_step(
                tenant_id,
                chat_session,
                active_skill,
                reason="graph_pending_step",
            ):
                step_result.next_step_id = chat_session.active_step_id

    def _sync_awaiting_input_from_step_result(
        self,
        chat_session: ChatSession,
        step_result: StepAgentResult,
        active_skill: Skill | None,
        *,
        source_skill_id: str | None,
        source_step_id: str | None,
    ) -> None:
        """根据步骤结果同步等待输入状态:追问时记录缺失字段,继续推进或信息齐全时清除。"""
        if not active_skill or active_skill.skill_id != source_skill_id or not source_step_id:
            return

        step = self._current_skill_step(active_skill, source_step_id)
        if not step:
            return
        missing_fields = [
            str(field)
            for field in step.get("expected_user_info", [])
            if not self._skill_slot_satisfied(chat_session.slots_json or {}, str(field))
        ]
        is_waiting_reply = step_result.action in {"ask_user", "clarify"}
        if is_waiting_reply and missing_fields:
            previous = (
                chat_session.awaiting_input_json
                if isinstance(chat_session.awaiting_input_json, dict)
                else {}
            )
            awaiting_input = {
                "skill_id": source_skill_id,
                "step_id": source_step_id,
                "expected_fields": missing_fields,
                "question_summary": str(step_result.reply or "").strip() or None,
            }
            if previous.get("task_id"):
                awaiting_input["task_id"] = previous["task_id"]
            chat_session.awaiting_input_json = awaiting_input
            chat_session.last_agent_question = awaiting_input["question_summary"]
            return

        should_clear = bool(
            step_result.next_step_id
            or step_result.tool_call
            or step_result.is_step_completed
            or not missing_fields
        )
        awaiting = chat_session.awaiting_input_json
        if not should_clear or not isinstance(awaiting, dict):
            return
        if awaiting.get("skill_id") not in {None, source_skill_id}:
            return
        if awaiting.get("step_id") not in {None, source_step_id}:
            return
        task_id = awaiting.get("task_id")
        chat_session.awaiting_input_json = {"task_id": task_id} if task_id else None
        chat_session.last_agent_question = None

    def _change_active_step(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        next_step_id: str,
        *,
        reason: str | None = None,
    ) -> None:
        """切换会话的活动步骤,位置真正变化时记录 skill_step_changed 事件。"""
        previous_step = chat_session.active_step_id
        chat_session.active_step_id = next_step_id
        if previous_step == next_step_id:
            return
        payload: dict[str, Any] = {
            "from_skill_id": chat_session.active_skill_id,
            "to_skill_id": chat_session.active_skill_id,
            "from_step_id": previous_step,
            "to_step_id": next_step_id,
        }
        if reason:
            payload["reason"] = reason
        self.events.record(tenant_id, chat_session.id, "skill_step_changed", payload)

    def _graph_pending_steps(self, chat_session: ChatSession) -> list[str]:
        """读取暂存在槽位里的图谱待处理分支步骤列表。"""
        value = (chat_session.slots_json or {}).get(GRAPH_PENDING_STEPS_SLOT)
        return GraphRules.normalize_pending_steps(value)

    def _store_graph_pending_steps(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        pending_steps: list[str],
    ) -> None:
        """把待处理分支步骤写回会话槽位,空列表表示清除,并记录更新事件。"""
        slots = dict(chat_session.slots_json or {})
        normalized = GraphRules.normalize_pending_steps(pending_steps)
        if normalized:
            slots[GRAPH_PENDING_STEPS_SLOT] = normalized
        else:
            slots.pop(GRAPH_PENDING_STEPS_SLOT, None)
        chat_session.slots_json = slots
        self.events.record(
            tenant_id,
            chat_session.id,
            "graph_pending_steps_updated",
            {"pending_step_ids": normalized},
        )

    def _queue_graph_sibling_steps(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        active_skill: Skill,
        source_step_id: str | None,
        selected_step_id: str,
    ) -> None:
        """把当前步骤其它出边指向的节点登记为待处理分支,供后续回合依次推进。"""
        if not source_step_id:
            return
        outgoing = self._graph_outgoing_edges(active_skill).get(source_step_id) or []
        sibling_steps = GraphRules.sibling_steps_from_edges(
            outgoing,
            selected_step_id,
            self._edge_condition,
        )
        if not sibling_steps:
            return
        pending_steps = self._graph_pending_steps(chat_session)
        for step_id in sibling_steps:
            if step_id not in pending_steps:
                pending_steps.append(step_id)
        self._store_graph_pending_steps(tenant_id, chat_session, pending_steps)

    def _edge_condition(self, edge: dict[str, Any]) -> str:
        """读取连线上的条件表达式,统一转小写,缺省时返回空串。"""
        return GraphRules.edge_condition(edge)

    def _activate_next_pending_graph_step(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        active_skill: Skill,
        *,
        reason: str,
    ) -> bool:
        """按队列顺序激活下一个仍存在的待处理步骤;全部失效时清空队列并返回 False。"""
        pending_steps = self._graph_pending_steps(chat_session)
        while pending_steps:
            next_step_id = pending_steps.pop(0)
            if not self._skill_has_step(active_skill, next_step_id):
                continue
            self._store_graph_pending_steps(tenant_id, chat_session, pending_steps)
            self._change_active_step(tenant_id, chat_session, next_step_id, reason=reason)
            return True
        self._store_graph_pending_steps(tenant_id, chat_session, [])
        return False

    def _skill_has_step(self, skill: Skill, step_id: str | None) -> bool:
        """判断技能节点集合里是否存在指定步骤 ID。"""
        return GraphRules.has_step(skill.content_json or {}, step_id)

    def _first_step_id(self, skill: Skill) -> str | None:
        """确定技能起始步骤:优先 start_node_id,不存在时退回排序后的第一个节点。"""
        content = skill.content_json or {}
        start_node_id = str(content.get("start_node_id") or "").strip()
        if start_node_id and self._skill_has_step(skill, start_node_id):
            return start_node_id
        steps = self._skill_steps(skill)
        first_step = steps[0] if steps and isinstance(steps[0], dict) else None
        return first_step.get("step_id") if first_step else None

    def _skill_steps(self, skill: Skill) -> list[dict[str, Any]]:
        """按图谱顺序返回技能的步骤视图,由有序节点转换而来。"""
        return GraphRules.steps_from_nodes(self._ordered_skill_nodes(skill))

    def _skill_nodes(self, skill: Skill) -> list[dict[str, Any]]:
        """返回技能 content_json 中声明的原始节点列表。"""
        return GraphRules.nodes(skill.content_json or {})

    def _ordered_skill_nodes(self, skill: Skill) -> list[dict[str, Any]]:
        """按拓扑顺序排列技能节点,起点与可达性由 GraphRules 处理。"""
        content = skill.content_json or {}
        return GraphRules.ordered_nodes(
            content,
            nodes=self._skill_nodes(skill),
            outgoing=self._graph_outgoing_edges(skill),
        )

    def _graph_outgoing_edges(self, skill: Skill) -> dict[str, list[dict[str, Any]]]:
        """按源节点归组技能图谱的出边,并保持优先级排序。"""
        return GraphRules.outgoing_edges(skill.content_json or {})

    def _default_next_step(self, skill: Skill, active_step_id: str | None) -> dict[str, Any] | None:
        """取当前步骤的出边推导默认后继节点,无出边时返回 None。"""
        if not active_step_id:
            return None
        return GraphRules.default_next_step_from_parts(
            self._skill_nodes(skill),
            self._graph_outgoing_edges(skill).get(active_step_id, []),
        )

    def _get_or_create_session(self, request: ChatTurnRequest) -> ChatSession:
        """按请求取会话,不存在则新建;已有会话缺少 agent 时补写 agent_id。"""
        session_id = request.session_id or new_id("session")
        chat_session = self.db.get(ChatSession, session_id)
        if not chat_session:
            chat_session = ChatSession(
                id=session_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                agent_id=request.agent_id,
                channel=(
                    request.channel
                    if request.channel in {PILOTDECK_GROUP_CHAT_CHANNEL, "skill_test"}
                    else None
                ),
            )
            self.db.add(chat_session)
            self.db.flush()
        elif not chat_session.agent_id and request.agent_id:
            chat_session.agent_id = request.agent_id
        return chat_session

    def _current_skill_step(
        self, skill: Skill, active_step_id: str | None
    ) -> dict[str, Any] | None:
        """按步骤 ID 查找当前技能中的步骤定义,未命中返回 None。"""
        if not active_step_id:
            return None
        return GraphRules.current_step_from_steps(self._skill_steps(skill), active_step_id)

    def _skill_slot_satisfied(self, slots: dict[str, Any], field: str) -> bool:
        """判断信息槽位是否已填值,空字段名视为已满足。"""
        return GraphRules.slot_satisfied(slots, field)

    def _get_request_model(
        self,
        request: ChatTurnRequest,
        agent_id: str | None = None,
        role: str = "default",
    ) -> ModelConfig | None:
        """解析本轮使用的模型配置:指定 ID 时校验归属与启用状态,否则回退到默认模型。"""
        if request.model_config_id:
            row = self.db.get(ModelConfig, request.model_config_id)
            if not row or row.tenant_id != request.tenant_id:
                raise AgentLoopPreconditionError("invalid_model_config", "选中的模型配置不存在。")
            if not row.enabled:
                raise AgentLoopPreconditionError("disabled_model_config", "选中的模型配置已停用。")
            return resolve_model_config_for_runtime(self.db, request.tenant_id, row.id)
        return self._get_default_model(request.tenant_id, agent_id, role)

    def _get_default_model(
        self, tenant_id: str, agent_id: str | None = None, role: str = "default"
    ) -> ModelConfig | None:
        """按 agent 与角色解析租户默认模型配置。"""
        return model_for_agent(self.db, tenant_id, agent_id, role)

    def _get_persona_prompt(self, tenant_id: str, agent_id: str | None = None) -> str | None:
        """解析人设提示词:子 agent 用身份提示词,全局 agent 用其人设,最后回退租户 PersonaConfig。"""
        agent = self._get_agent_profile(tenant_id, agent_id)
        if agent and not agent.is_overall:
            return _agent_identity_prompt(agent)
        if agent and agent.is_overall and agent.persona_prompt:
            return agent.persona_prompt
        row = self.db.get(PersonaConfig, tenant_id)
        return row.system_prompt if row else None

    def _get_agent_loop_max_actions(
        self, tenant_id: str, agent_id: str | None = None
    ) -> int:
        """解析单轮工具动作上限:agent 配置优先,其次租户 UIConfig,最后用常量并夹在允许区间内。"""
        if not hasattr(self.db, "get"):
            return MAX_TOOL_ACTIONS_PER_TURN
        agent = self.db.get(AgentProfile, agent_id) if agent_id else None
        if agent is not None and (
            agent.tenant_id != tenant_id or agent.status != "active"
        ):
            agent = None
        if agent is not None:
            value = agent.harness_max_actions
            return max(1, min(int(value), MAX_TOOL_ACTIONS_PER_TURN_LIMIT))
        row = self.db.get(UIConfig, tenant_id)
        value = row.agent_loop_max_actions if row else MAX_TOOL_ACTIONS_PER_TURN
        return max(1, min(int(value), MAX_TOOL_ACTIONS_PER_TURN_LIMIT))

    def _get_conversation_context_settings(
        self,
        tenant_id: str,
    ) -> ConversationContextSettings:
        """从租户 UIConfig 读取上下文压缩参数,缺失字段取默认值后归一化。"""
        if not hasattr(self.db, "get"):
            return ConversationContextSettings()
        row = self.db.get(UIConfig, tenant_id)
        if row is None:
            return ConversationContextSettings()
        return ConversationContextSettings(
            token_budget=getattr(row, "context_token_budget", 32_000),
            compaction_trigger_ratio=getattr(
                row,
                "context_compaction_trigger_ratio",
                0.70,
            ),
            recent_round_limit=getattr(row, "context_recent_round_limit", 6),
            long_summary_token_budget=getattr(
                row,
                "context_long_summary_token_budget",
                4_000,
            ),
            medium_summary_token_budget=getattr(
                row,
                "context_medium_summary_token_budget",
                4_000,
            ),
            allowed_roles=frozenset(
                getattr(row, "context_allowed_roles", None)
                or {"user", "assistant"}
            ),
            long_summary_prefix=getattr(
                row,
                "context_long_summary_prefix",
                "历史的信息可以被总结为：",
            ),
            medium_summary_prefix=getattr(
                row,
                "context_medium_summary_prefix",
                "近期的历史信息总结为：",
            ),
        ).normalized()

    def _list_published_skills(self, tenant_id: str, agent_id: str | None = None) -> list[Skill]:
        """列出该租户下对指定 agent 可见的已发布技能。"""
        return visible_published_skills(self.db, tenant_id, agent_id)

    def _get_agent_profile(self, tenant_id: str, agent_id: str | None) -> AgentProfile | None:
        """按 ID 取启用中的同租户 agent 档案,条件不满足返回 None。"""
        if not agent_id:
            return None
        row = self.db.get(AgentProfile, agent_id)
        if not row or row.tenant_id != tenant_id or row.status != "active":
            return None
        return row

    def _get_active_skill(
        self, tenant_id: str, skill_id: str | None, agent_id: str | None = None
    ) -> Skill | None:
        """按 ID 取对指定 agent 可见的技能,不可见或不存在时返回 None。"""
        if not skill_id:
            return None
        return visible_skill(self.db, tenant_id, skill_id, agent_id)

    def _drop_unavailable_skill_state(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        skills: list[Skill],
    ) -> bool:
        """清理指向已不可用技能的状态:技能栈、待办、等待输入与活动技能,并尽量修复步骤位置。

        返回是否发生修改;有修改时记录 skill_state_pruned 事件并刷新 updated_at。
        """
        skills_by_id = {skill.skill_id: skill for skill in skills}
        available_skill_ids = set(skills_by_id)
        changed = False
        removed_skill_ids: set[str] = set()
        repaired_steps: list[dict[str, str | None]] = []

        if chat_session.skill_stack_json or chat_session.resume_after_answer_json:
            chat_session.skill_stack_json = []
            chat_session.resume_after_answer_json = None
            changed = True

        def frame_skill_id(frame: object) -> str:
            """取出待办帧里的目标技能 ID,兼容 target_skill_id 与 skill_id 两种字段。"""
            if not isinstance(frame, dict):
                return ""
            return str(frame.get("target_skill_id") or frame.get("skill_id") or "").strip()

        def keep_frame(frame: object) -> bool:
            """判断待办帧是否保留:技能可用或无技能标记时保留,否则登记为被移除项。"""
            skill_id = frame_skill_id(frame)
            if not skill_id:
                return True
            if skill_id in available_skill_ids:
                return True
            removed_skill_ids.add(skill_id)
            return False

        active_skill_id = str(chat_session.active_skill_id or "").strip()
        if active_skill_id and active_skill_id not in available_skill_ids:
            removed_skill_ids.add(active_skill_id)
            chat_session.active_skill_id = None
            chat_session.active_step_id = None
            chat_session.slots_json = {}
            chat_session.awaiting_input_json = None
            chat_session.resume_after_answer_json = None
            changed = True
        elif active_skill_id:
            active_skill = skills_by_id[active_skill_id]
            active_step_id = str(chat_session.active_step_id or "").strip()
            restored_step_id = self._first_step_id(active_skill)
            if (
                restored_step_id
                and active_step_id != restored_step_id
                and not self._skill_has_step(active_skill, active_step_id)
            ):
                chat_session.active_step_id = restored_step_id
                awaiting = (
                    chat_session.awaiting_input_json
                    if isinstance(chat_session.awaiting_input_json, dict)
                    else {}
                )
                task_id = awaiting.get("task_id")
                chat_session.awaiting_input_json = {"task_id": task_id} if task_id else None
                chat_session.last_agent_question = None
                repaired_steps.append(
                    {
                        "skill_id": active_skill_id,
                        "from_step_id": active_step_id,
                        "to_step_id": restored_step_id,
                    }
                )
                changed = True

        for attr in ("pending_tasks_json",):
            value = getattr(chat_session, attr) or []
            if not isinstance(value, list):
                continue
            kept = [frame for frame in value if keep_frame(frame)]
            if len(kept) != len(value):
                setattr(chat_session, attr, kept)
                changed = True

        awaiting = chat_session.awaiting_input_json
        if isinstance(awaiting, dict):
            awaiting_skill_id = str(awaiting.get("skill_id") or "").strip()
            if awaiting_skill_id and awaiting_skill_id not in available_skill_ids:
                removed_skill_ids.add(awaiting_skill_id)
                chat_session.awaiting_input_json = None
                changed = True

        if changed:
            chat_session.updated_at = utc_now()
            if hasattr(self, "events"):
                self.events.record(
                    tenant_id,
                    chat_session.id,
                    "skill_state_pruned",
                    {
                        "removed_skill_ids": sorted(removed_skill_ids),
                        "repaired_steps": repaired_steps,
                    },
                )
        return changed

    def _conversation_context(
        self,
        chat_session: ChatSession,
        model_config: ModelConfig | None = None,
    ) -> dict[str, object]:
        """构建本轮对话上下文:读取可见消息、按配置压缩,并把新的压缩状态回写会话。"""
        if not hasattr(self, "db") or not hasattr(self.db, "exec"):
            return build_conversation_context([])
        rows = list(
            self.db.exec(
                select(Message)
                .where(
                    Message.tenant_id == chat_session.tenant_id,
                    Message.session_id == chat_session.id,
                )
                .order_by(Message.created_at.asc())
            ).all()
        )
        visible_rows = visible_message_rows(rows)
        context = build_conversation_context(
            [
                ConversationProjection.message_context_entry(
                    row,
                    content=visible_message_content(row),
                )
                for row in visible_rows
            ],
            settings=self._get_conversation_context_settings(chat_session.tenant_id),
            context_state=chat_session.context_state_json,
            summary_builder=self._context_summary_builder(model_config) if model_config else None,
        )
        next_state = context.get("context_state")
        if isinstance(next_state, dict) and next_state != (chat_session.context_state_json or {}):
            chat_session.context_state_json = next_state
            self.db.add(chat_session)
        return context

    def _context_summary_builder(self, model_config: ModelConfig) -> Callable[[str, str, int], str]:
        """返回压缩函数:用给定模型把历史片段压成可复用的中文事实摘要。"""
        def summarize(label: str, source: str, token_budget: int) -> str:
            """调用 LLM 把指定历史片段压缩到 token 预算内的中文纯文本摘要。"""
            payload = stage_payload(
                phase="Context Compression",
                user_message=f"请压缩{label}",
                conversation_context={},
                memory_context=None,
                instructions=(
                    "把输入的历史对话压缩成一段可供后续对话继续使用的中文事实摘要。"
                    "保留用户身份与偏好、已确认事实、未完成任务、关键约束、工具或知识结论；"
                    "删除寒暄、重复内容、内部 ID、时间戳和推理过程，不新增原文没有的信息。"
                ),
                stage_data={"history_to_compress": source},
                output_contract=(f"只输出一段纯文本摘要，控制在约 {token_budget} tokens 以内。"),
            )
            with llm_operation("context.compact"):
                return (
                    LLMClient(model_config).generate_text(unified_system_prompt(), payload).strip()
                )

        return summarize

    def _message_context_entry(self, row: Message) -> dict[str, Any]:
        """把消息行转换成上下文条目,附带附件文本与用户图片信息。"""
        return ConversationProjection.message_context_entry(row)

    def _assistant_message_metadata(
        self,
        step_result: StepAgentResult | None,
        chat_session: ChatSession,
        source_message: str | None = None,
    ) -> dict[str, Any]:
        """生成助手消息元数据,主要是去重后的知识引用与最近一次检索条件。"""
        return ConversationProjection.assistant_message_metadata(
            step_result, citation_deduper=self._dedupe_knowledge_citations
        )

    def _dedupe_knowledge_citations(self, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按引用标识去重知识引用,保留首次出现顺序并跳过非字典项。"""
        return ConversationProjection.dedupe_knowledge_citations(citations)

    def _append_message(
        self,
        tenant_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """新增一条消息并加入当前事务,不提交、不刷新。"""
        message = Message(
            tenant_id=tenant_id,
            session_id=session_id,
            role=role,
            content=content,
            metadata_json=metadata or {},
        )
        self.db.add(message)
        return message

    def _persist_cancelled_assistant_message(
        self,
        tenant_id: str,
        chat_session: ChatSession,
        user_message_id: str,
        client_turn_id: str | None = None,
    ) -> Message | None:
        """取消回合时补写一条 CANCELLED_ASSISTANT_REPLY 助手消息,避免用户消息悬空。

        先校验用户消息归属并跳过已有助手回复的回合,重复调用返回 None;
        沿用原消息可见性,并记录消息创建与会话状态事件。
        """
        if not user_message_id:
            return None
        user_message = self.db.get(Message, user_message_id)
        if (
            not user_message
            or user_message.tenant_id != tenant_id
            or user_message.session_id != chat_session.id
            or user_message.role != "user"
        ):
            return None

        normalized_client_turn_id = (client_turn_id or "").strip()
        turn_ids = {user_message_id}
        if normalized_client_turn_id:
            turn_ids.add(normalized_client_turn_id)
        existing_messages = self.db.exec(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.session_id == chat_session.id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at)
        ).all()
        for row in existing_messages:
            metadata = row.metadata_json or {}
            row_turn_ids = {
                str(metadata.get("turn_id") or "").strip(),
                str(metadata.get("user_message_id") or "").strip(),
                str(metadata.get("client_turn_id") or "").strip(),
            }
            if turn_ids & row_turn_ids:
                return None

        chat_session.updated_at = utc_now()
        chat_session.status = "active"
        chat_session.summary = f"最近回复：{CANCELLED_ASSISTANT_REPLY}"
        user_visibility = str(
            (user_message.metadata_json or {}).get("message_visibility") or "visible"
        )
        cancelled_metadata = {
            "turn_id": user_message_id,
            "user_message_id": user_message_id,
            "client_turn_id": normalized_client_turn_id or None,
            "status": "cancelled",
        }
        if user_visibility != "visible":
            cancelled_metadata["message_visibility"] = user_visibility
        assistant_message = self._append_message(
            tenant_id,
            chat_session.id,
            "assistant",
            CANCELLED_ASSISTANT_REPLY,
            metadata=cancelled_metadata,
        )
        self.events.record(
            tenant_id,
            chat_session.id,
            "assistant_message_created",
            {
                "message_id": assistant_message.id,
                "assistant_message_id": assistant_message.id,
                "user_message_id": user_message_id,
                "turn_id": user_message_id,
                "client_turn_id": normalized_client_turn_id or None,
                "reply": CANCELLED_ASSISTANT_REPLY,
                "status": "cancelled",
            },
        )
        self.events.record(
            tenant_id,
            chat_session.id,
            "session_state_changed",
            public_session(chat_session).model_dump(),
        )
        return assistant_message

    def _user_message_metadata(self, request: ChatTurnRequest) -> dict[str, Any]:
        """生成用户消息元数据,记录 client_turn_id、可见性、附件等非默认字段。"""
        return ConversationProjection.user_message_metadata(request)

    def _enqueue_memory_capture(
        self,
        request: ChatTurnRequest,
        chat_session: ChatSession,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        model_config: ModelConfig,
    ) -> list[dict[str, object]]:
        """把记忆抽取登记为异步任务并返回任务标识;入队失败只记事件、不阻断本轮回复。"""
        try:
            job = enqueue_memory_capture(
                request,
                chat_session.id,
                step_result,
                tool_result,
                model_config.id,
            )
        except Exception as exc:
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "memory_error",
                {"message": str(exc)},
            )
            return []
        self.events.record(
            request.tenant_id,
            chat_session.id,
            "async_job_enqueued",
            {"job_id": job.id, "job_name": job.name, "feature": "memory"},
        )
        self.db.commit()
        return [{"job_id": job.id, "job_name": job.name}]

    def _finish_with_error(
        self, chat_session: ChatSession, code: str, message: str
    ) -> ChatTurnResponse:
        """前置条件或命令错误时统一收尾:拼装失败回复、落库并返回回合响应。"""
        reply = format_runtime_failure_reply(
            "系统配置错误",
            message,
            code,
            "请在管理端补齐配置后重试。",
        )
        self.events.record(
            chat_session.tenant_id,
            chat_session.id,
            "error_occurred",
            {"code": code, "message": message},
        )
        reply = self._finalize_turn(chat_session, chat_session.tenant_id, reply)
        self.db.commit()
        self.db.refresh(chat_session)
        return ChatTurnResponse(
            reply=reply,
            session_id=chat_session.id,
            session_state=public_session(chat_session),
        )

    def _finalize_turn(
        self,
        chat_session: ChatSession,
        tenant_id: str,
        reply: str,
        step_result: StepAgentResult | None = None,
        source_message: str | None = None,
        user_message_id: str | None = None,
        assistant_metadata_override: dict[str, Any] | None = None,
    ) -> str:
        """收尾一轮对话:规整引用标签、补齐标题与摘要、写入助手消息并按需投递渠道。

        流式投递已成功时跳过渠道投递以免重复发送;返回最终落库的回复文本。
        """
        chat_session.updated_at = utc_now()
        if chat_session.status != "handoff":
            chat_session.status = "active"
        metadata = self._assistant_message_metadata(step_result, chat_session, source_message)
        if assistant_metadata_override:
            metadata = {**metadata, **dict(assistant_metadata_override)}
        reply = restore_truncated_atomic_references(reply, metadata.get("knowledge_citations"))
        reply = self._normalize_reply_citation_labels(reply, metadata.get("knowledge_citations"))
        reply = self._strip_trailing_citation_summary(reply)
        reply, compacted_citations = compact_knowledge_citation_labels(
            reply,
            metadata.get("knowledge_citations"),
        )
        metadata = dict(metadata)
        if compacted_citations:
            metadata["knowledge_citations"] = compacted_citations
        else:
            metadata.pop("knowledge_citations", None)
            metadata.pop("knowledge_query", None)
        if not chat_session.title and source_message:
            fallback_title = self._fallback_session_title_from_message(source_message)
            if fallback_title:
                chat_session.title = fallback_title
        chat_session.summary = f"最近回复：{reply[:120]}"
        assistant_metadata = dict(metadata or {})
        if user_message_id:
            assistant_metadata.setdefault("user_message_id", user_message_id)
            assistant_metadata.setdefault("turn_id", user_message_id)
        assistant_message = self._append_message(
            tenant_id,
            chat_session.id,
            "assistant",
            reply,
            metadata=assistant_metadata,
        )
        if not self.stream_delivery_succeeded:
            stage_channel_delivery(self.db, chat_session, assistant_message)
        event_payload: dict[str, Any] = {
            "message_id": assistant_message.id,
            "assistant_message_id": assistant_message.id,
            "reply": reply,
        }
        if user_message_id:
            event_payload["user_message_id"] = user_message_id
            event_payload["turn_id"] = user_message_id
        if assistant_metadata.get("knowledge_citations"):
            event_payload["knowledge_citations"] = assistant_metadata["knowledge_citations"]
        if assistant_metadata.get("message_visibility"):
            event_payload["message_visibility"] = assistant_metadata["message_visibility"]
        self.events.record(
            tenant_id,
            chat_session.id,
            "assistant_message_created",
            event_payload,
        )
        self.events.record(
            tenant_id,
            chat_session.id,
            "session_state_changed",
            public_session(chat_session).model_dump(),
        )
        return reply

    def _mark_session_running(self, chat_session: ChatSession) -> None:
        """把会话标记为 running 并刷新更新时间,handoff 状态保持不变。"""
        if chat_session.status == "handoff":
            return
        chat_session.status = "running"
        chat_session.updated_at = utc_now()
        self.db.add(chat_session)

    @staticmethod
    def _fallback_session_title_from_message(message: str) -> str:
        """从首条用户消息截出会话标题,压缩空白并截断到 28 字。"""
        return ConversationProjection.fallback_session_title(message)

    def _normalize_reply_citation_labels(self, reply: str, citations: object) -> str:
        """把超出引用数量的 [n] 标签收敛到最大编号,避免出现悬空引用。"""
        return ConversationProjection.normalize_reply_citation_labels(reply, citations)

    def _strip_trailing_citation_summary(self, reply: str) -> str:
        """删除回复末尾自动附带的「参考资料: [1][2]」摘要行。"""
        return ConversationProjection.strip_trailing_citation_summary(reply)
