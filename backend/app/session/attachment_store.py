from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePath
from typing import Any

from app import paths
from app.harness.execution_context import SANDBOX_WORKSPACE
from app.session.session_schema import ChatAttachmentRead


def sandbox_attachment_path(attachment: ChatAttachmentRead, index: int = 1) -> str:
    """生成附件在沙箱工作区中的虚拟路径，避免向模型泄露宿主机真实路径。

    文件名会被清洗为安全的 ASCII 片段，并前置附件 id 的哈希片段以防重名冲突。
    """

    basename = PurePath(attachment.filename or f"attachment-{index}").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", basename).strip(".-")
    safe_name = safe_name[:96] or f"attachment-{index}.bin"
    attachment_key = hashlib.sha256(attachment.id.encode("utf-8")).hexdigest()[:32]
    relative = f"attachments/{attachment_key}-{safe_name}"
    return f"{SANDBOX_WORKSPACE}/{relative}"


def stage_chat_attachment(
    attachment: ChatAttachmentRead,
    data: bytes,
    *,
    tenant_id: str,
    user_id: str,
) -> ChatAttachmentRead:
    """把上传字节持久化到 TaskFrame 工作区之外，并返回补齐哈希与沙箱路径的附件副本。

    目录已存在但为软链接时抛 OSError；payload 与 metadata.json 均原子写入，
    因此读取方不会看到写了一半的内容。
    """

    directory = _attachment_directory(
        tenant_id=tenant_id,
        user_id=user_id,
        attachment_id=attachment.id,
    )
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError("attachment staging directory cannot be a symbolic link")
    digest = hashlib.sha256(data).hexdigest()
    _atomic_write(directory / "payload", data)
    metadata = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "attachment_id": attachment.id,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size": len(data),
        "sha256": digest,
    }
    _atomic_write(
        directory / "metadata.json",
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    return attachment.model_copy(
        update={
            "sha256": digest,
            "sandbox_path": sandbox_attachment_path(attachment),
        }
    )


def read_staged_chat_attachment(
    attachment: ChatAttachmentRead,
    *,
    tenant_id: str,
    user_id: str,
) -> bytes | None:
    """读取暂存的上传内容，返回 None 表示数据缺失、被篡改或不属于该租户/用户。

    除重算 payload 的 sha256 外，还会逐项比对 metadata 中的归属字段与附件记录，
    并拒绝任何软链接路径。
    """

    directory = _attachment_directory(
        tenant_id=tenant_id,
        user_id=user_id,
        attachment_id=attachment.id,
    )
    payload_path = directory / "payload"
    metadata_path = directory / "metadata.json"
    try:
        if directory.is_symlink() or payload_path.is_symlink() or metadata_path.is_symlink():
            return None
        metadata: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        data = payload_path.read_bytes()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    digest = hashlib.sha256(data).hexdigest()
    expected_sha = str(attachment.sha256 or "").lower()
    if (
        metadata.get("tenant_id") != tenant_id
        or metadata.get("user_id") != user_id
        or metadata.get("attachment_id") != attachment.id
        or metadata.get("filename") != attachment.filename
        or metadata.get("content_type") != attachment.content_type
        or int(metadata.get("size") or -1) != attachment.size
        or str(metadata.get("sha256") or "").lower() != digest
        or expected_sha != digest
    ):
        return None
    return data


def _attachment_directory(*, tenant_id: str, user_id: str, attachment_id: str) -> Path:
    """解析附件暂存目录，并确认根目录与末级目录都未逃逸出用户数据目录。

    任何解析后越界的路径都以 ValueError 拒绝，防止伪造 id 穿越目录。
    """

    data_root = paths.user_data_dir().resolve()
    root = (data_root / "harness_uploads").resolve()
    if not root.is_relative_to(data_root):
        raise ValueError("attachment storage root escapes user data directory")
    key = _attachment_storage_key(
        tenant_id=tenant_id,
        user_id=user_id,
        attachment_id=attachment_id,
    )
    directory = (root / key[:2] / key).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("attachment path escapes storage root")
    return directory


def _attachment_storage_key(*, tenant_id: str, user_id: str, attachment_id: str) -> str:
    """对租户、用户与附件 id 三元组取 sha256，得到不透明的存储目录名。

    同一附件 id 在不同租户或用户下会落到不同目录，避免跨身份覆盖。
    """

    identity = json.dumps(
        [tenant_id, user_id, attachment_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    """先写入同目录临时文件并 fsync，再用 os.replace 原子替换目标文件。

    无论成功与否都会清理临时文件，避免在暂存目录留下残留。
    """

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


__all__ = [
    "read_staged_chat_attachment",
    "sandbox_attachment_path",
    "stage_chat_attachment",
]
