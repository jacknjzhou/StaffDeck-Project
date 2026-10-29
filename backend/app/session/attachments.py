from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import mimetypes
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.db.models import new_id
from app.session.attachment_store import sandbox_attachment_path
from app.session.session_schema import ChatAttachmentRead


MAX_EXTRACTED_TEXT_CHARS = 24_000
MAX_PREVIEW_CHARS = 600
IMAGE_DATA_URL_LIMIT_BYTES = 4 * 1024 * 1024
SUPPORTED_IMAGE_EXTENSIONS = {".gif", ".png", ".svg", ".jpg", ".jpeg", ".webp", ".bmp"}
SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/gif",
    "image/png",
    "image/svg+xml",
    "image/jpeg",
    "image/webp",
    "image/bmp",
}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".log",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
}


def parse_chat_attachment(
    filename: str,
    content_type: str | None,
    data: bytes,
    *,
    extract_text: bool = True,
) -> ChatAttachmentRead:
    """按 MIME 与扩展名把上传字节解析成对话轮可直接使用的附件对象。

    图片内联为 data URL，PDF 只提取前 30 页文本，文本类文件解码后按上限截断；
    extract_text=False 时仅保留沙箱路径信息，不解析正文。
    """
    safe_name = _safe_filename(filename)
    detected_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    lower_name = safe_name.lower()
    if _is_supported_image_file(lower_name, detected_type):
        detected_type = _image_content_type_for(lower_name, detected_type)
        return _image_attachment(safe_name, detected_type, data)
    if lower_name.endswith(".pdf") or detected_type == "application/pdf":
        if not extract_text:
            return _path_only_attachment(safe_name, detected_type, data, kind="pdf")
        return _pdf_attachment(safe_name, detected_type, data)
    if _is_text_file(lower_name, detected_type):
        if not extract_text:
            return _path_only_attachment(safe_name, detected_type, data, kind="text")
        return _text_attachment(safe_name, detected_type, data)
    return _path_only_attachment(safe_name, detected_type, data, kind="binary")


def attachment_context_lines(attachments: Iterable[ChatAttachmentRead | dict[str, Any]]) -> list[str]:
    """为每轮对话生成附件清单，提示模型该用哪种工具读取各个文件。

    无法解析为 ChatAttachmentRead 的条目会被跳过；图片与文档附件额外附上
    对应的读取约束，避免模型直接对二进制内容调用 read_file。
    """
    lines: list[str] = []
    normalized = [_coerce_attachment(item) for item in attachments]
    normalized = [item for item in normalized if item]
    if not normalized:
        return lines
    lines.append("上传附件上下文：")
    lines.append(
        "调用 typed 文件工具时使用 /workspace/... 文件工具路径；调用 exec_command 处理上传 "
        "附件时优先使用 attachments/... 工作区相对路径。用户明确提供的其他绝对路径可以 "
        "原样使用，但不要猜测或推导宿主机路径。"
    )
    for index, attachment in enumerate(normalized, start=1):
        sandbox_path = attachment.sandbox_path or sandbox_attachment_path(attachment, index)
        relative_path = sandbox_path.removeprefix("/workspace/")
        lines.append(
            f"{index}. 文件名：{attachment.filename}；类型：{attachment.kind}/{attachment.content_type}；"
            f"大小：{attachment.size} bytes；"
            f"exec_command 相对路径：{relative_path}；typed 文件工具路径：{sandbox_path}"
        )
        if attachment.kind == "image":
            lines.append("图片同时作为本轮视觉输入提供；若模型不支持视觉输入，请读取沙箱文件。")
        elif Path(attachment.filename).suffix.lower() in {
            ".pdf",
            ".doc",
            ".docx",
            ".html",
            ".htm",
        }:
            lines.append(
                "这是文档附件；不要直接使用 read_file。先调用 extract_document_text，"
                "再用 read_file 分页读取生成的 UTF-8 文本。"
            )
        elif attachment.kind == "text":
            lines.append("需要内容时请使用 read_file 读取工作区文件。")
        else:
            lines.append(
                "这是二进制附件，不能直接使用 read_file；请调用与文件格式匹配的能力"
                "或使用工作区相对路径交给受控命令处理。"
            )
    return lines


def message_content_with_attachment_context(content: str, metadata: dict[str, Any] | None) -> str:
    """把附件上下文段落拼接到用户消息正文之后，供模型当轮读取。

    没有可解析附件时原样返回 content；正文为空时用占位文案说明用户只上传了附件。
    """
    attachments = []
    if isinstance(metadata, dict):
        raw = metadata.get("attachments")
        if isinstance(raw, list):
            attachments = raw
    lines = attachment_context_lines(attachments)
    if not lines:
        return content
    return "\n\n".join([content.strip() or "（用户仅上传了附件）", "\n".join(lines)])


def image_payloads_from_attachments(attachments: Iterable[ChatAttachmentRead | dict[str, Any]]) -> list[dict[str, Any]]:
    """把受支持且 data URL 签名有效的图片附件转成视觉输入 payload。

    detail 固定为 auto；kind 非 image、缺 data URL 或魔数与 MIME 不符的条目直接跳过。
    """
    payloads: list[dict[str, Any]] = []
    normalized = [_coerce_attachment(item) for item in attachments]
    for attachment in normalized:
        if not attachment or not _attachment_is_supported_image(attachment) or not attachment.data_url:
            continue
        if not _data_url_has_valid_image_signature(attachment.data_url, attachment.content_type):
            continue
        payloads.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": attachment.data_url,
                    "detail": "auto",
                },
            }
        )
    return payloads


def message_images_from_metadata(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从消息 metadata 的 attachments 字段取出图片视觉输入 payload。

    metadata 缺失或 attachments 不是列表时返回空列表。
    """
    if not isinstance(metadata, dict):
        return []
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return []
    return image_payloads_from_attachments(attachments)


def request_has_image_attachments(attachments: Iterable[ChatAttachmentRead | dict[str, Any]]) -> bool:
    """判断本轮附件里是否存在受支持的图片，用于决定是否启用视觉输入。"""
    normalized = [_coerce_attachment(item) for item in attachments]
    return any(bool(item and _attachment_is_supported_image(item)) for item in normalized)


def validate_chat_turn_attachments(
    attachments: list[ChatAttachmentRead],
    *,
    max_attachments: int,
    max_attachment_bytes: int,
) -> list[ChatAttachmentRead]:
    """校验并归一化客户端回传的附件，任一字段不合规即抛 ValueError。

    /attachments 上传响应是无状态的，所以回传的每个字段都按不可信用户输入处理：
    重新裁剪文件名与 MIME、核对大小上限、重算沙箱路径，并重新生成正文、预览与摘要，
    浏览器回传的 text/preview 一律丢弃。
    """

    if len(attachments) > max_attachments:
        raise ValueError(f"最多携带 {max_attachments} 个附件")
    normalized: list[ChatAttachmentRead] = []
    for attachment in attachments:
        filename = _safe_filename(attachment.filename)[:255]
        content_type = str(attachment.content_type or "").strip()[:128]
        size = int(attachment.size)
        if size < 0 or size > max_attachment_bytes:
            raise ValueError(f"{filename} 超过附件大小限制")
        # The raw upload is authoritative. Never accept extracted text or previews
        # round-tripped by the browser as model context.
        text = None
        preview = (
            "图片附件"
            if attachment.kind == "image"
            else "文件已上传，请通过沙箱路径读取。"
        )
        data_url = _validated_image_data_url(
            attachment,
            filename=filename,
            content_type=content_type,
            size=size,
        )
        sha256 = str(attachment.sha256 or "").strip().lower() or None
        if sha256 and not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError(f"{filename} 的 SHA-256 无效")
        sandbox_path = str(attachment.sandbox_path or "").strip() or None
        if sandbox_path and sandbox_path != sandbox_attachment_path(
            attachment.model_copy(update={"filename": filename})
        ):
            raise ValueError(f"{filename} 的沙箱路径无效")
        normalized.append(
            attachment.model_copy(
                update={
                    "filename": filename,
                    "content_type": content_type,
                    "size": size,
                    "text": text,
                    "preview": preview,
                    "data_url": data_url,
                    "sandbox_path": sandbox_path,
                    "sha256": sha256,
                    "python_summary": _round_trip_summary(
                        filename,
                        content_type,
                        size,
                        text,
                    ),
                    "error": str(attachment.error or "")[:2_000] or None,
                }
            )
        )
    return normalized


def _validated_image_data_url(
    attachment: ChatAttachmentRead,
    *,
    filename: str,
    content_type: str,
    size: int,
) -> str | None:
    """校验图片附件的 data URL 并原样返回，任一环节不符即抛 ValueError。

    依次检查 kind 与 MIME 白名单、前缀、base64 编码、解码后字节数与声明的 size 一致、
    魔数与 MIME 匹配，以及提供了 sha256 时与解码内容一致；空 data URL 返回 None。
    """
    raw = str(attachment.data_url or "").strip()
    if not raw:
        return None
    if attachment.kind != "image" or not _is_supported_image_file(
        filename.lower(),
        content_type,
    ):
        raise ValueError(f"{filename} 不是受支持的图片附件")
    prefix = f"data:{content_type};base64,"
    if not raw.startswith(prefix):
        raise ValueError(f"{filename} 的图片 data URL 无效")
    encoded = raw.removeprefix(prefix)
    if len(encoded) > ((IMAGE_DATA_URL_LIMIT_BYTES + 2) // 3) * 4:
        raise ValueError(f"{filename} 的图片 data URL 超限")
    try:
        decoded = base64.b64decode(
            encoded,
            validate=True,
        )
    except Exception as exc:
        raise ValueError(f"{filename} 的图片 data URL 无效") from exc
    if len(decoded) > IMAGE_DATA_URL_LIMIT_BYTES or len(decoded) != size:
        raise ValueError(f"{filename} 的图片 data URL 大小不一致或超限")
    if not _image_bytes_match_content_type(decoded, content_type):
        raise ValueError(f"{filename} 的图片内容与 MIME 类型不一致")
    if attachment.sha256 and hashlib.sha256(decoded).hexdigest() != attachment.sha256.lower():
        raise ValueError(f"{filename} 的图片 data URL 与上传文件不一致")
    return raw


def _round_trip_summary(
    filename: str,
    content_type: str,
    size: int,
    text: str | None,
) -> str:
    """按回传字段重新生成 python_summary，不沿用浏览器提交的摘要文本。"""
    return _python_file_summary_from_size(
        filename,
        content_type,
        size,
        text or "",
    )


def _text_attachment(filename: str, content_type: str, data: bytes) -> ChatAttachmentRead:
    """构造文本类附件：解码正文、按上限截断，并生成短预览与体量摘要。"""
    text = _decode_text(data)
    trimmed = _trim_text(text, MAX_EXTRACTED_TEXT_CHARS)
    return ChatAttachmentRead(
        id=new_id("file"),
        filename=filename,
        content_type=content_type,
        size=len(data),
        kind="text",
        text=trimmed,
        preview=_trim_text(trimmed, MAX_PREVIEW_CHARS),
        python_summary=_python_file_summary(filename, content_type, data, trimmed),
    )


def _path_only_attachment(
    filename: str,
    content_type: str,
    data: bytes,
    *,
    kind: str,
) -> ChatAttachmentRead:
    """构造只带沙箱路径的附件，正文留空，交由受控命令或文件工具读取。"""
    return ChatAttachmentRead(
        id=new_id("file"),
        filename=filename,
        content_type=content_type,
        size=len(data),
        kind=kind,
        preview="文件已上传，请通过沙箱路径读取。",
        python_summary=_python_file_summary(filename, content_type, data, ""),
    )


def _pdf_attachment(filename: str, content_type: str, data: bytes) -> ChatAttachmentRead:
    """提取 PDF 前 30 页文本，解析失败时把错误写入 error 字段而不抛出。

    超过 30 页时在正文末尾注明总页数；未抽到文本则 text 与 preview 都为 None。
    """
    text = ""
    error: str | None = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages[:30]:
            pages.append(page.extract_text() or "")
        text = "\n\n".join(page.strip() for page in pages if page.strip())
        if len(reader.pages) > 30:
            text += f"\n\n... PDF 共 {len(reader.pages)} 页，仅提取前 30 页。"
    except Exception as exc:  # noqa: BLE001 - return readable parse error to caller.
        error = f"PDF 解析失败：{exc}"
    trimmed = _trim_text(text, MAX_EXTRACTED_TEXT_CHARS)
    return ChatAttachmentRead(
        id=new_id("file"),
        filename=filename,
        content_type=content_type or "application/pdf",
        size=len(data),
        kind="pdf",
        text=trimmed or None,
        preview=_trim_text(trimmed, MAX_PREVIEW_CHARS) if trimmed else None,
        python_summary=_python_file_summary(filename, content_type, data, trimmed),
        error=error,
    )


def _image_attachment(filename: str, content_type: str, data: bytes) -> ChatAttachmentRead:
    """构造图片附件，超过 IMAGE_DATA_URL_LIMIT_BYTES 时不生成 data URL。"""
    data_url = None
    if len(data) <= IMAGE_DATA_URL_LIMIT_BYTES:
        encoded = base64.b64encode(data).decode("ascii")
        data_url = f"data:{content_type};base64,{encoded}"
    return ChatAttachmentRead(
        id=new_id("file"),
        filename=filename,
        content_type=content_type,
        size=len(data),
        kind="image",
        data_url=data_url,
        preview="图片附件",
        python_summary=_python_file_summary(filename, content_type, data, ""),
    )


def _python_file_summary(filename: str, content_type: str, data: bytes, text: str) -> str:
    """按字节长度生成附件摘要，正文统计交给 _python_file_summary_from_size。"""
    return _python_file_summary_from_size(
        filename,
        content_type,
        len(data),
        text,
    )


def _python_file_summary_from_size(
    filename: str,
    content_type: str,
    size: int,
    text: str,
) -> str:
    """生成一行附件摘要：大小与 MIME，有正文时再补体量统计与结构识别结果。

    正文为空时明确说明未抽取到可读文本，避免模型误判文件为空。
    """
    parts = [f"文件 {filename}，{size} bytes，MIME {content_type}。"]
    if text:
        lines = text.splitlines()
        words = re.findall(r"\S+", text)
        parts.append(f"解析得到 {len(text)} 个字符、{len(lines)} 行、约 {len(words)} 个词。")
        tabular = _tabular_summary(text)
        if tabular:
            parts.append(tabular)
        json_summary = _json_summary(text)
        if json_summary:
            parts.append(json_summary)
    else:
        parts.append("未抽取到可直接阅读的文本正文。")
    return " ".join(parts)


def _tabular_summary(text: str) -> str:
    """嗅探文本前 20 行是否为表格，是则返回列数与前几列名，否则返回空串。"""
    sample = "\n".join(text.splitlines()[:20])
    if not sample.strip():
        return ""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return ""
    rows = list(csv.reader(io.StringIO(sample), dialect))
    if not rows:
        return ""
    columns = rows[0]
    return f"检测到表格结构，约 {len(columns)} 列；前几列：{', '.join(columns[:6])}。"


def _json_summary(text: str) -> str:
    """识别正文是否为 JSON 并概述结构：对象列顶层字段，数组给元素数量。

    解析失败或首字符不是 { / [ 时返回空串。
    """
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return ""
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return ""
    if isinstance(parsed, dict):
        keys = list(parsed.keys())[:8]
        return f"检测到 JSON 对象，顶层字段：{', '.join(map(str, keys))}。"
    if isinstance(parsed, list):
        return f"检测到 JSON 数组，元素数量：{len(parsed)}。"
    return "检测到 JSON 标量。"


def _decode_text(data: bytes) -> str:
    """按 utf-8-sig、utf-8、gb18030、latin-1 顺序尝试解码，失败时替换非法字节。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _safe_filename(filename: str) -> str:
    """剥掉路径分隔符与首尾空白，只留文件名；结果为空时回退为 uploaded-file。

    防止客户端提交 ../ 之类的路径穿越串影响沙箱路径推导。
    """
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return name or "uploaded-file"


def _trim_text(text: str, max_chars: int) -> str:
    """去掉 NUL 字符并裁剪首尾空白，超长时截断并追加截断提示。"""
    normalized = text.replace("\x00", "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "\n...（内容已截断）"


def _is_text_file(lower_name: str, content_type: str) -> bool:
    """判断文件是否按文本处理：扩展名在白名单内，或 MIME 属于文本类。"""
    extension = "." + lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
    return (
        extension in TEXT_EXTENSIONS
        or content_type.startswith("text/")
        or content_type
        in {
            "application/json",
            "application/xml",
            "application/x-yaml",
            "application/yaml",
        }
    )


def _is_supported_image_file(lower_name: str, content_type: str) -> bool:
    """判断文件是否为受支持图片：MIME 在白名单内或扩展名受支持。

    两者满足其一即返回 True，因此扩展名与 MIME 不一致时仍可能进入图片分支，
    内容真实性由后续魔数校验兜底。
    """
    extension = "." + lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
    return content_type.lower() in SUPPORTED_IMAGE_CONTENT_TYPES or extension in SUPPORTED_IMAGE_EXTENSIONS


def _attachment_is_supported_image(attachment: ChatAttachmentRead) -> bool:
    """判断附件 kind 为 image 且文件名与 MIME 落在受支持图片格式内。"""
    return attachment.kind == "image" and _is_supported_image_file(attachment.filename.lower(), attachment.content_type)


def _data_url_has_valid_image_signature(data_url: str, content_type: str) -> bool:
    """校验 data URL 前缀与 base64 内容，并确认魔数与声明的 MIME 一致。

    前缀不符或 base64 解码失败时返回 False，不抛异常，便于调用方直接跳过该附件。
    """
    prefix = f"data:{content_type};base64,"
    if not data_url.startswith(prefix):
        return False
    try:
        data = base64.b64decode(data_url.removeprefix(prefix), validate=True)
    except (ValueError, TypeError):
        return False
    return _image_bytes_match_content_type(data, content_type)


def _image_bytes_match_content_type(data: bytes, content_type: str) -> bool:
    """按文件魔数判断字节内容是否与声明的图片 MIME 匹配。

    仅识别 jpeg/png/gif/webp/bmp/svg 六类；SVG 只检查前 1024 字节里的根标签，
    未识别的 MIME 一律返回 False。
    """
    normalized = content_type.lower()
    if normalized == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and b"\xff\xd9" in data[3:]
    if normalized == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if normalized == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if normalized == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if normalized == "image/bmp":
        return data.startswith(b"BM")
    if normalized == "image/svg+xml":
        sample = data[:1024].lstrip().lower()
        return sample.startswith(b"<svg") or (sample.startswith(b"<?xml") and b"<svg" in sample)
    return False


def _image_content_type_for(lower_name: str, content_type: str) -> str:
    """补齐图片 MIME：优先保留受支持的原值，其次按扩展名推断，兜底回退原值。"""
    normalized = content_type.lower()
    if normalized in SUPPORTED_IMAGE_CONTENT_TYPES:
        return content_type
    guessed = mimetypes.guess_type(lower_name)[0]
    if guessed and guessed.lower() in SUPPORTED_IMAGE_CONTENT_TYPES:
        return guessed
    if lower_name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower_name.endswith(".png"):
        return "image/png"
    if lower_name.endswith(".gif"):
        return "image/gif"
    if lower_name.endswith(".svg"):
        return "image/svg+xml"
    if lower_name.endswith(".webp"):
        return "image/webp"
    if lower_name.endswith(".bmp"):
        return "image/bmp"
    return content_type


def _coerce_attachment(value: ChatAttachmentRead | dict[str, Any]) -> ChatAttachmentRead | None:
    """把 dict 或模型实例统一为 ChatAttachmentRead，无法解析时返回 None。"""
    if isinstance(value, ChatAttachmentRead):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return ChatAttachmentRead.model_validate(value)
    except Exception:
        return None
