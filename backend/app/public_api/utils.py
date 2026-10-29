from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastapi import Request
from sqlmodel import Session

from app.db.models import APIAuditLog
from app.public_api.auth import PublicPrincipal


def etag_for(value: Any) -> str:
    """计算值的稳定 ETag：JSON 规范化后取 SHA-256，键序与分隔符固定，跨进程一致。"""
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f'"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"'


def if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """按 RFC 9110 §13.1.2 比较 If-None-Match 与 ETag，判断条件请求是否命中。

    兼容弱校验符（W/"x"）、逗号分隔列表与 * 三种写法，因为 HTTP 缓存和
    代理会以这些方式改写请求头。
    """
    if not header_value:
        return False
    candidates = [item.strip() for item in header_value.split(",") if item.strip()]
    if "*" in candidates:
        return True
    normalized = etag.strip()
    for candidate in candidates:
        if candidate.removeprefix("W/").strip() == normalized:
            return True
    return False


def encode_cursor(created_at: Any, row_id: str) -> str:
    """把 created_at 与 row_id 编码成 URL 安全的 base64 游标，去掉尾部 padding。"""
    raw = json.dumps([str(created_at), row_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    """解析 encode_cursor 生成的游标，返回 (created_at, row_id)。

    游标为空或格式非法时返回 None 而不抛异常，调用方可直接按首页处理。
    """
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if isinstance(value, list) and len(value) == 2:
            return str(value[0]), str(value[1])
    except Exception:
        return None
    return None


def audit_request(
    db: Session,
    request: Request,
    principal: PublicPrincipal | None,
    *,
    status_code: int,
    duration_ms: float,
) -> None:
    """写入一条公共 API 审计日志并提交，记录方法、路径、状态码与耗时。

    principal 为 None（未认证请求）时租户与凭据字段留空。
    """
    row = APIAuditLog(
        tenant_id=principal.tenant_id if principal else None,
        credential_id=principal.credential_id if principal else None,
        request_id=str(getattr(request.state, "request_id", "")),
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        duration_ms=duration_ms,
    )
    db.add(row)
    db.commit()
