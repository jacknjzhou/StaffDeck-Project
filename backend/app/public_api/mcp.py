from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.db import get_session
from app.db.models import APIAuditLog
from app.mcp_server.catalog import build_tool_catalog
from app.mcp_server.invoker import call_tool, extract_agent_id
from app.mcp_server.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RpcError,
    error_response,
    negotiate_protocol_version,
    parse_request,
    success_response,
)
from app.mcp_server.session import issue_session_id
from app.public_api.auth import PublicPrincipal, enforce_agent_access, require_scopes
from app.public_api.errors import PublicAPIError

router = APIRouter(prefix="/mcp", tags=["mcp"])

_SERVER_INFO = {"name": "StaffDeck MCP", "version": "1.0.0"}


def _audit(
    db: Session,
    principal: PublicPrincipal,
    request: Request,
    *,
    resource_id: str,
    status_code: int = 200,
) -> None:
    """为一次 MCP 调用写入 APIAuditLog 并立即提交，使审计与响应同步落库。"""
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="POST",
            path=request.url.path,
            resource_type="tool",
            resource_id=resource_id,
            status_code=status_code,
            duration_ms=0.0,
        )
    )
    db.commit()


def _resolve_agent(
    principal: PublicPrincipal,
    params: dict[str, Any],
    path_agent_id: str | None,
) -> str:
    """确定请求指向的员工，并校验调用方有权使用该员工。

    来源按优先级取：/mcp/agents/{id} 路径段、MCP 的 _meta.agentId、绑定单一员工的
    凭证。提供路径形式是因为标准 MCP 客户端无法在 tools/list 中注入 _meta。
    """
    agent_id = path_agent_id or extract_agent_id(params, principal.agent_id)
    if not agent_id:
        raise RpcError(
            INVALID_PARAMS,
            "Specify the employee via /mcp/agents/{id}, params._meta.agentId, "
            "or a key bound to one employee.",
        )
    enforce_agent_access(principal, agent_id)
    return agent_id


def _dispatch(
    *,
    payload: dict[str, Any],
    request: Request,
    response: Response,
    principal: PublicPrincipal,
    db: Session,
    path_agent_id: str | None,
):
    """解析 JSON-RPC 请求并按 method 分发，把各类错误统一转成 JSON-RPC 错误响应。

    通知（notification）直接返回 202；PublicAPIError 原样抛出以保留 HTTP 状态码，
    避免把 403 伪装成 200 的工具级错误。
    """
    try:
        request_id, method, params, is_notification = parse_request(payload)
    except RpcError as error:
        return JSONResponse(error_response(None, error), status_code=200)

    if is_notification:
        # notifications/initialized needs no reply; per JSON-RPC 2.0 nothing does.
        return Response(status_code=202)

    try:
        if method == "initialize":
            result = {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
            }
            response.headers["Mcp-Session-Id"] = issue_session_id()
            _audit(db, principal, request, resource_id=path_agent_id or "initialize")
            return success_response(request_id, result)

        if method == "ping":
            return success_response(request_id, {})

        if method == "tools/list":
            agent_id = _resolve_agent(principal, params, path_agent_id)
            tools = build_tool_catalog(db, principal.tenant_id, agent_id)
            _audit(db, principal, request, resource_id=agent_id)
            return success_response(request_id, {"tools": tools})

        if method == "tools/call":
            agent_id = _resolve_agent(principal, params, path_agent_id)
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise RpcError(INVALID_PARAMS, "params.name is required.")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise RpcError(INVALID_PARAMS, "params.arguments must be an object.")
            result = call_tool(db, principal.tenant_id, agent_id, name, arguments)
            _audit(db, principal, request, resource_id=name)
            return success_response(request_id, result)

        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
    except RpcError as error:
        return JSONResponse(error_response(request_id, error), status_code=200)
    except PublicAPIError:
        # Authorization failures keep their HTTP status: a 403 must not be
        # laundered into a 200 with a JSON-RPC error body, or a client would
        # treat an access denial as a tool-level hiccup.
        raise
    except Exception as error:  # noqa: BLE001
        return JSONResponse(
            error_response(request_id, RpcError(INTERNAL_ERROR, str(error))),
            status_code=200,
        )


@router.post("")
def mcp_endpoint(
    payload: dict[str, Any],
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),
    db: Session = Depends(get_session),
):
    """处理 MCP streamable HTTP 的通用入口。

    JSON-RPC 只有单一端点，因此所有方法都在这里分发，而不是各占一条 FastAPI 路由。
    """
    return _dispatch(
        payload=payload,
        request=request,
        response=response,
        principal=principal,
        db=db,
        path_agent_id=None,
    )


@router.post("/agents/{agent_id}")
def mcp_agent_endpoint(
    agent_id: str,
    payload: dict[str, Any],
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),
    db: Session = Depends(get_session),
):
    """按员工寻址的路由变体，供无法逐请求设置 _meta 的客户端使用。

    deepchat 的 MCPServerConfig 只支持静态 customHeaders，员工必须写进运营人员粘贴
    到设置页的 URL 里。
    """
    return _dispatch(
        payload=payload,
        request=request,
        response=response,
        principal=principal,
        db=db,
        path_agent_id=agent_id,
    )


@router.get("")
def mcp_get_not_supported(
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),
) -> Response:
    """不支持服务端主动推送流，GET 无可返回内容，固定回 405。"""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/agents/{agent_id}")
def mcp_agent_get_not_supported(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("mcp:invoke")),
) -> Response:
    """员工寻址路由的 GET 同样无内容可返回，固定回 405 并仅允许 POST。"""
    return Response(status_code=405, headers={"Allow": "POST"})
