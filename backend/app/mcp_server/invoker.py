from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_version_ids
from app.db.models import AgentProfile, KnowledgeBaseVersion
from app.knowledge.schema import KnowledgeSearchRequest
from app.knowledge.service import KnowledgeService
from app.mcp_server.catalog import (
    KNOWLEDGE_SEARCH_TOOL,
    build_tool_catalog,
    tool_name_map,
)
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall


def extract_agent_id(params: dict[str, Any], principal_agent_id: str | None) -> str | None:
    """Resolve the employee context for a ``tools/call``.

    The MCP ``_meta`` channel is the primary source; a credential bound to one
    employee supplies a fallback. Nothing is guessed beyond that.
    """
    meta = params.get("_meta")
    if isinstance(meta, dict):
        candidate = meta.get("agentId")
        if isinstance(candidate, str) and candidate:
            return candidate
    return principal_agent_id


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    body = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def _narrow_to_requested_bases(
    db: Session, tenant_id: str, allowed: list[str], requested: list[str]
) -> list[str]:
    """Keep only visible versions belonging to the knowledge bases the caller named."""
    rows = db.exec(
        select(KnowledgeBaseVersion.id).where(
            KnowledgeBaseVersion.tenant_id == tenant_id,
            KnowledgeBaseVersion.knowledge_base_id.in_(requested),  # type: ignore[attr-defined]
        )
    ).all()
    wanted = set(rows)
    return [version_id for version_id in allowed if version_id in wanted]


def _knowledge_search(
    db: Session, tenant_id: str, agent_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Search the employee's synced knowledge without going through the JWT layer.

    ``app.api.knowledge.search_knowledge`` is a FastAPI endpoint whose first act
    is ``require_agent_scope_viewer(current_user)``; an MCP caller presents a
    credential, not a ``User``, so that wrapper cannot be reused. Visibility is
    enforced here instead through the same branching helper the wrapper uses.
    """
    query = str(arguments.get("query") or "")
    allowed = list(visible_knowledge_base_version_ids(db, tenant_id, agent_id))

    requested = [str(item) for item in (arguments.get("knowledgeBaseIds") or [])]
    if requested:
        allowed = _narrow_to_requested_bases(db, tenant_id, allowed, requested)

    if not allowed:
        return _text_result({"citations": [], "chunks": [], "query": query})

    request = KnowledgeSearchRequest(
        tenant_id=tenant_id,
        agent_id=agent_id,
        query=query,
        knowledge_base_version_ids=allowed,
        max_chunks=max(1, min(int(arguments.get("maxChunks") or 8), 20)),
    )
    result = KnowledgeService(db).search(request, None)
    dumped = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    dumped["citations"] = dumped.get("okf_citations") or dumped.get("evidence_pack") or []
    return _text_result(dumped)


def call_tool(
    db: Session,
    tenant_id: str,
    agent_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute one MCP tool call for one employee.

    Returns an MCP ``CallToolResult`` shape; protocol-level failures (bad name,
    unknown employee) are reported as ``isError`` rather than raised, because a
    tool error is a valid JSON-RPC response.
    """
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        return _text_result(
            {"code": "AGENT_NOT_FOUND", "message": "unknown digital employee"},
            is_error=True,
        )

    catalog = build_tool_catalog(db, tenant_id, agent_id)
    if not any(item["name"] == name for item in catalog):
        return _text_result(
            {"code": "TOOL_NOT_FOUND", "message": f"{name} is not exposed"}, is_error=True
        )

    if name == KNOWLEDGE_SEARCH_TOOL:
        return _knowledge_search(db, tenant_id, agent_id, arguments)

    # The raw slug cannot always be recovered from the namespaced name
    # (normalisation is lossy), so the catalog map is the source of truth.
    bare = tool_name_map(db, tenant_id, agent_id)[name]
    result = ToolExecutor(db).execute(
        tenant_id=tenant_id,
        tool_call=ToolCall(name=bare, arguments=arguments),
        agent_id=agent_id,
    )
    if not result.success:
        code = result.error.code if result.error else "EXECUTION_ERROR"
        message = result.error.message if result.error else "tool execution failed"
        return _text_result({"code": code, "message": message}, is_error=True)
    return _text_result(result.data)
