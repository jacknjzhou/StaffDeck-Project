from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import AgentProfile, utc_now
from app.digital_employees.payload import build_employee, build_employee_list
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.gallery import visible_gallery_agents
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["digital-employees"])


def _distribution_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfile]:
    """The gallery set, resolved back to ORM rows for payload building."""
    allowed = {row.id for row in visible_gallery_agents(db, principal)}
    if not allowed:
        return []
    rows = db.exec(
        select(AgentProfile).where(
            AgentProfile.tenant_id == principal.tenant_id,
            AgentProfile.id.in_(allowed),  # type: ignore[attr-defined]
        )
    ).all()
    return [row for row in rows if row.status == "active"]


@router.get("", response_model=dict)
def list_digital_employees(
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response | dict:
    core = build_employee_list(principal, _distribution_agents(db, principal), db)
    # The ETag must not include fetchedAt: a per-request timestamp would make
    # every conditional request miss and defeat incremental fetching entirely.
    etag = etag_for(core)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {**core, "fetchedAt": _now_iso()}


@router.get("/{agent_id}", response_model=dict)
def get_digital_employee(
    agent_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response | dict:
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    employee = build_employee(principal, agent, db)
    etag = etag_for(employee)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {"employee": employee}


def _now_iso() -> str:
    return utc_now().isoformat() + "Z"
