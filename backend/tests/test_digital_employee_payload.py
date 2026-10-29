from __future__ import annotations

import pytest
from deepchat_contract import validate_digital_employee
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    AgentSkillBranch,
    GeneralSkill,
    Tool,
    User,
)
from app.db.seed import seed_demo_data
from app.digital_employees.payload import build_employee, build_employee_list
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


def _agent(db: Session, name: str) -> AgentProfile:
    """按名称取出非汇总员工，取不到时断言失败。"""
    row = db.exec(
        select(AgentProfile).where(
            AgentProfile.name == name,
            AgentProfile.is_overall == False,
        )
    ).first()
    assert row is not None, name
    return row


def _gallery_agents(db: Session) -> list[AgentProfile]:
    """返回全部非汇总员工，即对外展示的员工画廊。"""
    return list(
        db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all()
    )


def test_seeded_employees_satisfy_the_contract(seeded):
    """验证 10 名种子员工的载荷都能通过 deepchat_contract 校验，且不下发 tenant_id。"""
    db, principal = seeded
    payloads = [build_employee(principal, agent, db) for agent in _gallery_agents(db)]
    assert len(payloads) == 10
    for payload in payloads:
        validate_digital_employee(payload)
        assert "tenant_id" not in payload


def test_payload_maps_profile_fields_and_capabilities(seeded):
    """验证画像字段映射：角色、启用状态、本地执行模式与系统提示，未配置的 greeting/avatar 为 None，标签去重且保序。"""
    db, principal = seeded
    payload = build_employee(principal, _agent(db, "财务"), db)
    assert payload["name"] == "财务"
    assert payload["role"] == "报销管家"  # metadata.role_name
    assert payload["enabled"] is True
    assert payload["executionMode"] == "local"  # 默认本地执行
    assert payload["systemPrompt"].strip()
    assert payload["greeting"] is None  # 未配置就不编造
    assert payload["avatar"] is None  # 只有 avatar_url 才下发，preset 属于管理端渲染细节
    # 财务种子没有 expertise_tags，标签去重与保序要看有标签的员工
    sales = build_employee(principal, _agent(db, "销售"), db)
    assert sales["capabilities"] == ["需求澄清", "商机推进", "客户沟通", "问答", "流程引导", "目标清晰", "审慎承诺"]


def test_tools_are_namespaced_and_never_leak_credentials(seeded):
    """验证工具名统一加 staffdeck_ 前缀，且请求头、令牌与 URL 等凭证信息不会出现在载荷中。"""
    db, principal = seeded
    db.add(
        Tool(
            id="tool_secret",
            tenant_id="tenant_demo",
            name="crm.lookup",
            method="GET",
            url="https://crm.example.com/customer",
            headers_json={"Authorization": "Bearer super-secret"},
            auth_json={"token": "super-secret"},
        )
    )
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=_agent(db, "财务").id,
            resource_type="tool",
            resource_id="tool_secret",
            status="active",
            # A freshly bound private tool is visible to its owner only; without
            # this marker the open-gallery check would hide it from everyone.
            metadata_json={"scope": "agent_private", "visibility": "agent_private"},
        )
    )
    db.commit()
    payload = build_employee(principal, _agent(db, "财务"), db)
    assert "staffdeck_crm_lookup_f398d2a2" in payload["tools"]
    assert all(item.startswith("staffdeck_") for item in payload["tools"])
    dumped = repr(payload)
    assert "super-secret" not in dumped
    assert "crm.example.com" not in dumped  # 连 URL 都不下发：凭证不出服务端


def test_skill_ids_come_from_bound_general_skills_with_download_links(seeded):
    """验证 skillIds 只含已发布的通用技能，且 skills 下载链接以 :export-skill 结尾。"""
    db, principal = seeded
    payload = build_employee(principal, _agent(db, "人事"), db)
    published = {
        row.slug
        for row in db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == "tenant_demo",
                GeneralSkill.status == "published",
            )
        ).all()
    }
    assert payload["skillIds"]
    assert set(payload["skillIds"]).issubset(published)
    assert [item["slug"] for item in payload["skills"]] == payload["skillIds"]
    for item in payload["skills"]:
        assert item["downloadUrl"].endswith(
            f"/digital-employees/{payload['id']}:export-skill?slug={item['slug']}"
        )


def test_knowledge_sync_is_opt_out(seeded):
    """验证知识库同步默认开启，只有版本 metadata 中 deepchat_sync_enabled 显式为 False 时才从 knowledgeBaseIds 排除。"""
    db, principal = seeded
    finance = _agent(db, "财务")
    visible = sorted(visible_knowledge_base_versions(db, "tenant_demo", finance.id))
    assert visible, "seed data must attach knowledge bases to the finance employee"
    payload = build_employee(principal, finance, db)
    assert payload["knowledgeBaseIds"] == visible

    # 开关在版本行上：只有显式 False 才排除，未标注的默认同步
    for version in visible_knowledge_base_versions(db, "tenant_demo", finance.id).values():
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": False}
        db.add(version)
    db.commit()

    payload = build_employee(principal, finance, db)
    assert payload["knowledgeBaseIds"] == []
    assert payload["knowledgePackagesUrl"].endswith(
        f"/digital-employees/{finance.id}/knowledge-packages"
    )


def test_remote_mode_stops_shipping_sops_but_hybrid_routes_per_sop(seeded):
    """验证 remote 模式不再下发 SOP 但仍下发技能包，hybrid 下只有显式 execution_hint=remote 的 SOP 被外派。"""
    db, principal = seeded
    finance = _agent(db, "财务")
    bindings = list(
        db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.agent_id == finance.id,
                AgentResourceBinding.resource_type == "skill",
            )
        ).all()
    )
    assert len(bindings) == 3
    total = len(build_employee(principal, finance, db)["sops"])

    finance.metadata_json = {**(finance.metadata_json or {}), "execution_mode": "remote"}
    db.add(finance)
    db.commit()
    remote_payload = build_employee(principal, _agent(db, "财务"), db)
    assert remote_payload["executionMode"] == "remote"
    assert remote_payload["sops"] == []
    assert remote_payload["skillIds"]  # 技能包仍下发：远端执行同样要装技能

    # hybrid 下未标注的 SOP 默认留在本地，显式标 remote 的才外派
    finance.metadata_json = {**(finance.metadata_json or {}), "execution_mode": "hybrid"}
    remote_binding = bindings[0]
    remote_binding.metadata_json = {
        **(remote_binding.metadata_json or {}),
        "execution_hint": "remote",
    }
    db.add(finance)
    db.add(remote_binding)
    db.commit()
    hybrid_payload = build_employee(principal, _agent(db, "财务"), db)
    assert hybrid_payload["executionMode"] == "hybrid"
    assert len(hybrid_payload["sops"]) == total - 1


def test_oversized_sop_is_clamped_with_a_warning(seeded):
    """验证超长 SOP 会被截断并产生含“截断”的 warning，载荷仍能通过契约校验。"""
    db, principal = seeded
    finance = _agent(db, "财务")
    branch = db.exec(
        select(AgentSkillBranch).where(AgentSkillBranch.agent_id == finance.id)
    ).first()
    card = dict(branch.content_json)
    card["nodes"] = [
        {"node_id": "only", "type": "respond", "name": "长节点", "instruction": "字" * 30000}
    ]
    card["edges"] = []
    card["start_node_id"] = "only"
    card["terminal_node_ids"] = ["only"]
    branch.content_json = card
    db.add(branch)
    db.commit()

    payload = build_employee(principal, _agent(db, "财务"), db)
    validate_digital_employee(payload)
    assert payload["warnings"]
    assert any("截断" in warning for warning in payload["warnings"])


def test_config_hash_changes_only_when_content_changes(seeded):
    """验证内容不变时 configHash 保持稳定，改动员工描述后 configHash 随之变化。"""
    db, principal = seeded
    finance = _agent(db, "财务")
    first = build_employee(principal, finance, db)
    again = build_employee(principal, _agent(db, "财务"), db)
    assert first["configHash"] == again["configHash"]

    finance.description = "改过的描述"
    db.add(finance)
    db.commit()
    changed = build_employee(principal, _agent(db, "财务"), db)
    assert changed["configHash"] != first["configHash"]


def test_list_payload_is_deterministic_and_versioned(seeded):
    """验证列表按名称排序、输入顺序不影响结果，且 configVersion 带 v1: 前缀。"""
    db, principal = seeded
    agents = _gallery_agents(db)
    first = build_employee_list(principal, agents, db)
    second = build_employee_list(principal, list(reversed(agents)), db)
    names = [item["name"] for item in first["employees"]]
    assert names == sorted(names)
    assert first == second  # 输入顺序不影响输出
    assert first["configVersion"].startswith("v1:")
