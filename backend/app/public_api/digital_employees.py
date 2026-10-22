from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import Response as RawResponse
from sqlmodel import Session, select

from app.agents.branching import visible_general_skill_rows
from app.db import get_session
from app.db.models import AgentProfile, APIAuditLog, utc_now
from app.digital_employees.payload import build_employee, build_employee_list
from app.digital_employees.skill_package import build_skill_zip, skill_zip_filename
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.gallery import visible_gallery_agents
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["digital-employees"])


def distribution_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfile]:
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


# Kept as an alias so the distribution endpoints and their tests keep working.
_distribution_agents = distribution_agents


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


@router.get("/{agent_id}:export-skill")
def export_employee_skill(
    agent_id: str,
    request: Request,
    slug: str = Query(..., min_length=1, max_length=200),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> RawResponse:
    """Download one general skill as a SKILL.md package for deepchat's skill dir.

    The slug is a query parameter rather than a path segment so the route keeps
    the spec's `/{id}:export-skill` shape while still addressing one of the
    employee's many skills.
    """
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    row = next(
        (
            item
            for item in visible_general_skill_rows(db, principal.tenant_id, agent.id)
            if item.slug == slug
        ),
        None,
    )
    if row is None:
        raise PublicAPIError(
            404, "SKILL_NOT_FOUND", "This skill is not distributed to the requested employee."
        )
    data = build_skill_zip(row)
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="GET",
            path=request.url.path,
            resource_type="general_skill",
            resource_id=row.id,
            status_code=200,
            duration_ms=0.0,
        )
    )
    db.commit()
    return RawResponse(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_zip_filename(slug)}"',
        },
    )


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
