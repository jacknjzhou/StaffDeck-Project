from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from sqlmodel import select

from app.agents.branching import (
    is_bound_resource_visible_for_agent,
    is_open_gallery_resource,
)
from app.capabilities.contracts import (
    CapabilityContext,
    GeneralSkillFile,
    GeneralSkillPackage,
    GeneralSkillResourceRef,
    GeneralSkillSummary,
)
from app.db.models import AgentProfile, AgentResourceBinding, GeneralSkill

LOCAL_GENERAL_SKILL_PROVIDER_ID = "local_general_skill"
LOCAL_GENERAL_SKILL_PACKAGE_CONTRACT = "1"


@dataclass(frozen=True)
class GeneralSkillRuntimeSnapshot:
    """一次调用生命周期内冻结在内存中的 Runner 输入快照。"""

    tenant_id: str
    slug: str
    name: str
    description: str | None
    homepage: str | None
    skill_markdown: str
    skill_files_json: tuple[Mapping[str, object], ...]
    metadata_json: Mapping[str, object] = field(default_factory=dict)
    permissions_json: Mapping[str, object] = field(default_factory=dict)
    runtime_config_json: Mapping[str, object] = field(default_factory=dict)
    status: str = "published"
    package_id: str = ""
    package_version: str = ""
    package_digest: str = ""


class LocalGeneralSkillCatalog:
    """基于既有数据行的兼容 Provider，只做列表与取包，从不执行代码。"""

    provider_id = LOCAL_GENERAL_SKILL_PROVIDER_ID

    def __init__(self, db: Any) -> None:
        """绑定查询所用的数据库会话。"""
        self.db = db

    def list_published(
        self, context: CapabilityContext
    ) -> Sequence[GeneralSkillSummary]:
        """列出当前上下文可见的已发布技能摘要，逐行交由 _is_visible 判定可见性。"""
        rows = self.db.exec(
            select(GeneralSkill).where(
                GeneralSkill.tenant_id == context.tenant_id,
                GeneralSkill.status == "published",
            )
        ).all()
        return tuple(
            _summary(row)
            for row in rows
            if self._is_visible(context, row)
        )

    def get_package(
        self,
        context: CapabilityContext,
        resource_ref: GeneralSkillResourceRef,
    ) -> GeneralSkillPackage | None:
        """按资源引用取技能包；引用与行内版本、摘要或契约版本不一致时返回 None。"""
        if resource_ref.catalog_binding_id != self.provider_id:
            return None
        row = self.db.get(GeneralSkill, resource_ref.package_id)
        if (
            row is None
            or row.tenant_id != context.tenant_id
            or row.status != "published"
            or not self._is_visible(context, row)
        ):
            return None
        package = package_from_row(row)
        if (
            package.version != resource_ref.version
            or package.digest != resource_ref.digest
            or package.package_contract_version
            != resource_ref.package_contract_version
        ):
            return None
        return package

    def _is_visible(self, context: CapabilityContext, row: GeneralSkill) -> bool:
        """判断技能是否对当前 Agent 可见。

        总览 Agent 与查不到的 Agent 走开放画廊判定；其余要求存在 active 绑定并通过绑定可见性校验。
        """
        agent = self.db.get(AgentProfile, context.agent_id)
        if not agent or agent.tenant_id != context.tenant_id or agent.is_overall:
            return is_open_gallery_resource(
                self.db, context.tenant_id, "general_skill", row
            )
        binding = self.db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == context.tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == "general_skill",
                AgentResourceBinding.resource_id == row.id,
                AgentResourceBinding.status == "active",
            )
        ).first()
        return bool(
            binding
            and is_bound_resource_visible_for_agent(
                self.db,
                context.tenant_id,
                "general_skill",
                row,
                binding,
            )
        )


def package_from_row(row: GeneralSkill) -> GeneralSkillPackage:
    """由数据行构造技能包，按规范化内容计算 sha256 摘要，入口文件优先 SKILL.md。"""
    files = _files_from_row(row)
    version = _package_version(row)
    canonical = {
        "package_id": row.id,
        "slug": row.slug,
        "version": version,
        "skill_markdown": row.skill_markdown,
        "files": [
            {
                "path": item.path,
                "content": item.content,
                "size": item.size,
                "mime_type": item.mime_type,
            }
            for item in files
        ],
    }
    digest = "sha256:" + sha256(
        json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    entrypoint = next(
        (item.path for item in files if item.path.lower() == "skill.md"),
        files[0].path,
    )
    return GeneralSkillPackage(
        package_id=row.id,
        slug=row.slug,
        version=version,
        digest=digest,
        package_contract_version=LOCAL_GENERAL_SKILL_PACKAGE_CONTRACT,
        skill_markdown=row.skill_markdown,
        files=files,
        entrypoint=entrypoint,
    )


def resource_ref_from_row(
    row: GeneralSkill,
    *,
    catalog_binding_id: str = LOCAL_GENERAL_SKILL_PROVIDER_ID,
) -> GeneralSkillResourceRef:
    """生成资源引用；非本地 Provider 且 metadata 内 provider_resource_ref 完整时优先采用。"""
    metadata = row.metadata_json if isinstance(row.metadata_json, Mapping) else {}
    configured = metadata.get("provider_resource_ref")
    if (
        catalog_binding_id != LOCAL_GENERAL_SKILL_PROVIDER_ID
        and isinstance(configured, Mapping)
    ):
        package_id = str(configured.get("package_id") or "").strip()
        version = str(configured.get("version") or "").strip()
        digest = str(configured.get("digest") or "").strip()
        contract_version = str(
            configured.get("package_contract_version") or ""
        ).strip()
        if package_id and version and digest and contract_version:
            return GeneralSkillResourceRef(
                catalog_binding_id=catalog_binding_id,
                package_id=package_id,
                version=version,
                digest=digest,
                package_contract_version=contract_version,
            )
    package = package_from_row(row)
    return GeneralSkillResourceRef(
        catalog_binding_id=catalog_binding_id,
        package_id=package.package_id,
        version=package.version,
        digest=package.digest,
        package_contract_version=package.package_contract_version,
    )


def runtime_snapshot_from_package(
    row: GeneralSkill,
    package: GeneralSkillPackage,
) -> GeneralSkillRuntimeSnapshot:
    """把包与数据行转成不可变运行快照，文件与各 JSON 字段均以 MappingProxyType 包装。"""
    files = tuple(
        MappingProxyType(
            {
                "path": item.path,
                "content": item.content,
                "size": item.size,
                "mime_type": item.mime_type,
            }
        )
        for item in package.files
    )
    return GeneralSkillRuntimeSnapshot(
        tenant_id=row.tenant_id,
        slug=package.slug,
        name=row.name,
        description=row.description,
        homepage=row.homepage,
        skill_markdown=package.skill_markdown,
        skill_files_json=files,
        metadata_json=MappingProxyType(dict(row.metadata_json or {})),
        permissions_json=MappingProxyType(dict(row.permissions_json or {})),
        runtime_config_json=MappingProxyType(dict(row.runtime_config_json or {})),
        status=row.status,
        package_id=package.package_id,
        package_version=package.version,
        package_digest=package.digest,
    )


def local_runtime_snapshot(row: GeneralSkill) -> GeneralSkillRuntimeSnapshot:
    """直接由数据行生成运行快照。"""
    return runtime_snapshot_from_package(row, package_from_row(row))


def _summary(row: GeneralSkill) -> GeneralSkillSummary:
    """由数据行构造列表用的技能摘要，版本与摘要复用 package_from_row 的结果。"""
    package = package_from_row(row)
    return GeneralSkillSummary(
        slug=row.slug,
        version=package.version,
        name=row.name,
        package_id=package.package_id,
        digest=package.digest,
    )


def _package_version(row: GeneralSkill) -> str:
    """取 metadata 中的 version；缺失时回退 updated_at 的 ISO 时间，再缺失用 legacy-v1。"""
    metadata = row.metadata_json if isinstance(row.metadata_json, Mapping) else {}
    configured = str(metadata.get("version") or "").strip()
    if configured:
        return configured
    updated_at = getattr(row, "updated_at", None)
    return updated_at.isoformat() if updated_at is not None else "legacy-v1"


def _files_from_row(row: GeneralSkill) -> tuple[GeneralSkillFile, ...]:
    """把 skill_files_json 解析成文件列表；无有效文件时回退为单个 SKILL.md。"""
    raw_files = row.skill_files_json
    files: list[GeneralSkillFile] = []
    if isinstance(raw_files, Sequence) and not isinstance(raw_files, (str, bytes)):
        for raw in raw_files:
            if not isinstance(raw, Mapping):
                continue
            path = str(raw.get("path") or "").strip()
            if not path:
                continue
            content = str(raw.get("content") or "")
            size = raw.get("size")
            files.append(
                GeneralSkillFile(
                    path=path,
                    content=content,
                    size=int(size) if isinstance(size, (int, float)) else len(content.encode()),
                    mime_type=str(raw.get("mime_type")) if raw.get("mime_type") else None,
                )
            )
    if files:
        return tuple(files)
    markdown = str(row.skill_markdown or "")
    return (
        GeneralSkillFile(
            path="SKILL.md",
            content=markdown,
            size=len(markdown.encode()),
            mime_type="text/markdown",
        ),
    )
