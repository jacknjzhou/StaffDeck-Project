from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from app.db.models import AgentEvent

logger = logging.getLogger(__name__)


class EventLog:
    """把 Agent 事件写入数据库，并可选地同步推送给外部事件接收器。

    绑定 turn 后会自动为事件 payload 补齐 turn 级追踪字段。
    """
    def __init__(
        self,
        db: Session,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """保存数据库会话与可选接收器，turn 绑定初始为空。"""
        self.db = db
        self._event_sink = event_sink
        self._turn_id: str | None = None
        self._client_turn_id: str | None = None

    def bind_turn(self, turn_id: str, client_turn_id: str | None = None) -> None:
        """绑定当前 turn 标识，空串视为未绑定，后续 record 会据此注入追踪字段。"""
        self._turn_id = str(turn_id or "").strip() or None
        self._client_turn_id = str(client_turn_id or "").strip() or None

    def record(self, tenant_id: str, session_id: str, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        """构造事件并挂到会话，同步投递给接收器后返回未提交的 AgentEvent。

        调用方负责 commit；接收器异常只记日志，不影响事件落库。
        """
        traced_payload = dict(payload)
        if self._turn_id:
            traced_payload.setdefault("turn_id", self._turn_id)
            traced_payload.setdefault("user_message_id", self._turn_id)
        if self._client_turn_id:
            traced_payload.setdefault("client_turn_id", self._client_turn_id)
        event = AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=event_type,
            payload_json=traced_payload,
        )
        self.db.add(event)
        if self._event_sink is not None:
            try:
                self._event_sink(event_type, traced_payload)
            except Exception:
                logger.exception("event_sink 调用失败 event_type=%s", event_type)
        return event
