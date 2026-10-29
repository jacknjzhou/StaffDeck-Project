from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session
from app.db.models import User


TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """用随机盐做 12 万轮 PBKDF2-HMAC-SHA256，返回 pbkdf2_sha256$salt$digest 形式。"""
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"


def verify_password(password: str, stored_hash: str) -> bool:
    """用存储哈希里的盐重算摘要并做常量时间比较；哈希格式非法时返回 False。"""
    try:
        _algo, salt, _digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    candidate = f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"
    return hmac.compare_digest(candidate, stored_hash)


def create_access_token(user: User) -> str:
    """签发访问令牌：payload 含 tenant_id、user_id、username 与到期时间 exp。

    载荷做 base64url 编码后用 app_secret 签名，返回 "body.signature" 字符串。
    """
    payload = {
        "tenant_id": user.tenant_id,
        "user_id": user.id,
        "username": user.username,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = _sign(body)
    return f"{body}.{signature}"


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_session),
) -> User:
    """FastAPI 依赖：从 Bearer 令牌解出 user_id 并加载用户，同时校验租户一致。

    缺少凭证、签名/期限非法、用户不存在或租户不符时统一抛 401。
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_token(credentials.credentials)
    user = db.get(User, payload.get("user_id", ""))
    if not user or user.tenant_id != payload.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user token")
    return user


def ensure_current_user_tenant(tenant_id: str | None, current_user: User) -> None:
    """校验请求声明的租户与当前登录用户一致；不一致抛 403。

    tenant_id 为 None 时以 JWT 中的用户身份为准，不做额外限制。
    """
    if not isinstance(current_user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    # tenant_id 未传(None)时以 JWT 中的用户身份为准;显式传入且不匹配才拒绝
    if tenant_id is not None and tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def require_current_tenant(
    tenant_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> User:
    """依赖：读取可选的 tenant_id 查询参数，校验后返回当前登录用户。"""
    ensure_current_user_tenant(tenant_id, current_user)
    return current_user


def _decode_token(token: str) -> dict[str, Any]:
    """校验令牌签名与有效期并返回解出的 payload；任一步失败抛 401。"""
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if not hmac.compare_digest(_sign(body), signature):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    try:
        payload = json.loads(base64.urlsafe_b64decode(_pad_b64(body)).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def _sign(body: str) -> str:
    """用配置的 app_secret 对 body 做 HMAC-SHA256，返回 base64url 编码的签名。"""
    secret = get_settings().app_secret.encode("utf-8")
    return _b64(hmac.new(secret, body.encode("utf-8"), hashlib.sha256).digest())


def _b64(value: bytes) -> str:
    """把字节做 base64url 编码并去掉尾部 "=" 填充。"""
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _pad_b64(value: str) -> bytes:
    """补回 base64url 的 "=" 填充并编码为字节串，供解码使用。"""
    return (value + "=" * (-len(value) % 4)).encode("utf-8")
