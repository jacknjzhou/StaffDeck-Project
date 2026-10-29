from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.harness_session_lock import HarnessSessionBusy
from app.db.models import (
    ChatSession,
    HarnessSessionLeaseRecord,
    new_id,
    utc_now,
)


SESSION_LEASE_SECONDS = 900


class HarnessSessionLeaseLost(RuntimeError):
    """会话执行租约已被其他 worker 接管或丢失,当前 worker 必须立即停止写入。"""


@dataclass(frozen=True)
class HarnessSessionLeaseToken:
    """一次已持有的会话租约凭据,供续期、释放与 owner fencing 比对使用。"""

    record_id: str
    tenant_id: str
    session_id: str
    lease_owner: str


class HarnessSessionLeaseStore:
    """基于数据库的会话互斥锁,支持 owner fencing 与到期接管。"""

    def __init__(self, db: Session) -> None:
        """绑定本次租约操作使用的数据库会话,构造时不开启事务。"""
        self.db = db

    def acquire(self, session: ChatSession) -> HarnessSessionLeaseToken:
        """插入租约行抢占会话执行权,唯一约束冲突时转去接管已过期租约。"""
        now = utc_now()
        row = HarnessSessionLeaseRecord(
            tenant_id=session.tenant_id,
            session_id=session.id,
            lease_owner=new_id("hsleaseowner"),
            lease_expires_at=now + timedelta(seconds=SESSION_LEASE_SECONDS),
        )
        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return self._take_expired(session)
        return _token(row)

    def renew(self, lease: HarnessSessionLeaseToken | None) -> None:
        """按 owner 原子续期租约,记录已被接管或过期时抛 HarnessSessionLeaseLost。"""
        if lease is None:
            raise HarnessSessionLeaseLost("Harness session lease is missing.")
        now = utc_now()
        result = self.db.exec(
            update(HarnessSessionLeaseRecord)
            .where(
                HarnessSessionLeaseRecord.id == lease.record_id,
                HarnessSessionLeaseRecord.tenant_id == lease.tenant_id,
                HarnessSessionLeaseRecord.session_id == lease.session_id,
                HarnessSessionLeaseRecord.lease_owner == lease.lease_owner,
                HarnessSessionLeaseRecord.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now
                + timedelta(seconds=SESSION_LEASE_SECONDS),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise HarnessSessionLeaseLost(
                "Harness session execution lease was fenced by another worker."
            )
        self.db.flush()

    def release(self, lease: HarnessSessionLeaseToken | None) -> None:
        """按 owner 删除租约行并提交,lease 为 None 时不做任何事。"""
        if lease is None:
            return
        self.db.rollback()
        self.db.exec(
            delete(HarnessSessionLeaseRecord).where(
                HarnessSessionLeaseRecord.id == lease.record_id,
                HarnessSessionLeaseRecord.lease_owner == lease.lease_owner,
            )
        )
        self.db.commit()

    def _take_expired(
        self,
        session: ChatSession,
    ) -> HarnessSessionLeaseToken:
        """接管已过期的租约行;抢不到则抛 HarnessSessionBusy,阻止并发执行。"""
        now = utc_now()
        owner = new_id("hsleaseowner")
        result = self.db.exec(
            update(HarnessSessionLeaseRecord)
            .where(
                HarnessSessionLeaseRecord.tenant_id == session.tenant_id,
                HarnessSessionLeaseRecord.session_id == session.id,
                HarnessSessionLeaseRecord.lease_expires_at <= now,
            )
            .values(
                lease_owner=owner,
                lease_expires_at=now
                + timedelta(seconds=SESSION_LEASE_SECONDS),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise HarnessSessionBusy(
                "该会话已有一个 Harness 执行正在进行，请等待其结束后重试。"
            )
        self.db.commit()
        row = self.db.exec(
            select(HarnessSessionLeaseRecord).where(
                HarnessSessionLeaseRecord.tenant_id == session.tenant_id,
                HarnessSessionLeaseRecord.session_id == session.id,
                HarnessSessionLeaseRecord.lease_owner == owner,
            )
        ).one()
        return _token(row)


def _token(row: HarnessSessionLeaseRecord) -> HarnessSessionLeaseToken:
    """把租约记录投影成不可变 token,只暴露续期与释放所需的标识。"""
    return HarnessSessionLeaseToken(
        record_id=row.id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        lease_owner=row.lease_owner,
    )


__all__ = [
    "HarnessSessionLeaseLost",
    "HarnessSessionLeaseStore",
    "HarnessSessionLeaseToken",
]
