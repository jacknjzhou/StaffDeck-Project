from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_knowledge_base_versions, visible_tool_rows
from app.digital_employees.payload import TOOL_PREFIX
from app.mcp_server.protocol import INVALID_PARAMS, RpcError

KNOWLEDGE_SEARCH_TOOL = f"{TOOL_PREFIX}knowledge_search"
_KNOWLEDGE_SYNC_FLAG = "deepchat_sync_enabled"


def describe_knowledge_search() -> dict[str, Any]:
    return {
        "name": KNOWLEDGE_SEARCH_TOOL,
        "description": "在数字员工已同步的知识库中做带引用的检索，返回命中的片段与 citations。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": "自然语言问题"},
                "knowledgeBaseIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "留空则检索该员工全部已同步的知识库",
                },
                "maxChunks": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
        },
    }


def _has_synced_knowledge(db: Session, tenant_id: str, agent_id: str) -> bool:
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    return any(
        (version.metadata_json or {}).get(_KNOWLEDGE_SYNC_FLAG) is True
        for version in versions.values()
    )


def build_tool_catalog(db: Session, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
    """The MCP-visible tool set for one employee.

    Only the three protocol fields are emitted: ``Tool`` carries the URL and the
    credentials, and none of that may leave the server. The names are byte-equal
    to the ``tools[]`` entries in the distribution payload, which is what keeps
    deepchat's session-level allowlist matching.
    """
    catalog: list[dict[str, Any]] = []
    for row in visible_tool_rows(db, tenant_id, agent_id, include_inactive=False):
        if not row.enabled:
            continue
        catalog.append(
            {
                "name": f"{TOOL_PREFIX}{row.name}",
                "description": row.description or row.display_name or row.name,
                "inputSchema": row.input_schema or {"type": "object", "properties": {}},
            }
        )
    if _has_synced_knowledge(db, tenant_id, agent_id):
        catalog.append(describe_knowledge_search())
    return sorted(catalog, key=lambda item: item["name"])


def resolve_tool_name(catalog: list[dict[str, Any]], name: str) -> str:
    """Strip the namespace and prove the tool is actually exposed to this agent."""
    if not name.startswith(TOOL_PREFIX):
        raise RpcError(INVALID_PARAMS, f"Tool names must start with {TOOL_PREFIX!r}.")
    bare = name[len(TOOL_PREFIX) :]
    if not any(item["name"] == name for item in catalog):
        raise RpcError(INVALID_PARAMS, "This tool is not available to the requesting employee.")
    return bare
