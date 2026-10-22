from __future__ import annotations

import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from app.db.seed import seed_demo_data
from app.mcp_server.catalog import KNOWLEDGE_SEARCH_TOOL
from app.mcp_server.invoker import call_tool, extract_agent_id

FINANCE_AGENT = "agent_f2828efc2a2a476d"
SALES_AGENT = "agent_preset_sales_001"


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        yield db


def _tenant(db) -> str:
    return db.exec(select(User).where(User.username == "admin")).first().tenant_id


def _enable_knowledge_sync(db, tenant_id: str, agent_id: str) -> None:
    """The search tool only exists once a base is opted into deepchat sync."""
    from app.agents.branching import visible_knowledge_base_versions

    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    assert versions, "the seed data must attach a knowledge base to the finance employee"
    _, version = next(iter(versions.items()))
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
    db.add(version)
    db.commit()


def test_extract_agent_id_prefers_meta_then_the_key_binding():
    assert extract_agent_id({"name": "x", "_meta": {"agentId": "a1"}}, "a2") == "a1"
    assert extract_agent_id({"name": "x"}, "a2") == "a2"
    assert extract_agent_id({"name": "x"}, None) is None
    # A blank meta value must not shadow the credential binding.
    assert extract_agent_id({"name": "x", "_meta": {"agentId": ""}}, "a2") == "a2"


def test_unknown_employee_is_refused_before_any_http_call(session):
    result = call_tool(
        session, _tenant(session), "agent_does_not_exist", "staffdeck:expense.submit", {}
    )
    assert result["isError"] is True
    assert "unknown" in result["content"][0]["text"].lower()


def test_tool_outside_the_employee_scope_is_refused(session):
    result = call_tool(session, _tenant(session), SALES_AGENT, "staffdeck:expense.submit", {})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["code"] == "TOOL_NOT_FOUND"


def test_catalog_tool_that_is_not_namespaced_is_refused(session):
    result = call_tool(session, _tenant(session), FINANCE_AGENT, "expense.submit", {})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["code"] == "TOOL_NOT_FOUND"


def test_knowledge_search_returns_citations(session):
    tenant_id = _tenant(session)
    _enable_knowledge_sync(session, tenant_id, FINANCE_AGENT)
    result = call_tool(
        session,
        tenant_id,
        FINANCE_AGENT,
        KNOWLEDGE_SEARCH_TOOL,
        {"query": "报销需要哪些材料"},
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert "citations" in payload
    assert "chunks" in payload


def test_knowledge_search_is_absent_without_a_synced_base(session):
    result = call_tool(
        session,
        _tenant(session),
        FINANCE_AGENT,
        KNOWLEDGE_SEARCH_TOOL,
        {"query": "报销"},
    )
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["code"] == "TOOL_NOT_FOUND"


def test_knowledge_search_cannot_widen_its_scope(session):
    """A caller naming a foreign knowledge base must simply get nothing back."""
    tenant_id = _tenant(session)
    _enable_knowledge_sync(session, tenant_id, FINANCE_AGENT)
    result = call_tool(
        session,
        tenant_id,
        FINANCE_AGENT,
        KNOWLEDGE_SEARCH_TOOL,
        {"query": "报销", "knowledgeBaseIds": ["kb_does_not_exist"]},
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["citations"] == []
    assert payload["chunks"] == []


def test_tool_result_never_contains_credentials(session):
    result = call_tool(
        session, _tenant(session), FINANCE_AGENT, "staffdeck:expense.quota_query", {}
    )
    blob = json.dumps(result, ensure_ascii=False).lower()
    for forbidden in ("headers_json", "auth_json", "sd_live_", "bearer "):
        assert forbidden not in blob
