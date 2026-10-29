from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_knowledge_base_versions
from app.db import get_session
from app.db.models import AgentProfile, APIAuditLog, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token


def _client(monkeypatch) -> tuple[TestClient, object, str]:
    """建内存库并写入种子数据，覆盖 get_session 依赖与 public_api 的 engine，返回客户端、engine 与 admin 令牌。"""
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
        """把应用的 get_session 依赖换成同一内存引擎上的会话，让测试复用数据。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr("app.public_api.app.engine", engine)
    return TestClient(app), engine, token


def _key(client: TestClient, token: str, scopes: list[str]) -> str:
    """通过公开 API 创建 api-client 及其凭据，返回带指定 scopes 的 api_key。"""
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


def _employee_with_kb(engine) -> tuple[str, str]:
    """返回种子数据中第一个能看见知识库的员工，结果为由 (agent_id, kb_id) 组成的元组。"""
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        for agent in db.exec(select(AgentProfile)).all():
            if agent.is_overall:
                continue
            versions = visible_knowledge_base_versions(db, admin.tenant_id, agent.id)
            if versions:
                return agent.id, next(iter(versions))
    raise AssertionError("seed data must attach a knowledge base to at least one employee")


def _agent_with_kb(engine) -> tuple[str, str]:
    """在 _employee_with_kb 基础上把该版本显式标为 deepchat_sync_enabled=True。

    同步是 opt-out，这不改变可见性，只是确认显式 True 仍可见。
    """
    agent_id, kb_id = _employee_with_kb(engine)
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        version = visible_knowledge_base_versions(db, admin.tenant_id, agent_id)[kb_id]
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": True}
        db.add(version)
        db.commit()
    return agent_id, kb_id


def _opt_out(engine, agent_id: str, kb_id: str) -> None:
    """把指定知识库版本显式标为 deepchat_sync_enabled=False，使其退出 deepchat 同步。"""
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        version = visible_knowledge_base_versions(db, admin.tenant_id, agent_id)[kb_id]
        version.metadata_json = {**(version.metadata_json or {}), "deepchat_sync_enabled": False}
        db.add(version)
        db.commit()


def _package_for(body: dict, kb_id: str) -> dict:
    """从列表响应中取出指定 knowledgeBaseId 的知识包。"""
    return next(row for row in body["packages"] if row["knowledgeBaseId"] == kb_id)


def test_packages_are_listed_without_an_opt_in(monkeypatch):
    """验证未做任何显式开关操作时知识包同样可见，即同步默认开启。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, _kb_id = _employee_with_kb(engine)
    response = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["packages"]


def test_packages_list_and_export_round_trip(monkeypatch):
    """验证列表、bundle 导出（zip 内含 index.md 与 log.md）与单概念下载三者一致可用。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)

    listed = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["configVersion"].startswith("kv1:")
    assert body["fetchedAt"]
    package = _package_for(body, kb_id)
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
        assert archive.testzip() is None

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
    """验证带 If-None-Match 的重复请求返回 304，ETag 不变且响应体为空。"""
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
    assert second.headers["ETag"] == etag
    assert not second.content


def test_concept_download_supports_conditional_requests(monkeypatch):
    """验证单概念下载同样支持 If-None-Match 条件请求并返回 304。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)
    body = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth).json()
    concept_id = _package_for(body, kb_id)["concepts"][0]["conceptId"]
    url = f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download"
    first = client.get(url, headers=auth)
    etag = first.headers["ETag"]
    second = client.get(url, headers={**auth, "If-None-Match": etag})
    assert second.status_code == 304
    assert not second.content


def test_concept_metadata_lists_graph_fields(monkeypatch):
    """验证概念元数据返回与列表清单一致的 type/title/links 字段，且不泄露 contentMd。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)

    listed = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    package = _package_for(listed.json(), kb_id)
    response = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts", headers=auth
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["knowledgeBaseId"] == kb_id
    assert body["version"] == package["version"]
    assert body["conceptCount"] == package["conceptCount"] > 0
    entries = {row["conceptId"]: row for row in body["concepts"]}
    manifest = package["concepts"][0]
    entry = entries[manifest["conceptId"]]
    assert entry["type"] == manifest["type"]
    assert entry["title"] == manifest["title"]
    assert isinstance(entry["links"], list)
    # Bodies stay behind :download; metadata must not leak content_md.
    assert all("contentMd" not in row for row in body["concepts"])


def test_concept_metadata_respects_opt_out(monkeypatch):
    """验证显式 opt-out 后概念元数据接口返回 404 KNOWLEDGE_PACKAGE_NOT_FOUND。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _employee_with_kb(engine)
    _opt_out(engine, agent_id, kb_id)

    response = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts", headers=auth
    )
    assert response.status_code == 404
    assert response.json()["code"] == "KNOWLEDGE_PACKAGE_NOT_FOUND"


def test_concept_metadata_unknown_package_is_404(monkeypatch):
    """验证知识库 id 不存在时概念元数据接口返回 404 KNOWLEDGE_PACKAGE_NOT_FOUND。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, _kb_id = _agent_with_kb(engine)

    response = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/kb_missing/concepts", headers=auth
    )
    assert response.status_code == 404
    assert response.json()["code"] == "KNOWLEDGE_PACKAGE_NOT_FOUND"


def test_explicit_opt_out_hides_the_package(monkeypatch):
    """验证显式 opt-out 后该知识库从包列表消失，bundle 导出返回 404。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _employee_with_kb(engine)
    _opt_out(engine, agent_id, kb_id)

    listed = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    assert listed.status_code == 200, listed.text
    assert kb_id not in {row["knowledgeBaseId"] for row in listed.json()["packages"]}
    exported = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}:export-bundle", headers=auth
    )
    assert exported.status_code == 404
    assert exported.json()["code"] == "KNOWLEDGE_PACKAGE_NOT_FOUND"


def test_unknown_agent_is_404(monkeypatch):
    """验证未知员工的知识包与知识库目录接口都返回 404 AGENT_NOT_FOUND。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    response = client.get("/digital-employees/agent_missing/knowledge-packages", headers=auth)
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"
    catalog = client.get("/digital-employees/agent_missing/knowledge-bases", headers=auth)
    assert catalog.status_code == 404
    assert catalog.json()["code"] == "AGENT_NOT_FOUND"


def test_unknown_concept_is_404(monkeypatch):
    """验证不存在的概念下载返回 404 CONCEPT_NOT_FOUND。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)
    response = client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}"
        "/concepts/sources/does-not-exist:download",
        headers=auth,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "CONCEPT_NOT_FOUND"


def test_downloads_are_audited(monkeypatch):
    """验证概念下载会写入 resource_type 为 knowledge_concept、路径含 :download 的审计日志。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)
    body = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth).json()
    concept_id = _package_for(body, kb_id)["concepts"][0]["conceptId"]
    client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}/concepts/{concept_id}:download",
        headers=auth,
    )
    client.get("/digital-employees", headers=auth)  # refresh the audit middleware
    with Session(engine) as db:
        row = db.exec(
            select(APIAuditLog)
            .where(APIAuditLog.resource_type == "knowledge_concept")
            .order_by(APIAuditLog.created_at.desc())
        ).first()
    assert row is not None
    assert row.resource_id
    assert ":download" in row.path


def test_bundle_export_is_audited(monkeypatch):
    """验证 bundle 导出会写入 resource_type 为 knowledge_base、resource_id 为知识库 id 的审计日志。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _agent_with_kb(engine)
    client.get(
        f"/digital-employees/{agent_id}/knowledge-packages/{kb_id}:export-bundle", headers=auth
    )
    client.get("/digital-employees", headers=auth)
    with Session(engine) as db:
        row = db.exec(
            select(APIAuditLog)
            .where(APIAuditLog.resource_type == "knowledge_base")
            .order_by(APIAuditLog.created_at.desc())
        ).first()
    assert row is not None
    assert row.resource_id == kb_id


def test_knowledge_base_catalog_lists_the_console_stats(monkeypatch):
    """验证知识库目录返回 name/version/documentCount/chunkCount/description 等控制台统计字段。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _employee_with_kb(engine)
    response = client.get(f"/digital-employees/{agent_id}/knowledge-bases", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    entries = {row["knowledgeBaseId"]: row for row in body["knowledgeBases"]}
    assert kb_id in entries
    entry = entries[kb_id]
    assert entry["name"]
    assert entry["version"]
    assert isinstance(entry["documentCount"], int) and entry["documentCount"] >= 0
    assert isinstance(entry["chunkCount"], int) and entry["chunkCount"] >= 0
    assert "description" in entry


def test_catalog_ignores_the_sync_opt_out_flag(monkeypatch):
    """验证知识库目录不受 opt-out 影响，而知识包列表会排除被 opt-out 的库。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    agent_id, kb_id = _employee_with_kb(engine)
    _opt_out(engine, agent_id, kb_id)

    catalog = client.get(f"/digital-employees/{agent_id}/knowledge-bases", headers=auth)
    assert catalog.status_code == 200, catalog.text
    assert kb_id in {row["knowledgeBaseId"] for row in catalog.json()["knowledgeBases"]}
    packages = client.get(f"/digital-employees/{agent_id}/knowledge-packages", headers=auth)
    assert kb_id not in {row["knowledgeBaseId"] for row in packages.json()["packages"]}


def test_requires_agents_read_scope(monkeypatch):
    """验证缺少 agents:read scope 时知识包与知识库目录接口都返回 403 INSUFFICIENT_SCOPE。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["runs:read"])
    response = client.get(
        "/digital-employees/whatever/knowledge-packages",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"
    catalog = client.get(
        "/digital-employees/whatever/knowledge-bases",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert catalog.status_code == 403
    assert catalog.json()["code"] == "INSUFFICIENT_SCOPE"
