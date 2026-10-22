from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import Response as RawResponse
from sqlmodel import Session

from app.agents.branching import visible_knowledge_base_versions
from app.db import get_session
from app.db.models import AgentProfile, APIAuditLog, KnowledgeBase, utc_now
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    concept_rows,
    content_hash,
    select_concept,
    select_package,
)
from app.knowledge.okf import export_okf_bundle
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.digital_employees import distribution_agents
from app.public_api.errors import PublicAPIError
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["knowledge-sync"])


def _agent_or_404(db: Session, principal: PublicPrincipal, agent_id: str) -> AgentProfile:
    agent = next(
        (row for row in distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    return agent


def _package_or_404(
    db: Session, principal: PublicPrincipal, agent: AgentProfile, knowledge_base_id: str
) -> dict:
    package = select_package(principal, agent, db, knowledge_base_id)
    if package is None:
        raise PublicAPIError(
            404,
            "KNOWLEDGE_PACKAGE_NOT_FOUND",
            "This knowledge base is not synced to the requested employee.",
        )
    return package


def _audit(
    db: Session,
    principal: PublicPrincipal,
    request: Request,
    *,
    resource_type: str,
    resource_id: str,
) -> None:
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="GET",
            path=request.url.path,
            resource_type=resource_type,
            resource_id=resource_id,
            status_code=200,
            duration_ms=0.0,
        )
    )
    db.commit()


@router.get("/{agent_id}/knowledge-packages", response_model=dict)
def list_knowledge_packages(
    agent_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response | dict:
    agent = _agent_or_404(db, principal, agent_id)
    core = build_knowledge_package_list(principal, agent, db)
    # Same rule as the employee endpoint: the ETag must not include fetchedAt.
    etag = etag_for(core)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {**core, "fetchedAt": utc_now().isoformat() + "Z"}


@router.get("/{agent_id}/knowledge-packages/{knowledge_base_id}:export-bundle")
def export_knowledge_bundle(
    agent_id: str,
    knowledge_base_id: str,
    request: Request,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> RawResponse:
    agent = _agent_or_404(db, principal, agent_id)
    package = _package_or_404(db, principal, agent, knowledge_base_id)
    kb = db.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.tenant_id != principal.tenant_id:
        raise PublicAPIError(
            404, "KNOWLEDGE_PACKAGE_NOT_FOUND", "This knowledge base is not available."
        )
    version = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)[knowledge_base_id]
    data = export_okf_bundle(
        kb, version.id, concept_rows(db, principal.tenant_id, knowledge_base_id, version.id)
    )
    _audit(
        db,
        principal,
        request,
        resource_type="knowledge_base",
        resource_id=knowledge_base_id,
    )
    filename = f"{kb.name or knowledge_base_id}-okf-{package['version']}.zip"
    return RawResponse(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{knowledge_base_id}.zip"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{agent_id}/knowledge-packages/{knowledge_base_id}/concepts/{concept_path:path}")
def download_concept(
    agent_id: str,
    knowledge_base_id: str,
    concept_path: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response:
    """Serve one concept body.

    Concept ids contain slashes (``sources/<doc>/sections/sec-1``), so the path
    converter captures them verbatim and the ``:download`` suffix is stripped
    here rather than by the router.
    """
    if not concept_path.endswith(":download"):
        raise PublicAPIError(404, "CONCEPT_NOT_FOUND", "Concept downloads must end with :download.")
    concept_id = concept_path[: -len(":download")]
    agent = _agent_or_404(db, principal, agent_id)
    _package_or_404(db, principal, agent, knowledge_base_id)
    row = select_concept(principal, agent, db, knowledge_base_id, concept_id)
    if row is None:
        raise PublicAPIError(
            404, "CONCEPT_NOT_FOUND", "This concept is not part of the synced package."
        )
    etag = f'"{content_hash(row.content_md)}"'
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    _audit(
        db,
        principal,
        request,
        resource_type="knowledge_concept",
        resource_id=row.id,
    )
    return RawResponse(
        content=row.content_md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"ETag": etag},
    )
