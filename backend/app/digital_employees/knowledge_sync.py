from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_versions
from app.config import get_settings
from app.db.models import AgentProfile, KnowledgeBase, KnowledgeConcept
from app.knowledge.concepts import ensure_okf_concepts_for_version

if TYPE_CHECKING:
    # Imported for typing only: app.public_api.__init__ eagerly imports the whole
    # Open API app, so a runtime import here would close an import cycle.
    from app.public_api.auth import PublicPrincipal

# Same key payload.py uses: one opt-out switch, never two.
SYNC_FLAG = "deepchat_sync_enabled"


def content_hash(value: str) -> str:
    """计算 concept 正文的短摘要：取 UTF-8 内容的 SHA-256 十六进制前 16 位。

    DeepChat 比对摘要判断内容是否变化，从而跳过重复下载。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_knowledge_packages(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> list[dict[str, Any]]:
    """构建该数字员工可同步的全部知识包。

    同步默认开启：只有版本 metadata 中 deepchat_sync_enabled 显式为 False 才排除。
    对每个可见版本先补齐 OKF concept，再汇总 concept 的 id、类型、标题、摘要与
    字节大小，并附上版本号、configHash 与 bundle 导出地址。
    """
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    packages: list[dict[str, Any]] = []
    for kb_id, version in sorted(versions.items()):
        metadata = dict(getattr(version, "metadata_json", None) or {})
        if metadata.get(SYNC_FLAG) is False:
            continue
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.tenant_id != principal.tenant_id:
            continue
        ensure_okf_concepts_for_version(db, principal.tenant_id, kb_id, version.id)
        entries = [
            {
                "conceptId": row.concept_id,
                "type": row.concept_type,
                "title": row.title,
                "hash": content_hash(row.content_md),
                "size": len(row.content_md.encode("utf-8")),
            }
            for row in concept_rows(db, principal.tenant_id, kb_id, version.id)
        ]
        packages.append(
            {
                "knowledgeBaseId": kb_id,
                "name": kb.name,
                "version": version.version,
                "configHash": _package_hash(version.version, entries),
                "bundleUrl": _bundle_url(agent.id, kb_id),
                "concepts": entries,
                "conceptCount": len(entries),
            }
        )
    return packages


def build_knowledge_package_list(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> dict[str, Any]:
    """组装知识包列表，并生成 configVersion（形如 kv1:<12 位摘要>）。

    摘要取值于各知识包 knowledgeBaseId 与 configHash 排序后的 JSON，只要内容不变
    该版本号就保持稳定。
    """
    packages = build_knowledge_packages(principal, agent, db)
    seed = json.dumps(
        sorted((item["knowledgeBaseId"], item["configHash"]) for item in packages),
        separators=(",", ":"),
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return {"packages": packages, "configVersion": f"kv1:{digest}"}


def select_package(
    principal: PublicPrincipal,
    agent: AgentProfile,
    db: Session,
    knowledge_base_id: str,
) -> dict[str, Any] | None:
    """在重建出的知识包中按 knowledge_base_id 查找，未命中或被 opt-out 排除时返回 None。"""
    return next(
        (
            package
            for package in build_knowledge_packages(principal, agent, db)
            if package["knowledgeBaseId"] == knowledge_base_id
        ),
        None,
    )


def select_concept(
    principal: PublicPrincipal,
    agent: AgentProfile,
    db: Session,
    knowledge_base_id: str,
    concept_id: str,
) -> KnowledgeConcept | None:
    """读取知识包内单个 concept。

    先确认该知识库确实已同步给员工（含 opt-out 判断），再按租户、知识库、版本、
    concept_id 取 status 为 active 的记录；任一环节不满足则返回 None。
    """
    if select_package(principal, agent, db, knowledge_base_id) is None:
        return None
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    version = versions.get(knowledge_base_id)
    if version is None:
        return None
    return db.exec(
        select(KnowledgeConcept).where(
            KnowledgeConcept.tenant_id == principal.tenant_id,
            KnowledgeConcept.knowledge_base_id == knowledge_base_id,
            KnowledgeConcept.knowledge_base_version_id == version.id,
            KnowledgeConcept.concept_id == concept_id,
            KnowledgeConcept.status == "active",
        )
    ).first()


def concept_rows(
    db: Session, tenant_id: str, knowledge_base_id: str, version_id: str
) -> list[KnowledgeConcept]:
    """按租户、知识库与版本列出 status 为 active 的 concept，并按 concept_id 升序返回。"""
    return list(
        db.exec(
            select(KnowledgeConcept)
            .where(
                KnowledgeConcept.tenant_id == tenant_id,
                KnowledgeConcept.knowledge_base_id == knowledge_base_id,
                KnowledgeConcept.knowledge_base_version_id == version_id,
                KnowledgeConcept.status == "active",
            )
            .order_by(KnowledgeConcept.concept_id)
        ).all()
    )


def _package_hash(version: str, entries: list[dict[str, Any]]) -> str:
    """对版本号与各 concept 的 id/摘要生成带 sha256: 前缀的 16 位配置摘要。"""
    seed = json.dumps(
        {"version": version, "concepts": [(item["conceptId"], item["hash"]) for item in entries]},
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _bundle_url(agent_id: str, knowledge_base_id: str) -> str:
    """拼接该知识包的 OKF bundle 导出地址。

    基地址取自 settings.normalized_tool_base_url，路径带数字员工与知识库 id。
    """
    base = get_settings().normalized_tool_base_url
    return (
        f"{base}/api/v1/digital-employees/{agent_id}"
        f"/knowledge-packages/{knowledge_base_id}:export-bundle"
    )
