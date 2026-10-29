from __future__ import annotations

import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import seed_demo_data
from app.digital_employees.tool_namespace import namespaced_tool_name
from app.mcp_server.catalog import (
    KNOWLEDGE_SEARCH_TOOL,
    build_tool_catalog,
    resolve_tool_name,
)
from app.mcp_server.protocol import INVALID_PARAMS, RpcError

FINANCE_AGENT = "agent_f2828efc2a2a476d"


@pytest.fixture()
def session():
    """建内存 SQLite 库、写入演示种子数据，并为每个用例提供 Session。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        yield db


def _tenant(db) -> str:
    """返回 admin 用户所属的租户 id。"""
    return db.exec(select(User).where(User.username == "admin")).first().tenant_id


def test_catalog_only_contains_namespaced_tools(session):
    """验证财务员工工具目录非空，且所有工具名都以 staffdeck_ 命名空间前缀开头。"""
    catalog = build_tool_catalog(session, _tenant(session), FINANCE_AGENT)
    assert catalog, "the finance employee ships with tools in the seed data"
    assert all(item["name"].startswith("staffdeck_") for item in catalog)


def test_catalog_never_leaks_credentials(session):
    """验证目录中不含 headers_json/auth_json/bearer 等凭据痕迹，每个工具只暴露三个协议字段。"""
    catalog = build_tool_catalog(session, _tenant(session), FINANCE_AGENT)
    blob = json.dumps(catalog, ensure_ascii=False).lower()
    for forbidden in ("headers_json", "auth_json", "config_json", "bearer ", "http://localhost"):
        assert forbidden not in blob
    for item in catalog:
        # Only the three protocol keys may appear on a tool descriptor.
        assert set(item) <= {"name", "description", "inputSchema"}


def test_catalog_names_round_trip_through_the_namespace(session):
    """验证命名空间名可反解出原始工具名，缺前缀或不在目录中的名字都会抛 INVALID_PARAMS 的 RpcError。"""
    tenant_id = _tenant(session)
    catalog = build_tool_catalog(session, tenant_id, FINANCE_AGENT)
    names = [item["name"] for item in catalog]
    # Dots are illegal in a model function name, so the digest carries the original.
    target = namespaced_tool_name("expense.submit")
    assert target in names
    assert resolve_tool_name(session, tenant_id, FINANCE_AGENT, target) == "expense.submit"
    with pytest.raises(RpcError) as bad_prefix:
        resolve_tool_name(session, tenant_id, FINANCE_AGENT, "expense.submit")
    assert bad_prefix.value.code == INVALID_PARAMS
    with pytest.raises(RpcError):
        resolve_tool_name(
            session, tenant_id, FINANCE_AGENT, namespaced_tool_name("not_in_the_catalog")
        )


def test_catalog_is_scoped_to_the_employee(session):
    """验证工具目录按员工收窄：未绑定通用工具的销售员工最多只有知识检索工具。"""
    tenant_id = _tenant(session)
    # The sales preset binds no generic tools; knowledge search is the only
    # tool it can carry.
    names = {
        item["name"] for item in build_tool_catalog(session, tenant_id, "agent_preset_sales_001")
    }
    assert names <= {KNOWLEDGE_SEARCH_TOOL}


def test_knowledge_search_is_hidden_only_by_an_explicit_opt_out(session):
    """验证知识检索工具默认下发，只有所有版本都显式标 deepchat_sync_enabled=False 时才隐藏。"""
    from app.agents.branching import visible_knowledge_base_versions

    tenant_id = _tenant(session)
    versions = visible_knowledge_base_versions(session, tenant_id, FINANCE_AGENT)
    assert versions, "the seed data must attach a knowledge base to the finance employee"
    names = {item["name"] for item in build_tool_catalog(session, tenant_id, FINANCE_AGENT)}
    assert KNOWLEDGE_SEARCH_TOOL in names

    for version in versions.values():
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": False}
        session.add(version)
    session.commit()

    names = {item["name"] for item in build_tool_catalog(session, tenant_id, FINANCE_AGENT)}
    assert KNOWLEDGE_SEARCH_TOOL not in names
