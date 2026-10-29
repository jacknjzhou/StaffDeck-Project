from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentKnowledgeBranch, KnowledgeConcept
from app.db.seed import seed_demo_data
from app.knowledge.concepts import ensure_okf_concepts_for_version


def _session() -> Session:
    """创建已灌入演示数据的内存数据库会话，供概念生成测试使用。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _branch(session: Session) -> tuple[AgentKnowledgeBranch, str]:
    """取一个已绑定知识库的分支及其可见版本 ID，供后续断言使用。"""
    branch = session.exec(select(AgentKnowledgeBranch)).first()
    assert branch is not None
    versions = visible_knowledge_base_versions(session, branch.tenant_id, branch.agent_id)
    return branch, versions[branch.knowledge_base_id].id


def test_is_idempotent_when_concepts_already_exist():
    """验证概念已存在时重复生成返回 0，保持幂等。"""
    session = _session()
    branch, version_id = _branch(session)
    created = ensure_okf_concepts_for_version(
        session, branch.tenant_id, branch.knowledge_base_id, version_id
    )
    assert created == 0


def test_recreates_concepts_for_a_version_that_has_none():
    """验证版本下概念被清空后能重新生成，且落库数量大于零。"""
    session = _session()
    branch, version_id = _branch(session)
    for row in session.exec(
        select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version_id)
    ).all():
        session.delete(row)
    session.commit()

    created = ensure_okf_concepts_for_version(
        session, branch.tenant_id, branch.knowledge_base_id, version_id
    )
    assert created > 0
    assert session.exec(
        select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version_id)
    ).all()


def test_returns_zero_for_an_unknown_knowledge_base():
    """验证知识库或版本不存在时返回 0，而不是抛出异常。"""
    session = _session()
    branch, _version_id = _branch(session)
    created = ensure_okf_concepts_for_version(
        session, branch.tenant_id, "kb_missing", "kbver_missing"
    )
    assert created == 0
