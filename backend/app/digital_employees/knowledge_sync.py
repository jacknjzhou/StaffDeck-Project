from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_versions
from app.config import get_settings
from app.db.models import AgentProfile, KnowledgeBase, KnowledgeConcept
from app.knowledge.concepts import ensure_okf_concepts_for_version

if TYPE_CHECKING:
    # Imported for typing only: app.public_api.__init__ eagerly imports the whole
    # Open API app, so a runtime import here would close an import cycle.
    from app.public_api.auth import PublicPrincipal

# Same key payload.py uses: one opt-out switch, never two.
SYNC_FLAG = "deepchat_sync_enabled"


def content_hash(value: str) -> str:
    """Short, stable digest of a concept body: deepchat compares these to skip work."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_knowledge_packages(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> list[dict[str, Any]]:
    """Every knowledge base this employee can use, unless explicitly opted out."""
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    packages: list[dict[str, Any]] = []
    for kb_id, version in sorted(versions.items()):
        metadata = dict(getattr(version, "metadata_json", None) or {})
        if metadata.get(SYNC_FLAG) is False:
            continue
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.tenant_id != principal.tenant_id:
            continue
        ensure_okf_concepts_for_version(db, principal.tenant_id, kb_id, version.id)
        entries = [
            {
                "conceptId": row.concept_id,
                "type": row.concept_type,
                "title": row.title,
                "hash": content_hash(row.content_md),
                "size": len(row.content_md.encode("utf-8")),
            }
            for row in concept_rows(db, principal.tenant_id, kb_id, version.id)
        ]
        packages.append(
            {
                "knowledgeBaseId": kb_id,
                "name": kb.name,
                "version": version.version,
                "configHash": _package_hash(version.version, entries),
                "bundleUrl": _bundle_url(agent.id, kb_id),
                "concepts": entries,
                "conceptCount": len(entries),
            }
        )
    return packages


def build_knowledge_package_list(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> dict[str, Any]:
    packages = build_knowledge_packages(principal, agent, db)
    seed = json.dumps(
        sorted((item["knowledgeBaseId"], item["configHash"]) for item in packages),
        separators=(",", ":"),
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return {"packages": packages, "configVersion": f"kv1:{digest}"}


def select_package(
    principal: PublicPrincipal,
    agent: AgentProfile,
    db: Session,
    knowledge_base_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            package
            for package in build_knowledge_packages(principal, agent, db)
            if package["knowledgeBaseId"] == knowledge_base_id
        ),
        None,
    )


def select_concept(
    principal: PublicPrincipal,
    agent: AgentProfile,
    db: Session,
    knowledge_base_id: str,
    concept_id: str,
) -> KnowledgeConcept | None:
    if select_package(principal, agent, db, knowledge_base_id) is None:
        return None
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    version = versions.get(knowledge_base_id)
    if version is None:
        return None
    return db.exec(
        select(KnowledgeConcept).where(
            KnowledgeConcept.tenant_id == principal.tenant_id,
            KnowledgeConcept.knowledge_base_id == knowledge_base_id,
            KnowledgeConcept.knowledge_base_version_id == version.id,
            KnowledgeConcept.concept_id == concept_id,
            KnowledgeConcept.status == "active",
        )
    ).first()


def concept_rows(
    db: Session, tenant_id: str, knowledge_base_id: str, version_id: str
) -> list[KnowledgeConcept]:
    return list(
        db.exec(
            select(KnowledgeConcept)
            .where(
                KnowledgeConcept.tenant_id == tenant_id,
                KnowledgeConcept.knowledge_base_id == knowledge_base_id,
                KnowledgeConcept.knowledge_base_version_id == version_id,
                KnowledgeConcept.status == "active",
            )
            .order_by(KnowledgeConcept.concept_id)
        ).all()
    )


def _package_hash(version: str, entries: list[dict[str, Any]]) -> str:
    seed = json.dumps(
        {"version": version, "concepts": [(item["conceptId"], item["hash"]) for item in entries]},
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _bundle_url(agent_id: str, knowledge_base_id: str) -> str:
    base = get_settings().normalized_tool_base_url
    return (
        f"{base}/api/v1/digital-employees/{agent_id}"
        f"/knowledge-packages/{knowledge_base_id}:export-bundle"
    )
