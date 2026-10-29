from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.agents.branching import visible_knowledge_base_versions, visible_tool_rows
from app.digital_employees.tool_namespace import TOOL_PREFIX, namespaced_tool_name
from app.mcp_server.protocol import INVALID_PARAMS, RpcError

KNOWLEDGE_SEARCH_TOOL = namespaced_tool_name("knowledge_search")
_KNOWLEDGE_SYNC_FLAG = "deepchat_sync_enabled"


def describe_knowledge_search() -> dict[str, Any]:
    """返回内置知识检索工具 knowledge_search 的 MCP 描述。

    包含带命名空间的工具名、中文说明与 inputSchema（query 必填，可按知识库与返回
    片段数限制）。
    """
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
    """判断该员工是否存在可同步的知识库版本。

    同步默认开启（opt-out）：只有 metadata 中 deepchat_sync_enabled 显式为 False
    的版本才被排除。
    """
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    return any(
        (version.metadata_json or {}).get(_KNOWLEDGE_SYNC_FLAG) is not False
        for version in versions.values()
    )


def tool_name_map(db: Session, tenant_id: str, agent_id: str) -> dict[str, str]:
    """构建「命名空间名 -> 原始 Tool.name」的映射，仅含可见且已启用的工具。

    命名归一化是有损的（expense.submit 与 expense_submit 无法同时保留可读名称），
    因此反向解析必须走这张表，不能靠字符串还原。
    """
    return {
        namespaced_tool_name(row.name): row.name
        for row in visible_tool_rows(db, tenant_id, agent_id, include_inactive=False)
        if row.enabled
    }


def build_tool_catalog(db: Session, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
    """构建该员工对 MCP 可见的工具目录，按工具名排序。

    只输出 name/description/inputSchema 三个协议字段：Tool 上还挂着 URL 与凭据，
    不得离开服务端。名称与分发包的 tools[] 完全一致，以保证 DeepChat 会话级
    白名单能匹配。存在可同步知识库时追加 knowledge_search。
    """
    catalog: list[dict[str, Any]] = []
    for row in visible_tool_rows(db, tenant_id, agent_id, include_inactive=False):
        if not row.enabled:
            continue
        catalog.append(
            {
                "name": namespaced_tool_name(row.name),
                "description": row.description or row.display_name or row.name,
                "inputSchema": row.input_schema or {"type": "object", "properties": {}},
            }
        )
    if _has_synced_knowledge(db, tenant_id, agent_id):
        catalog.append(describe_knowledge_search())
    return sorted(catalog, key=lambda item: item["name"])


def resolve_tool_name(db: Session, tenant_id: str, agent_id: str, name: str) -> str:
    """剥离命名空间并校验该工具确实暴露给此员工，返回原始 Tool.name。

    名称缺少工具命名空间前缀，或不在该员工的可见工具映射中时，抛 INVALID_PARAMS。
    """
    if not name.startswith(TOOL_PREFIX):
        raise RpcError(INVALID_PARAMS, f"Tool names must start with {TOOL_PREFIX!r}.")
    bare = tool_name_map(db, tenant_id, agent_id).get(name)
    if bare is None:
        raise RpcError(INVALID_PARAMS, "This tool is not available to the requesting employee.")
    return bare
