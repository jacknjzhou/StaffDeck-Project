from __future__ import annotations

import asyncio
import inspect
import pathlib
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from app.api import chat

FIXED_START = datetime(2026, 11, 8, 10, 0, 0, tzinfo=UTC)


def _row(
    event_type: str,
    payload: dict[str, object],
    *,
    index: int,
    session_id: str = "sess-1",
) -> SimpleNamespace:
    """构造一条与 AgentEvent 读取路径兼容的假事件行。"""
    return SimpleNamespace(
        id=f"evt-{index:03d}",
        session_id=session_id,
        event_type=event_type,
        payload_json=payload,
        created_at=FIXED_START + timedelta(seconds=index),
    )


def _off_loop() -> bool:
    """判断当前调用是否发生在事件循环所在主线程之外（即执行器线程）。"""
    return threading.current_thread() is not threading.main_thread()


class _FakeClock:
    """每次调用前进固定步长的单调时钟，用来驱动心跳与空闲超时判定。"""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step

    def monotonic(self) -> float:
        """返回推进后的时钟读数。"""
        self.now += self.step
        return self.now


def _install_fake_asyncio(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把 chat 模块里的 asyncio 换成记录 sleep 的假模块，to_thread 仍走真实实现。"""
    recorded: list[float] = []
    real_to_thread = asyncio.to_thread

    async def fake_sleep(delay: float) -> None:
        """记录协程轮询间隔但不真正等待。"""
        recorded.append(delay)

    monkeypatch.setattr(
        chat,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep, to_thread=real_to_thread),
    )
    return recorded


def _install_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """用假单调时钟替换 chat 模块的 time，避免真实等待心跳周期。"""
    clock = _FakeClock()
    monkeypatch.setattr(chat, "time", SimpleNamespace(monotonic=clock.monotonic))
    return clock


async def _collect(stream: AsyncIterator[str], limit: int = 20) -> list[str]:
    """消费异步生成器，最多收 limit 帧以防实现失控时无限循环。"""
    frames: list[str] = []
    async for frame in stream:
        frames.append(frame)
        if len(frames) >= limit:
            break
    return frames


def _stream(**overrides: object) -> AsyncIterator[str]:
    """以测试默认值调用模块级 stream_events，并按需覆盖参数。"""
    relay_ready = threading.Event()
    relay_ready.set()
    kwargs: dict[str, object] = {
        "tenant_id": "tenant-1",
        "fallback_session_id": "",
        "client_turn_id": "turn-1",
        "session_id_ref": {"value": "sess-1"},
        "initial_cursor": None,
        "relay_ready": relay_ready,
        "worker_done": threading.Event(),
    }
    kwargs.update(overrides)
    return cast(AsyncIterator[str], chat.stream_events(**kwargs))


def _assert_off_loop(ran_off_loop: list[bool]) -> None:
    """断言假函数至少被调用过一次，且每次都落在执行器线程而非事件循环线程。"""
    assert ran_off_loop, "fake was never invoked"
    assert all(ran_off_loop), "fake ran on the event loop thread"


def test_stream_events_is_module_level_async_generator() -> None:
    """中继必须是模块级异步生成器，不能在事件循环里 time.sleep。"""
    assert hasattr(chat, "stream_events")
    assert inspect.isasyncgenfunction(chat.stream_events)


def test_stream_events_signature_is_keyword_only() -> None:
    """模块级中继的签名必须与控制器裁决一致，且不携带 worker 私有状态。"""
    params = inspect.signature(chat.stream_events).parameters
    assert set(params) == {
        "tenant_id",
        "fallback_session_id",
        "client_turn_id",
        "session_id_ref",
        "initial_cursor",
        "relay_ready",
        "worker_done",
    }
    assert all(param.kind is inspect.Parameter.KEYWORD_ONLY for param in params.values())


def test_relay_source_has_no_blocking_sleep() -> None:
    """chat.py 中继路径不得再出现 time.sleep(0.08) 式的阻塞轮询。"""
    source = pathlib.Path(chat.__file__).read_text(encoding="utf-8")
    assert "time.sleep(STREAM_RELAY_POLL_SECONDS)" not in source


def test_chat_stream_route_stays_sync() -> None:
    """路由本身保持同步 def，阻塞点已移入后台线程与异步中继。"""
    assert not inspect.iscoroutinefunction(chat.chat_stream)


@pytest.mark.asyncio
async def test_terminal_event_then_worker_done_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """终止事件之后，等 worker 结束且无新事件即关闭连接，不空转。"""
    sleeps = _install_fake_asyncio(monkeypatch)
    _install_clock(monkeypatch)
    terminal = _row("complete", {"reply": "done"}, index=1)
    worker_done = threading.Event()
    batches = [[terminal], []]
    calls = {"count": 0}
    cursors: list[object] = []
    ran_off_loop: list[bool] = []

    def fake_reader(tenant_id: str, session_id: str, cursor: object) -> list[object]:
        """按批次返回事件，并在第二批时标记 worker 已结束。"""
        ran_off_loop.append(_off_loop())
        index = calls["count"]
        calls["count"] += 1
        cursors.append(cursor)
        if calls["count"] >= 2:
            worker_done.set()
        return list(batches[index]) if index < len(batches) else []

    monkeypatch.setattr(chat, "_read_relay_rows", fake_reader)

    frames = await asyncio.wait_for(
        _collect(_stream(worker_done=worker_done)),
        timeout=2,
    )

    assert worker_done.is_set()
    assert calls["count"] == 2
    assert cursors == [None, (terminal.created_at, terminal.id)]
    assert sleeps == [chat.STREAM_RELAY_POLL_SECONDS]
    assert len(frames) == 1
    assert frames[0].startswith(f"id: {terminal.id}\nevent: complete\n")
    assert '"reply": "done"' in frames[0]
    _assert_off_loop(ran_off_loop)


@pytest.mark.asyncio
async def test_heartbeat_when_no_rows_and_poll_delay_is_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有新事件时按心跳周期发 heartbeat，轮询间隔通过 asyncio.sleep 让出。"""
    sleeps = _install_fake_asyncio(monkeypatch)
    _install_clock(monkeypatch)
    worker_done = threading.Event()
    calls = {"count": 0}
    ran_off_loop: list[bool] = []

    def fake_reader(tenant_id: str, session_id: str, cursor: object) -> list[object]:
        """始终返回空批，第四次读取时结束 worker。"""
        ran_off_loop.append(_off_loop())
        calls["count"] += 1
        if calls["count"] >= 4:
            worker_done.set()
        return []

    monkeypatch.setattr(chat, "_read_relay_rows", fake_reader)

    frames = await asyncio.wait_for(
        _collect(_stream(worker_done=worker_done)),
        timeout=2,
    )

    assert calls["count"] == 4
    assert sleeps == [chat.STREAM_RELAY_POLL_SECONDS] * 3
    assert len(frames) == 1
    assert "event: heartbeat" in frames[0]
    assert '"phase": "relay"' in frames[0]
    assert '"sessionId": "sess-1"' in frames[0]
    _assert_off_loop(ran_off_loop)


@pytest.mark.asyncio
async def test_internal_visibility_rows_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """internal 轮次的四个身份键各自命中后续行都会被跳过，游标仍正常推进。"""
    _install_fake_asyncio(monkeypatch)
    _install_clock(monkeypatch)
    internal = _row(
        "stream_delta",
        {
            "content": "secret",
            "turn_id": "turn-internal",
            "user_message_id": "user-internal",
            "message_id": "msg-internal",
            "client_turn_id": "client-internal",
            "message_visibility": "internal",
        },
        index=1,
    )
    follow_turn = _row(
        "stream_delta", {"content": "secret turn", "turn_id": "turn-internal"}, index=2
    )
    follow_user = _row(
        "stream_delta",
        {"content": "secret user", "user_message_id": "user-internal"},
        index=3,
    )
    follow_message = _row(
        "stream_delta",
        {"content": "secret message", "message_id": "msg-internal"},
        index=4,
    )
    follow_client = _row(
        "stream_delta",
        {"content": "secret client", "client_turn_id": "client-internal"},
        index=5,
    )
    visible = _row("stream_delta", {"content": "hello", "turn_id": "turn-visible"}, index=6)
    batches = [[internal, follow_turn, follow_user, follow_message, follow_client, visible], []]
    worker_done = threading.Event()
    calls = {"count": 0}
    cursors: list[object] = []
    ran_off_loop: list[bool] = []

    def fake_reader(tenant_id: str, session_id: str, cursor: object) -> list[object]:
        """返回内部行与可见行混合的一批，第二批为空并结束 worker。"""
        ran_off_loop.append(_off_loop())
        index = calls["count"]
        calls["count"] += 1
        cursors.append(cursor)
        if calls["count"] >= 2:
            worker_done.set()
        return list(batches[index]) if index < len(batches) else []

    monkeypatch.setattr(chat, "_read_relay_rows", fake_reader)

    frames = await asyncio.wait_for(
        _collect(_stream(worker_done=worker_done)),
        timeout=2,
    )

    assert len(frames) == 1
    assert frames[0].startswith(f"id: {visible.id}\nevent: stream_delta\n")
    assert "hello" in frames[0]
    assert "secret" not in frames[0]
    assert cursors == [None, (visible.created_at, visible.id)]
    _assert_off_loop(ran_off_loop)


@pytest.mark.asyncio
async def test_multiple_rows_in_one_batch_are_emitted_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单次读取返回多条可见事件时按行序全部下发，游标停在最后一行。"""
    _install_fake_asyncio(monkeypatch)
    _install_clock(monkeypatch)
    first = _row("stream_delta", {"content": "one"}, index=1)
    second = _row("stream_delta", {"content": "two"}, index=2)
    worker_done = threading.Event()
    batches = [[first, second], []]
    calls = {"count": 0}
    cursors: list[object] = []
    ran_off_loop: list[bool] = []

    def fake_reader(tenant_id: str, session_id: str, cursor: object) -> list[object]:
        """首批返回两行，第二批为空并结束 worker。"""
        ran_off_loop.append(_off_loop())
        index = calls["count"]
        calls["count"] += 1
        cursors.append(cursor)
        if calls["count"] >= 2:
            worker_done.set()
        return list(batches[index]) if index < len(batches) else []

    monkeypatch.setattr(chat, "_read_relay_rows", fake_reader)

    frames = await asyncio.wait_for(
        _collect(_stream(worker_done=worker_done)),
        timeout=2,
    )

    assert len(frames) == 2
    assert frames[0].startswith(f"id: {first.id}\nevent: stream_delta\n")
    assert frames[1].startswith(f"id: {second.id}\nevent: stream_delta\n")
    assert '"content": "one"' in frames[0]
    assert '"content": "two"' in frames[1]
    assert cursors == [None, (second.created_at, second.id)]
    _assert_off_loop(ran_off_loop)


@pytest.mark.asyncio
async def test_idle_timeout_persists_interruption_and_keeps_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空闲超时只落一条中断事件并继续等待，不在本轮直接结束生成器。"""
    _install_fake_asyncio(monkeypatch)
    clock = _FakeClock(step=chat.STREAM_RELAY_IDLE_TIMEOUT_SECONDS)
    monkeypatch.setattr(chat, "time", SimpleNamespace(monotonic=clock.monotonic))
    worker_done = threading.Event()
    calls = {"count": 0}
    persisted: list[tuple[str, str, str]] = []
    reader_off_loop: list[bool] = []
    persist_off_loop: list[bool] = []

    def fake_reader(tenant_id: str, session_id: str, cursor: object) -> list[object]:
        """始终返回空批，第二次读取时结束 worker，避免超时路径无限循环。"""
        reader_off_loop.append(_off_loop())
        calls["count"] += 1
        if calls["count"] >= 2:
            worker_done.set()
        return []

    def fake_timeout(tenant_id: str, session_id: str, client_turn_id: str) -> None:
        """记录超时落库调用，替代真实数据库写入。"""
        persist_off_loop.append(_off_loop())
        persisted.append((tenant_id, session_id, client_turn_id))

    monkeypatch.setattr(chat, "_read_relay_rows", fake_reader)
    monkeypatch.setattr(chat, "_persist_relay_idle_timeout", fake_timeout)

    frames = await asyncio.wait_for(
        _collect(_stream(worker_done=worker_done)),
        timeout=2,
    )

    assert calls["count"] == 2
    assert persisted == [("tenant-1", "sess-1", "turn-1")]
    assert frames == []
    _assert_off_loop(reader_off_loop)
    assert persist_off_loop, "idle-timeout persistence was never invoked"
    assert all(persist_off_loop), "idle-timeout persistence ran on the event loop thread"
