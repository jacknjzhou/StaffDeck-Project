from __future__ import annotations

from typing import Literal

AgentCredentialAccess = Literal["runtime", "full_access"]

AGENT_RUNTIME_SCOPES = frozenset(
    {
        "agents:read",
        "capabilities:read",
        "runs:create",
        "runs:read",
        "runs:cancel",
        "sessions:read",
        "sessions:write",
        "artifacts:read",
        "traces:read",
        # An employee-scoped key is how deepchat connects to the MCP endpoint for
        # one employee; without this the MCP server is unreachable for the very
        # keys the per-employee runtime issues.
        "mcp:invoke",
    }
)

# Kept for existing employee-scoped credentials created before account-level
# keys were introduced. New employee keys use AGENT_RUNTIME_SCOPES only.
AGENT_FULL_ACCESS_SCOPES = frozenset(
    {
        *AGENT_RUNTIME_SCOPES,
        "sops:read",
        "knowledge:read",
        "skills:read",
        "tools:read",
        "scheduled_tasks:read",
        "operations:read",
    }
)

# An account master key represents the signed-in user in the public API. It is
# not bound to one employee, and every request re-evaluates both view and manage
# permissions. It covers the employee lifecycle while deliberately excluding
# tenant credentials, provider secrets, webhooks, audit logs, and tenant usage.
USER_FULL_ACCESS_SCOPES = frozenset(
    {
        *AGENT_FULL_ACCESS_SCOPES,
        "agents:write",
        "gallery:read",
        "gallery:use",
        "sops:write",
        "sops:publish",
        "knowledge:write",
        "knowledge:publish",
        "skills:write",
        "skills:test",
        "tools:write",
        "tools:test",
        "scheduled_tasks:write",
        "scheduled_tasks:run",
    }
)

AGENT_KEY_ALLOWED_SCOPES = AGENT_RUNTIME_SCOPES


def scopes_for_agent_access(access: AgentCredentialAccess) -> list[str]:
    """按访问级别返回应授予的 scope 列表，排序后输出便于比对与展示。"""
    scopes = AGENT_FULL_ACCESS_SCOPES if access == "full_access" else AGENT_RUNTIME_SCOPES
    return sorted(scopes)


def agent_access_for_scopes(scopes: list[str]) -> AgentCredentialAccess:
    """由已持有的 scope 反推访问级别，只有覆盖 full_access 全集才算 full_access。"""
    return "full_access" if AGENT_FULL_ACCESS_SCOPES.issubset(scopes) else "runtime"
