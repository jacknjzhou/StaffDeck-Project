from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlmodel import Session, select

from app.agents.schema import (
    AgentModelsUpdateRequest,
    AgentProfileCreateRequest,
    AgentProfileUpdateRequest,
    AgentResourceBindingInput,
    AgentResourcesUpdateRequest,
)
from app.api import agents as internal_agents
from app.agents.branching import visible_skill
from app.core.capability_manifest import CapabilityManifestBuilder
from app.db import get_session
from app.db.models import AgentProfile, ModelConfig
from app.public_api.auth import (
    PublicPrincipal,
    enforce_agent_access,
    require_scopes,
)
from app.public_api.errors import PublicAPIError
from app.public_api.idempotency import replay_idempotent_response, store_idempotent_response
from app.public_api.schemas import (
    AgentCreate,
    AgentUpdate,
    ModelBindingsUpdate,
    ResourceBindingsUpdate,
)
from app.public_api.utils import etag_for


router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_payload(value: object) -> dict:
    """把 pydantic 模型或字典转成可下发的 JSON 字典，并剔除 tenant_id。"""
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        raise TypeError("Unsupported agent payload")
    payload.pop("tenant_id", None)
    return payload


@router.get("", response_model=dict)
def list_agents(
    limit: int = Query(50, ge=1, le=100),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """列出当前租户的数字员工，供 Open API 客户端读取。

    只返回前 limit 条且不分页（next_cursor 恒为 None）；agent 作用域的
    API Key 只能看到自己绑定的那一个员工。
    """
    rows = internal_agents.list_agents(principal.tenant_id, db, principal.actor_user)
    if principal.agent_id:
        rows = [row for row in rows if row.id == principal.agent_id]
    data = [_agent_payload(row) for row in rows[:limit]]
    return {"data": data, "next_cursor": None}


@router.post("", response_model=dict, status_code=201)
def create_agent(
    body: AgentCreate,
    request: Request,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("agents:write")),
    db: Session = Depends(get_session),
) -> dict:
    """创建数字员工并返回其 JSON 表示。

    仅租户级 Key 可创建（agent 作用域返回 403）；相同幂等键的重放直接返回
    首次结果，创建成功时附带 ETag 供后续条件更新使用。
    """
    if principal.agent_id:
        raise PublicAPIError(403, "TENANT_KEY_REQUIRED", "A tenant key is required.")
    replay = replay_idempotent_response(db, principal, request, body.model_dump(mode="json"))
    if replay:
        response.status_code = replay[0]
        return replay[1]
    created = internal_agents.create_agent(
        AgentProfileCreateRequest(
            tenant_id=principal.tenant_id,
            name=body.name,
            description=body.description,
            persona_prompt=body.persona_prompt,
            source_mode=body.source_mode,
            copy_from_agent_id=body.copy_from_agent_id,
            harness_max_actions=body.harness_max_actions,
            metadata=body.metadata,
        ),
        db,
        principal.actor_user,
    )
    payload = _agent_payload(created)
    response.headers["ETag"] = etag_for(payload)
    store_idempotent_response(
        db,
        principal,
        request,
        body.model_dump(mode="json"),
        payload,
        status_code=201,
        resource_id=created.id,
    )
    return payload


@router.get("/{agent_id}", response_model=dict)
def get_agent(
    agent_id: str,
    response: Response,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """按 id 读取单个员工并返回 JSON 表示，响应头同时带上当前 ETag。"""
    enforce_agent_access(principal, agent_id)
    payload = _agent_payload(
        internal_agents.get_agent(agent_id, principal.tenant_id, db, principal.actor_user)
    )
    response.headers["ETag"] = etag_for(payload)
    return payload


@router.patch("/{agent_id}", response_model=dict)
def update_agent(
    agent_id: str,
    body: AgentUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:write")),
    db: Session = Depends(get_session),
) -> dict:
    """条件更新员工：必须携带与当前资源一致的 If-Match。

    缺少 If-Match 返回 428，ETag 不匹配返回 412；只应用请求中显式给出的字段。
    """
    enforce_agent_access(principal, agent_id, write=True)
    current = _agent_payload(
        internal_agents.get_agent(agent_id, principal.tenant_id, db, principal.actor_user)
    )
    current_etag = etag_for(current)
    if not if_match:
        raise PublicAPIError(428, "IF_MATCH_REQUIRED", "If-Match is required for this update.")
    if if_match != current_etag:
        raise PublicAPIError(412, "ETAG_MISMATCH", "The agent changed since it was read.")
    updated = internal_agents.update_agent(
        agent_id,
        AgentProfileUpdateRequest(
            tenant_id=principal.tenant_id,
            **body.model_dump(exclude_unset=True),
        ),
        db,
        principal.actor_user,
    )
    payload = _agent_payload(updated)
    response.headers["ETag"] = etag_for(payload)
    return payload


@router.post("/{agent_id}:archive", response_model=dict)
def archive_agent(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("agents:write")),
    db: Session = Depends(get_session),
) -> dict:
    """把员工状态置为 archived（逻辑删除），返回更新后的员工。"""
    enforce_agent_access(principal, agent_id, write=True)
    updated = internal_agents.update_agent(
        agent_id,
        AgentProfileUpdateRequest(tenant_id=principal.tenant_id, status="archived"),
        db,
        principal.actor_user,
    )
    return _agent_payload(updated)


@router.get("/{agent_id}/resources", response_model=dict)
def get_agent_resources(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """返回员工的资源绑定列表，逐项剔除 tenant_id 后放在 data 中。"""
    enforce_agent_access(principal, agent_id)
    rows = internal_agents.get_agent_resources(
        agent_id, principal.tenant_id, db, principal.actor_user
    )
    return {"data": [row.model_dump(mode="json", exclude={"tenant_id"}) for row in rows]}


@router.put("/{agent_id}/resources", response_model=dict)
def update_agent_resources(
    agent_id: str,
    body: ResourceBindingsUpdate,
    principal: PublicPrincipal = Depends(require_scopes("agents:write")),
    db: Session = Depends(get_session),
) -> dict:
    """整体替换员工的资源绑定，返回替换后的资源列表。"""
    enforce_agent_access(principal, agent_id, write=True)
    rows = internal_agents.update_agent_resources(
        agent_id,
        AgentResourcesUpdateRequest(
            tenant_id=principal.tenant_id,
            resources=[AgentResourceBindingInput(**item.model_dump()) for item in body.resources],
        ),
        db,
        principal.actor_user,
    )
    return {"data": [row.model_dump(mode="json", exclude={"tenant_id"}) for row in rows]}


@router.get("/{agent_id}/models", response_model=dict)
def get_agent_models(
    agent_id: str,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> dict:
    """返回员工的模型绑定视图。

    当前只把租户默认模型暴露为 role=default 的继承项，effective 恒为 False；
    员工不存在或不属于当前租户时返回 AGENT_NOT_FOUND。
    """
    enforce_agent_access(principal, agent_id)
    agent = db.get(AgentProfile, agent_id)
    if not agent or agent.tenant_id != principal.tenant_id:
        raise PublicAPIError(404, "AGENT_NOT_FOUND", "Agent not found.")
    default = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == principal.tenant_id,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    return {
        "data": (
            [{"role": "default", "model_config_id": default.id, "effective": False}]
            if default
            else []
        )
    }


@router.put("/{agent_id}/models", response_model=dict)
def update_agent_models(
    agent_id: str,
    body: ModelBindingsUpdate,
    principal: PublicPrincipal = Depends(require_scopes("agents:write")),
    db: Session = Depends(get_session),
) -> dict:
    """兼容性重置接口：清空员工模型绑定，使其回到继承默认模型。

    请求体不被采用，内部服务删除遗留绑定行后返回默认模型视图。
    """
    enforce_agent_access(principal, agent_id, write=True)
    # Retained as a compatibility reset endpoint. Per-employee model bindings are no longer
    # accepted; the internal service removes any legacy rows and the agent inherits the default.
    internal_agents.update_agent_models(
        agent_id,
        AgentModelsUpdateRequest(
            tenant_id=principal.tenant_id,
            bindings=[],
        ),
        db,
        principal.actor_user,
    )
    return get_agent_models(agent_id, principal, db)


@router.get("/{agent_id}/capabilities", response_model=dict)
def get_agent_capabilities(
    agent_id: str,
    sop_id: str | None = Query(default=None),
    step_id: str | None = Query(default=None),
    principal: PublicPrincipal = Depends(require_scopes("capabilities:read")),
    db: Session = Depends(get_session),
) -> dict:
    """返回员工（可按 SOP 与步骤限定）的能力清单。

    员工不存在、已归档或不属于当前租户返回 AGENT_NOT_FOUND；sop_id 不在该
    员工可见范围时返回 SOP_NOT_FOUND。
    """
    enforce_agent_access(principal, agent_id)
    agent = db.get(AgentProfile, agent_id)
    if not agent or agent.tenant_id != principal.tenant_id or agent.status != "active":
        raise PublicAPIError(404, "AGENT_NOT_FOUND", "Agent not found.")
    skill = visible_skill(db, principal.tenant_id, sop_id, agent_id) if sop_id else None
    if sop_id and skill is None:
        raise PublicAPIError(404, "SOP_NOT_FOUND", "SOP not found in this agent scope.")
    manifest = CapabilityManifestBuilder(db).build(
        principal.tenant_id, agent_id, skill, step_id
    )
    return manifest.model_dump(mode="json")
