from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.harness_turn_store import (
    HarnessTurnConflict,
    HarnessTurnStore,
)
from app.core.harness_v2_engine import _with_recoverable_first_session
from app.db.models import ChatSession
from app.session.session_schema import (
    ChatTurnRequest,
    ChatTurnResponse,
    SessionPublic,
)


def _engine():
    """创建内存 SQLite 引擎并建表，让每个用例拥有独立的 Turn 存储。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _request(message: str = "hello") -> ChatTurnRequest:
    """构造固定租户、会话与客户端轮次 ID 的对话请求，可替换消息内容。"""
    return ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="session-1",
        client_turn_id="turn-client-1",
        message=message,
    )


def test_harness_turn_receipt_replays_completed_response() -> None:
    """验证已完成的轮次按 client_turn_id 重放原始响应，不会重复执行。"""
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = HarnessTurnStore(db)

        claim = store.claim(session, _request())
        assert claim.record is not None
        assert claim.replay is None
        store.bind_user_message(claim.record, "message-1")
        expected = ChatTurnResponse(
            reply="done",
            session_id=session.id,
            session_state=SessionPublic(
                session_id=session.id,
                tenant_id=session.tenant_id,
            ),
        )
        store.complete(claim.record, expected)

        replay = store.claim(session, _request())
        assert replay.replay == expected


def test_harness_turn_receipt_blocks_in_progress_and_mismatched_reuse() -> None:
    """验证执行中的轮次拒绝重复提交，且同一 client_turn_id 换内容时报冲突。"""
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = HarnessTurnStore(db)
        store.claim(session, _request())

        try:
            store.claim(session, _request())
        except HarnessTurnConflict as exc:
            assert "不会重复执行" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("duplicate in-progress turn was not blocked")

        try:
            store.claim(session, _request("different"))
        except HarnessTurnConflict as exc:
            assert "不能用于不同" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("mismatched client_turn_id reuse was not blocked")


def test_harness_turn_terminal_receipt_allows_only_cancel_or_completion() -> None:
    """验证已取消的轮次不能再完成，已进入 finalizing 的轮次也不能再取消。"""
    engine = _engine()
    with Session(engine) as db:
        session = ChatSession(id="session-1", tenant_id="tenant-demo")
        db.add(session)
        db.commit()
        store = HarnessTurnStore(db)

        cancelled = store.claim(session, _request()).record
        assert cancelled is not None
        assert store.cancel(cancelled) is True
        assert cancelled.status == "cancelled"
        try:
            store.begin_completion(cancelled)
        except HarnessTurnConflict:
            pass
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("cancelled receipt allowed normal completion")

        completing_request = _request().model_copy(
            update={"client_turn_id": "turn-client-2"}
        )
        completing = store.claim(session, completing_request).record
        assert completing is not None
        store.begin_completion(completing)
        assert completing.status == "finalizing"
        assert store.cancel(completing) is False
        response = ChatTurnResponse(
            reply="done",
            session_id=session.id,
            session_state=SessionPublic(
                session_id=session.id,
                tenant_id=session.tenant_id,
            ),
        )
        store.complete(completing, response)
        assert completing.status == "completed"


def test_first_turn_retry_without_returned_session_id_replays_original() -> None:
    """验证首轮未返回 session_id 时重试可恢复同一会话并重放原响应。"""
    engine = _engine()
    original_request = ChatTurnRequest(
        tenant_id="tenant-demo",
        user_id="user-1",
        client_turn_id="turn-client-first",
        message="first message",
    )
    recovered_request = _with_recoverable_first_session(original_request)
    assert recovered_request.session_id

    with Session(engine) as db:
        session = ChatSession(
            id=str(recovered_request.session_id),
            tenant_id=original_request.tenant_id,
            user_id=original_request.user_id,
        )
        db.add(session)
        db.commit()
        store = HarnessTurnStore(db)
        first = store.claim(session, original_request)
        expected = ChatTurnResponse(
            reply="done",
            session_id=session.id,
            session_state=SessionPublic(
                session_id=session.id,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
            ),
        )
        store.complete(first.record, expected)

        retry_request = _with_recoverable_first_session(
            original_request.model_copy()
        )
        retry_session = db.get(ChatSession, retry_request.session_id)
        assert retry_session is not None
        retry = store.claim(retry_session, original_request.model_copy())

        assert retry.replay == expected
        assert retry.replay.session_id == recovered_request.session_id
