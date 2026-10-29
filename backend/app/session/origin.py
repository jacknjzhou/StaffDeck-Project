from __future__ import annotations

from collections.abc import Iterable

from sqlmodel import Session, select

from app.db.models import AgentEvent, ChatSession, ExternalSessionBinding


PILOTDECK_GROUP_CHAT_CHANNEL = "pilotdeck_group_chat"


def _is_pilotdeck_metadata(value: object) -> bool:
    """判断元数据里的 channel 是否为 PilotDeck 群聊，用于识别隐藏会话来源。"""
    return (
        isinstance(value, dict)
        and str(value.get("channel") or "").strip() == PILOTDECK_GROUP_CHAT_CHANNEL
    )


def pilotdeck_origin_session_ids(
    db: Session,
    tenant_id: str,
    session_ids: Iterable[str] | None = None,
) -> set[str]:
    """返回为隐藏的 PilotDeck 协作而创建的 StaffDeck 会话 ID 集合。

    Open API 会话把来源记在外部绑定上，旧版聊天适配器记在会话的 channel 字段；
    事件兜底让已存在的历史会话无需迁移也能保持隐藏。session_ids 只收窄查询范围，
    传空集合时直接返回空集。
    """

    scoped_ids = (
        None
        if session_ids is None
        else {str(session_id).strip() for session_id in session_ids if session_id}
    )
    if scoped_ids == set():
        return set()

    session_statement = select(ChatSession.id).where(
        ChatSession.tenant_id == tenant_id,
        ChatSession.channel == PILOTDECK_GROUP_CHAT_CHANNEL,
    )
    binding_statement = select(ExternalSessionBinding).where(
        ExternalSessionBinding.tenant_id == tenant_id
    )
    event_statement = select(AgentEvent).where(
        AgentEvent.tenant_id == tenant_id,
        AgentEvent.event_type == "user_message_received",
    )
    if scoped_ids is not None:
        session_statement = session_statement.where(ChatSession.id.in_(scoped_ids))
        binding_statement = binding_statement.where(
            ExternalSessionBinding.session_id.in_(scoped_ids)
        )
        event_statement = event_statement.where(AgentEvent.session_id.in_(scoped_ids))

    hidden_ids = set(db.exec(session_statement).all())
    hidden_ids.update(
        row.session_id
        for row in db.exec(binding_statement).all()
        if _is_pilotdeck_metadata(row.metadata_json)
    )
    hidden_ids.update(
        row.session_id
        for row in db.exec(event_statement).all()
        if _is_pilotdeck_metadata(row.payload_json)
    )
    return hidden_ids
