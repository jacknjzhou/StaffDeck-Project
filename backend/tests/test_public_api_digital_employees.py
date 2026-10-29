from __future__ import annotations

import io
import zipfile

from deepchat_contract import validate_digital_employee
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import APIAuditLog, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token


def _client(monkeypatch) -> tuple[TestClient, object, str]:
    """构建基于内存数据库并注入演示数据的公开 API 测试客户端。"""
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
        """为测试注入独立数据库会话，避免访问真实数据库连接。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr("app.public_api.app.engine", engine)
    return TestClient(app), engine, token


def _key(client: TestClient, token: str, scopes: list[str]) -> str:
    """创建 API 客户端并签发带指定 scope 的 API Key，供鉴权用例复用。"""
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


def test_list_returns_every_gallery_employee_with_a_valid_contract(monkeypatch):
    """验证画廊列表返回全部 10 个员工，且每个载荷都符合数字员工契约。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configVersion"].startswith("v1:")
    assert payload["fetchedAt"]
    assert len(payload["employees"]) == 10
    for employee in payload["employees"]:
        validate_digital_employee(employee)
    assert all(employee["enabled"] for employee in payload["employees"])


def test_list_etag_returns_304_without_a_body(monkeypatch):
    """验证 ETag 命中时返回 304 且无响应体，弱校验器与多值列表同样命中。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}

    first = client.get("/digital-employees", headers=auth)
    etag = first.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')

    second = client.get("/digital-employees", headers={**auth, "If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["ETag"] == etag
    assert not second.content

    # 弱校验器与多值列表也必须命中（HTTP 语义要求）
    weak = client.get("/digital-employees", headers={**auth, "If-None-Match": f"W/{etag}"})
    assert weak.status_code == 304
    many = client.get("/digital-employees", headers={**auth, "If-None-Match": f'"nope", {etag}'})
    assert many.status_code == 304

    stale = client.get("/digital-employees", headers={**auth, "If-None-Match": '"stale"'})
    assert stale.status_code == 200


def test_etag_is_stable_across_calls(monkeypatch):
    """验证同一内容多次请求时 ETag 保持稳定。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    assert (
        client.get("/digital-employees", headers=auth).headers["ETag"]
        == client.get("/digital-employees", headers=auth).headers["ETag"]
    )


def test_single_employee_endpoint_matches_the_list_entry(monkeypatch):
    """验证单个员工端点返回的条目与列表中的对应条目完全一致。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    listed = client.get("/digital-employees", headers=auth).json()["employees"]
    target = listed[0]

    single = client.get(f"/digital-employees/{target['id']}", headers=auth)
    assert single.status_code == 200, single.text
    assert single.json()["employee"] == target
    assert single.headers["ETag"]


def test_unknown_agent_is_404(monkeypatch):
    """验证查询不存在的员工时返回 404 与 AGENT_NOT_FOUND。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    missing = client.get("/digital-employees/agent_missing", headers=auth)
    assert missing.status_code == 404
    assert missing.json()["code"] == "AGENT_NOT_FOUND"


def test_unpublished_agent_disappears_from_distribution(monkeypatch):
    """验证取消画廊发布后，该员工立即从列表与单条端点中消失。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read", "agents:write"])
    auth = {"Authorization": f"Bearer {key}"}
    listed = client.get("/digital-employees", headers=auth).json()["employees"]
    target_id = listed[0]["id"]

    # 用公开 API 取消画廊发布，分发集合必须同步收缩
    detail = client.get(f"/agents/{target_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    current = detail.json()
    patch = client.patch(
        f"/agents/{target_id}",
        headers={**auth, "If-Match": detail.headers["ETag"]},
        json={
            "metadata": {
                **(current.get("metadata") or {}),
                "published_to_gallery": False,
            }
        },
    )
    assert patch.status_code == 200, patch.text
    assert client.get(f"/digital-employees/{target_id}", headers=auth).status_code == 404
    remaining = client.get("/digital-employees", headers=auth).json()["employees"]
    assert target_id not in {item["id"] for item in remaining}


def test_requires_agents_read_scope(monkeypatch):
    """验证缺少 agents:read scope 的 API Key 访问画廊时返回 INSUFFICIENT_SCOPE。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["runs:read"])
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 403
    assert response.json()["code"] == "INSUFFICIENT_SCOPE"


def test_user_jwt_is_rejected(monkeypatch):
    """验证用户 JWT 不能访问公开 API，必须使用 API Key。"""
    client, _engine, token = _client(monkeypatch)
    response = client.get("/digital-employees", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "API_KEY_REQUIRED"


def test_openapi_documents_the_distribution_endpoints(monkeypatch):
    """验证 OpenAPI 文档包含列表与单条数字员工分发端点。"""
    client, _engine, _token = _client(monkeypatch)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/digital-employees" in paths
    assert "/digital-employees/{agent_id}" in paths


def _first_employee_with_skills(client: TestClient, auth: dict) -> dict:
    """取出首个绑定了技能的员工，供导出技能用例复用。"""
    employees = client.get("/digital-employees", headers=auth).json()["employees"]
    return next(item for item in employees if item["skills"])


def test_export_skill_returns_importable_zip(monkeypatch):
    """验证导出技能返回可导入的 ZIP，内含 SKILL.md 且文件名带 slug。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    target = _first_employee_with_skills(client, auth)
    skill = target["skills"][0]

    exported = client.get(
        f"/digital-employees/{target['id']}:export-skill",
        params={"slug": skill["slug"]},
        headers=auth,
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert skill["slug"] in exported.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "SKILL.md" in archive.namelist()


def test_export_skill_rejects_an_unrelated_employee(monkeypatch):
    """验证对不存在的员工导出技能时返回 AGENT_NOT_FOUND。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    response = client.get(
        "/digital-employees/agent_missing:export-skill",
        params={"slug": "whatever"},
        headers=auth,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"


def test_export_skill_for_an_undistributed_skill_is_404(monkeypatch):
    """验证导出该员工未分发的技能时返回 SKILL_NOT_FOUND。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    target = _first_employee_with_skills(client, auth)
    response = client.get(
        f"/digital-employees/{target['id']}:export-skill",
        params={"slug": "not-a-real-skill"},
        headers=auth,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "SKILL_NOT_FOUND"


def test_export_skill_is_audited(monkeypatch):
    """验证导出技能会写入 resource_type 为 general_skill 的审计日志。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["agents:read"])
    auth = {"Authorization": f"Bearer {key}"}
    target = _first_employee_with_skills(client, auth)
    slug = target["skills"][0]["slug"]
    client.get(
        f"/digital-employees/{target['id']}:export-skill",
        params={"slug": slug},
        headers=auth,
    )
    # 审计中间件在响应返回后才写，用一个必然成功的请求刷新一次
    client.get("/digital-employees", headers=auth)
    with Session(engine) as db:
        row = db.exec(
            select(APIAuditLog)
            .where(APIAuditLog.resource_type == "general_skill")
            .order_by(APIAuditLog.created_at.desc())
        ).first()
    assert row is not None
    assert row.resource_id
    assert ":export-skill" in row.path
