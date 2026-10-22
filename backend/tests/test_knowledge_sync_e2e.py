from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentKnowledgeBranch, AgentProfile, KnowledgeConcept, User
from app.db.seed import seed_demo_data
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    content_hash,
    select_concept,
)
from app.public_api.auth import PublicPrincipal


def _seeded() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _principal(db: Session) -> PublicPrincipal:
    admin = db.exec(select(User).where(User.username == "admin")).first()
    assert admin is not None
    return PublicPrincipal(tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"}))


def _enable_all_sync(db: Session, principal: PublicPrincipal) -> None:
    """Opt every branch-resolved version in: the flag lives on the version row."""
    for branch in db.exec(select(AgentKnowledgeBranch)).all():
        versions = visible_knowledge_base_versions(db, principal.tenant_id, branch.agent_id)
        version = versions.get(branch.knowledge_base_id)
        if version is None:
            continue
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
        db.add(version)
    db.commit()


def test_every_synced_concept_is_addressable_and_hashed():
    session = _seeded()
    try:
        principal = _principal(session)
        _enable_all_sync(session, principal)

        seen = 0
        for agent in session.exec(select(AgentProfile)).all():
            if agent.is_overall:
                continue
            payload = build_knowledge_package_list(principal, agent, session)
            assert payload["configVersion"].startswith("kv1:")
            for package in payload["packages"]:
                for concept in package["concepts"]:
                    seen += 1
                    row = select_concept(
                        principal,
                        agent,
                        session,
                        package["knowledgeBaseId"],
                        concept["conceptId"],
                    )
                    assert row is not None, concept["conceptId"]
                    assert concept["hash"] == content_hash(row.content_md)
                    assert concept["size"] == len(row.content_md.encode("utf-8"))
        assert seen > 0, "seed data must sync at least one knowledge package"
    finally:
        session.close()


def test_config_version_changes_when_a_concept_changes():
    session = _seeded()
    try:
        principal = _principal(session)
        branch = session.exec(select(AgentKnowledgeBranch)).first()
        assert branch is not None
        versions = visible_knowledge_base_versions(session, principal.tenant_id, branch.agent_id)
        version = versions[branch.knowledge_base_id]
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
        session.add(version)
        session.commit()

        agent = session.get(AgentProfile, branch.agent_id)
        assert agent is not None
        before = build_knowledge_package_list(principal, agent, session)

        row = session.exec(
            select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version.id)
        ).first()
        assert row is not None
        row.content_md = row.content_md + "\n\n新增一段。"
        session.add(row)
        session.commit()

        after = build_knowledge_package_list(principal, agent, session)
        assert before["configVersion"] != after["configVersion"]
        assert before["packages"][0]["configHash"] != after["packages"][0]["configHash"]
    finally:
        session.close()
