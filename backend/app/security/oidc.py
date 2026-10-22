"""OIDC 授权码登录辅助:discovery / state 管理 / token 交换,httpx 手工实现。

不引入 authlib 等重依赖;state 存进程内存字典(服务端单组 api 进程内有效,
M1 可接受)。配置读取走 get_settings() 延迟导入,便于测试注入。
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import httpx

# state -> 过期时间戳(秒)
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def get_settings():
    """延迟读取配置:让测试可通过 monkeypatch app.config.get_settings 注入。"""
    from app.config import get_settings

    return get_settings()


def _discover(issuer: str) -> dict:
    resp = httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10)
    resp.raise_for_status()
    return resp.json()


def new_state() -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = time.time() + _STATE_TTL_SECONDS
    return state


def consume_state(state: str) -> bool:
    """一次性消费 state:不存在/过期均返回 False(防重放)。"""
    exp = _pending_states.pop(state, 0)
    return bool(exp) and time.time() < exp


def authorization_url(discovery: dict, state: str) -> str:
    s = get_settings()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": s.oidc_redirect_uri,
            "scope": "openid profile email",
            "state": state,
        }
    )
    return f"{discovery['authorization_endpoint']}?{query}"


def _exchange_code(discovery: dict, code: str) -> dict:
    s = get_settings()
    resp = httpx.post(
        discovery["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": s.oidc_redirect_uri,
            "client_id": s.oidc_client_id,
            "client_secret": s.oidc_client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_userinfo(discovery: dict, access_token: str) -> dict:
    resp = httpx.get(
        discovery["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
