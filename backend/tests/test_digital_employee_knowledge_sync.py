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
    """构建内存 SQLite 库并写入演示种子数据，产出会话与全权限 PublicPrincipal。"""
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
    """返回种子数据中第一个绑定了知识库的普通员工，找不到时断言失败。"""
    for agent in db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all():
        if db.exec(
            select(AgentKnowledgeBranch).where(AgentKnowledgeBranch.agent_id == agent.id)
        ).first():
            return agent
    raise AssertionError("seed data must attach a knowledge base to at least one employee")


def _enable_sync(db: Session, tenant_id: str, agent_id: str) -> str:
    """把某个可见版本显式标为 deepchat_sync_enabled=True，并返回其知识库 id。"""
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    kb_id, version = next(iter(versions.items()))
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
    db.add(version)
    db.commit()
    return kb_id


def test_packages_are_listed_without_the_sync_flag(seeded):
    """验证未做任何开关标注时知识包默认全部下发，且每个包都带 knowledgeBaseId。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    packages = build_knowledge_packages(principal, agent, db)
    assert packages
    assert all(package["knowledgeBaseId"] for package in packages)


def test_enabled_knowledge_base_is_listed_with_hashed_concepts(seeded):
    """验证显式开启同步的知识库会出现在包列表中，并带版本、bundleUrl 与逐概念的哈希清单。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)

    packages = build_knowledge_packages(principal, agent, db)
    package = next(row for row in packages if row["knowledgeBaseId"] == kb_id)
    assert package["version"]
    assert package["bundleUrl"].endswith(f"{kb_id}:export-bundle")
    assert package["conceptCount"] == len(package["concepts"]) > 0
    for concept in package["concepts"]:
        assert concept["conceptId"]
        assert concept["hash"]
        assert concept["size"] > 0
        assert concept["type"]


def test_package_hash_is_stable_across_calls(seeded):
    """验证同一份数据连续两次构建得到完全相同的知识包，哈希与排序均稳定。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    assert build_knowledge_packages(principal, agent, db) == build_knowledge_packages(
        principal, agent, db
    )


def test_list_wraps_packages_with_a_config_version(seeded):
    """验证列表结果在 packages 之外附带 kv1: 前缀的 configVersion，且包列表非空。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    payload = build_knowledge_package_list(principal, agent, db)
    assert payload["configVersion"].startswith("kv1:")
    assert payload["packages"]


def test_select_package_refuses_an_explicit_opt_out(seeded):
    """验证版本 metadata 中 deepchat_sync_enabled 显式为 False 时，select_package 返回 None。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id, version = next(
        iter(visible_knowledge_base_versions(db, principal.tenant_id, agent.id).items())
    )
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": False}
    db.add(version)
    db.commit()
    assert select_package(principal, agent, db, kb_id) is None


def test_select_concept_rejects_a_concept_from_another_base(seeded):
    """验证本库存在的概念能取回，而本库不存在的 conceptId 返回 None。"""
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)
    packages = build_knowledge_packages(principal, agent, db)
    package = next(row for row in packages if row["knowledgeBaseId"] == kb_id)
    concept_id = package["concepts"][0]["conceptId"]
    assert select_concept(principal, agent, db, kb_id, concept_id) is not None
    assert select_concept(principal, agent, db, kb_id, "sources/does-not-exist") is None


def test_content_hash_is_stable_hex():
    """验证 content_hash 输出 16 位十六进制摘要，同内容一致、不同内容不同。"""
    digest = content_hash("# 标题\n\n正文")
    assert len(digest) == 16
    assert all(char in "0123456789abcdef" for char in digest)
    assert content_hash("same") == content_hash("same")
    assert content_hash("a") != content_hash("b")
