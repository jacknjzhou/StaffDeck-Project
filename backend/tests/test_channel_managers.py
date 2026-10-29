from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.channels as channels_api
from app.db import get_session
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelBindCode,
    ChannelBindingManager,
    Tenant,
    User,
)
from app.security.auth import create_access_token


def _engine():
    """创建共享连接的内存 SQLite 引擎并建表，供渠道接口测试复用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine) -> TestClient:
    """构建挂载渠道路由的测试应用，并用内存引擎覆盖数据库会话依赖。"""
    app = FastAPI()
    app.include_router(channels_api.router)

    def override_session():
        """覆盖数据库依赖，为每个请求提供独立的 Session。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _seed(engine) -> dict[str, User]:
    """写入租户、所有者、普通用户、管理员与外部用户，返回按角色索引的字典。"""
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
        other = User(id="user_other", tenant_id="tenant_demo", username="other", password_hash="x")
        admin = User(id="user_admin", tenant_id="tenant_demo", username="admin", role="admin", password_hash="x")
        outsider = User(id="user_outsider", tenant_id="tenant_demo", username="outsider", password_hash="x")
        db.add(
            AgentProfile(
                id="agent_1",
                tenant_id="tenant_demo",
                name="客服员工",
                is_overall=False,
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.add_all([owner, other, admin, outsider])
        db.commit()
        for u in (owner, other, admin, outsider):
            db.refresh(u)
            db.expunge(u)
        return {"owner": owner, "other": other, "admin": admin, "outsider": outsider}


def _auth(user: User) -> dict[str, str]:
    """生成携带该用户身份的 Bearer 认证请求头。"""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _seed_binding(
    engine,
    *,
    channel: str = "feishu",
    created_by: str = "user_owner",
    status: str = "active",
) -> str:
    """插入一条渠道绑定记录，返回其 id 供后续接口调用使用。"""
    with Session(engine) as db:
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel=channel,
            status=status,
            created_by_user_id=created_by,
        )
        db.add(binding)
        db.commit()
        return binding.id


def _add_collaborator(engine, binding_id: str, user_id: str, *, granted_by: str = "user_owner") -> None:
    """直接写入一条协作者授权记录，绕开接口校验以构造前置状态。"""
    with Session(engine) as db:
        db.add(
            ChannelBindingManager(
                tenant_id="tenant_demo",
                binding_id=binding_id,
                user_id=user_id,
                granted_by_user_id=granted_by,
            )
        )
        db.commit()


def test_collaborator_can_save_feishu_credentials(monkeypatch) -> None:
    """验证协作者提交飞书凭据后待激活绑定转为 active，且自身角色为 collaborator。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    monkeypatch.setattr(
        channels_api,
        "validate_feishu_credentials",
        lambda app_id, secret: {"bot_open_id": "ou_bot", "bot_name": "StaffDeck Bot"},
    )
    binding_id = _seed_binding(engine, status="pending")
    client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    resp = client.post(
        f"/api/enterprise/channels/{binding_id}/feishu/credentials",
        json={"tenant_id": "tenant_demo", "app_id": "cli_1", "app_secret": "sec"},
        headers=_auth(users["other"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert resp.json()["my_role"] == "collaborator"


def test_collaborator_can_manage_agents() -> None:
    """验证协作者可修改绑定的路由配置，并拿到更新后的 auto_route。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    _add_collaborator(engine, binding_id, "user_other")
    resp = client.put(
        f"/api/enterprise/channels/{binding_id}",
        json={"auto_route": False},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_route"] is False


def test_collaborator_can_toggle_status(monkeypatch) -> None:
    """验证协作者可反复切换绑定状态，在 disabled 与 active 之间往返。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    binding_id = _seed_binding(engine, status="active")
    _add_collaborator(engine, binding_id, "user_other")
    disable = client.post(
        f"/api/enterprise/channels/{binding_id}/toggle-status",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    )
    assert disable.status_code == 200, disable.text
    assert disable.json()["status"] == "disabled"
    enable = client.post(
        f"/api/enterprise/channels/{binding_id}/toggle-status",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    )
    assert enable.status_code == 200, enable.text
    assert enable.json()["status"] == "active"


def test_manager_can_invite_internal_user_to_bind_identity() -> None:
    """验证管理者可为内部用户生成 6 位身份绑定码，且无权者发起邀请返回 403。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        binding.identity_scope_key = "cli_feishu:tenant_a"
        binding.credentials_enc = "encrypted-secret"
        db.add(binding)
        db.commit()

    invited = client.post(
        f"/api/enterprise/channels/{binding_id}/identity-bind-code",
        params={"tenant_id": "tenant_demo"},
        json={"user_id": "user_other"},
        headers=_auth(users["owner"]),
    )
    assert invited.status_code == 200, invited.text
    assert len(invited.json()["code"]) == 6
    with Session(engine) as db:
        record = db.exec(
            select(ChannelBindCode).where(ChannelBindCode.user_id == "user_other")
        ).one()
        assert record.code == invited.json()["code"]

    forbidden = client.post(
        f"/api/enterprise/channels/{binding_id}/identity-bind-code",
        params={"tenant_id": "tenant_demo"},
        json={"user_id": "user_owner"},
        headers=_auth(users["outsider"]),
    )
    assert forbidden.status_code == 403


def test_collaborator_cannot_delete(monkeypatch) -> None:
    """验证协作者删除绑定返回 403，删除权限仅限所有者或管理员。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    monkeypatch.setattr(channels_api, "channel_services_enabled", lambda: False)
    binding_id = _seed_binding(engine, status="active")
    _add_collaborator(engine, binding_id, "user_other")
    resp = client.delete(
        f"/api/enterprise/channels/{binding_id}",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    )
    assert resp.status_code == 403


def test_collaborator_cannot_manage_managers() -> None:
    """验证协作者无权新增其他协作者，请求返回 403。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    _add_collaborator(engine, binding_id, "user_other")
    resp = client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_outsider"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    )
    assert resp.status_code == 403


def test_non_collaborator_forbidden() -> None:
    """验证未被授权的用户修改绑定时返回 403。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    resp = client.put(
        f"/api/enterprise/channels/{binding_id}",
        json={"auto_route": False},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["outsider"]),
    )
    assert resp.status_code == 403


def test_list_visible_to_collaborator_and_my_role() -> None:
    """验证列表按身份返回 my_role，且未授权用户看不到该绑定。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    _add_collaborator(engine, binding_id, "user_other")
    other_items = client.get(
        "/api/enterprise/channels",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    ).json()
    assert any(b["id"] == binding_id for b in other_items)
    mine = next(b for b in other_items if b["id"] == binding_id)
    assert mine["my_role"] == "collaborator"
    owner_items = client.get(
        "/api/enterprise/channels",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    ).json()
    own = next(b for b in owner_items if b["id"] == binding_id)
    assert own["my_role"] == "owner"
    admin_items = client.get(
        "/api/enterprise/channels",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["admin"]),
    ).json()
    adm = next(b for b in admin_items if b["id"] == binding_id)
    assert adm["my_role"] == "admin"
    # 未被授权的用户看不到该绑定
    outsider_items = client.get(
        "/api/enterprise/channels",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["outsider"]),
    ).json()
    assert not any(b["id"] == binding_id for b in outsider_items)


def test_add_remove_collaborator_revokes_access() -> None:
    """验证移除协作者后其访问被收回，重新添加后权限恢复。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    add = client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    assert add.status_code == 201
    assert client.put(
        f"/api/enterprise/channels/{binding_id}",
        json={"auto_route": False},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    ).status_code == 200
    dele = client.delete(
        f"/api/enterprise/channels/{binding_id}/managers/user_other",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    assert dele.status_code == 204
    assert client.put(
        f"/api/enterprise/channels/{binding_id}",
        json={"auto_route": True},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    ).status_code == 403
    # 重新添加恢复权限(复活已撤销行)
    client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    assert client.put(
        f"/api/enterprise/channels/{binding_id}",
        json={"auto_route": True},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["other"]),
    ).status_code == 200


def test_add_collaborator_validations() -> None:
    """验证新增协作者对创建者、管理员、不存在用户与重复授权分别返回 400 或 409。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    headers = _auth(users["owner"])
    params = {"tenant_id": "tenant_demo"}
    # 创建者无需添加
    assert client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_owner"},
        params=params,
        headers=headers,
    ).status_code == 400
    # 管理员无需添加
    assert client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_admin"},
        params=params,
        headers=headers,
    ).status_code == 400
    # 不存在用户
    assert client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_nope"},
        params=params,
        headers=headers,
    ).status_code == 400
    # 正常添加
    assert client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params=params,
        headers=headers,
    ).status_code == 201
    # 重复添加
    assert client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params=params,
        headers=headers,
    ).status_code == 409


def test_admin_can_manage_managers() -> None:
    """验证租户管理员可为绑定新增协作者。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    resp = client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["admin"]),
    )
    assert resp.status_code == 201


def test_list_managers() -> None:
    """验证协作者列表返回用户 id、名称与授权人信息。"""
    engine = _engine()
    users = _seed(engine)
    client = _client(engine)
    binding_id = _seed_binding(engine)
    client.post(
        f"/api/enterprise/channels/{binding_id}/managers",
        json={"user_id": "user_other"},
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    resp = client.get(
        f"/api/enterprise/channels/{binding_id}/managers",
        params={"tenant_id": "tenant_demo"},
        headers=_auth(users["owner"]),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == "user_other"
    assert data[0]["name"] == "other"
    assert data[0]["granted_by_user_id"] == "user_owner"
