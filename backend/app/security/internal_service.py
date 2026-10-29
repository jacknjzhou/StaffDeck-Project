from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException

from app.config import get_settings


INTERNAL_SERVICE_HEADER = "X-UltraRAG-Internal-Token"
_INTERNAL_SERVICE_SCOPE = b"ultrarag-internal-mock-api-v1"


def internal_service_token() -> str:
    """用 APP_SECRET 对固定 scope 做 HMAC-SHA256，派生内部服务调用令牌。

    令牌不落库、不单独轮换：服务端与调用方按同一 secret 现算现比，
    APP_SECRET 一变更旧令牌立即失效。
    """
    secret = get_settings().app_secret.encode("utf-8")
    return hmac.new(secret, _INTERNAL_SERVICE_SCOPE, hashlib.sha256).hexdigest()


def require_internal_service(
    token: str | None = Header(default=None, alias=INTERNAL_SERVICE_HEADER),
) -> None:
    """FastAPI 依赖：校验 X-UltraRAG-Internal-Token 请求头是否为合法内部令牌。

    缺失或不匹配时抛 401；比较走 hmac.compare_digest 以避免时序侧信道。
    """
    if token is None or not hmac.compare_digest(token, internal_service_token()):
        raise HTTPException(status_code=401, detail="Internal service authentication required")
