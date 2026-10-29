from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import (
    ChatSession,
    HarnessTurnRecord,
    new_id,
    utc_now,
)
from app.session.session_schema import ChatTurnRequest, ChatTurnResponse


TURN_LEASE_SECONDS = 900


class HarnessTurnConflict(RuntimeError):
    """幂等收据冲突：同一 client_turn_id 被复用，或收据已被其他执行者改写。"""


class HarnessTurnClaim:
    """claim 的返回值：record 为本次收据行，replay 非空表示可直接回放已完成的响应。"""
    def __init__(
        self,
        *,
        record: HarnessTurnRecord | None,
        replay: ChatTurnResponse | None = None,
    ) -> None:
        """保存收据行与可选的回放响应。"""
        self.record = record
        self.replay = replay


class HarnessTurnStore:
    """以调用方 client_turn_id 为键的持久化 exactly-once 收据存储。"""

    def __init__(self, db: Session) -> None:
        """绑定本次收据读写使用的数据库会话。"""
        self.db = db

    def claim(
        self,
        session: ChatSession,
        request: ChatTurnRequest,
    ) -> HarnessTurnClaim:
        """为带 client_turn_id 的请求抢占一条租约收据。

        重复请求不会重复执行：已完成则回放响应，执行中或已结束则抛 HarnessTurnConflict；唯一约束冲突时回滚并转入已有收据处理。
        """
        client_turn_id = str(request.client_turn_id or "").strip()
        if not client_turn_id:
            return HarnessTurnClaim(record=None)
        digest = _request_digest(request)
        existing = self._find(session, client_turn_id)
        if existing is not None:
            return self._existing_claim(existing, digest)

        now = utc_now()
        record = HarnessTurnRecord(
            tenant_id=session.tenant_id,
            session_id=session.id,
            client_turn_id=client_turn_id,
            request_digest=digest,
            lease_owner=new_id("hturnlease"),
            lease_expires_at=now + timedelta(seconds=TURN_LEASE_SECONDS),
        )
        self.db.add(record)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self._find(session, client_turn_id)
            if existing is None:
                raise
            return self._existing_claim(existing, digest)
        self.db.refresh(record)
        return HarnessTurnClaim(record=record)

    def bind_user_message(
        self,
        record: HarnessTurnRecord | None,
        user_message_id: str,
    ) -> None:
        """把持久化的用户消息 id 记到收据上；record 为空时跳过。"""
        if record is None:
            return
        self._fenced_update(
            record,
            values={
                "user_message_id": user_message_id,
                "updated_at": utc_now(),
            },
        )

    def renew(self, record: HarnessTurnRecord | None) -> None:
        """在收据仍处于 started/finalizing 时续租，让健康执行者的租约持续有效。"""

        if record is None or record.status not in {"started", "finalizing"}:
            return
        now = utc_now()
        self._fenced_update(
            record,
            expected_statuses=("started", "finalizing"),
            values={
                "lease_expires_at": now + timedelta(seconds=TURN_LEASE_SECONDS),
                "updated_at": now,
            },
        )

    def complete(
        self,
        record: HarnessTurnRecord | None,
        response: ChatTurnResponse,
    ) -> None:
        """在 started/finalizing 状态下把收据置为 completed 并写入响应快照。"""
        if record is None:
            return
        now = utc_now()
        self._fenced_update(
            record,
            # ``started`` remains accepted for compatibility with non-projecting
            # callers; the Harness v2 response path always reserves
            # ``finalizing`` first.
            expected_statuses=("started", "finalizing"),
            values={
                "status": "completed",
                "response_json": response.model_dump(mode="json"),
                "finished_at": now,
                "updated_at": now,
            },
        )

    def begin_completion(self, record: HarnessTurnRecord | None) -> None:
        """把 started 收据原子推进到 finalizing，独占正常终态回复的发布权。"""

        if record is None:
            return
        self._fenced_update(
            record,
            expected_statuses=("started",),
            values={"status": "finalizing", "updated_at": utc_now()},
        )

    def cancel(
        self,
        record: HarnessTurnRecord | None,
        *,
        message: str = "用户取消了当前 Harness 执行。",
    ) -> bool:
        """把 started 收据原子置为 cancelled 并写入 CANCELLED 错误，返回是否抢到终态。"""

        if record is None:
            return False
        now = utc_now()
        return self._try_fenced_update(
            record,
            expected_statuses=("started",),
            values={
                "status": "cancelled",
                "error_json": {"code": "CANCELLED", "message": message},
                "finished_at": now,
                "updated_at": now,
            },
        )

    def finish_with_error(
        self,
        record: HarnessTurnRecord | None,
        *,
        status: str,
        code: str,
        message: str,
    ) -> None:
        """在 started/finalizing 状态下写入失败状态与错误码，消息截断到 2000 字符。"""
        if record is None or record.status not in {"started", "finalizing"}:
            return
        now = utc_now()
        self._fenced_update(
            record,
            expected_statuses=("started", "finalizing"),
            values={
                "status": status,
                "error_json": {
                    "code": code,
                    "message": str(message)[:2_000],
                },
                "finished_at": now,
                "updated_at": now,
            },
        )

    def _find(
        self,
        session: ChatSession,
        client_turn_id: str,
    ) -> HarnessTurnRecord | None:
        """在租户与会话范围内按 client_turn_id 查收据。"""
        return self.db.exec(
            select(HarnessTurnRecord).where(
                HarnessTurnRecord.tenant_id == session.tenant_id,
                HarnessTurnRecord.session_id == session.id,
                HarnessTurnRecord.client_turn_id == client_turn_id,
            )
        ).first()

    def _existing_claim(
        self,
        existing: HarnessTurnRecord,
        request_digest: str,
    ) -> HarnessTurnClaim:
        """比对请求摘要后决定回放、拒绝重复执行或抛冲突。"""
        if existing.request_digest != request_digest:
            raise HarnessTurnConflict(
                "同一个 client_turn_id 不能用于不同的 Harness 请求。"
            )
        if existing.status == "completed" and existing.response_json:
            return HarnessTurnClaim(
                record=existing,
                replay=ChatTurnResponse.model_validate(existing.response_json),
            )
        if existing.status in {"started", "finalizing"}:
            state = (
                "仍在执行"
                if existing.lease_expires_at > utc_now()
                else "执行结果未知，需先核对执行记录"
            )
            raise HarnessTurnConflict(
                f"该 client_turn_id 对应的 Harness turn {state}，不会重复执行。"
            )
        raise HarnessTurnConflict(
            "该 client_turn_id 已结束且不能自动重试；请使用新的 client_turn_id。"
        )

    def _fenced_update(
        self,
        record: HarnessTurnRecord,
        *,
        expected_statuses: tuple[str, ...] = ("started",),
        values: dict[str, Any],
    ) -> None:
        """执行带状态与 lease_owner 栅栏的更新，未命中时抛 HarnessTurnConflict。"""
        if self._try_fenced_update(
            record,
            expected_statuses=expected_statuses,
            values=values,
        ):
            return
        raise HarnessTurnConflict("Harness turn receipt 已由其他执行者更新。")

    def _try_fenced_update(
        self,
        record: HarnessTurnRecord,
        *,
        expected_statuses: tuple[str, ...],
        values: dict[str, Any],
    ) -> bool:
        """按 id、期望状态和 lease_owner 做栅栏更新，返回是否恰好命中一行。"""
        result = self.db.exec(
            update(HarnessTurnRecord)
            .where(
                HarnessTurnRecord.id == record.id,
                HarnessTurnRecord.status.in_(expected_statuses),
                HarnessTurnRecord.lease_owner == record.lease_owner,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            self.db.refresh(record)
            return False
        self.db.commit()
        self.db.refresh(record)
        return True


def _request_digest(request: ChatTurnRequest) -> str:
    """计算请求指纹（排除 session_id 与 client_turn_id），用于识别摘要不一致的复用。"""
    payload = request.model_dump(
        mode="json",
        exclude={"session_id", "client_turn_id"},
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "HarnessTurnClaim",
    "HarnessTurnConflict",
    "HarnessTurnStore",
]
