from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import Response as RawResponse
from sqlmodel import Session, select

from app.agents.branching import visible_general_skill_rows
from app.db import get_session
from app.db.models import AgentProfile, APIAuditLog, utc_now
from app.digital_employees.payload import build_employee, build_employee_list
from app.digital_employees.skill_package import build_skill_zip, skill_zip_filename
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.errors import PublicAPIError
from app.public_api.gallery import visible_gallery_agents
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["digital-employees"])


def distribution_agents(db: Session, principal: PublicPrincipal) -> list[AgentProfile]:
    """取对外发布的数字员工集合，还原成供 payload 构建使用的 ORM 行。

    先按 principal 求出可见 gallery 的 id 集合，集合为空时直接返回空列表，
    再按租户过滤出 status 为 active 的记录。
    """
    allowed = {row.id for row in visible_gallery_agents(db, principal)}
    if not allowed:
        return []
    rows = db.exec(
        select(AgentProfile).where(
            AgentProfile.tenant_id == principal.tenant_id,
            AgentProfile.id.in_(allowed),  # type: ignore[attr-defined]
        )
    ).all()
    return [row for row in rows if row.status == "active"]


# Kept as an alias so the distribution endpoints and their tests keep working.
_distribution_agents = distribution_agents


@router.get("", response_model=dict)
def list_digital_employees(
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response | dict:
    """返回数字员工清单，并用 ETag 支持条件请求与增量拉取。

    ETag 只由业务核心字段计算、刻意不含 fetchedAt，否则每次请求都会失效；
    If-None-Match 命中时返回 304，未命中则回写 ETag 并附上本次 fetchedAt。
    """
    core = build_employee_list(principal, _distribution_agents(db, principal), db)
    # The ETag must not include fetchedAt: a per-request timestamp would make
    # every conditional request miss and defeat incremental fetching entirely.
    etag = etag_for(core)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {**core, "fetchedAt": _now_iso()}


@router.get("/{agent_id}:export-skill")
def export_employee_skill(
    agent_id: str,
    request: Request,
    slug: str = Query(..., min_length=1, max_length=200),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> RawResponse:
    """把指定数字员工的一项通用技能打包成 SKILL.md 压缩包供 deepchat 技能目录使用。

    slug 走查询参数而非路径段，以便在保持 `/{id}:export-skill` 路由形状的同时
    定位该员工众多技能中的一项；成功下载后追加一条 API 审计日志。
    """
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    row = next(
        (
            item
            for item in visible_general_skill_rows(db, principal.tenant_id, agent.id)
            if item.slug == slug
        ),
        None,
    )
    if row is None:
        raise PublicAPIError(
            404, "SKILL_NOT_FOUND", "This skill is not distributed to the requested employee."
        )
    data = build_skill_zip(row)
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="GET",
            path=request.url.path,
            resource_type="general_skill",
            resource_id=row.id,
            status_code=200,
            duration_ms=0.0,
        )
    )
    db.commit()
    return RawResponse(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_zip_filename(slug)}"',
        },
    )


@router.get("/{agent_id}", response_model=dict)
def get_digital_employee(
    agent_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> Response | dict:
    """按 id 返回单个数字员工详情，同样用 ETag 支持条件请求。

    目标不在可见发布集合内时抛 404 AGENT_NOT_FOUND；版本未变化时返回 304。
    """
    agent = next(
        (row for row in _distribution_agents(db, principal) if row.id == agent_id),
        None,
    )
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    employee = build_employee(principal, agent, db)
    etag = etag_for(employee)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {"employee": employee}


def _now_iso() -> str:
    """返回带 Z 后缀的 UTC 当前时刻 ISO 字符串。"""
    return utc_now().isoformat() + "Z"
