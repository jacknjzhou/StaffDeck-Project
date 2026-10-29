from fastapi import HTTPException
from sqlmodel import Session

from app.db.models import Tenant


def ensure_tenant(session: Session, tenant_id: str) -> Tenant:
    """按 ID 读取租户并返回，作为后续操作的前置校验。

    租户不存在时抛出 404 的 HTTPException，不写库。
    """
    tenant = session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")
    return tenant

