# StaffDeck M3 知识同步 API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/api/v1` 上交付知识同步能力（知识包清单 / 包导出 / 概念文档下载），让 deepchat 能按 hash 增量拉取知识并在本地重建检索索引，同时保持"凭证与未授权知识不出服务端"。

**Architecture:** 复用既有的 OKF（Open Knowledge Format）导出内核 `app/knowledge/okf.py`（`export_okf_bundle` / `build_index_markdown` / `upsert_concepts` 已存在），新增 `app/digital_employees/knowledge_sync.py` 纯函数层负责清单组装与概念 hash，新增 `app/public_api/knowledge_sync.py` 只做鉴权、HTTP 语义与审计。**不新建导出格式**——OKF bundle 就是传输格式。

**Tech Stack:** Python 3.11 + FastAPI + SQLModel/SQLAlchemy 2 + pytest；zip 用标准库 `zipfile`；hash 用 `hashlib`

**Spec:** `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（§6.2 通道②、§11 里程碑 M3）

**Prerequisite:** M2 已交付（`app/digital_employees/{contract,payload,sop_text,skill_package}.py`、`app/public_api/digital_employees.py`、分发路由与 ETag 语义）。本计划复用其中的 `zod_len` / `clamp_text` 风格与 `visible_*` 可见性链路。

## Global Constraints

- 遵循 AGENTS.md：Conventional Commits（`feat(api): ...`）；Python ruff 100 字符行宽；后端测试目录 `backend/tests`；每个任务单独 commit，不 `--no-verify`
- 依赖不得新增：zip 用标准库 `zipfile`，hash 用标准库 `hashlib`，JSON 用标准库 `json`
- 本机 Python：`backend/.venv/Scripts/python.exe`（Windows）；`uv sync --frozen --extra dev` 是唯一可信安装方式；`backend/uv.lock` 是依赖唯一来源
- **零数据库迁移**：M3 不新增表、不改列。知识同步开关存 `KnowledgeBaseVersion.metadata_json["deepchat_sync_enabled"]`（与 M2 `payload._knowledge_base_ids` 完全同一条键，不得引入第二套开关）
- 鉴权沿用既有 scope：同步端点用 `agents:read`（spec §8 未引入新 scope）；必须是 `sd_live_` API key，不支持用户 JWT
- **导出必须留痕**：每次包导出与文档下载写一行 `APIAuditLog`（`resource_type="knowledge_base"` / `"knowledge_concept"`），spec §6.2 明确要求
- **未开启同步开关的知识库绝不导出**：即便调用方可见该 KB，只要 `metadata.deepchat_sync_enabled is not True`，一律 404（不泄露"存在但未授权导出"这一事实）
- 所有数据读取走 `app.agents.branching.visible_knowledge_base_versions`，保证与 M2 payload 口径一致

### 关键源码事实（实现前必读，均已核对）

1. **`KnowledgeDocument` 没有正文字段**。它只有 `filename` / `file_type` / `title` / `status` / `bucket_count` / `chunk_count` / `metadata_json`（含 `section_tree` / `char_count` / `chunk_stats`）。**文档正文存在于 `KnowledgeConcept.content_md`**（`concept_type="Source Document"` 的那条是整篇，`"Source Section"` 是分节）。
   - 因此 spec §6.2 的 `GET /knowledge-bases/{kb_id}/documents/{doc_id}:download` 若按"原始文件字节"实现是无解的——原始上传件在入库解析后就丢弃了。
   - **本计划把该端点定义为"概念文档下载"**：`GET /digital-employees/{id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download`，返回该 concept 的 `content_md`（`text/markdown`）。这既符合现有数据形态，也正好是 deepchat 本地重建索引所需的最小单元。spec 需同步勘误。
2. **OKF bundle 已存在**：`app/knowledge/okf.py:export_okf_bundle(kb, version_id, concepts, log_entries=None) -> bytes`，产物是 zip，根含 `index.md` + `log.md` + 每个 `{concept_id}.md`。既有端点 `GET /knowledge-bases/{kb_id}/okf/export`（`app/api/knowledge_bases.py:517`）走的是管理端鉴权。注意：`version_id` 形参在函数体里**并未被使用**（当前实现只写 `index.md`/`log.md`/concepts），调用时传正确的 version 只为未来兼容，不要依赖它影响产物。
3. **概念是懒生成的**：`app/api/knowledge_bases.py:_ensure_okf_concepts_for_version(db, tenant_id, kb_id, version_id)` 会遍历 `status="ready"` 的文档补 concepts。它是私有函数且被 4 处调用，**本计划把它提升为 `app/knowledge/concepts.py:ensure_okf_concepts_for_version` 并让原处委托**（与 M2 里 `visible_general_skill_rows` 的提取手法一致）。
4. **版本是分支化的**：`visible_knowledge_base_versions` 对非整体员工返回的是 `AgentKnowledgeBranch` 解析出的**分支版本**（如 `1.0.0-branch.agent_xxx.1`），不是 KB 的 `current_version`。种子数据实测：19 个 KB / 33 个 version / 19 篇文档 / 298 个 concept，其中分支版本下挂着 17 个 concept。清单与导出必须用**同一个** version，否则 hash 对不上。
5. `KnowledgeConcept` 主键是 `id`，业务键是 `(tenant_id, knowledge_base_version_id, concept_id)`；`concept_id` 形如 `sources/人事-薪酬福利与证明办理指南/sections/sec-1`（含中文与斜杠，用作 URL 参数时必须 encode）。

### 传输契约（本次新增，deepchat 侧 D3 消费）

```
GET /digital-employees/{agent_id}/knowledge-packages
  → 200 {
      packages: [{
        knowledgeBaseId, name, version, configHash,
        bundleUrl,           # 该 KB 的 OKF zip 下载地址
        concepts: [{ conceptId, type, title, hash, size }],
        conceptCount
      }],
      configVersion,         # 与 M2 同构，用于整包变更感知
      fetchedAt
    }

GET /digital-employees/{agent_id}/knowledge-packages/{kb_id}:export-bundle
  → 200 application/zip（OKF：index.md + log.md + {concept_id}.md）

GET /digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download
  → 200 text/markdown（concept.content_md），ETag 为该 concept 的 hash
```

- 两者均需 `agents:read`；写 `APIAuditLog`
- 清单自身支持 `ETag` / `If-None-Match`（复用 M2 的 `if_none_match_hit`）
- `hash` = `sha256(content_md)` 前 16 位，十六进制；`size` = `zod_len(content_md)`（UTF-8 字节数更适合传输估算，故此处用 `len(content_md.encode("utf-8"))`）

---

### Task 1: 提升概念懒生成函数为可复用模块

**Files:**
- Create: `backend/app/knowledge/concepts.py`
- Modify: `backend/app/api/knowledge_bases.py`（`_ensure_okf_concepts_for_version` 改为委托）
- Test: `backend/tests/test_knowledge_concepts_module.py`

**Interfaces:**
- Produces: `app.knowledge.concepts.ensure_okf_concepts_for_version(db: Session, tenant_id: str, knowledge_base_id: str, version_id: str) -> int`（返回本次新建的 concept 数，便于测试与观测）
- 保持 `app.api.knowledge_bases._ensure_okf_concepts_for_version` 同名可调用（内部委托），4 处既有调用点不用改

- [ ] **Step 1: 写失败测试**

`backend/tests/test_knowledge_concepts_module.py`

```python
from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import AgentKnowledgeBranch, KnowledgeBase, KnowledgeConcept
from app.db.seed import seed_demo_data
from app.knowledge.concepts import ensure_okf_concepts_for_version


def _session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def test_returns_zero_when_concepts_already_exist():
    session = _session()
    branch = session.exec(select(AgentKnowledgeBranch)).first()
    # 种子数据里 concepts 已经生成过，重复调用必须是幂等的
    created = ensure_okf_concepts_for_version(
        session, branch.tenant_id, branch.knowledge_base_id, _branch_version_id(session, branch)
    )
    assert created == 0


def test_creates_concepts_for_a_version_that_has_none():
    session = _session()
    branch = session.exec(select(AgentKnowledgeBranch)).first()
    version_id = _branch_version_id(session, branch)
    session.exec(  # type: ignore[call-overload]
        select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version_id)
    )
    for row in session.exec(
        select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version_id)
    ).all():
        session.delete(row)
    session.commit()

    created = ensure_okf_concepts_for_version(
        session, branch.tenant_id, branch.knowledge_base_id, version_id
    )
    assert created > 0
    remaining = session.exec(
        select(KnowledgeConcept).where(KnowledgeConcept.knowledge_base_version_id == version_id)
    ).all()
    assert remaining


def _branch_version_id(session: Session, branch: AgentKnowledgeBranch) -> str:
    """The branch resolves to its own version row, not the KB's current version."""
    kb = session.get(KnowledgeBase, branch.knowledge_base_id)
    assert kb is not None
    from app.agents.branching import visible_knowledge_base_versions

    versions = visible_knowledge_base_versions(session, branch.tenant_id, branch.agent_id)
    return versions[branch.knowledge_base_id].id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_knowledge_concepts_module.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.knowledge.concepts'`

- [ ] **Step 3: 迁出实现**

`backend/app/knowledge/concepts.py`（把 `app/api/knowledge_bases.py:821-868` 的逻辑原样搬出，补返回值）：

```python
from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import KnowledgeBucket, KnowledgeConcept, KnowledgeDocument
from app.knowledge.okf import build_okf_for_document, upsert_concepts


def ensure_okf_concepts_for_version(
    db: Session,
    tenant_id: str,
    knowledge_base_id: str,
    version_id: str,
) -> int:
    """Materialise OKF concepts for every ready document of one KB version.

    Idempotent: documents that already have concepts for this version are skipped,
    so the management endpoints can call this on every read without churn.
    Returns the number of documents that produced new concepts.
    """
    documents = db.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.knowledge_base_version_id == version_id,
            KnowledgeDocument.status == "ready",
        )
    ).all()
    touched = 0
    for document in documents:
        existing = db.exec(
            select(KnowledgeConcept.id).where(
                KnowledgeConcept.tenant_id == tenant_id,
                KnowledgeConcept.knowledge_base_id == knowledge_base_id,
                KnowledgeConcept.knowledge_base_version_id == version_id,
                KnowledgeConcept.document_id == document.id,
            )
        ).first()
        if existing:
            continue
        metadata = document.metadata_json or {}
        section_nodes = (
            metadata.get("section_tree") if isinstance(metadata.get("section_tree"), list) else []
        )
        buckets = db.exec(
            select(KnowledgeBucket)
            .where(
                KnowledgeBucket.tenant_id == tenant_id,
                KnowledgeBucket.knowledge_base_id == knowledge_base_id,
                KnowledgeBucket.knowledge_base_version_id == version_id,
                KnowledgeBucket.document_id == document.id,
            )
            .order_by(KnowledgeBucket.created_at.asc())
        ).all()
        if not section_nodes and not buckets:
            continue
        upsert_concepts(
            db,
            tenant_id,
            knowledge_base_id,
            version_id,
            build_okf_for_document(document, section_nodes, buckets),
        )
        touched += 1
    return touched
```

`backend/app/api/knowledge_bases.py` 把原函数体替换为委托（保留函数名与签名，4 处调用点不动）：

```python
def _ensure_okf_concepts_for_version(
    db: Session,
    tenant_id: str,
    knowledge_base_id: str,
    version_id: str,
) -> None:
    ensure_okf_concepts_for_version(db, tenant_id, knowledge_base_id, version_id)
```

并在文件顶部导入区加入 `from app.knowledge.concepts import ensure_okf_concepts_for_version`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_knowledge_concepts_module.py tests/test_capability_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/concepts.py backend/app/api/knowledge_bases.py backend/tests/test_knowledge_concepts_module.py
git commit -m "refactor(knowledge): expose OKF concept materialisation for reuse"
```

---

### Task 2: 知识包清单组装（纯函数层）

**Files:**
- Create: `backend/app/digital_employees/knowledge_sync.py`
- Test: `backend/tests/test_digital_employee_knowledge_sync.py`

**Interfaces:**
- Consumes: `app.agents.branching.visible_knowledge_base_versions`、`app.knowledge.concepts.ensure_okf_concepts_for_version`
- Produces: `content_hash(value: str) -> str`（`sha256` 前 16 位 hex）
- Produces: `build_knowledge_packages(principal, agent, db) -> list[dict]`（每项见传输契约）
- Produces: `build_knowledge_package_list(principal, agent, db) -> dict`（`{"packages": [...], "configVersion": "kv1:<12hex>"}`）
- Produces: `select_package(principal, agent, db, knowledge_base_id) -> dict | None`
- Produces: `select_concept(principal, agent, db, knowledge_base_id, concept_id) -> KnowledgeConcept | None`
- 常量 `SYNC_FLAG = "deepchat_sync_enabled"`（与 `payload.py` 同为一条键）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_digital_employee_knowledge_sync.py`

```python
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentKnowledgeBranch, AgentProfile, User
from app.db.seed import seed_demo_data
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    build_knowledge_packages,
    content_hash,
    select_concept,
    select_package,
)
from app.public_api.auth import PublicPrincipal


@pytest.fixture()
def seeded():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        principal = PublicPrincipal(
            tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"})
        )
        yield db, principal


def _agent_with_kb(db: Session) -> AgentProfile:
    for agent in db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all():  # noqa: E712
        if db.exec(
            select(AgentKnowledgeBranch).where(AgentKnowledgeBranch.agent_id == agent.id)
        ).first():
            return agent
    raise AssertionError("seed data must attach a knowledge base to at least one employee")


def _enable_sync(db: Session, tenant_id: str, agent_id: str) -> str:
    """Flip the opt-in flag on whatever version this agent actually resolves to."""
    versions = visible_knowledge_base_versions(db, tenant_id, agent_id)
    kb_id, version = next(iter(versions.items()))
    version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
    db.add(version)
    db.commit()
    return kb_id


def test_no_packages_until_the_sync_flag_is_set(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    assert build_knowledge_packages(principal, agent, db) == []


def test_enabled_knowledge_base_is_listed_with_hashed_concepts(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)

    packages = build_knowledge_packages(principal, agent, db)
    assert len(packages) == 1
    package = packages[0]
    assert package["knowledgeBaseId"] == kb_id
    assert package["version"]
    assert package["bundleUrl"].endswith(f"{kb_id}:export-bundle")
    assert package["conceptCount"] == len(package["concepts"]) > 0
    for concept in package["concepts"]:
        assert concept["conceptId"]
        assert concept["hash"]
        assert concept["size"] > 0
        assert concept["type"]


def test_package_hash_is_stable_across_calls(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    first = build_knowledge_packages(principal, agent, db)
    second = build_knowledge_packages(principal, agent, db)
    assert first == second


def test_list_wraps_packages_with_a_config_version(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    _enable_sync(db, principal.tenant_id, agent.id)
    payload = build_knowledge_package_list(principal, agent, db)
    assert payload["configVersion"].startswith("kv1:")
    assert len(payload["packages"]) == 1


def test_select_package_refuses_an_unflagged_base(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = next(iter(visible_knowledge_base_versions(db, principal.tenant_id, agent.id)))
    assert select_package(principal, agent, db, kb_id) is None


def test_select_concept_rejects_a_concept_from_another_base(seeded):
    db, principal = seeded
    agent = _agent_with_kb(db)
    kb_id = _enable_sync(db, principal.tenant_id, agent.id)
    package = build_knowledge_packages(principal, agent, db)[0]
    concept_id = package["concepts"][0]["conceptId"]
    assert select_concept(principal, agent, db, kb_id, concept_id) is not None
    assert select_concept(principal, agent, db, kb_id, "sources/does-not-exist") is None


def test_content_hash_is_sha256_prefixed_hex():
    digest = content_hash("# 标题\n\n正文")
    assert len(digest) == 16
    assert all(char in "0123456789abcdef" for char in digest)
    assert content_hash("same") == content_hash("same")
    assert content_hash("a") != content_hash("b")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_knowledge_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.digital_employees.knowledge_sync'`

- [ ] **Step 3: 实现纯函数层**

`backend/app/digital_employees/knowledge_sync.py`

```python
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentProfile, KnowledgeBase, KnowledgeConcept
from app.knowledge.concepts import ensure_okf_concepts_for_version

if TYPE_CHECKING:
    # Imported for typing only: app.public_api.__init__ eagerly imports the whole
    # Open API app, so a runtime import here would close an import cycle.
    from app.public_api.auth import PublicPrincipal

SYNC_FLAG = "deepchat_sync_enabled"


def content_hash(value: str) -> str:
    """Short, stable digest of a concept body: deepchat compares these to skip work."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_knowledge_packages(
    principal: PublicPrincipal, agent: AgentProfile, db: Session
) -> list[dict[str, Any]]:
    """Every opt-in knowledge base this employee can use, with per-concept hashes."""
    versions = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)
    packages: list[dict[str, Any]] = []
    for kb_id, version in sorted(versions.items()):
        metadata = dict(getattr(version, "metadata_json", None) or {})
        if metadata.get(SYNC_FLAG) is not True:
            continue
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.tenant_id != principal.tenant_id:
            continue
        ensure_okf_concepts_for_version(db, principal.tenant_id, kb_id, version.id)
        concepts = _concepts(db, principal.tenant_id, kb_id, version.id)
        entries = [
            {
                "conceptId": row.concept_id,
                "type": row.concept_type,
                "title": row.title,
                "hash": content_hash(row.content_md),
                "size": len(row.content_md.encode("utf-8")),
            }
            for row in concepts
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
    for package in build_knowledge_packages(principal, agent, db):
        if package["knowledgeBaseId"] == knowledge_base_id:
            return package
    return None


def select_concept(
    principal: PublicPrincipal,
    agent: AgentProfile,
    db: Session,
    knowledge_base_id: str,
    concept_id: str,
) -> KnowledgeConcept | None:
    package = select_package(principal, agent, db, knowledge_base_id)
    if package is None:
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


def _concepts(
    db: Session, tenant_id: str, knowledge_base_id: str, version_id: str
) -> list[KnowledgeConcept]:
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
    seed = json.dumps(
        {"version": version, "concepts": [(item["conceptId"], item["hash"]) for item in entries]},
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _bundle_url(agent_id: str, knowledge_base_id: str) -> str:
    from app.config import get_settings

    base = get_settings().normalized_tool_base_url
    return (
        f"{base}/api/v1/digital-employees/{agent_id}"
        f"/knowledge-packages/{knowledge_base_id}:export-bundle"
    )
```

**注意**：base_url 的取法必须与 `payload.py:95` 完全一致（`get_settings().normalized_tool_base_url`），不要新增配置项。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_digital_employee_knowledge_sync.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/digital_employees/knowledge_sync.py backend/tests/test_digital_employee_knowledge_sync.py
git commit -m "feat(api): assemble hashed knowledge packages for opt-in bases"
```

---

### Task 3: 同步路由（清单 / 包导出 / 概念下载）

**Files:**
- Create: `backend/app/public_api/knowledge_sync.py`
- Modify: `backend/app/public_api/app.py`（注册 router）
- Test: `backend/tests/test_public_api_knowledge_sync.py`

**Interfaces:**
- Consumes: `knowledge_sync.build_knowledge_package_list` / `select_package` / `select_concept`、`okf.export_okf_bundle`、`public_api.digital_employees._distribution_agents`、`utils.etag_for` / `if_none_match_hit`
- Produces: `router`（`prefix="/digital-employees"`, `tags=["knowledge-sync"]`）
- HTTP 契约：
  - `GET /digital-employees/{agent_id}/knowledge-packages` → `200 {packages[], configVersion, fetchedAt}`；`If-None-Match` 命中 → `304`
  - `GET /digital-employees/{agent_id}/knowledge-packages/{kb_id}:export-bundle` → `200 application/zip`
  - `GET /digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download` → `200 text/markdown` + `ETag`
  - 员工不在分发集合 → `404 AGENT_NOT_FOUND`；KB 未开同步或不可见 → `404 KNOWLEDGE_PACKAGE_NOT_FOUND`；概念不存在 → `404 CONCEPT_NOT_FOUND`
  - 三者均写 `APIAuditLog`；均需 `agents:read`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_public_api_knowledge_sync.py`

```python
from __future__ import annotations

import io
import zipfile

from deepchat_contract import validate_digital_employee
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db import get_session
from app.db.models import APIAuditLog, AgentProfile, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token


def _client(monkeypatch) -> tuple[TestClient, object, str]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        admin = db.exec(select(User).where(User.username == "admin")).first()
        token = create_access_token(admin)

    app = create_public_api_app()

    def session_override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr("app.public_api.app.engine", engine)
    return TestClient(app), engine, token


def _key(client: TestClient, token: str, scopes: list[str]) -> str:
    created = client.post(
        "/api-clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "deepchat", "scopes": ["*"]},
    )
    assert created.status_code == 201, created.text
    credential = client.post(
        f"/api-clients/{created.json()['id']}/credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "deepchat runtime", "scopes": scopes},
    )
    assert credential.status_code == 201, credential.text
    return credential.json()["api_key"]


def _agent_with_kb(engine) -> tuple[str, str]:
    """Enable sync on one employee's knowledge base and return (agent_id, kb_id)."""
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        for agent in db.exec(select(AgentProfile)).all():
            if agent.is_overall:
                continue
            versions = visible_knowledge_base_versions(db, admin.tenant_id, agent.id)
            if not versions:
                continue
            kb_id, version = next(iter(versions.items()))
            version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
            db.add(version)
            db.commit()
            return agent.id, kb_id
    raise AssertionError("seed data must attach a knowledge base to at least one employee")


def test_packages_are_empty_before_the_opt_in(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        agent = next(
            row
            for row in db.exec(select(AgentProfile)).all()
            if not row.is_overall
            and visible_knowledge_base_versions(db, admin.tenant_id, row.id)
        )
    response = client.get(f"/digital-employees/{agent.id}/knowledge-packages", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["packages"] == []


def test_packages_list_and_export_round_trip(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)

    listed = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["configVersion"].startswith("kv1:")
    assert len(body["packages"]) == 1
    package = body["packages"][0]
    assert package["knowledgeBaseId"] == kb_id
    assert package["conceptCount"] > 0

    exported = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}:export-bundle", headers=auth
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        names = archive.namelist()
        assert "index.md" in names
        assert "log.md" in names

    concept = package["concepts"][0]
    single = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}"
        f"/concepts/{concept['conceptId']}:download",
        headers=auth,
    )
    assert single.status_code == 200, single.text
    assert single.headers["content-type"].startswith("text/markdown")
    assert single.headers["ETag"]
    assert single.text.strip()


def test_packages_etag_returns_304(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, _kb_id = _agent_with_kb(engine)
    first = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    etag = first.headers["ETag"]
    second = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages",
        headers={**auth, "If-None-Match": etag},
    )
    assert second.status_code == 304
    assert not second.content


def test_unflagged_package_export_is_404(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        agent = next(
            row
            for row in db.exec(select(AgentProfile)).all()
            if not row.is_overall
            and visible_knowledge_base_versions(db, admin.tenant_id, row.id)
        )
        kb_id = next(iter(visible_knowledge_base_versions(db, admin.tenant_id, agent.id)))
    response = client.get(
        f"/digital-employees/{agent.id}/knowledge-packages/{kb_id}:export-bundle", headers=auth
    )
    assert response.status_code == 404
    assert response.json()["code"] == "KNOWLEDGE_PACKAGE_NOT_FOUND"


def test_unknown_agent_is_404(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    response = client.get("/digital-employees/agent_missing/knowledge-packages", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"


def test_downloads_are_audited(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)
    package = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages", headers=auth
    ).json()["packages"][0]
    concept_id = package["concepts"][0]["conceptId"]
    client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}"
        f"/concepts/{concept_id}:download",
        headers=auth,
    )
    client.get("/digital-employees", headers=auth)  # 刷新审计中间件
    with Session(engine) as db:
        row = db.exec(
            select(APIAuditLog)
            .where(APIAuditLog.resource_type == "knowledge_concept")
            .order_by(APIAuditLog.created_at.desc())
        ).first()
    assert row is not None
    assert ":download" in row.path


def test_requires_agents_read_scope(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["runs:read"])
    response = client.get(
        "/digital-employees/whatever/knowledge-packages",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"
```

**注意**：Task 3 Step 3 的参考实现里出现的 `__import__(...)` 与函数内导入只是行文紧凑的权宜写法，**实做时一律改为文件顶部正常导入**（`KnowledgeBase`、`AgentProfile`、`utc_now`、`visible_knowledge_base_versions`、`concept_rows`），不要保留任何一种动态导入。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_knowledge_sync.py -q`
Expected: FAIL — 404（路由未注册）

- [ ] **Step 3: 实现路由**

`backend/app/public_api/knowledge_sync.py`

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import Response as RawResponse
from sqlmodel import Session

from app.db import get_session
from app.db.models import APIAuditLog
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    content_hash,
    select_concept,
    select_package,
)
from app.knowledge.okf import export_okf_bundle
from app.public_api.auth import PublicPrincipal, require_scopes
from app.public_api.digital_employees import _distribution_agents
from app.public_api.errors import PublicAPIError
from app.public_api.utils import etag_for, if_none_match_hit

router = APIRouter(prefix="/digital-employees", tags=["knowledge-sync"])


def _agent_or_404(db: Session, principal: PublicPrincipal, agent_id: str):
    agent = next((row for row in _distribution_agents(db, principal) if row.id == agent_id), None)
    if agent is None:
        raise PublicAPIError(
            404, "AGENT_NOT_FOUND", "No published digital employee matches this id."
        )
    return agent


def _package_or_404(db: Session, principal: PublicPrincipal, agent, knowledge_base_id: str):
    package = select_package(principal, agent, db, knowledge_base_id)
    if package is None:
        raise PublicAPIError(
            404,
            "KNOWLEDGE_PACKAGE_NOT_FOUND",
            "This knowledge base is not synced to the requested employee.",
        )
    return package


def _audit(db: Session, principal: PublicPrincipal, request: Request, path: str,
           resource_type: str, resource_id: str) -> None:
    db.add(
        APIAuditLog(
            tenant_id=principal.tenant_id,
            credential_id=principal.credential_id,
            request_id=str(getattr(request.state, "request_id", "")),
            method="GET",
            path=path,
            resource_type=resource_type,
            resource_id=resource_id,
            status_code=200,
            duration_ms=0.0,
        )
    )
    db.commit()


@router.get("/{agent_id}/knowledge-packages", response_model=dict)
def list_knowledge_packages(
    agent_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
):
    agent = _agent_or_404(db, principal, agent_id)
    core = build_knowledge_package_list(principal, agent, db)
    # Same rule as the employee endpoint: the ETag must not include fetchedAt.
    etag = etag_for(core)
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return {**core, "fetchedAt": _now_iso()}


@router.get("/{agent_id}/knowledge-packages/{knowledge_base_id}:export-bundle")
def export_knowledge_bundle(
    agent_id: str,
    knowledge_base_id: str,
    request: Request,
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
) -> RawResponse:
    agent = _agent_or_404(db, principal, agent_id)
    package = _package_or_404(db, principal, agent, knowledge_base_id)
    rows = _concept_rows(db, principal, agent, knowledge_base_id)
    kb = db.get(__import__("app.db.models", fromlist=["KnowledgeBase"]).KnowledgeBase, knowledge_base_id)
    if kb is None:
        raise PublicAPIError(404, "KNOWLEDGE_PACKAGE_NOT_FOUND", "Knowledge base not found.")
    data = export_okf_bundle(kb, package["version"], rows)
    _audit(
        db, principal, request, request.url.path, "knowledge_base", knowledge_base_id
    )
    filename = f"{kb.name or knowledge_base_id}-okf-{package['version']}.zip"
    return RawResponse(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{knowledge_base_id}.zip\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{agent_id}/knowledge-packages/{knowledge_base_id}/concepts/{concept_id}:download")
def download_concept(
    agent_id: str,
    knowledge_base_id: str,
    concept_id: str,
    request: Request,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    principal: PublicPrincipal = Depends(require_scopes("agents:read")),
    db: Session = Depends(get_session),
):
    agent = _agent_or_404(db, principal, agent_id)
    _package_or_404(db, principal, agent, knowledge_base_id)
    row = select_concept(principal, agent, db, knowledge_base_id, concept_id)
    if row is None:
        raise PublicAPIError(
            404, "CONCEPT_NOT_FOUND", "This concept is not part of the synced package."
        )
    etag = f'"{content_hash(row.content_md)}"'
    if if_none_match_hit(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    _audit(db, principal, request, request.url.path, "knowledge_concept", row.id)
    response.headers["ETag"] = etag
    return RawResponse(
        content=row.content_md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


def _concept_rows(db: Session, principal: PublicPrincipal, agent, knowledge_base_id: str):
    from app.agents.branching import visible_knowledge_base_versions
    from app.digital_employees.knowledge_sync import _concepts

    version = visible_knowledge_base_versions(db, principal.tenant_id, agent.id)[knowledge_base_id]
    return _concepts(db, principal.tenant_id, knowledge_base_id, version.id)


def _now_iso() -> str:
    from app.db.models import utc_now

    return utc_now().isoformat() + "Z"
```

**实现要点（不要照抄上面的权宜写法）**：
- 把 `KnowledgeBase`、`AgentProfile`、`utc_now`、`visible_knowledge_base_versions`、`_concepts` 一律在文件顶部正常导入，不要用 `__import__` / 函数内导入
- `quote` 需 `from urllib.parse import quote`
- `_concepts` 若要跨模块复用，在 `knowledge_sync.py` 中改名为 `concept_rows` 并公开，`knowledge_sync.py` 内部同步改名
- 路由注册顺序：`{agent_id}/knowledge-packages` 系列必须在 `digital_employees.py` 的 `/{agent_id}` 之前被匹配到，否则会被单员工路由吞掉。FastAPI 按注册顺序匹配，故 **`app.py` 中先 `include_router(knowledge_sync.router)` 再 `include_router(digital_employees.router)`**；同时 `_distribution_agents` 目前是 `digital_employees.py` 的私有函数，本任务把它改名为公开的 `distribution_agents` 并保留 `_distribution_agents` 别名

`backend/app/public_api/app.py`：导入元组加 `knowledge_sync`，并在 `include_router(digital_employees.router)` **之前**插入 `app.include_router(knowledge_sync.router)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_knowledge_sync.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 跑 M2 分发回归，确认路由顺序没打碎既有端点**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_digital_employees.py -q`
Expected: PASS（13 passed）

- [ ] **Step 6: Commit**

```bash
git add backend/app/public_api/knowledge_sync.py backend/app/public_api/digital_employees.py backend/app/public_api/app.py backend/tests/test_public_api_knowledge_sync.py
git commit -m "feat(api): sync knowledge packages and concept documents to deepchat"
```

---

### Task 4: 端到端验收与 spec 勘误

**Files:**
- Create: `backend/tests/test_knowledge_sync_e2e.py`
- Modify: `docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md`（§6.2 端点定义勘误）
- Modify: `docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md`（§5.4 知识同步任务对齐实现）

**Interfaces:**
- Consumes: 前三个任务全部产出
- Produces: 一条覆盖「opt-in → 清单 → bundle → 单概念 → ETag 增量」的端到端用例；两处 spec 勘误

- [ ] **Step 1: 写端到端测试**

`backend/tests/test_knowledge_sync_e2e.py`

```python
from __future__ import annotations

import io
import zipfile

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db.models import AgentKnowledgeBranch, AgentProfile, User
from app.db.seed import seed_demo_data
from app.digital_employees.knowledge_sync import (
    build_knowledge_package_list,
    content_hash,
    select_concept,
)
from app.public_api.auth import PublicPrincipal


def _seeded() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _principal(db: Session) -> PublicPrincipal:
    admin = db.exec(select(User).where(User.username == "admin")).first()
    assert admin is not None
    return PublicPrincipal(tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"}))


def test_every_synced_concept_is_addressable_and_hashed():
    session = _seeded()
    try:
        principal = _principal(session)
        enabled = 0
        for branch in session.exec(select(AgentKnowledgeBranch)).all():
            versions = visible_knowledge_base_versions(
                session, principal.tenant_id, branch.agent_id
            )
            version = versions.get(branch.knowledge_base_id)
            if version is None:
                continue
            version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
            session.add(version)
        session.commit()

        for agent in session.exec(select(AgentProfile)).all():
            if agent.is_overall:
                continue
            payload = build_knowledge_package_list(principal, agent, session)
            for package in payload["packages"]:
                enabled += 1
                for concept in package["concepts"]:
                    row = select_concept(
                        principal, agent, session, package["knowledgeBaseId"], concept["conceptId"]
                    )
                    assert row is not None
                    assert concept["hash"] == content_hash(row.content_md)
                    assert concept["size"] == len(row.content_md.encode("utf-8"))
        assert enabled > 0, "seed data must sync at least one knowledge package"
    finally:
        session.close()


def test_config_version_changes_when_a_concept_changes():
    session = _seeded()
    try:
        principal = _principal(session)
        branch = session.exec(select(AgentKnowledgeBranch)).first()
        versions = visible_knowledge_base_versions(session, principal.tenant_id, branch.agent_id)
        version = versions[branch.knowledge_base_id]
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
        session.add(version)
        session.commit()

        agent = session.get(AgentProfile, branch.agent_id)
        before = build_knowledge_package_list(principal, agent, session)

        from app.db.models import KnowledgeConcept

        row = session.exec(
            select(KnowledgeConcept).where(
                KnowledgeConcept.knowledge_base_version_id == version.id
            )
        ).first()
        row.content_md = row.content_md + "\n\n新增一段。"
        session.add(row)
        session.commit()

        after = build_knowledge_package_list(principal, agent, session)
        assert before["configVersion"] != after["configVersion"]
    finally:
        session.close()
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_knowledge_sync_e2e.py -q`
Expected: PASS

- [ ] **Step 3: 修 spec 勘误**

`docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md` §6.2 的端点块替换为：

```markdown
```
GET /digital-employees/{id}/knowledge-packages                       → 增量清单（含每个 concept 的 hash）
GET /digital-employees/{id}/knowledge-packages/{kb_id}:export-bundle  → OKF bundle（zip：index.md + log.md + concepts）
GET /digital-employees/{id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download → 单个 concept 的 markdown
```

- **勘误（2026-10-22 核对源码）**：原设计写的是 `documents/{doc_id}:download`，但 `KnowledgeDocument` 表**不存原始文件正文**（上传件解析后即丢弃，只留 `filename`/`file_type`/`metadata.section_tree`/`chunk_stats`）。正文的最小可寻址单元是 `KnowledgeConcept.content_md`，故端点改为按 concept 下载。传输格式沿用既有的 OKF（`app/knowledge/okf.py:export_okf_bundle`），不新建格式。
```

`docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md` §5.4 补一条实现对齐说明：

```markdown
- **实现对齐（2026-10-22）**：知识包以 OKF bundle（zip）传输，内含 `index.md` + `log.md` + 每概念一个 `{concept_id}.md`；清单里每个 concept 带 `hash`（`sha256` 前 16 位）与 `size`（UTF-8 字节数），本地据此跳过未变概念。同步开关是 `KnowledgeBaseVersion.metadata_json.deepchat_sync_enabled === true`，未开启的库在清单里根本不出现。
```

- [ ] **Step 4: 全量回归与 lint**

Run: `cd backend && ./.venv/Scripts/python.exe -m ruff check app/knowledge app/digital_employees app/public_api/knowledge_sync.py app/public_api/digital_employees.py tests/test_knowledge_*.py tests/test_digital_employee_knowledge_sync.py tests/test_public_api_knowledge_sync.py`
Expected: 仅剩 FastAPI 惯用的 B008

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_public_api_v1.py tests/test_capability_registry.py tests/test_capability_discovery.py tests/test_staffdeck_seed.py tests/test_knowledge_concepts_module.py tests/test_digital_employee_knowledge_sync.py tests/test_public_api_knowledge_sync.py tests/test_knowledge_sync_e2e.py tests/test_public_api_digital_employees.py tests/test_digital_employee_distribution_e2e.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_knowledge_sync_e2e.py docs/superpowers/specs/2026-10-22-staffdeck-server-side-design.md docs/superpowers/specs/2026-10-22-deepchat-side-integration-design.md
git commit -m "test(api): verify the knowledge sync path end to end

Corrects the spec: KnowledgeDocument stores no original bytes, so the
download endpoint addresses concepts (KnowledgeConcept.content_md) rather
than documents, and the wire format is the existing OKF bundle."
```

---

## Self-Review

**1. 覆盖度**
- §6.2 三个端点全部落地（清单 / bundle / 单概念），开关语义与 M2 `payload._knowledge_base_ids` 共用 `deepchat_sync_enabled` 一条键 ✓
- 审计留痕（spec §6.2 硬要求）在 Task 3 覆盖，Task 3 Step 1 有专门用例 ✓
- ETag 增量在清单与单概念两层都实现 ✓
- spec 勘误在 Task 4 显式收敛，避免文档继续误导 ✓

**2. 类型一致性**
- `build_knowledge_packages` 返回 `list[dict]`，`select_package` 返回其中一项，`select_concept` 返回 ORM 行 —— Task 3 全程按此签名调用 ✓
- `content_hash` 在 Task 2 定义、Task 3 的 ETag 与 Task 4 的断言共三处使用，签名始终 `(str) -> str` ✓
- `ensure_okf_concepts_for_version` 返回 `int`，Task 1 的委托层丢弃返回值（`-> None`），不破坏 4 处既有调用点 ✓

**3. 已知风险（按重要性）**
- **分支版本陷阱**：非整体员工解析出的 version 是 `1.0.0-branch.agent_xxx.1`，与 KB 的 `current_version` 不同。清单、导出、单概念**三处必须用同一个 version_id**，否则 hash 与 bundle 内容不一致。Task 2 的 `build_knowledge_packages` 在循环内取 `version.id` 并贯穿到底，`select_concept` 重新解析版本——两者指向同一行，但若中途发生 branch 重算仍可能不一致，D3 应把 `configHash` 当唯一变更依据。
- **路由顺序**：`knowledge_sync.router` 必须先于 `digital_employees.router` 注册，否则 `/{agent_id}` 会吞掉 `/{agent_id}/knowledge-packages`。Task 3 Step 5 用 M2 的 13 项回归专门守这条。
- **bundle 体积**：单 KB 的 OKF zip 未设上限。种子数据实测 17 个 concept / 单篇 5344 字符量级，尚可；若接入大库需加 `MAX_BUNDLE_BYTES` 并在超限时改为 413 + 提示分批拉 concept。本计划不实现，D3 联调后按真实体积决定。
- **`_concepts` 跨模块复用**：Task 3 的 `_concept_rows` 需要它。计划要求改名为公开的 `concept_rows`，实现时不要保留下划线私有名跨模块调用。
