from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4


SpanSink = Callable[[str, dict[str, Any]], None]


_span_sink: ContextVar[SpanSink | None] = ContextVar("span_sink", default=None)
_span_operation: ContextVar[str] = ContextVar("span_operation", default="llm.request")
_span_attributes: ContextVar[dict[str, Any]] = ContextVar("span_attributes", default={})
_parent_span_id: ContextVar[str | None] = ContextVar("parent_span_id", default=None)


def _utc_iso() -> str:
    """取去掉时区后缀的 UTC 时间字符串,与既有 span 落库格式保持一致。"""
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _span_id() -> str:
    """生成带 span_ 前缀的 16 位十六进制随机 span id。"""
    return f"span_{uuid4().hex[:16]}"


def emit_span_event(event_type: str, payload: dict[str, Any]) -> None:
    """把观测事件交给当前上下文绑定的 sink;无 sink 或 sink 抛错时静默丢弃。

    观测链路不允许把一次成功的业务请求变成失败。
    """
    sink = _span_sink.get()
    if sink is None:
        return
    try:
        sink(event_type, payload)
    except Exception:
        # Observability must never turn a successful business request into a failure.
        return


@contextmanager
def bind_span_sink(sink: SpanSink) -> Iterator[None]:
    """在 with 块内把 sink 绑定到当前上下文,退出时恢复此前的绑定。"""
    token = _span_sink.set(sink)
    try:
        yield
    finally:
        _span_sink.reset(token)


def set_span_sink(sink: SpanSink):  # noqa: ANN201 - ContextVar token type is implementation-specific.
    """设置当前上下文的 sink,并返回可交给 reset_span_sink 回滚的 token。"""
    return _span_sink.set(sink)


def reset_span_sink(token: Any) -> None:
    """用 set_span_sink 返回的 token 恢复此前绑定的 sink。"""
    _span_sink.reset(token)


@contextmanager
def llm_operation(operation: str, **attributes: Any) -> Iterator[None]:
    """在 with 块内设定当前 LLM 操作名,并把 attributes 合并进 span 属性。

    退出时恢复外层的操作名与属性集合,支持嵌套。
    """
    operation_token = _span_operation.set(operation)
    attributes_token = _span_attributes.set({**_span_attributes.get(), **attributes})
    try:
        yield
    finally:
        _span_attributes.reset(attributes_token)
        _span_operation.reset(operation_token)


@contextmanager
def llm_span_attributes(**attributes: Any) -> Iterator[None]:
    """在 with 块内把额外属性合并进当前 span 属性,退出时还原为先前的集合。"""
    token = _span_attributes.set({**_span_attributes.get(), **attributes})
    try:
        yield
    finally:
        _span_attributes.reset(token)


def current_llm_operation() -> str:
    """返回当前上下文生效的 LLM 操作名,未显式设置时为 llm.request。"""
    return _span_operation.get()


@dataclass
class ManualSpan:
    """手工掌控生命周期的 span:创建即上报 started,由持有者显式调用 finish 或 fail。"""

    event_prefix: str
    operation: str
    attributes: dict[str, Any] = field(default_factory=dict)
    span_id: str = field(default_factory=_span_id)
    parent_span_id: str | None = field(default_factory=lambda: _parent_span_id.get())
    started_at: str = field(default_factory=_utc_iso)
    _started_perf: float = field(default_factory=perf_counter)
    _finished: bool = False

    def __post_init__(self) -> None:
        """构造完成即上报一次 <event_prefix>_started 事件。"""
        emit_span_event(f"{self.event_prefix}_started", self._payload())

    def elapsed_ms(self) -> float:
        """返回从创建到此刻的毫秒耗时,保留 3 位小数。"""
        return round((perf_counter() - self._started_perf) * 1000, 3)

    def finish(self, *, status: str = "success", **attributes: Any) -> None:
        """上报结束事件,携带 duration_ms、status 及额外属性;重复调用直接忽略。"""
        if self._finished:
            return
        self._finished = True
        emit_span_event(
            f"{self.event_prefix}_finished",
            self._payload(
                finished_at=_utc_iso(),
                duration_ms=self.elapsed_ms(),
                status=status,
                **attributes,
            ),
        )

    def fail(self, error: BaseException, **attributes: Any) -> None:
        """上报失败事件,带 error_type 与截断到 500 字符的 error 文本;重复调用忽略。"""
        if self._finished:
            return
        self._finished = True
        emit_span_event(
            f"{self.event_prefix}_failed",
            self._payload(
                finished_at=_utc_iso(),
                duration_ms=self.elapsed_ms(),
                status="failed",
                error_type=error.__class__.__name__,
                error=str(error)[:500],
                **attributes,
            ),
        )

    def _payload(self, **attributes: Any) -> dict[str, Any]:
        """组装事件体:span 标识、父 span、操作名、开始时间,叠加实例与本次属性。"""
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "operation": self.operation,
            "started_at": self.started_at,
            **self.attributes,
            **attributes,
        }


@contextmanager
def observed_span(
    event_prefix: str,
    operation: str,
    **attributes: Any,
) -> Iterator[ManualSpan]:
    """托管 span 生命周期的上下文管理器:正常退出上报 finish,异常时先 fail 再向上抛。

    块内新建的 span 以本次的 span_id 为父 span,退出时恢复外层父 span。
    """
    span = ManualSpan(event_prefix, operation, attributes)
    parent_token = _parent_span_id.set(span.span_id)
    try:
        yield span
    except BaseException as exc:
        span.fail(exc)
        raise
    else:
        span.finish()
    finally:
        _parent_span_id.reset(parent_token)


def start_llm_call(**attributes: Any) -> ManualSpan:
    """以当前 LLM 操作名和已累积属性开启一个 llm_call span,由调用方负责收尾。"""
    return ManualSpan(
        "llm_call",
        _span_operation.get(),
        {**_span_attributes.get(), **attributes},
    )
