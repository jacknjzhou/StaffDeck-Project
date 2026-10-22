from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentKnowledgeBranch, AgentProfile, User
from app.db.seed import seed_demo_data
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    build_knowledge_packages,
    content_hash,
    select_concept,
    select_package,
)
from app.public_api.auth import PublicPrincipal


@pytest.fixture()
def seeded():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        principal = PublicPrincipal(
            tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"})
        )
        yield db, principal


def _agent_with_kb(db: Session) -> AgentProfile:
    for agent in db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all():
        if db.exec(
            select(AgentKnowledgeBranch).where(AgentKnowledgeBranch.agent_id == agent.id)
        ).first():
            return agent
    raise AssertionError("seed data must attach a knowledge base to at least one employee")


def _enable_sync(db: Session, tenant_id: str, agent_id: str) -> str:
    """Flip the opt-in flag on whatever version this agent actually resolves to."""
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    kb_id, version = next(iter(versions.items()))
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
    db.add(version)
    db.commit()
    return kb_id


def test_no_packages_until_the_sync_flag_is_set(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    assert build_knowledge_packages(principal, agent, db) == []


def test_enabled_knowledge_base_is_listed_with_hashed_concepts(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)

    packages = build_knowledge_packages(principal, agent, db)
    assert len(packages) == 1
    package = packages[0]
    assert package["knowledgeBaseId"] == kb_id
    assert package["version"]
    assert package["bundleUrl"].endswith(f"{kb_id}:export-bundle")
    assert package["conceptCount"] == len(package["concepts"]) > 0
    for concept in package["concepts"]:
        assert concept["conceptId"]
        assert concept["hash"]
        assert concept["size"] > 0
        assert concept["type"]


def test_package_hash_is_stable_across_calls(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    assert build_knowledge_packages(principal, agent, db) == build_knowledge_packages(
        principal, agent, db
    )


def test_list_wraps_packages_with_a_config_version(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    payload = build_knowledge_package_list(principal, agent, db)
    assert payload["configVersion"].startswith("kv1:")
    assert len(payload["packages"]) == 1


def test_select_package_refuses_an_unflagged_base(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = next(iter(visible_knowledge_base_versions(db, principal.tenant_id, agent.id)))
    assert select_package(principal, agent, db, kb_id) is None


def test_select_concept_rejects_a_concept_from_another_base(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)
    package = build_knowledge_packages(principal, agent, db)[0]
    concept_id = package["concepts"][0]["conceptId"]
    assert select_concept(principal, agent, db, kb_id, concept_id) is not None
    assert select_concept(principal, agent, db, kb_id, "sources/does-not-exist") is None


def test_content_hash_is_stable_hex():
    digest = content_hash("# 标题\n\n正文")
    assert len(digest) == 16
    assert all(char in "0123456789abcdef" for char in digest)
    assert content_hash("same") == content_hash("same")
    assert content_hash("a") != content_hash("b")
