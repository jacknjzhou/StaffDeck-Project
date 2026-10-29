from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

from app.agents.branching import (
    resource_binding_metadata,
    visible_general_skill_rows,
    visible_knowledge_base_versions,
    visible_tool_rows,
)
from app.config import get_settings
from app.db.models import AgentProfile
from app.digital_employees.contract import (
    MAX_AVATAR,
    MAX_CAPABILITIES,
    MAX_CAPABILITY,
    MAX_DESCRIPTION,
    MAX_GREETING,
    MAX_ID,
    MAX_KNOWLEDGE_BASE_IDS,
    MAX_NAME,
    MAX_ROLE,
    MAX_SKILL_IDS,
    MAX_SYSTEM_PROMPT,
    MAX_TOOLS,
    clamp_items,
    clamp_text,
    zod_len,
)
from app.digital_employees.sop_text import select_sop_documents
from app.digital_employees.tool_namespace import namespaced_tool_name

if TYPE_CHECKING:
    # Imported for typing only: app.public_api.__init__ eagerly imports the
    # whole Open API app, so a runtime import here would close an import cycle.
    from app.public_api.auth import PublicPrincipal

MODES = {"local", "remote", "hybrid"}
DEFAULT_MODE = "local"
# Opt-out switch: a version is synced unless it explicitly sets this to False.
SYNC_FLAG = "deepchat_sync_enabled"


def build_employee(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> dict[str, Any]:
    """把 AgentProfile 组装成 DeepChat 可消费的员工对象。

    各字段按 contract 中的长度与数量上限裁剪，超限项汇总进 warnings；执行模式非法时
    回落 local；知识库仅排除 deepchat_sync_enabled 显式为 False 的版本。最后计算
    configHash 并校验 id/name/role/systemPrompt 非空。
    """
    warnings: list[str] = []
    metadata = dict(agent.metadata_json or {})
    mode = _mode(metadata.get("execution_mode"), warnings)

    name = _text(agent.name, fallback=agent.id, limit=MAX_NAME)
    role = _text(metadata.get("role_name"), fallback=name, limit=MAX_ROLE)
    description = _text(
        agent.description, fallback="", limit=MAX_DESCRIPTION, allow_empty=True
    )

    capabilities, cap_overflow = clamp_items(_capabilities(metadata), MAX_CAPABILITIES)
    if cap_overflow:
        warnings.append(f"能力标签超过 {MAX_CAPABILITIES} 个，已省略其余。")

    sop_documents, sop_warnings = select_sop_documents(
        db,
        principal.tenant_id,
        agent.id,
        mode,
        resource_binding_metadata(db, principal.tenant_id, agent.id, "skill"),
    )
    warnings.extend(sop_warnings)

    general_skills, skill_overflow = clamp_items(
        sorted(
            visible_general_skill_rows(db, principal.tenant_id, agent.id),
            key=lambda row: (row.name or row.slug, row.slug),
        ),
        MAX_SKILL_IDS,
    )
    if skill_overflow:
        warnings.append(f"技能超过 {MAX_SKILL_IDS} 个，已省略其余。")

    tool_names, tool_overflow = clamp_items(_tool_names(db, principal, agent), MAX_TOOLS)
    if tool_overflow:
        warnings.append(f"工具超过 {MAX_TOOLS} 个，已省略其余。")

    knowledge_ids, kb_overflow = clamp_items(
        _knowledge_base_ids(db, principal, agent), MAX_KNOWLEDGE_BASE_IDS
    )
    if kb_overflow:
        warnings.append(f"可同步知识库超过 {MAX_KNOWLEDGE_BASE_IDS} 个，已省略其余。")

    base_url = get_settings().normalized_tool_base_url
    employee: dict[str, Any] = {
        "id": clamp_text(agent.id, MAX_ID)[0],
        "name": name,
        "role": role,
        "description": description,
        "avatar": _avatar(metadata),
        "capabilities": capabilities,
        "greeting": _greeting(metadata),
        "systemPrompt": _system_prompt(agent, name, role, description, warnings),
        "sops": sop_documents,
        "knowledgeBaseIds": knowledge_ids,
        "skillIds": [_slug(row) for row in general_skills],
        "tools": tool_names,
        "enabled": agent.status == "active",
        "updatedAt": _iso(_latest_update(agent, general_skills)),
        # Distribution extensions. deepchat's schema is non-strict, so these are
        # ignored by the current renderer and picked up as D2 starts consuming them.
        "executionMode": mode,
        "skills": [
            {
                "id": row.id,
                "name": _text(row.name, fallback=_slug(row), limit=MAX_ROLE),
                "slug": _slug(row),
                "downloadUrl": (
                    f"{base_url}/api/v1/digital-employees/{agent.id}"
                    f":export-skill?slug={_slug(row)}"
                ),
            }
            for row in general_skills
        ],
        "knowledgePackagesUrl": (
            f"{base_url}/api/v1/digital-employees/{agent.id}/knowledge-packages"
        ),
    }
    if warnings:
        employee["warnings"] = warnings
    employee["configHash"] = config_hash(employee)
    validate_required_fields(employee)
    return employee


def build_employee_list(
    principal: PublicPrincipal,
    agents: list[AgentProfile],
    db: Session,
) -> dict[str, Any]:
    """批量构建员工对象，按名称与 id 排序后汇总为列表。

    同时基于各员工 id 与 configHash 生成形如 v1:<12 位摘要> 的 configVersion。
    """
    employees = [build_employee(principal, agent, db) for agent in agents]
    employees.sort(key=lambda item: (item["name"], item["id"]))
    version_seed = json.dumps(
        sorted((item["id"], item["configHash"]) for item in employees),
        separators=(",", ":"),
    )
    digest = hashlib.sha256(version_seed.encode("utf-8")).hexdigest()[:12]
    return {"employees": employees, "configVersion": f"v1:{digest}"}


def config_hash(employee: dict[str, Any]) -> str:
    """计算员工配置的稳定摘要，带 sha256: 前缀并截取 16 位。

    只对契约内容取摘要，排除 configHash、warnings 与 updatedAt，避免提示信息或
    时间戳变化引起摘要抖动。
    """
    content = {
        key: value
        for key, value in employee.items()
        if key not in {"configHash", "warnings", "updatedAt"}
    }
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def validate_required_fields(employee: dict[str, Any]) -> None:
    """校验 id、name、role、systemPrompt 均非空，为空时抛 ValueError。

    这样在构建阶段即报错，而不是下发一份客户端无法解析的配置。
    """
    for key in ("id", "name", "role", "systemPrompt"):
        if not str(employee.get(key) or "").strip():
            raise ValueError(f"digital employee {employee.get('id')!r} has an empty {key}")


def _mode(raw: Any, warnings: list[str]) -> str:
    """归一化执行模式。

    空值取默认 local，命中 local/remote/hybrid 原样返回，未知取值则追加警告并
    回落 local。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_MODE
    if value in MODES:
        return value
    warnings.append(f"未知执行模式 {value!r}，已按 {DEFAULT_MODE} 下发。")
    return DEFAULT_MODE


def _text(value: Any, *, fallback: str, limit: int, allow_empty: bool = False) -> str:
    """去除首尾空白后按上限裁剪文本。

    文本为空时：allow_empty 为真返回空串，否则返回裁剪后的 fallback。
    """
    text = str(value or "").strip()
    if not text:
        return "" if allow_empty else clamp_text(fallback, limit)[0]
    clamped, _ = clamp_text(text, limit)
    return clamped


def _slug(row: Any) -> str:
    """取行对象的 slug，缺失时回退到其 id。"""
    slug = str(getattr(row, "slug", "") or "").strip()
    return slug or str(getattr(row, "id", "") or "")


def _capabilities(metadata: dict[str, Any]) -> list[str]:
    """按 expertise_tags、work_modes、work_styles 顺序合并能力标签并去重。

    空串与超出单标签长度上限的取值会被丢弃，结果按出现顺序保留。
    """
    seen: list[str] = []
    for key in ("expertise_tags", "work_modes", "work_styles"):
        for item in metadata.get(key) or []:
            text = str(item).strip()
            if not text or text in seen or zod_len(text) > MAX_CAPABILITY:
                continue
            seen.append(text)
    return seen


def _avatar(metadata: dict[str, Any]) -> str | None:
    """读取 metadata 中的 avatar_url 并按上限裁剪后下发。

    仅真实 URL 对外分发，预设头像 key 属于管理端细节，不在此返回。
    """
    url = str(metadata.get("avatar_url") or "").strip()
    if not url:
        return None
    clamped, _ = clamp_text(url, MAX_AVATAR)
    return clamped or None


def _greeting(metadata: dict[str, Any]) -> str | None:
    """读取 metadata 中的 greeting，为空或裁剪后为空时返回 None。"""
    text = str(metadata.get("greeting") or "").strip()
    if not text:
        return None
    clamped, _ = clamp_text(text, MAX_GREETING)
    return clamped or None


def _system_prompt(
    agent: AgentProfile,
    name: str,
    role: str,
    description: str,
    warnings: list[str],
) -> str:
    """确定下发给 DeepChat 的系统提示词。

    优先使用 agent 的人设提示词；为空时按角色、名称与描述生成默认文案。超出契约上限
    会截断并追加警告，裁剪后仍为空则退回最小角色说明。
    """
    prompt = str(agent.persona_prompt or "").strip()
    if not prompt:
        prompt = f"# 角色：{role}\n你是「{name}」，一名专业的{role}数字员工。"
        if description:
            prompt += f"\n\n职责范围：{description}"
    clamped, cut = clamp_text(prompt, MAX_SYSTEM_PROMPT)
    if cut:
        warnings.append("人设提示词超出契约上限，已截断。")
    return clamped or f"# 角色：{role}"


def _tool_names(db: Session, principal: PublicPrincipal, agent: AgentProfile) -> list[str]:
    """返回该员工可见且已启用工具的命名空间名称，去重后排序。

    只有名称会离开服务端，工具 URL 与凭据一律不下发。
    """
    names = [
        namespaced_tool_name(row.name)
        for row in visible_tool_rows(db, principal.tenant_id, agent.id, include_inactive=False)
        if row.enabled
    ]
    return sorted(set(names))


def _knowledge_base_ids(
    db: Session, principal: PublicPrincipal, agent: AgentProfile
) -> list[str]:
    """列出可同步给该员工的知识库 id 并去重排序。

    同步默认开启（opt-out）：只有版本 metadata 中 deepchat_sync_enabled 显式为 False
    的知识库才会被排除。
    """
    ids: list[str] = []
    for kb_id, version in visible_knowledge_base_versions(
        db, principal.tenant_id, agent.id
    ).items():
        metadata = dict(getattr(version, "metadata_json", None) or {})
        if metadata.get(SYNC_FLAG) is False:
            continue
        ids.append(kb_id)
    return sorted(set(ids))


def _latest_update(agent: AgentProfile, general_skills: list[Any]) -> datetime:
    """取员工自身与全部通用技能中最近的 updated_at；均无有效时间时回退员工时间。"""
    stamps = [agent.updated_at]
    stamps.extend(getattr(row, "updated_at", None) for row in general_skills)
    present = [stamp for stamp in stamps if isinstance(stamp, datetime)]
    return max(present) if present else agent.updated_at


def _iso(value: datetime) -> str:
    """把时间转成秒级精度、以 Z 结尾的 ISO 8601 字符串。"""
    return value.replace(microsecond=0).isoformat() + "Z"
