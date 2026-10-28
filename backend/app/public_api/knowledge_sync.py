from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_versions
from app.db import get_session
from app.db.models import (
    AgentProfile,
    APIAuditLog,
    KnowledgeBase,
    KnowledgeBucket,
    KnowledgeChunk,
    KnowledgeDocument,
    utc_now,
)
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


def _version_stats(
    db: Session, tenant_id: str, version_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Per-version document/bucket/chunk counts, the console page's yardstick."""
    stats: dict[str, dict[str, int]] = {}
    if not version_ids:
        return stats
    for model, key in (
        (KnowledgeDocument, "document_count"),
        (KnowledgeBucket, "bucket_count"),
        (KnowledgeChunk, "chunk_count"),
    ):
        rows = db.exec(
            select(model.knowledge_base_id, func.count(model.id))
            .where(
                model.tenant_id == tenant_id,
                model.knowledge_base_version_id.in_(version_ids),
            )
            .group_by(model.knowledge_base_id)
        ).all()
        for kb_id, count in rows:
            stats.setdefault(kb_id, {})[key] = int(count or 0)
    return stats


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


@router.get("/{agent_id}/knowledge-bases", response_model=dict)
def list_employee_knowledge_bases(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """Console-style catalog of every knowledge base an employee can use.

    Deepchat's marketplace page mirrors the enterprise knowledge console, so
    this listing is not gated by the sync opt-out flag and carries no concept
    bodies — just the same document/chunk counts the console shows.
    """
    agent = _agent_or_404(db, principal, agent_id)
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    stats = _version_stats(
        db, principal.tenant_id, [version.id for version in versions.values()]
    )
    knowledge_bases = []
    for kb_id, version in sorted(versions.items()):
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.tenant_id != principal.tenant_id:
            continue
        counters = stats.get(kb_id, {})
        knowledge_bases.append(
            {
                "knowledgeBaseId": kb_id,
                "name": kb.name,
                "description": kb.description or "",
                "version": version.version,
                "documentCount": counters.get("document_count", 0),
                "chunkCount": counters.get("chunk_count", 0),
            }
        )
    return {"knowledgeBases": knowledge_bases}


@router.get(
    "/{agent_id}/knowledge-packages/{knowledge_base_id}/concepts",
    response_model=dict,
)
def list_concept_metadata(
    agent_id: str,
    knowledge_base_id: str,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """Concept metadata for Deepchat's detail sheet and knowledge graph.

    The console builds its graph client-side from ``links``; this endpoint
    hands the same fields to public API consumers. Bodies stay behind
    ``:download`` — no content_md here.
    """
    agent = _agent_or_404(db, principal, agent_id)
    package = _package_or_404(db, principal, agent, knowledge_base_id)
    version = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)[
        knowledge_base_id
    ]
    concepts = []
    for row in concept_rows(db, principal.tenant_id, knowledge_base_id, version.id):
        # The console graph drops citation-section links before drawing; doing
        # the same here keeps client graphs free of reference-list noise.
        citation_keys = {
            (str(link.get("label", "")).strip(), str(link.get("target", "")).strip())
            for link in row.citations_json or []
        }
        links = [
            link
            for link in row.links_json or []
            if (str(link.get("label", "")).strip(), str(link.get("target", "")).strip())
            not in citation_keys
        ]
        concepts.append(
            {
                "conceptId": row.concept_id,
                "type": row.concept_type,
                "title": row.title,
                "description": row.description or "",
                "links": links,
            }
        )
    return {
        "knowledgeBaseId": knowledge_base_id,
        "version": package["version"],
        "conceptCount": len(concepts),
        "concepts": concepts,
    }


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
