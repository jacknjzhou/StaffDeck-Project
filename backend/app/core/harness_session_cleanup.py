from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from app import paths
from app.db.models import (
    HarnessInvocationRecord,
    HarnessRunRecord,
    HarnessSessionLeaseRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    UIConfig,
    utc_now,
)


@dataclass(frozen=True)
class HarnessSessionRecordCleanup:
    """一次 Harness 会话记录删除的计数摘要，供调用方记录清理规模。"""
    session_lease_count: int
    turn_count: int
    invocation_count: int
    run_count: int
    task_frame_count: int


def stage_harness_session_execution_reset(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
) -> None:
    """取消会话中仍在执行的 Harness 持久化状态，但保留 turn 收据。

    started 的 turn 被标记为 cancelled 并写入 SESSION_RESET 错误；started 的
    invocation、running 的 run、未终结的 task frame 与全部租约则被删除。
    """
    invocations = db.exec(
        select(HarnessInvocationRecord).where(
            HarnessInvocationRecord.tenant_id == tenant_id,
            HarnessInvocationRecord.session_id == session_id,
            HarnessInvocationRecord.status == "started",
        )
    ).all()
    runs = db.exec(
        select(HarnessRunRecord).where(
            HarnessRunRecord.tenant_id == tenant_id,
            HarnessRunRecord.session_id == session_id,
            HarnessRunRecord.status == "running",
        )
    ).all()
    task_frames = db.exec(
        select(HarnessTaskFrameRecord).where(
            HarnessTaskFrameRecord.tenant_id == tenant_id,
            HarnessTaskFrameRecord.session_id == session_id,
            HarnessTaskFrameRecord.status.notin_(
                {"completed", "cancelled", "failed"}
            ),
        )
    ).all()
    turns = db.exec(
        select(HarnessTurnRecord).where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
            HarnessTurnRecord.status == "started",
        )
    ).all()
    leases = db.exec(
        select(HarnessSessionLeaseRecord).where(
            HarnessSessionLeaseRecord.tenant_id == tenant_id,
            HarnessSessionLeaseRecord.session_id == session_id,
        )
    ).all()

    now = utc_now()
    for turn in turns:
        turn.status = "cancelled"
        turn.error_json = {
            "code": "SESSION_RESET",
            "message": "会话已重置，原 Harness turn 已取消。",
        }
        turn.finished_at = now
        turn.updated_at = now
        db.add(turn)
    for invocation in invocations:
        db.delete(invocation)
    db.flush()
    for run in runs:
        db.delete(run)
    db.flush()
    for task_frame in task_frames:
        db.delete(task_frame)
    db.flush()
    for lease in leases:
        db.delete(lease)
    db.flush()


def stage_harness_session_record_deletion(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
) -> HarnessSessionRecordCleanup:
    """按依赖顺序登记 Harness v2 记录的删除，并返回各类记录的删除数量。

    外层事务由调用方持有，使该清理与消息、事件、反馈的删除保持原子。
    """

    invocations = db.exec(
        select(HarnessInvocationRecord).where(
            HarnessInvocationRecord.tenant_id == tenant_id,
            HarnessInvocationRecord.session_id == session_id,
        )
    ).all()
    runs = db.exec(
        select(HarnessRunRecord).where(
            HarnessRunRecord.tenant_id == tenant_id,
            HarnessRunRecord.session_id == session_id,
        )
    ).all()
    task_frames = db.exec(
        select(HarnessTaskFrameRecord).where(
            HarnessTaskFrameRecord.tenant_id == tenant_id,
            HarnessTaskFrameRecord.session_id == session_id,
        )
    ).all()
    turns = db.exec(
        select(HarnessTurnRecord).where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
        )
    ).all()
    session_leases = db.exec(
        select(HarnessSessionLeaseRecord).where(
            HarnessSessionLeaseRecord.tenant_id == tenant_id,
            HarnessSessionLeaseRecord.session_id == session_id,
        )
    ).all()

    for invocation in invocations:
        db.delete(invocation)
    db.flush()
    for run in runs:
        db.delete(run)
    db.flush()
    for task_frame in task_frames:
        db.delete(task_frame)
    db.flush()
    for turn in turns:
        db.delete(turn)
    db.flush()
    for session_lease in session_leases:
        db.delete(session_lease)
    db.flush()

    return HarnessSessionRecordCleanup(
        session_lease_count=len(session_leases),
        turn_count=len(turns),
        invocation_count=len(invocations),
        run_count=len(runs),
        task_frame_count=len(task_frames),
    )


def harness_path_segment(value: str) -> str:
    """把外部标识映射为确定的 Harness 工作区路径片段。

    保留字母、数字与 -/_，取前 72 个字符并追加原始值的短哈希，既避免碰撞
    也阻断路径穿越；空值的前缀退化为 unknown。
    """

    raw = str(value or "")
    normalized = "".join(
        character
        for character in raw
        if character.isalnum() or character in {"-", "_"}
    )
    prefix = normalized[:72] or "unknown"
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{suffix}"


def harness_storage_root(*, tenant_id: str, db: Session | None = None) -> Path:
    """解析新非沙箱工作区的根目录。

    仅当租户配置了 UI 存储路径且未启用沙箱时使用该路径，否则回退到用户
    数据目录下的 harness_workspaces。
    """

    default_root = paths.user_data_dir().resolve() / "harness_workspaces"
    if db is not None:
        row = db.get(UIConfig, tenant_id)
        configured = str(getattr(row, "harness_storage_path", "") or "").strip()
        if row is not None and not bool(getattr(row, "sandbox_enabled", False)) and configured:
            return Path(configured).expanduser().resolve()
    return default_root


def harness_session_workspace_path(
    *, tenant_id: str, session_id: str, db: Session | None = None
) -> Path:
    """返回租户与会话两级路径片段拼出的 Harness 工作区目录。"""
    return (
        harness_storage_root(tenant_id=tenant_id, db=db)
        / harness_path_segment(tenant_id)
        / harness_path_segment(session_id)
    )


def harness_task_workspace_path(
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    db: Session | None = None,
) -> Path:
    """返回某个 task frame 的工作区路径，遇到符号链接时拒绝创建。

    会话目录及其父级任一为符号链接都会抛 OSError，防止工作区被重定向到别处。
    """
    session_path = harness_session_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        db=db,
    )
    task_path = session_path / harness_path_segment(task_frame_id)
    for parent in (
        session_path.parents[1],
        session_path.parent,
        session_path,
        task_path,
    ):
        if parent.is_symlink():
            raise OSError(
                "refusing to provision Harness workspace through a symlink"
            )
    return task_path


def remove_harness_session_workspace(
    *, tenant_id: str, session_id: str, db: Session | None = None
) -> bool:
    """只删除一个确切的租户/会话工作区，并返回是否发生了删除。

    父级为符号链接时一律拒绝，避免清理被重定向；会话路径本身是符号链接时只
    unlink 链接而不触碰目标。非目录文件直接删除，路径不存在则返回 False。
    """

    session_path = harness_session_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        db=db,
    )
    harness_root = session_path.parents[1]
    tenant_path = session_path.parent

    if harness_root.is_symlink() or tenant_path.is_symlink():
        raise OSError("refusing to clean Harness workspace through a symlinked parent")
    if session_path.is_symlink():
        session_path.unlink()
        return True
    if not session_path.exists():
        return False
    if not session_path.is_dir():
        session_path.unlink()
        return True

    shutil.rmtree(session_path)
    return True
