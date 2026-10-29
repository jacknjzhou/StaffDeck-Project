from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Annotated, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.db.models import APIClient, APICredential, AgentProfile, User, utc_now
from app.public_api.credential_profiles import USER_FULL_ACCESS_SCOPES
from app.public_api.errors import PublicAPIError
from app.security.auth import _decode_token
from app.security.permissions import agent_owned_by_user, is_admin_user


PUBLIC_KEY_PREFIX = "sd_live_"
_security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class PublicPrincipal:
    """一次公开 API 调用的已鉴权主体：租户、操作人、生效 scope，以及可见/可管理员工集合。"""
    tenant_id: str
    actor_user: User
    scopes: frozenset[str]
    client_id: str | None = None
    credential_id: str | None = None
    agent_id: str | None = None
    allowed_agent_ids: frozenset[str] | None = None
    manageable_agent_ids: frozenset[str] | None = None
    bootstrap_user: bool = False

    def can(self, required: str) -> bool:
        """判断主体是否具备某个 scope：支持 "*" 全量授权与同命名空间 "ns:*" 通配。"""
        if "*" in self.scopes or required in self.scopes:
            return True
        namespace = required.split(":", 1)[0]
        return f"{namespace}:*" in self.scopes


def generate_api_key() -> tuple[str, str, str]:
    """生成新的 API key，返回（完整 token、前 20 位前缀、HMAC 摘要），库中只存前缀与摘要。"""
    token = f"{PUBLIC_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    prefix = token[:20]
    return token, prefix, _credential_digest(token)


def _credential_digest(token: str) -> str:
    """用 public_api_key_pepper（缺省回退 app_secret）对 token 做 HMAC-SHA256 摘要。"""
    settings = get_settings()
    pepper = settings.public_api_key_pepper or settings.app_secret
    return hmac.new(
        pepper.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _api_key_principal(token: str, db: Session) -> PublicPrincipal:
    """校验 sd_* API key 并构造主体：前缀查凭据、摘要比对，再校验状态、过期与客户端。

    校验通过会顺带刷新 last_used_at；生效 scope 取凭据与客户端 scope 的交集，
    user_full_access 凭据自动补齐新增账户能力，可见员工集合每次请求重新解析。
    """
    prefix = token[:20]
    credential = db.exec(
        select(APICredential).where(APICredential.key_prefix == prefix)
    ).first()
    if not credential or not hmac.compare_digest(
        credential.key_digest, _credential_digest(token)
    ):
        raise PublicAPIError(401, "INVALID_API_KEY", "The API key is invalid.")
    now = utc_now()
    if credential.status != "active" or credential.revoked_at is not None:
        raise PublicAPIError(401, "API_KEY_REVOKED", "The API key has been revoked.")
    if credential.expires_at is not None and credential.expires_at <= now:
        raise PublicAPIError(401, "API_KEY_EXPIRED", "The API key has expired.")
    client = db.get(APIClient, credential.client_id)
    if not client or client.tenant_id != credential.tenant_id or client.status != "active":
        raise PublicAPIError(401, "API_CLIENT_INACTIVE", "The API client is inactive.")
    actor = db.get(User, client.created_by_user_id or "")
    if not actor or actor.tenant_id != client.tenant_id:
        raise PublicAPIError(401, "API_CLIENT_OWNER_MISSING", "The API client owner is unavailable.")
    credential.last_used_at = now
    credential.updated_at = now
    db.add(credential)
    db.commit()
    client_scopes = set(client.scopes_json or [])
    credential_scopes = set(credential.scopes_json or [])
    if (
        (client.metadata_json or {}).get("credential_mode") == "user_full_access"
        and credential.agent_id is None
    ):
        # Profile credentials gain newly introduced account capabilities
        # without forcing every user to rotate an existing key.
        client_scopes.update(USER_FULL_ACCESS_SCOPES)
        credential_scopes.update(USER_FULL_ACCESS_SCOPES)
    effective = {
        scope
        for scope in credential_scopes
        if "*" in client_scopes
        or scope in client_scopes
        or f"{scope.split(':', 1)[0]}:*" in client_scopes
    }
    allowed_agent_ids, manageable_agent_ids = _agent_access_sets(
        db, client.tenant_id, actor
    )
    return PublicPrincipal(
        tenant_id=client.tenant_id,
        actor_user=actor,
        scopes=frozenset(effective),
        client_id=client.id,
        credential_id=credential.id,
        agent_id=credential.agent_id,
        allowed_agent_ids=allowed_agent_ids,
        manageable_agent_ids=manageable_agent_ids,
    )


def _agent_access_sets(
    db: Session,
    tenant_id: str,
    actor: User,
) -> tuple[frozenset[str], frozenset[str]]:
    """每次 API-key 请求都重新解析账户可见范围，返回（可见员工 id、可管理员工 id）。

    账户主密钥因此能跟随后续的归属、上架、隐藏与角色变更，无需重新签发凭据；
    metadata_json 中 hidden_from_staffdeck 为 True 的员工直接跳过。
    """
    rows = db.exec(select(AgentProfile).where(AgentProfile.tenant_id == tenant_id)).all()
    visible: set[str] = set()
    manageable: set[str] = set()
    for row in rows:
        metadata = row.metadata_json or {}
        if metadata.get("hidden_from_staffdeck") is True:
            continue
        if (
            is_admin_user(actor)
            or row.is_overall
            or agent_owned_by_user(row, actor)
            or metadata.get("published_to_gallery") is True
        ):
            visible.add(row.id)
        if is_admin_user(actor) or agent_owned_by_user(row, actor):
            manageable.add(row.id)
    return frozenset(visible), frozenset(manageable)


def _bootstrap_principal(token: str, db: Session) -> PublicPrincipal:
    """用控制台签发的用户 token 构造管理员主体（scope 为 {"*"}）；非租户管理员抛 403。"""
    payload = _decode_token(token)
    user = db.get(User, str(payload.get("user_id") or ""))
    if not user or user.tenant_id != payload.get("tenant_id"):
        raise PublicAPIError(401, "INVALID_USER_TOKEN", "The user token is invalid.")
    if not is_admin_user(user):
        raise PublicAPIError(403, "ADMIN_REQUIRED", "Tenant administrator access is required.")
    return PublicPrincipal(
        tenant_id=user.tenant_id,
        actor_user=user,
        scopes=frozenset({"*"}),
        bootstrap_user=True,
    )


def get_public_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    db: Session = Depends(get_session),
) -> PublicPrincipal:
    """FastAPI 依赖：要求 Bearer sd_* API key，校验后写入 request.state.public_principal。"""
    if not credentials:
        raise PublicAPIError(401, "NOT_AUTHENTICATED", "A Bearer API key is required.")
    token = credentials.credentials
    if not token.startswith(PUBLIC_KEY_PREFIX):
        raise PublicAPIError(401, "API_KEY_REQUIRED", "A StaffDeck sd_* API key is required.")
    principal = _api_key_principal(token, db)
    request.state.public_principal = principal
    return principal


def get_public_or_admin_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    db: Session = Depends(get_session),
) -> PublicPrincipal:
    """FastAPI 依赖：sd_* 前缀走 API key 鉴权，其余 token 按控制台管理员鉴权。"""
    if not credentials:
        raise PublicAPIError(401, "NOT_AUTHENTICATED", "A Bearer token is required.")
    token = credentials.credentials
    if token.startswith(PUBLIC_KEY_PREFIX):
        principal = _api_key_principal(token, db)
        request.state.public_principal = principal
        return principal
    try:
        principal = _bootstrap_principal(token, db)
        request.state.public_principal = principal
        return principal
    except PublicAPIError:
        raise
    except Exception as exc:
        raise PublicAPIError(401, "INVALID_USER_TOKEN", "The user token is invalid.") from exc


def require_scopes(*required_scopes: str) -> Callable[[PublicPrincipal], PublicPrincipal]:
    """生成 scope 校验依赖：任一必需 scope 缺失即抛 403 INSUFFICIENT_SCOPE（带 WWW-Authenticate）。"""
    def dependency(
        principal: Annotated[PublicPrincipal, Depends(get_public_principal)],
    ) -> PublicPrincipal:
        """校验主体是否具备闭包捕获的全部必需 scope，通过则原样返回主体。"""
        missing = [scope for scope in required_scopes if not principal.can(scope)]
        if missing:
            raise PublicAPIError(
                403,
                "INSUFFICIENT_SCOPE",
                f"Missing required scope: {', '.join(missing)}",
                headers={"WWW-Authenticate": f'Bearer scope="{" ".join(missing)}"'},
            )
        return principal

    return dependency


def enforce_agent_access(principal: PublicPrincipal, agent_id: str, *, write: bool = False) -> None:
    """校验主体能否访问指定员工：绑定员工不符、不在可见集合、写操作越权时抛 PublicAPIError。

    员工级 key 只能操作自身且不能改配置；写操作要求该员工在 manageable_agent_ids 内。
    """
    if principal.agent_id and principal.agent_id != agent_id:
        raise PublicAPIError(403, "AGENT_SCOPE_MISMATCH", "This key is bound to another agent.")
    if principal.allowed_agent_ids is not None and agent_id not in principal.allowed_agent_ids:
        raise PublicAPIError(404, "AGENT_NOT_FOUND", "Agent not found.")
    if write and principal.agent_id:
        raise PublicAPIError(
            403,
            "AGENT_KEY_READ_ONLY_CONFIG",
            "Agent keys cannot modify agent configuration.",
        )
    if (
        write
        and principal.manageable_agent_ids is not None
        and agent_id not in principal.manageable_agent_ids
    ):
        raise PublicAPIError(
            403,
            "AGENT_MANAGE_FORBIDDEN",
            "This account cannot modify the selected agent.",
        )
