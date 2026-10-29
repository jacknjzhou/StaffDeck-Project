from __future__ import annotations

from typing import Any

from dingtalk_stream import AckMessage, CallbackHandler

from app.channels.adapters.dingtalk import normalize_dingtalk_message
from app.channels.service_dingtalk_inbox import stage_dingtalk_inbound


class DingTalkCallbackHandler(CallbackHandler):
    """钉钉 Stream 回调处理器：把原始消息归一化后写入入站表，并唤醒消费 worker。

    绑定信息在构造时固定；process 只做落库与 ack 判定，真正的业务处理交给后台 worker。
    """

    def __init__(self, *, db_engine, binding_id: str, expected_revision: int, client_id: str):
        """保存数据库引擎与绑定的版本快照，供每条回调校验该绑定是否仍然有效。"""
        super().__init__()
        self.db_engine = db_engine
        self.binding_id = binding_id
        self.expected_revision = expected_revision
        self.client_id = client_id

    async def process(self, message):
        """处理一条钉钉回调消息，返回 ack 状态与处置结果标签。

        归一化不出入站消息时直接 ack 为 ignored；落库判定需要确认时唤醒定稿 worker，
        否则返回 STATUS_SYSTEM_EXCEPTION 与 error_code，让钉钉重投该消息。
        """
        raw: dict[str, Any] = dict(message.data or {})
        tenant_key = str(raw.get("chatbotCorpId") or raw.get("senderCorpId") or "").strip()
        inbound = normalize_dingtalk_message(raw, account_scope=tenant_key)
        if inbound is None:
            return AckMessage.STATUS_OK, "ignored"
        result = stage_dingtalk_inbound(
            db_engine=self.db_engine,
            binding_id=self.binding_id,
            expected_revision=self.expected_revision,
            client_id=self.client_id,
            tenant_key=tenant_key,
            inbound=inbound,
        )
        if result.should_ack:
            from app.channels.service_intake import wake_staged_inbound_worker

            wake_staged_inbound_worker()
            return AckMessage.STATUS_OK, result.disposition.value
        return AckMessage.STATUS_SYSTEM_EXCEPTION, result.error_code or "staging_failed"
