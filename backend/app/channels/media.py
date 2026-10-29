from __future__ import annotations

from pathlib import PurePath

MAX_CHANNEL_MEDIA_BYTES = 25 * 1024 * 1024
MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES = MAX_CHANNEL_MEDIA_BYTES + 32


class ChannelMediaTooLargeError(ValueError):
    """渠道附件超过大小上限时抛出；继承 ValueError，便于按参数错误统一处理。"""


def ensure_channel_media_size(size: int, *, encrypted: bool = False) -> None:
    """校验渠道附件字节数未超上限，超限抛 ChannelMediaTooLargeError。

    encrypted 为真时按加密后上限（多出 32 字节）判断。
    """
    limit = MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES if encrypted else MAX_CHANNEL_MEDIA_BYTES
    if size > limit:
        raise ChannelMediaTooLargeError(f"渠道附件超过大小上限: size={size} limit={limit}")


def collect_limited_media(chunks, *, encrypted: bool = False) -> bytes:
    """逐块累积字节流并检查上限，超限立即抛出，避免把超大附件整个读进内存。

    空块会被跳过；返回所有非空块拼接后的完整 bytes。
    """
    limit = MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES if encrypted else MAX_CHANNEL_MEDIA_BYTES
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ChannelMediaTooLargeError(
                f"渠道附件超过大小上限: size>{limit} limit={limit}"
            )
        parts.append(chunk)
    return b"".join(parts)


def normalize_image_media(data: bytes) -> tuple[bytes, str, str] | None:
    """识别图片真实格式并归一化，返回 (数据, content_type, 扩展名)。

    JPEG 截断到 EOI 结束符以丢掉尾部噪声；无法识别的格式返回 None。
    """
    if data.startswith(b"\xff\xd8\xff"):
        eoi = data.rfind(b"\xff\xd9")
        if eoi >= 3:
            return data[: eoi + 2], "image/jpeg", ".jpg"
    detected = detect_image_media_type(data)
    if detected is None:
        return None
    content_type, extension = detected
    return data, content_type, extension


def detect_image_media_type(data: bytes) -> tuple[str, str] | None:
    """按文件头魔数识别图片类型，返回 (content_type, 扩展名)。

    支持 JPEG、PNG、GIF、WebP、BMP 与 SVG；其余格式返回 None。
    """
    if data.startswith(b"\xff\xd8\xff") and b"\xff\xd9" in data[3:]:
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"BM"):
        return "image/bmp", ".bmp"
    sample = data[:1024].lstrip().lower()
    if sample.startswith(b"<svg") or (sample.startswith(b"<?xml") and b"<svg" in sample):
        return "image/svg+xml", ".svg"
    return None


def filename_with_extension(filename: str, extension: str) -> str:
    """去掉原文件名的目录与旧扩展名后换成指定扩展名；空文件名回退为 image。"""
    name = PurePath(filename or "image").name
    stem = PurePath(name).stem or "image"
    return f"{stem}{extension}"


__all__ = [
    "MAX_CHANNEL_MEDIA_BYTES",
    "MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES",
    "ChannelMediaTooLargeError",
    "collect_limited_media",
    "detect_image_media_type",
    "ensure_channel_media_size",
    "filename_with_extension",
    "normalize_image_media",
]
