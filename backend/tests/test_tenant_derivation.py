"""租户推导收紧:tenant_id 查询参数可选化,JWT 为权威来源。

- tenant_id 未传(None)→ 采信 token(current_user.tenant_id),放行
- tenant_id 显式传入且不匹配 → 403
"""

import pytest
from fastapi import HTTPException

from app.db.models import User
from app.security.auth import ensure_current_user_tenant


def make_user(tenant_id: str = "t1") -> User:
    return User(id="u1", tenant_id=tenant_id, username="u", password_hash="x")


def test_missing_tenant_param_falls_back_to_token():
    user = make_user(tenant_id="t1")
    # 新语义:tenant_id=None 表示"未传",直接采信 token
    assert ensure_current_user_tenant(None, user) is None


def test_matching_tenant_passes():
    user = make_user(tenant_id="t1")
    assert ensure_current_user_tenant("t1", user) is None


def test_mismatched_tenant_still_403():
    user = make_user(tenant_id="t1")
    with pytest.raises(HTTPException) as exc:
        ensure_current_user_tenant("t2", user)
    assert exc.value.status_code == 403


def test_non_user_still_401():
    with pytest.raises(HTTPException) as exc:
        ensure_current_user_tenant(None, "not-a-user")  # type: ignore[arg-type]
    assert exc.value.status_code == 401
