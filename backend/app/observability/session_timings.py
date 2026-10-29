from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db.models import AgentEvent


@dataclass(frozen=True)
class _ModelSpan:
    """一次已结束的模型调用跨度，带所属任务帧与重试信息，供耗时归因使用。"""

    span_id: str
    operation: str
    model_name: str
    task_frame_id: str
    iteration: str
    request_attempt: int
    request_max_attempts: int
    json_attempt: int
    json_max_attempts: int
    started_ms: float
    finished_ms: float
    duration_ms: float


@dataclass
class _LineObservation:
    """某条轨迹日志行被观测到的时间点：运行中事件与终态事件分开记录。"""

    running_ms: list[float]
    terminal_ms: list[float]

    @property
    def all_ms(self) -> list[float]:
        """返回运行中与终态时间点的合集，按运行中在前、终态在后拼接。"""
        return [*self.running_ms, *self.terminal_ms]


def enrich_turn_traces_with_timings(
    traces: list[dict[str, Any]],
    events: list[AgentEvent],
) -> list[dict[str, Any]]:
    """给企业会话轨迹补齐可持久化的总耗时与模型耗时。

    耗时是持久事件的投影而非原始数据，因此每轮先清空旧字段再重算，避免历史缓存里
    残留 0ms / 0 次调用的占位值；返回值仍是传入的 traces 列表本身。
    """
    aliases = _turn_aliases(events)
    spans_by_turn = _model_spans_by_turn(events, aliases)
    observations_by_turn = _trace_observations_by_turn(events, aliases)
    windows_by_turn = _resolve_trace_windows(traces, observations_by_turn)
    _extend_action_windows_with_model_decisions(events, aliases, spans_by_turn, windows_by_turn)
    _extend_router_windows_with_planner(
        spans_by_turn,
        windows_by_turn,
        observations_by_turn,
    )

    for trace in traces:
        turn_id = str(trace.get("turn_id") or "").strip()
        spans = spans_by_turn.get(turn_id, [])
        windows = windows_by_turn.get(turn_id, {})
        observations = observations_by_turn.get(turn_id, {})
        lines = trace.get("lines") if isinstance(trace.get("lines"), list) else []

        # Timing fields are a projection over durable events rather than source
        # data. Always clear a previous projection first so historical cached
        # traces cannot keep the old ``0ms / 0 calls`` placeholders.
        for key in ("duration_ms", "model_duration_ms", "model_names", "model_call_count"):
            trace.pop(key, None)
        for line in lines:
            for key in ("duration_ms", "model_duration_ms", "model_names"):
                line.pop(key, None)

        response_spans = [
            span
            for span in spans
            if span.operation in {"response.generate", "response.generate_stream"}
        ]
        if response_spans and not any(line.get("id") == "response_generation" for line in lines):
            response_started = min(span.started_ms for span in response_spans)
            response_finished = max(span.finished_ms for span in response_spans)
            windows["response_generation"] = (response_started, response_finished)
            lines.append(
                {
                    "id": "response_generation",
                    "kind": "decision",
                    "text": "生成最终回复",
                    "detail": f"模型调用 {len(response_spans)} 次",
                    "state": "completed",
                }
            )

        for line in lines:
            line_id = str(line.get("id") or "")
            window = windows.get(line_id)
            if not window:
                continue
            started_ms, finished_ms = window
            if finished_ms < started_ms:
                continue
            model_duration_ms = _model_duration_in_window(
                spans,
                started_ms,
                finished_ms,
            )
            observation = observations.get(line_id)
            # A terminal-only event such as ``skill_started`` is an instantaneous
            # state transition. Its inferred window is merely the gap from the
            # previous log entry and must not be presented as execution time.
            has_measured_window = bool(observation and observation.running_ms)
            if not (
                has_measured_window
                or model_duration_ms is not None
                or line_id in {"decision_router", "response_generation"}
            ):
                continue
            line["duration_ms"] = round(max(0.0, finished_ms - started_ms), 3)
            if model_duration_ms is not None:
                line["model_duration_ms"] = model_duration_ms
            model_names = _model_names_in_window(spans, started_ms, finished_ms)
            if model_names:
                line["model_names"] = model_names

        _project_harness_model_calls(lines, spans)

        trace_started = _iso_ms(trace.get("started_at"))
        trace_finished = _iso_ms(trace.get("completed_at"))
        if trace_started is not None and trace_finished is not None:
            trace["duration_ms"] = round(max(0.0, trace_finished - trace_started), 3)
            model_duration_ms = _model_duration_in_window(
                spans,
                trace_started,
                trace_finished,
            )
            if model_duration_ms is not None:
                trace["model_duration_ms"] = model_duration_ms
            model_names = _model_names_in_window(spans, trace_started, trace_finished)
            if model_names:
                trace["model_names"] = model_names
            model_call_count = sum(
                1
                for span in spans
                if span.finished_ms >= trace_started - 1
                and span.started_ms <= trace_finished + 1
            )
            if model_call_count > 0:
                trace["model_call_count"] = model_call_count
    return traces


def _project_harness_model_calls(
    lines: list[dict[str, Any]],
    spans: list[_ModelSpan],
) -> None:
    """把每次 Harness 决策调用单独渲染到所属 TaskFrame 下。

    新跨度自带 task_frame_id 与 iteration；历史事件缺少关联时按先后顺序配对兜底，
    且不再把父级动作窗口的聚合耗时伪装成单次模型调用。
    """
    harness_spans = [span for span in spans if span.operation == "harness.task_action"]
    if not harness_spans:
        return

    action_rows: list[tuple[int, str, str, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        line_id = str(line.get("id") or "")
        parsed = _harness_action_line_key(line_id)
        if parsed is None:
            continue
        frame_id, iteration = parsed
        line["depth"] = 1
        action_rows.append((index, frame_id, iteration, line))

    if not action_rows:
        return

    scoped: dict[tuple[str, str], list[_ModelSpan]] = {}
    unscoped: list[_ModelSpan] = []
    for span in harness_spans:
        if span.task_frame_id and span.iteration:
            scoped.setdefault((span.task_frame_id, span.iteration), []).append(span)
        else:
            unscoped.append(span)

    assigned: dict[int, list[_ModelSpan]] = {}
    for index, frame_id, iteration, _line in action_rows:
        matches = scoped.get((frame_id, iteration), [])
        if matches:
            assigned[index] = matches

    # Older durable events predate explicit span correlation.  Their Harness
    # action events and task-action spans are both sequential, so pair only the
    # still-unassigned rows in order.
    remaining_rows = [row for row in action_rows if row[0] not in assigned]
    for row, span in zip(remaining_rows, unscoped, strict=False):
        assigned.setdefault(row[0], []).append(span)

    projected: list[dict[str, Any]] = []
    for index, frame_id, iteration, line in action_rows:
        matches = assigned.get(index, [])
        if not matches:
            continue
        # The action row represents the state transition/tool execution.  Its
        # old model timing was an inferred aggregate and would duplicate the
        # exact child calls below.
        line.pop("model_duration_ms", None)
        line.pop("model_names", None)
        line.pop("model_call_count", None)
        if line_id := str(line.get("id") or ""):
            if line_id.startswith("harness_finish_"):
                line.pop("duration_ms", None)

        for call_index, span in enumerate(matches, start=1):
            suffix = f"-{call_index}" if len(matches) > 1 else ""
            projected.append(
                {
                    "_insert_before": index,
                    "id": f"harness_model_{frame_id}_{iteration}{suffix}_{span.span_id}",
                    "kind": "thinking",
                    "text": _harness_model_call_text(line, iteration, call_index, len(matches)),
                    "detail": _harness_model_call_detail(span),
                    "state": "completed",
                    "depth": 1,
                    "model_duration_ms": round(span.duration_ms, 3),
                    "model_names": [span.model_name] if span.model_name else [],
                    "model_call_count": 1,
                }
            )

    if not projected:
        return
    by_index: dict[int, list[dict[str, Any]]] = {}
    for child in projected:
        insert_before = int(child.pop("_insert_before"))
        by_index.setdefault(insert_before, []).append(child)
    rebuilt: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        rebuilt.extend(by_index.get(index, []))
        rebuilt.append(line)
    lines[:] = rebuilt


def _harness_action_line_key(line_id: str) -> tuple[str, str] | None:
    """从 harness_action_* / harness_finish_* 行 id 解析出 (task_frame_id, iteration)。"""
    for prefix in ("harness_action_", "harness_finish_"):
        if not line_id.startswith(prefix):
            continue
        payload = line_id.removeprefix(prefix)
        frame_id, separator, iteration = payload.rpartition("_")
        if separator and frame_id and iteration:
            return frame_id, iteration
    return None


def _harness_model_call_text(
    action_line: dict[str, Any],
    iteration: str,
    call_index: int,
    call_count: int,
) -> str:
    """按动作行类型生成单次模型调用的中文标题，同一动作多次调用时标注尝试序号。"""
    action_text = str(action_line.get("text") or "")
    if action_line.get("kind") == "tool" or "能力调用" in action_text:
        decision = "决定调用能力"
    elif action_text == "整理任务结果":
        decision = "决定完成任务"
    else:
        decision = "模型决策"
    retry = f"（尝试 {call_index}/{call_count}）" if call_count > 1 else ""
    return f"第 {iteration} 轮{decision}{retry}"


def _harness_model_call_detail(span: _ModelSpan) -> str:
    """拼出模型调用的详情文本，仅在发生重试时标注 JSON 与请求尝试次数。"""
    parts = ["Harness 模型决策"]
    if span.json_max_attempts > 1:
        parts.append(f"JSON 尝试 {span.json_attempt}/{span.json_max_attempts}")
    if span.request_max_attempts > 1:
        parts.append(f"请求尝试 {span.request_attempt}/{span.request_max_attempts}")
    return " · ".join(parts)


def _turn_aliases(events: list[AgentEvent]) -> dict[str, str]:
    """建立轮次标识别名表，把 client_turn_id 映射到规范的用户消息 id。"""
    aliases: dict[str, str] = {}
    for event in events:
        payload = event.payload_json or {}
        canonical = str(payload.get("user_message_id") or "").strip()
        if event.event_type == "user_message_received":
            canonical = str(payload.get("message_id") or canonical).strip()
        if not canonical:
            continue
        aliases[canonical] = canonical
        client_turn_id = str(payload.get("client_turn_id") or "").strip()
        if client_turn_id:
            aliases[client_turn_id] = canonical
    return aliases


def _event_turn_id(event: AgentEvent, aliases: dict[str, str]) -> str:
    """取出事件所属的轮次 id，并按别名表归一化为规范 id；无法判定时返回空串。"""
    payload = event.payload_json or {}
    raw = str(
        payload.get("user_message_id")
        or payload.get("turn_id")
        or payload.get("client_turn_id")
        or (payload.get("message_id") if event.event_type == "user_message_received" else "")
        or ""
    ).strip()
    return aliases.get(raw, raw)


def _model_spans_by_turn(
    events: list[AgentEvent],
    aliases: dict[str, str],
) -> dict[str, list[_ModelSpan]]:
    """把 llm_call_finished 事件按轮次聚成模型调用跨度，并各自按结束时间排序。

    事件缺少 started_at 时用结束时间减去 duration_ms 反推，duration_ms 也缺失时补 0。
    """
    spans_by_turn: dict[str, list[_ModelSpan]] = {}
    for event in events:
        if event.event_type != "llm_call_finished":
            continue
        turn_id = _event_turn_id(event, aliases)
        if not turn_id:
            continue
        payload = event.payload_json or {}
        finished_ms = _event_ms(event)
        duration_ms = _float_ms(payload.get("duration_ms"))
        started_ms = _iso_ms(payload.get("started_at"))
        if started_ms is None:
            started_ms = finished_ms - duration_ms
        spans_by_turn.setdefault(turn_id, []).append(
            _ModelSpan(
                span_id=str(payload.get("span_id") or event.id).strip(),
                operation=str(payload.get("operation") or "llm.request"),
                model_name=str(
                    payload.get("model_name") or payload.get("model") or ""
                ).strip(),
                task_frame_id=str(payload.get("task_frame_id") or "").strip(),
                iteration=str(payload.get("iteration") or "").strip(),
                request_attempt=_positive_int(payload.get("attempt"), default=1),
                request_max_attempts=_positive_int(payload.get("max_attempts"), default=1),
                json_attempt=_positive_int(payload.get("json_attempt"), default=1),
                json_max_attempts=_positive_int(payload.get("json_max_attempts"), default=1),
                started_ms=started_ms,
                finished_ms=finished_ms,
                duration_ms=duration_ms or max(0.0, finished_ms - started_ms),
            )
        )
    for spans in spans_by_turn.values():
        spans.sort(key=lambda span: (span.finished_ms, span.started_ms))
    return spans_by_turn


def _trace_observations_by_turn(
    events: list[AgentEvent],
    aliases: dict[str, str],
) -> dict[str, dict[str, _LineObservation]]:
    """复用聊天轨迹行格式化器，收集每条可见日志行的运行中与终态时间点。

    时间点全部来自 app.api.chat._event_trace_lines，使耗时归因与用户看到的日志同源。
    """
    # Keep the timing projection coupled to the same trace-line formatter that
    # produces the visible log. This avoids a second, inevitably drifting list
    # of event-type-to-line-id mappings.
    from app.api.chat import _event_trace_lines

    observations_by_turn: dict[str, dict[str, _LineObservation]] = {}
    skill_hints_by_turn: dict[str, str | None] = {}
    for event in events:
        turn_id = _event_turn_id(event, aliases)
        if not turn_id:
            continue
        payload = event.payload_json or {}
        if event.event_type == "router_decision_created":
            target_skill_id = str(payload.get("target_skill_id") or "").strip()
            if target_skill_id:
                skill_hints_by_turn[turn_id] = target_skill_id
        lines = _event_trace_lines(
            event,
            {},
            skill_hints_by_turn.get(turn_id),
        )
        if not lines:
            continue
        event_ms = _event_ms(event)
        turn_observations = observations_by_turn.setdefault(turn_id, {})
        for line in lines:
            line_id = str(line.get("id") or "").strip()
            if not line_id:
                continue
            observation = turn_observations.setdefault(
                line_id,
                _LineObservation(running_ms=[], terminal_ms=[]),
            )
            if line.get("state") == "running":
                observation.running_ms.append(event_ms)
            else:
                observation.terminal_ms.append(event_ms)
    return observations_by_turn


def _resolve_trace_windows(
    traces: list[dict[str, Any]],
    observations_by_turn: dict[str, dict[str, _LineObservation]],
) -> dict[str, dict[str, tuple[float, float]]]:
    """为每条轨迹日志行推断起止时间窗口。

    有运行中事件时取最早的时间点作为起点，否则回退到前一条观测时间或轨迹开始时间；
    终点同理取最晚终态事件，缺失时回退到下一条观测或轨迹完成时间，缺失端点的行直接跳过。
    """
    windows_by_turn: dict[str, dict[str, tuple[float, float]]] = {}
    for trace in traces:
        turn_id = str(trace.get("turn_id") or "").strip()
        observations = observations_by_turn.get(turn_id, {})
        lines = trace.get("lines") if isinstance(trace.get("lines"), list) else []
        trace_started = _iso_ms(trace.get("started_at"))
        trace_finished = _iso_ms(trace.get("completed_at"))
        line_observations = [observations.get(str(line.get("id") or "")) for line in lines]

        windows: dict[str, tuple[float, float]] = {}
        for index, line in enumerate(lines):
            line_id = str(line.get("id") or "").strip()
            observation = line_observations[index]
            if not line_id or not observation or not observation.all_ms:
                continue

            if observation.running_ms:
                started_ms = min(observation.running_ms)
            else:
                started_ms = _previous_observation_ms(
                    line_observations,
                    index,
                    before_ms=max(observation.terminal_ms),
                )
                if started_ms is None:
                    started_ms = trace_started

            if observation.terminal_ms:
                finished_ms = max(observation.terminal_ms)
            else:
                finished_ms = _next_observation_ms(
                    line_observations,
                    index,
                    after_ms=min(observation.running_ms),
                )
                if finished_ms is None:
                    finished_ms = trace_finished

            if started_ms is None or finished_ms is None:
                continue
            windows[line_id] = (min(started_ms, finished_ms), max(started_ms, finished_ms))
        windows_by_turn[turn_id] = windows
    return windows_by_turn


def _previous_observation_ms(
    observations: list[_LineObservation | None],
    index: int,
    *,
    before_ms: float,
) -> float | None:
    """向前找最近一个不晚于 before_ms 的观测时间点，找不到返回 None。"""
    for candidate in reversed(observations[:index]):
        if not candidate or not candidate.all_ms:
            continue
        eligible = [value for value in candidate.all_ms if value <= before_ms]
        if eligible:
            return max(eligible)
    return None


def _next_observation_ms(
    observations: list[_LineObservation | None],
    index: int,
    *,
    after_ms: float,
) -> float | None:
    """向后找最近一个不早于 after_ms 的观测时间点，找不到返回 None。"""
    for candidate in observations[index + 1 :]:
        if not candidate or not candidate.all_ms:
            continue
        eligible = [value for value in candidate.all_ms if value >= after_ms]
        if eligible:
            return min(eligible)
    return None


def _extend_action_windows_with_model_decisions(
    events: list[AgentEvent],
    aliases: dict[str, str],
    spans_by_turn: dict[str, list[_ModelSpan]],
    windows_by_turn: dict[str, dict[str, tuple[float, float]]],
) -> None:
    """用 harness.task_action 跨度把动作行的窗口向前扩展到模型决策开始处。

    每个跨度只与一个动作事件配对；harness_finish_* 行表示终态收尾，直接以决策开始
    时间作为起点，避免把整轮思考时长算进完成动作。
    """
    actions_by_turn: dict[str, list[tuple[float, str]]] = {}
    for event in events:
        if event.event_type != "harness_action_created":
            continue
        turn_id = _event_turn_id(event, aliases)
        line_id = _trace_line_id(event)
        if turn_id and line_id:
            actions_by_turn.setdefault(turn_id, []).append((_event_ms(event), line_id))

    for turn_id, actions in actions_by_turn.items():
        available = [
            span
            for span in spans_by_turn.get(turn_id, [])
            if span.operation == "harness.task_action"
        ]
        used: set[int] = set()
        for action_ms, line_id in sorted(actions):
            candidates = [
                (index, span)
                for index, span in enumerate(available)
                if index not in used and span.finished_ms <= action_ms + 250
            ]
            if not candidates:
                continue
            index, span = max(candidates, key=lambda item: item[1].finished_ms)
            used.add(index)
            current = windows_by_turn.setdefault(turn_id, {}).get(line_id)
            if current:
                windows_by_turn[turn_id][line_id] = (
                    span.started_ms
                    if line_id.startswith("harness_finish_")
                    else min(span.started_ms, current[0]),
                    current[1],
                )


def _extend_router_windows_with_planner(
    spans_by_turn: dict[str, list[_ModelSpan]],
    windows_by_turn: dict[str, dict[str, tuple[float, float]]],
    observations_by_turn: dict[str, dict[str, _LineObservation]],
) -> None:
    """把 decision_router 行窗口向前扩展到 turn_planner.plan / router.scene 跨度。

    仅把规划器耗时计入决策行，不改变已推断出的结束时间；同轮无规划跨度则保持原窗口。
    """
    for turn_id, windows in windows_by_turn.items():
        current = windows.get("decision_router")
        if not current:
            continue
        planner_spans = [
            span
            for span in spans_by_turn.get(turn_id, [])
            if span.operation in {"turn_planner.plan", "router.scene"}
            and span.finished_ms <= current[1] + 250
        ]
        if planner_spans:
            planner = max(planner_spans, key=lambda span: span.finished_ms)
            observation = observations_by_turn.get(turn_id, {}).get("decision_router")
            started_ms = (
                min(planner.started_ms, current[0])
                if observation and observation.running_ms
                else planner.started_ms
            )
            windows["decision_router"] = (started_ms, current[1])


def _trace_line_id(event: AgentEvent) -> str:
    """按事件类型反推它对应哪条轨迹行 id；无法对应时返回空串。"""
    payload = event.payload_json or {}
    event_type = event.event_type
    frame_id = str(payload.get("task_frame_id") or event.id).strip()
    iteration = str(payload.get("iteration") or "").strip()
    if event_type in {"task_frame_started", "task_frame_finished"}:
        return f"harness_frame_{frame_id}"
    if event_type == "harness_action_created":
        action = str(payload.get("action") or "").strip()
        if action == "tool":
            return f"harness_action_{frame_id}_{iteration or event.id}"
        if action == "finish":
            return f"harness_finish_{frame_id}_{iteration or event.id}"
    if event_type == "harness_tool_completed":
        return f"harness_action_{frame_id}_{iteration or event.id}"
    if event_type == "router_decision_created":
        return "decision_router"
    if event_type in {"knowledge_query_started", "knowledge_query_finished", "knowledge_result"}:
        query = payload.get("query")
        if isinstance(query, dict):
            query = query.get("query")
        query_text = " ".join(str(query or payload.get("text") or "").split())
        return f"knowledge_lookup_{query_text}" if query_text else "knowledge_lookup"
    if event_type in {"tool_call_started", "tool_call_finished"}:
        tool_name = str(payload.get("tool_name") or payload.get("name") or "").strip()
        call_id = str(payload.get("tool_call_id") or tool_name or event.id).strip()
        return f"tool_{call_id}"
    if event_type == "tool_result":
        raw_name = str(payload.get("rawToolName") or payload.get("toolId") or "").strip()
        call_id = str(payload.get("toolCallId") or raw_name or event.id).strip()
        return f"tool_{call_id}"
    return ""


def _model_duration_in_window(
    spans: list[_ModelSpan],
    started_ms: float,
    finished_ms: float,
) -> float | None:
    """统计窗口内的模型耗时：先裁剪并合并相交的跨度区间再求和，避免重叠区间重复计时。

    没有任何跨度与窗口相交时返回 None，让调用方区分“0ms”与“无模型调用”。
    """
    intervals = sorted(
        (
            max(started_ms, span.started_ms),
            min(finished_ms, span.finished_ms),
        )
        for span in spans
        if span.finished_ms >= started_ms and span.started_ms <= finished_ms
    )
    if not intervals:
        return None

    merged: list[tuple[float, float]] = []
    for interval_started, interval_finished in intervals:
        if interval_finished < interval_started:
            continue
        if not merged or interval_started > merged[-1][1]:
            merged.append((interval_started, interval_finished))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], interval_finished))
    return round(sum(end - start for start, end in merged), 3)


def _model_names_in_window(
    spans: list[_ModelSpan],
    started_ms: float,
    finished_ms: float,
) -> list[str]:
    """列出窗口内出现过的模型名，去重并保持首次出现顺序。"""
    names: list[str] = []
    for span in spans:
        if span.finished_ms < started_ms or span.started_ms > finished_ms:
            continue
        if span.model_name and span.model_name not in names:
            names.append(span.model_name)
    return names


def _event_ms(event: AgentEvent) -> float:
    """把事件的 created_at 转成毫秒时间戳；naive 时间按 UTC 解释。"""
    value = event.created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp() * 1000


def _iso_ms(value: object) -> float | None:
    """解析 ISO 8601 时间为毫秒时间戳；空值或格式非法时返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp() * 1000


def _float_ms(value: object) -> float:
    """把输入转成非负毫秒浮点数；无法转换或为负时返回 0.0。"""
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value: object, *, default: int) -> int:
    """把输入转成至少为 1 的整数；无法转换时返回 default。"""
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default
