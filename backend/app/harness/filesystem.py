from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import json
import mimetypes
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.harness.contracts import HarnessToolContext
from app.harness.execution_context import SANDBOX_WORKSPACE
from app.harness.errors import HarnessExecutionError
from app.harness.registry import HarnessRegistry
from app.knowledge.parser import KnowledgeParseError, extract_text

_TRASH_DIRECTORY = ".harness-trash"
_SHA256_PATTERN = r"^[A-Fa-f0-9]{64}$"
_MAX_GREP_LINE_CHARS = 400


class _FileArguments(BaseModel):
    """文件工具参数模型的公共基类；禁止调用方传入未声明的额外字段。"""
    model_config = ConfigDict(extra="forbid")


class ReadFileArguments(_FileArguments):
    """read_file 的入参：目标路径、起始字节偏移、读取上限与续读令牌。"""
    path: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    max_bytes: int | None = Field(default=None, ge=1)
    continuation_token: str | None = Field(default=None, min_length=1)


class ExtractDocumentTextArguments(_FileArguments):
    """extract_document_text 的入参：源文档路径与可选的抽取文本输出路径。"""
    path: str = Field(min_length=1)
    output_path: str | None = Field(default=None, min_length=1)


class WriteFileArguments(_FileArguments):
    """write_file 的入参：文件内容、是否创建父目录与可选的乐观并发 SHA-256。"""
    path: str = Field(min_length=1)
    content: str
    create_parents: bool = False
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class EditFileArguments(_FileArguments):
    """edit_file 的入参：原文、替换文本、是否全量替换与期望的 SHA-256。"""
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str
    replace_all: bool = False
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class ListDirectoryArguments(_FileArguments):
    """list_directory 的入参：目录路径、是否递归与最大返回条目数。"""
    path: str = "."
    recursive: bool = False
    max_entries: int | None = Field(default=None, ge=1)


class GlobArguments(_FileArguments):
    """glob 的入参：glob 模式、搜索起始目录与最大返回数量。"""
    pattern: str = Field(min_length=1)
    path: str = "."
    max_results: int | None = Field(default=None, ge=1)


class GrepArguments(_FileArguments):
    """grep 的入参：字面量模式、文件过滤 glob、大小写与结果上限。

    regex 固定为 False：Python 回溯引擎无法限制单次匹配耗时，
    只支持字面量，避免模型提供的模式拖死 worker。
    """
    pattern: str = Field(min_length=1)
    path: str = "."
    file_glob: str | None = None
    # Python's backtracking ``re`` engine has no per-match timeout. Keep the
    # first Harness version literal-only so model-provided patterns cannot
    # stall a worker through catastrophic backtracking.
    regex: Literal[False] = False
    case_sensitive: bool = True
    max_results: int | None = Field(default=None, ge=1)


class FileInfoArguments(_FileArguments):
    """file_info 的入参：待查询的路径。"""
    path: str = Field(min_length=1)


class PublishArtifactArguments(_FileArguments):
    """publish_artifact 的入参：文件路径、展示名与下载描述。"""
    path: str = Field(min_length=1)
    display_name: str | None = Field(default=None, max_length=180)
    description: str | None = Field(default=None, max_length=500)


class MakeDirectoryArguments(_FileArguments):
    """mkdir 的入参：目录路径、是否创建父级与目录已存在时是否放行。"""
    path: str = Field(min_length=1)
    parents: bool = True
    exist_ok: bool = True


class DeleteFileArguments(_FileArguments):
    """delete_file 的入参：待删除路径与可选的期望 SHA-256。"""
    path: str = Field(min_length=1)
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class MoveFileArguments(_FileArguments):
    """move_file 的入参：源/目标路径、覆盖策略与两侧的期望 SHA-256。"""
    source_path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)
    create_parents: bool = False
    overwrite: bool = False
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    expected_destination_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )


class CopyFileArguments(MoveFileArguments):
    """copy_file 的入参；结构与 move_file 相同，语义为复制而非移动。"""


class _Workspace:
    """以受控方式访问 Harness 工作区的路径解析与文件校验器。

    统一处理根目录校验、符号链接拒绝、配额与哈希校验，
    使各工具处理器只需关注自身业务逻辑。
    """
    def __init__(self, context: HarnessToolContext) -> None:
        """解析并校验工作区根目录；根目录缺失时按需创建。

        根目录为符号链接、不可用或不是目录时抛出 INVALID_WORKSPACE。
        """
        root = context.workspace_root
        try:
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink():
                raise HarnessExecutionError(
                    "INVALID_WORKSPACE",
                    "Harness workspace root cannot be a symbolic link.",
                )
            self.root = root.resolve(strict=True)
        except HarnessExecutionError:
            raise
        except OSError as exc:
            raise HarnessExecutionError(
                "INVALID_WORKSPACE",
                "Harness workspace root is unavailable.",
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not self.root.is_dir():
            raise HarnessExecutionError(
                "INVALID_WORKSPACE",
                "Harness workspace root is not a directory.",
            )
        self.context = context

    def resolve(
        self,
        raw_path: str,
        *,
        allow_root: bool = False,
    ) -> Path:
        """把调用方路径解析为工作区内的绝对路径，并拒绝符号链接与内部回收站。"""
        candidate = _normalize_access_path(raw_path, root=self.root, allow_root=allow_root)
        if _TRASH_DIRECTORY in candidate.parts:
            raise HarnessExecutionError(
                "RESERVED_PATH",
                "The Harness internal trash directory is not directly accessible.",
                details={"path": str(candidate)},
            )
        self._reject_symlink_components(candidate)
        return candidate

    def relative(self, path: Path) -> str:
        """返回 path 相对工作区根目录的 POSIX 形式；工作区外路径原样返回。"""
        try:
            return path.relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(path)

    def is_inside_workspace(self, path: Path) -> bool:
        """判断 path 是否位于工作区根目录之内。"""
        try:
            path.relative_to(self.root)
        except ValueError:
            return False
        return True

    def require_file(self, path: Path) -> os.stat_result:
        """校验 path 为普通文件并返回其 lstat 结果。

        不存在、为符号链接或非普通文件时分别抛出 NOT_FOUND、
        SYMLINK_NOT_ALLOWED、NOT_A_FILE。
        """
        self._reject_symlink_components(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise HarnessExecutionError(
                "NOT_FOUND",
                "File does not exist.",
                details={"path": self.relative(path)},
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            self._raise_symlink(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise HarnessExecutionError(
                "NOT_A_FILE",
                "Path is not a regular file.",
                details={"path": self.relative(path)},
            )
        return metadata

    def require_directory(self, path: Path) -> os.stat_result:
        """校验 path 为真实目录并返回其 lstat 结果。

        不存在、为符号链接或非目录时分别抛出 NOT_FOUND、
        SYMLINK_NOT_ALLOWED、NOT_A_DIRECTORY。
        """
        self._reject_symlink_components(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise HarnessExecutionError(
                "NOT_FOUND",
                "Directory does not exist.",
                details={"path": self.relative(path)},
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            self._raise_symlink(path)
        if not stat.S_ISDIR(metadata.st_mode):
            raise HarnessExecutionError(
                "NOT_A_DIRECTORY",
                "Path is not a directory.",
                details={"path": self.relative(path)},
            )
        return metadata

    def prepare_parent(self, path: Path, *, create: bool) -> None:
        """确认目标父目录存在且不是符号链接；按需创建缺失的父级目录。"""
        parent = path.parent
        self._reject_symlink_components(parent)
        if not parent.exists():
            if not create:
                raise HarnessExecutionError(
                    "PARENT_NOT_FOUND",
                    "Parent directory does not exist.",
                    details={"path": self.relative(parent)},
                )
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HarnessExecutionError(
                    "IO_ERROR",
                    "Parent directory could not be created.",
                    details={"exception_type": type(exc).__name__},
                ) from exc
        self.require_directory(parent)
        self._reject_symlink_components(parent)

    def assert_expected_hash(
        self,
        path: Path,
        expected_sha256: str | None,
        *,
        actual_sha256: str | None = None,
    ) -> str:
        """校验文件 SHA-256 与调用方期望值一致，并返回实际哈希值。

        不一致时抛出可重试的 HASH_MISMATCH，提示文件已被改动。
        """
        actual = actual_sha256 or _sha256(path)
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise HarnessExecutionError(
                "HASH_MISMATCH",
                "File changed since the caller last read it.",
                retryable=True,
                details={
                    "path": self.relative(path),
                    "expected_sha256": expected_sha256.lower(),
                    "actual_sha256": actual,
                },
            )
        return actual

    def ensure_file_size(self, size: int) -> None:
        """确保文件大小不超过单文件上限，否则抛出 FILE_TOO_LARGE。"""
        if size > self.context.limits.max_file_bytes:
            raise HarnessExecutionError(
                "FILE_TOO_LARGE",
                "File exceeds the Harness per-file size limit.",
                details={
                    "actual_bytes": size,
                    "max_bytes": self.context.limits.max_file_bytes,
                },
            )

    def ensure_workspace_capacity(
        self,
        *,
        path: Path,
        replacing_bytes: int,
        new_bytes: int,
    ) -> None:
        """按替换后的字节数预判工作区配额，超限时抛出 WORKSPACE_QUOTA_EXCEEDED。

        工作区外的路径不做配额检查。
        """
        if not self.is_inside_workspace(path):
            return
        current = self.workspace_bytes()
        projected = current - replacing_bytes + new_bytes
        if projected > self.context.limits.max_workspace_bytes:
            raise HarnessExecutionError(
                "WORKSPACE_QUOTA_EXCEEDED",
                "Operation would exceed the Harness workspace quota.",
                details={
                    "current_bytes": current,
                    "projected_bytes": projected,
                    "max_bytes": self.context.limits.max_workspace_bytes,
                },
            )

    def workspace_bytes(self) -> int:
        """统计工作区内普通文件的总字节数，跳过符号链接与内部回收站目录。"""
        total = 0
        for directory, directory_names, file_names in os.walk(
            self.root,
            topdown=True,
            followlinks=False,
        ):
            base = Path(directory)
            directory_names[:] = [
                name
                for name in directory_names
                if name != _TRASH_DIRECTORY
                and not (base / name).is_symlink()
            ]
            for name in file_names:
                path = base / name
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
        return total

    def trash_target(self, relative: Path, trash_id: str) -> Path:
        """为相对路径分配独立回收站目录并返回其迁移目标路径。

        回收站目录按需创建，目标目录已存在时复用同一个 trash_id。
        """
        trash_root = self.root / _TRASH_DIRECTORY
        self._reject_symlink_components(trash_root)
        try:
            trash_root.mkdir(exist_ok=True)
        except OSError as exc:
            raise _io_error("Harness trash could not be created.", exc) from exc
        self.require_directory(trash_root)

        target = trash_root / trash_id / relative
        self._reject_symlink_components(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            self.require_directory(target.parent)
        except OSError as exc:
            raise _io_error("Harness trash destination could not be created.", exc) from exc
        self.require_directory(target.parent)
        return target

    def iter_files(self, start: Path) -> Iterator[Path]:
        """按确定顺序遍历 start 下的普通文件；start 为文件时只产出自身。"""
        metadata = start.lstat()
        if stat.S_ISREG(metadata.st_mode):
            yield start
            return
        self.require_directory(start)
        for directory, directory_names, file_names in os.walk(
            start,
            topdown=True,
            followlinks=False,
        ):
            base = Path(directory)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name != _TRASH_DIRECTORY and not (base / name).is_symlink()
            )
            for name in sorted(file_names):
                path = base / name
                try:
                    child_metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(child_metadata.st_mode):
                    yield path

    def _reject_symlink_components(self, path: Path) -> None:
        """逐级检查路径各组件，发现符号链接即抛出 SYMLINK_NOT_ALLOWED。"""
        try:
            relative = path.relative_to(self.root)
            current = self.root
        except ValueError:
            if not path.is_absolute():
                raise HarnessExecutionError(
                    "INVALID_PATH",
                    "Harness file-tool paths must resolve to an absolute host path.",
                )
            anchor = Path(path.anchor)
            current = anchor
            relative = Path(*path.parts[1:])
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                self._raise_symlink(current)

    def _raise_symlink(self, path: Path) -> None:
        """抛出 SYMLINK_NOT_ALLOWED 并附带相对工作区的路径信息。"""
        raise HarnessExecutionError(
            "SYMLINK_NOT_ALLOWED",
            "Symbolic links are not allowed in Harness file-tool paths.",
            details={"path": self.relative(path)},
        )


def read_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """按字节区间读取 UTF-8 文本并返回续读游标。

    偏移会前移到 UTF-8 字符边界，越界偏移收敛到文件末尾，
    内容被截断时返回 continuation_token 供下次续读。
    """
    args = _as(arguments, ReadFileArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    metadata = workspace.require_file(path)
    workspace.ensure_file_size(metadata.st_size)
    digest = _sha256(path)
    requested_offset = args.offset
    if args.continuation_token:
        continuation = _decode_read_continuation(args.continuation_token)
        relative_path = workspace.relative(path)
        if continuation["path"] != relative_path or continuation["sha256"] != digest:
            raise HarnessExecutionError(
                "STALE_CONTINUATION",
                "Read continuation no longer matches this file.",
                details={"path": relative_path, "sha256": digest},
            )
        requested_offset = int(continuation["offset"])
    # EOF is an ordinary terminal state.  Returning it deterministically keeps
    # an AgentLoop from burning actions by guessing ever larger byte offsets.
    requested_offset = min(requested_offset, metadata.st_size)

    safe_result_bytes = max(1, context.limits.max_result_bytes // 8)
    requested_bytes = args.max_bytes or min(context.limits.max_read_bytes, safe_result_bytes)
    if requested_bytes > context.limits.max_read_bytes:
        raise HarnessExecutionError(
            "READ_LIMIT_EXCEEDED",
            "Requested read exceeds the per-call read limit.",
            details={
                "requested_bytes": requested_bytes,
                "max_bytes": context.limits.max_read_bytes,
            },
        )
    read_bytes = min(requested_bytes, safe_result_bytes)
    try:
        with path.open("rb") as handle:
            actual_offset = _align_utf8_offset(handle, requested_offset, metadata.st_size)
            handle.seek(actual_offset)
            content_bytes = handle.read(read_bytes)
    except OSError as exc:
        raise _io_error("File could not be read.", exc) from exc

    content, consumed_bytes = _decode_utf8_chunk(content_bytes)
    next_offset = actual_offset + consumed_bytes
    truncated = next_offset < metadata.st_size
    result = {
        "path": workspace.relative(path),
        "content": content,
        "requested_offset": requested_offset,
        "offset": actual_offset,
        "next_offset": next_offset,
        "truncated": truncated,
        "eof": not truncated,
        "size": metadata.st_size,
        "sha256": digest,
    }
    if truncated:
        result["continuation_token"] = _encode_read_continuation(
            path=workspace.relative(path),
            sha256=digest,
            offset=next_offset,
        )
    return result


def _align_utf8_offset(handle: Any, offset: int, size: int) -> int:
    """把旧的字节偏移推进到下一个 UTF-8 字符边界。"""

    if offset <= 0 or offset >= size:
        return offset
    handle.seek(offset)
    for _ in range(3):
        marker = handle.read(1)
        if not marker or marker[0] & 0xC0 != 0x80:
            return handle.tell() - (1 if marker else 0)
    return handle.tell()


def _encode_read_continuation(*, path: str, sha256: str, offset: int) -> str:
    """把路径、哈希与偏移编码为 URL 安全的 base64 续读令牌。"""
    payload = json.dumps(
        {"v": 1, "path": path, "sha256": sha256, "offset": offset},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_read_continuation(token: str) -> dict[str, Any]:
    """解析并校验续读令牌，非法或版本不符时抛出 INVALID_CONTINUATION。"""
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or not isinstance(payload.get("path"), str)
            or not isinstance(payload.get("sha256"), str)
            or not isinstance(payload.get("offset"), int)
            or payload["offset"] < 0
        ):
            raise ValueError("invalid continuation payload")
        return payload
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HarnessExecutionError(
            "INVALID_CONTINUATION",
            "Read continuation token is invalid.",
        ) from exc


def extract_document_text(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """把受支持的文档抽取为工作区内的 UTF-8 文件。

    二进制文档不经过 read_file；抽取结果落盘后由模型用受限的
    文本读取工具分页查看，避免一次性返回超大内容。
    """

    args = _as(arguments, ExtractDocumentTextArguments)
    workspace = _Workspace(context)
    source = workspace.resolve(args.path)
    source_metadata = workspace.require_file(source)
    workspace.ensure_file_size(source_metadata.st_size)
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise _io_error("Document could not be read.", exc) from exc

    try:
        text, document_format = extract_text(source.name, source_bytes)
    except KnowledgeParseError as exc:
        raise HarnessExecutionError(
            "DOCUMENT_EXTRACTION_FAILED",
            str(exc),
            details={"path": workspace.relative(source)},
        ) from exc
    except Exception as exc:
        raise HarnessExecutionError(
            "DOCUMENT_EXTRACTION_FAILED",
            "Document content could not be extracted.",
            details={
                "path": workspace.relative(source),
                "exception_type": type(exc).__name__,
            },
        ) from exc

    output_raw = args.output_path or f"{workspace.relative(source)}.extracted.txt"
    output = workspace.resolve(output_raw)
    if output == source:
        raise HarnessExecutionError(
            "INVALID_PATH",
            "Extracted text output must differ from the source document.",
        )
    workspace.prepare_parent(output, create=True)
    content_bytes = text.encode("utf-8")
    workspace.ensure_file_size(len(content_bytes))
    previous_size = 0
    if output.exists():
        previous_size = workspace.require_file(output).st_size
    workspace.ensure_workspace_capacity(
        path=output,
        replacing_bytes=previous_size,
        new_bytes=len(content_bytes),
    )
    _atomic_write(output, content_bytes)
    return {
        "source_path": workspace.relative(source),
        "extracted_text_path": workspace.relative(output),
        "format": document_format,
        "characters": len(text),
        "size": len(content_bytes),
        "empty": not bool(text.strip()),
        "sha256": _sha256(output),
    }


def write_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """原子写入 UTF-8 文件，并返回创建标记与写入前后的哈希。

    目标已存在时按期望 SHA-256 做乐观并发校验，
    写入前统一检查单文件大小与工作区配额。
    """
    args = _as(arguments, WriteFileArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    workspace.prepare_parent(path, create=args.create_parents)
    content_bytes = args.content.encode("utf-8")
    workspace.ensure_file_size(len(content_bytes))

    existed = path.exists()
    previous_size = 0
    previous_sha256: str | None = None
    if existed:
        metadata = workspace.require_file(path)
        previous_size = metadata.st_size
        previous_sha256 = workspace.assert_expected_hash(path, args.expected_sha256)
    elif args.expected_sha256:
        raise HarnessExecutionError(
            "HASH_MISMATCH",
            "Expected an existing file, but the destination does not exist.",
            retryable=True,
            details={"path": workspace.relative(path)},
        )

    workspace.ensure_workspace_capacity(
        path=path,
        replacing_bytes=previous_size,
        new_bytes=len(content_bytes),
    )
    _atomic_write(path, content_bytes)
    return {
        "path": workspace.relative(path),
        "created": not existed,
        "size": len(content_bytes),
        "sha256": _sha256(path),
        "previous_sha256": previous_sha256,
    }


def edit_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """按精确文本替换文件内容，并返回替换次数与新旧哈希。

    old_text 缺失，或多次命中却未开启 replace_all 时拒绝执行，
    避免产生歧义编辑。
    """
    args = _as(arguments, EditFileArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    metadata = workspace.require_file(path)
    workspace.ensure_file_size(metadata.st_size)
    previous_sha256 = workspace.assert_expected_hash(path, args.expected_sha256)
    content = _read_utf8_file(path, context.limits.max_file_bytes)
    occurrences = content.count(args.old_text)
    if occurrences == 0:
        raise HarnessExecutionError(
            "EDIT_TARGET_NOT_FOUND",
            "old_text was not found in the target file.",
            details={"path": workspace.relative(path)},
        )
    if occurrences > 1 and not args.replace_all:
        raise HarnessExecutionError(
            "AMBIGUOUS_EDIT",
            "old_text occurs more than once; set replace_all or provide more context.",
            details={"path": workspace.relative(path), "occurrences": occurrences},
        )

    replacements = occurrences if args.replace_all else 1
    updated = content.replace(
        args.old_text,
        args.new_text,
        -1 if args.replace_all else 1,
    )
    updated_bytes = updated.encode("utf-8")
    workspace.ensure_file_size(len(updated_bytes))
    workspace.ensure_workspace_capacity(
        path=path,
        replacing_bytes=metadata.st_size,
        new_bytes=len(updated_bytes),
    )
    _atomic_write(path, updated_bytes)
    return {
        "path": workspace.relative(path),
        "replacements": replacements,
        "size": len(updated_bytes),
        "sha256": _sha256(path),
        "previous_sha256": previous_sha256,
    }


def list_directory(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """列出目录条目并标注是否因上限而截断。

    条目按名称排序；递归模式不跟随符号链接，但仍会列出链接自身。
    """
    args = _as(arguments, ListDirectoryArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path, allow_root=True)
    workspace.require_directory(path)
    limit = _entry_limit(args.max_entries, context)
    entries: list[dict[str, Any]] = []
    truncated = False

    if args.recursive:
        iterator = _iter_directory_entries(workspace, path)
    else:
        iterator = iter(sorted(path.iterdir(), key=lambda item: item.name))

    for child in iterator:
        if child.name == _TRASH_DIRECTORY and child.parent == workspace.root:
            continue
        if len(entries) >= limit:
            truncated = True
            break
        try:
            metadata = child.lstat()
        except FileNotFoundError:
            continue
        entries.append(_entry_payload(workspace, child, metadata))

    return {
        "path": workspace.relative(path),
        "entries": entries,
        "truncated": truncated,
    }


def glob_files(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """在起始目录下按 glob 模式查找路径。

    模式只允许相对写法且不能越出工作区，结果按遍历顺序截断到上限。
    """
    args = _as(arguments, GlobArguments)
    workspace = _Workspace(context)
    start = workspace.resolve(args.path, allow_root=True)
    workspace.require_directory(start)
    pattern = _normalize_glob(args.pattern)
    limit = _entry_limit(args.max_results, context)
    matches: list[dict[str, Any]] = []
    truncated = False

    for child in _iter_directory_entries(workspace, start):
        relative_to_start = child.relative_to(start).as_posix()
        if not _glob_matches(relative_to_start, pattern):
            continue
        if len(matches) >= limit:
            truncated = True
            break
        try:
            metadata = child.lstat()
        except FileNotFoundError:
            continue
        matches.append(_entry_payload(workspace, child, metadata))

    return {
        "path": workspace.relative(start),
        "pattern": pattern,
        "matches": matches,
        "truncated": truncated,
    }


def grep_files(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """在 UTF-8 文本文件中做字面量检索并返回命中行。

    超过读取上限的文件只搜索前半段，非 UTF-8 文件计入跳过数，
    命中行文本会截断到 _MAX_GREP_LINE_CHARS。
    """
    args = _as(arguments, GrepArguments)
    workspace = _Workspace(context)
    start = workspace.resolve(args.path, allow_root=True)
    if not start.exists():
        raise HarnessExecutionError(
            "NOT_FOUND",
            "Search path does not exist.",
            details={"path": workspace.relative(start)},
        )
    workspace._reject_symlink_components(start)
    file_pattern = _normalize_glob(args.file_glob) if args.file_glob else None
    matcher = _grep_matcher(args.pattern, regex=args.regex, case_sensitive=args.case_sensitive)
    limit = min(
        _entry_limit(args.max_results, context),
        max(1, context.limits.max_result_bytes // 1024),
    )
    matches: list[dict[str, Any]] = []
    scanned_files = 0
    skipped_binary_files = 0
    truncated_files = 0
    truncated = False

    for path in workspace.iter_files(start):
        relative_to_start = path.name if start.is_file() else path.relative_to(start).as_posix()
        if file_pattern and not _glob_matches(relative_to_start, file_pattern):
            continue
        workspace.require_file(path)
        scanned_files += 1
        try:
            with path.open("rb") as handle:
                raw = handle.read(context.limits.max_read_bytes + 1)
        except OSError as exc:
            raise _io_error("File could not be searched.", exc) from exc
        if len(raw) > context.limits.max_read_bytes:
            raw = raw[: context.limits.max_read_bytes]
            truncated_files += 1
        try:
            content, _ = _decode_utf8_chunk(raw)
        except HarnessExecutionError:
            skipped_binary_files += 1
            continue

        for line_number, line in enumerate(content.splitlines(), start=1):
            column = matcher(line)
            if column is None:
                continue
            if len(matches) >= limit:
                truncated = True
                break
            display_line = line[:_MAX_GREP_LINE_CHARS]
            matches.append(
                {
                    "path": workspace.relative(path),
                    "line": line_number,
                    "column": column + 1,
                    "text": display_line,
                    "text_truncated": len(display_line) < len(line),
                }
            )
        if truncated:
            break

    return {
        "path": workspace.relative(start),
        "pattern": args.pattern,
        "matches": matches,
        "scanned_files": scanned_files,
        "skipped_binary_files": skipped_binary_files,
        "truncated_files": truncated_files,
        "truncated": truncated,
    }


def file_info(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """返回路径的类型、大小、SHA-256 与修改时间。

    仅接受普通文件与目录，符号链接和其他类型直接拒绝。
    """
    args = _as(arguments, FileInfoArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    workspace._reject_symlink_components(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise HarnessExecutionError(
            "NOT_FOUND",
            "Path does not exist.",
            details={"path": workspace.relative(path)},
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        workspace._raise_symlink(path)
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise HarnessExecutionError(
            "UNSUPPORTED_FILE_TYPE",
            "Path is not a regular file or directory.",
            details={"path": workspace.relative(path)},
        )
    is_file = stat.S_ISREG(metadata.st_mode)
    return {
        "path": workspace.relative(path),
        "type": "file" if is_file else "directory",
        "size": metadata.st_size if is_file else None,
        "sha256": _sha256(path) if is_file else None,
        "modified_at": datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
    }


def publish_artifact(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """把已校验的最终交付文件显式暴露给用户下载。

    工作区外的文件会先复制到 published 目录再发布，
    展示名与描述会过滤控制字符并截断到限长。
    """

    args = _as(arguments, PublishArtifactArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    metadata = workspace.require_file(path)
    workspace.ensure_file_size(metadata.st_size)
    if not workspace.is_inside_workspace(path):
        staged = workspace.root / "published" / f"{uuid.uuid4().hex}-{path.name}"
        workspace.prepare_parent(staged, create=True)
        workspace.ensure_workspace_capacity(
            path=staged,
            replacing_bytes=0,
            new_bytes=metadata.st_size,
        )
        try:
            shutil.copyfile(path, staged)
        except OSError as exc:
            raise _io_error("External artifact could not be staged for download.", exc) from exc
        path = staged
        metadata = workspace.require_file(path)
    display_name = _safe_artifact_text(args.display_name, 180) or path.name
    description = _safe_artifact_text(args.description, 500)
    return {
        "path": workspace.relative(path),
        "display_name": display_name,
        "description": description,
        "size": metadata.st_size,
        "sha256": _sha256(path),
        "content_type": (
            mimetypes.guess_type(display_name)[0]
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        ),
    }


def make_directory(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """创建目录并返回是否为本次新建。

    目录已存在时按 exist_ok 决定放行或抛出 ALREADY_EXISTS。
    """
    args = _as(arguments, MakeDirectoryArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    existed = path.exists()
    if existed:
        workspace.require_directory(path)
        if not args.exist_ok:
            raise HarnessExecutionError(
                "ALREADY_EXISTS",
                "Directory already exists.",
                details={"path": workspace.relative(path)},
            )
    else:
        parent = path.parent
        if not args.parents:
            workspace.require_directory(parent)
        else:
            workspace._reject_symlink_components(parent)
        try:
            path.mkdir(parents=args.parents, exist_ok=args.exist_ok)
        except OSError as exc:
            raise _io_error("Directory could not be created.", exc) from exc
        workspace.require_directory(path)
    return {
        "path": workspace.relative(path),
        "created": not existed,
    }


def delete_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """把文件软删除到 Harness 回收站，返回可恢复标记与哈希。

    删除前按期望 SHA-256 校验；迁移失败时回退到 shutil.move。
    """
    args = _as(arguments, DeleteFileArguments)
    workspace = _Workspace(context)
    path = workspace.resolve(args.path)
    metadata = workspace.require_file(path)
    sha256 = workspace.assert_expected_hash(path, args.expected_sha256)
    trash_id = uuid.uuid4().hex
    display_path = workspace.relative(path)
    trash_relative = (
        path.relative_to(workspace.root)
        if workspace.is_inside_workspace(path)
        else Path(path.name)
    )
    trash_target = workspace.trash_target(trash_relative, trash_id)
    try:
        os.replace(path, trash_target)
    except OSError:
        try:
            shutil.move(str(path), str(trash_target))
        except OSError as move_exc:
            raise _io_error("File could not be moved to Harness trash.", move_exc) from move_exc
    return {
        "path": display_path,
        "deleted": True,
        "recoverable": True,
        "trash_id": trash_id,
        "size": metadata.st_size,
        "sha256": sha256,
    }


def move_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """原子移动普通文件，并返回源、目标与目标原有哈希。

    源文件按期望 SHA-256 校验；目标已存在时需显式 overwrite。
    """
    args = _as(arguments, MoveFileArguments)
    workspace = _Workspace(context)
    source = workspace.resolve(args.source_path)
    destination = workspace.resolve(args.destination_path)
    _different_paths(source, destination)
    source_metadata = workspace.require_file(source)
    source_sha256 = workspace.assert_expected_hash(source, args.expected_sha256)
    workspace.prepare_parent(destination, create=args.create_parents)
    destination_previous_sha256 = _prepare_destination(
        workspace,
        destination,
        overwrite=args.overwrite,
        expected_sha256=args.expected_destination_sha256,
    )
    try:
        os.replace(source, destination)
    except OSError as exc:
        raise _io_error("File could not be moved.", exc) from exc
    return {
        "source_path": workspace.relative(source),
        "destination_path": workspace.relative(destination),
        "size": source_metadata.st_size,
        "sha256": source_sha256,
        "destination_previous_sha256": destination_previous_sha256,
    }


def copy_file(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    """把普通文件原子复制到目标位置，并返回新旧哈希。

    复制前检查单文件大小、工作区配额与目标覆盖策略。
    """
    args = _as(arguments, CopyFileArguments)
    workspace = _Workspace(context)
    source = workspace.resolve(args.source_path)
    destination = workspace.resolve(args.destination_path)
    _different_paths(source, destination)
    source_metadata = workspace.require_file(source)
    workspace.ensure_file_size(source_metadata.st_size)
    source_sha256 = workspace.assert_expected_hash(source, args.expected_sha256)
    workspace.prepare_parent(destination, create=args.create_parents)
    destination_previous_sha256 = _prepare_destination(
        workspace,
        destination,
        overwrite=args.overwrite,
        expected_sha256=args.expected_destination_sha256,
    )
    destination_size = destination.stat().st_size if destination.exists() else 0
    workspace.ensure_workspace_capacity(
        path=destination,
        replacing_bytes=destination_size,
        new_bytes=source_metadata.st_size,
    )
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise _io_error("Source file could not be read.", exc) from exc
    _atomic_write(destination, content)
    return {
        "source_path": workspace.relative(source),
        "destination_path": workspace.relative(destination),
        "size": source_metadata.st_size,
        "sha256": source_sha256,
        "destination_previous_sha256": destination_previous_sha256,
    }


def register_file_tools(registry: HarnessRegistry) -> HarnessRegistry:
    """把内置的强类型文件工具注册到 registry，并原样返回该 registry。"""

    path_contract = (
        "Relative paths start in the current TaskFrame workspace; absolute paths and "
        "parent-directory paths are accepted subject to host permissions and any enabled OS sandbox. "
    )

    registry.register(
        name="read_file",
        description=(
            path_contract
            + "Read UTF-8 text from a file. When truncated, continue by passing the "
            "returned continuation_token with the same path; never guess byte offsets."
        ),
        argument_model=ReadFileArguments,
        handler=read_file,
        side_effect="read",
    )
    registry.register(
        name="extract_document_text",
        description=(
            path_contract
            + "Extract PDF, DOCX, HTML, Markdown, or plain-text content into a UTF-8 "
            "file, then use read_file to inspect the extracted text. Use this "
            "instead of read_file for binary documents such as PDF and DOCX."
        ),
        argument_model=ExtractDocumentTextArguments,
        handler=extract_document_text,
        side_effect="write",
    )
    registry.register(
        name="write_file",
        description=path_contract + "Atomically create or replace a UTF-8 file.",
        argument_model=WriteFileArguments,
        handler=write_file,
        side_effect="write",
    )
    registry.register(
        name="edit_file",
        description=path_contract + "Atomically replace exact text in a UTF-8 file.",
        argument_model=EditFileArguments,
        handler=edit_file,
        side_effect="write",
    )
    registry.register(
        name="list_directory",
        description=path_contract + "List files and directories without following symbolic links.",
        argument_model=ListDirectoryArguments,
        handler=list_directory,
        side_effect="read",
    )
    registry.register(
        name="glob",
        description=path_contract + "Find paths under a start directory matching a glob pattern.",
        argument_model=GlobArguments,
        handler=glob_files,
        side_effect="read",
    )
    registry.register(
        name="grep",
        description=path_contract + "Search UTF-8 files using literal text.",
        argument_model=GrepArguments,
        handler=grep_files,
        side_effect="read",
    )
    registry.register(
        name="file_info",
        description=path_contract + "Return type, size, SHA-256, and modification time.",
        argument_model=FileInfoArguments,
        handler=file_info,
        side_effect="read",
    )
    registry.register(
        name="publish_artifact",
        description=(
            path_contract
            + "Explicitly publish one verified final file for user download; external files "
            "are copied into the TaskFrame artifact area. "
            "Use only for final deliverables, never for inputs, caches, logs, temporary "
            "files, runner code, or build intermediates."
        ),
        argument_model=PublishArtifactArguments,
        handler=publish_artifact,
        side_effect="write",
    )
    registry.register(
        name="mkdir",
        description=path_contract + "Create a directory.",
        argument_model=MakeDirectoryArguments,
        handler=make_directory,
        side_effect="write",
    )
    registry.register(
        name="delete_file",
        description=(
            path_contract
            + "Soft-delete a file into private Harness trash so it remains recoverable."
        ),
        argument_model=DeleteFileArguments,
        handler=delete_file,
        side_effect="delete",
    )
    registry.register(
        name="move_file",
        description=path_contract + "Move a regular file.",
        argument_model=MoveFileArguments,
        handler=move_file,
        side_effect="write",
    )
    registry.register(
        name="copy_file",
        description=path_contract + "Atomically copy a regular file.",
        argument_model=CopyFileArguments,
        handler=copy_file,
        side_effect="write",
    )
    return registry


def build_file_tool_registry() -> HarnessRegistry:
    """构造仅包含内置文件工具的新 HarnessRegistry。"""
    return register_file_tools(HarnessRegistry())


def _as(arguments: BaseModel, expected: type[BaseModel]) -> Any:
    """把通用参数模型断言为处理器期望的类型，否则抛出 INVALID_ARGUMENTS。"""
    if not isinstance(arguments, expected):
        raise HarnessExecutionError(
            "INVALID_ARGUMENTS",
            f"Handler expected {expected.__name__}.",
        )
    return arguments


def _normalize_access_path(raw_path: str, *, root: Path, allow_root: bool) -> Path:
    """把原始路径规范化为绝对路径，并拒绝空路径、空字节与根目录。

    相对路径按工作区根目录拼接，沙箱工作区前缀会被剥离为相对形式。
    """
    if "\x00" in raw_path:
        raise HarnessExecutionError("INVALID_PATH", "Path cannot contain a null byte.")
    if not raw_path.strip():
        raise HarnessExecutionError("INVALID_PATH", "Path cannot be empty.")
    portable = raw_path.replace("\\", "/")
    normalized_alias = _strip_sandbox_workspace_prefix(portable)
    normalized_raw = normalized_alias if normalized_alias != portable else raw_path
    try:
        candidate = Path(normalized_raw).expanduser()
    except (KeyError, RuntimeError) as exc:
        raise HarnessExecutionError(
            "INVALID_PATH",
            "Home-relative path could not be expanded on this host.",
        ) from exc
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(os.path.normpath(candidate)))
    if candidate == root and not allow_root:
        raise HarnessExecutionError(
            "INVALID_PATH",
            "A file or child-directory path is required.",
        )
    if not candidate.parts:
        if allow_root:
            return candidate
        raise HarnessExecutionError("INVALID_PATH", "A file or child-directory path is required.")
    return candidate


def _normalize_glob(raw_pattern: str | None) -> str:
    """规范化并校验 glob 模式，拒绝空模式、绝对路径与向上越界。"""
    if raw_pattern is None or not raw_pattern.strip() or "\x00" in raw_pattern:
        raise HarnessExecutionError("INVALID_PATTERN", "Glob pattern cannot be empty.")
    if PureWindowsPath(raw_pattern).drive:
        raise HarnessExecutionError("INVALID_PATTERN", "Absolute glob patterns are denied.")
    pattern = _strip_sandbox_workspace_prefix(raw_pattern.replace("\\", "/"))
    parsed = PurePosixPath(pattern)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise HarnessExecutionError("INVALID_PATTERN", "Glob traversal outside the workspace is denied.")
    if _TRASH_DIRECTORY in parsed.parts:
        raise HarnessExecutionError("RESERVED_PATH", "Harness trash cannot be searched.")
    return pattern


def _strip_sandbox_workspace_prefix(raw_path: str) -> str:
    """把沙箱工作区前缀路径改写为相对形式，其他路径原样返回。"""
    if raw_path == SANDBOX_WORKSPACE:
        return "."
    prefix = f"{SANDBOX_WORKSPACE}/"
    if raw_path.startswith(prefix):
        return raw_path[len(prefix) :]
    return raw_path


def _glob_matches(relative_path: str, pattern: str) -> bool:
    """判断相对路径是否匹配 glob 模式，兼容 **/ 前缀与 fnmatch 语义。"""
    path = PurePosixPath(relative_path)
    if path.match(pattern):
        return True
    if pattern.startswith("**/") and path.match(pattern[3:]):
        return True
    return fnmatch.fnmatchcase(relative_path, pattern)


def _iter_directory_entries(workspace: _Workspace, start: Path) -> Iterator[Path]:
    """按名称排序遍历目录树，产出目录与文件并跳过内部回收站。"""
    for directory, directory_names, file_names in os.walk(
        start,
        topdown=True,
        followlinks=False,
    ):
        base = Path(directory)
        allowed_directories: list[str] = []
        for name in sorted(directory_names):
            child = base / name
            if name == _TRASH_DIRECTORY and child == workspace.root / _TRASH_DIRECTORY:
                continue
            if child.is_symlink():
                yield child
                continue
            allowed_directories.append(name)
            yield child
        directory_names[:] = allowed_directories
        for name in sorted(file_names):
            yield base / name


def _entry_payload(
    workspace: _Workspace,
    path: Path,
    metadata: os.stat_result,
) -> dict[str, Any]:
    """把 stat 结果转换为条目字典，标注符号链接、目录或其他类型。"""
    if stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
        size: int | None = None
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
        size = None
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
        size = metadata.st_size
    else:
        kind = "other"
        size = None
    return {
        "path": workspace.relative(path),
        "type": kind,
        "size": size,
    }


def _entry_limit(requested: int | None, context: HarnessToolContext) -> int:
    """把请求的条目上限收敛到上下文允许的最大值。"""
    return min(requested or context.limits.max_entries, context.limits.max_entries)


def _grep_matcher(
    pattern: str,
    *,
    regex: bool,
    case_sensitive: bool,
) -> Any:
    """构造逐行匹配器，返回首次命中的列索引，未命中返回 None。

    regex 为真时抛出 REGEX_NOT_SUPPORTED；大小写不敏感时对
    模式与每行同时 casefold。
    """
    if regex:
        raise HarnessExecutionError(
            "REGEX_NOT_SUPPORTED",
            "Harness grep currently supports literal matching only.",
        )

    needle = pattern if case_sensitive else pattern.casefold()

    def literal_match(line: str) -> int | None:
        """在被检索行中定位 needle，未命中时返回 None。"""
        haystack = line if case_sensitive else line.casefold()
        column = haystack.find(needle)
        return column if column >= 0 else None

    return literal_match


def _decode_utf8_chunk(raw: bytes) -> tuple[str, int]:
    """解码 UTF-8 字节块，末尾截断的半个字符不计入消耗字节。

    首字符即不完整时抛出 READ_CHUNK_TOO_SMALL，
    其他非法编码抛出 UNSUPPORTED_ENCODING。
    """
    try:
        return raw.decode("utf-8"), len(raw)
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data" and exc.end == len(raw):
            complete = raw[: exc.start]
            if not complete and raw:
                raise HarnessExecutionError(
                    "READ_CHUNK_TOO_SMALL",
                    "Read chunk ends inside the first UTF-8 character; request more bytes.",
                ) from exc
            return complete.decode("utf-8"), len(complete)
        raise HarnessExecutionError(
            "UNSUPPORTED_ENCODING",
            "File is not valid UTF-8 text.",
        ) from exc


def _read_utf8_file(path: Path, max_bytes: int) -> str:
    """读取 UTF-8 文本文件，并在此处统一转换大小与编码错误。"""
    try:
        metadata = path.stat()
        if metadata.st_size > max_bytes:
            raise HarnessExecutionError(
                "FILE_TOO_LARGE",
                "File exceeds the Harness per-file size limit.",
                details={"actual_bytes": metadata.st_size, "max_bytes": max_bytes},
            )
        return path.read_text(encoding="utf-8")
    except HarnessExecutionError:
        raise
    except UnicodeDecodeError as exc:
        raise HarnessExecutionError(
            "UNSUPPORTED_ENCODING",
            "File is not valid UTF-8 text.",
        ) from exc
    except OSError as exc:
        raise _io_error("File could not be read.", exc) from exc


def _sha256(path: Path) -> str:
    """分块计算文件的 SHA-256 十六进制摘要。"""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise _io_error("File hash could not be calculated.", exc) from exc
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    """先写同目录临时文件再 os.replace，保证写入的原子性。

    异常路径下会清理临时文件，失败统一转换为 IO_ERROR。
    """
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise _io_error("File could not be written atomically.", exc) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _prepare_destination(
    workspace: _Workspace,
    destination: Path,
    *,
    overwrite: bool,
    expected_sha256: str | None,
) -> str | None:
    """校验复制/移动目标的覆盖条件并返回其原哈希。

    目标不存在时返回 None；存在但未开启 overwrite 时抛出 ALREADY_EXISTS。
    """
    if not destination.exists():
        if expected_sha256:
            raise HarnessExecutionError(
                "HASH_MISMATCH",
                "Expected an existing destination, but it does not exist.",
                retryable=True,
                details={"path": workspace.relative(destination)},
            )
        return None
    workspace.require_file(destination)
    actual = workspace.assert_expected_hash(destination, expected_sha256)
    if not overwrite:
        raise HarnessExecutionError(
            "ALREADY_EXISTS",
            "Destination already exists; set overwrite to replace it.",
            details={"path": workspace.relative(destination), "sha256": actual},
        )
    return actual


def _different_paths(source: Path, destination: Path) -> None:
    """确保源与目标路径不同，否则抛出 INVALID_ARGUMENTS。"""
    if source == destination:
        raise HarnessExecutionError(
            "INVALID_ARGUMENTS",
            "Source and destination must be different paths.",
        )


def _safe_artifact_text(value: str | None, max_length: int) -> str | None:
    """去掉首尾空白与控制字符并按上限截断，空结果返回 None。"""
    if value is None:
        return None
    cleaned = "".join(
        character
        for character in str(value).strip()
        if ord(character) >= 32 and ord(character) != 127
    )
    return cleaned[:max_length] or None


def _io_error(message: str, exc: OSError) -> HarnessExecutionError:
    """把 OSError 映射为带错误码与可重试标记的 HarnessExecutionError。"""
    if isinstance(exc, PermissionError):
        code = "PERMISSION_DENIED"
    elif isinstance(exc, FileExistsError):
        code = "ALREADY_EXISTS"
    elif isinstance(exc, FileNotFoundError):
        code = "NOT_FOUND"
    else:
        code = "IO_ERROR"
    return HarnessExecutionError(
        code,
        message,
        retryable=isinstance(exc, (BlockingIOError, TimeoutError)),
        details={"exception_type": type(exc).__name__},
    )


__all__ = [
    "CopyFileArguments",
    "DeleteFileArguments",
    "EditFileArguments",
    "ExtractDocumentTextArguments",
    "FileInfoArguments",
    "GlobArguments",
    "GrepArguments",
    "ListDirectoryArguments",
    "MakeDirectoryArguments",
    "MoveFileArguments",
    "PublishArtifactArguments",
    "ReadFileArguments",
    "WriteFileArguments",
    "build_file_tool_registry",
    "copy_file",
    "delete_file",
    "edit_file",
    "extract_document_text",
    "file_info",
    "glob_files",
    "grep_files",
    "list_directory",
    "make_directory",
    "move_file",
    "publish_artifact",
    "read_file",
    "register_file_tools",
    "write_file",
]
