from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import ChatSession, ExternalSessionBinding, Message, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"


def _client(monkeypatch):
    """构建带演示数据的内存库公共 API 客户端,返回客户端、引擎与管理员令牌。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_data(db)
        db.commit()
        token = create_access_token(db.exec(select(User).where(User.username == "admin")).first())

    app = create_public_api_app()

    def override():
        """用内存引擎开新会话替换请求期数据库依赖。"""
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    return TestClient(app), engine, token


def _key(client, token, scopes):
    """创建名称唯一的 API 客户端并为它签发凭据,返回 api_key。"""
    admin = {"Authorization": f"Bearer {token}"}
    # Names must be unique per call: the test that proves two credentials do not
    # collide needs to actually create two of them.
    name = f"dc-backflow-{uuid4().hex[:8]}"
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": name, "scopes": scopes}
    ).json()["id"]
    return client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": name, "scopes": scopes},
    ).json()["api_key"]


def _headers(key):
    """把 API key 包装成 Bearer 认证头。"""
    return {"Authorization": f"Bearer {key}"}


def _create(client, key, *, external_session_id="local-abc", agent_id=FINANCE_AGENT, **extra):
    """会话创建复用现有公共会话接口。

    外部会话没有独立的创建端点:该接口本就接受 `external_session_id`,
    另开一条路径只会让同一规则在两处漂移。
    """
    body = {"external_session_id": external_session_id, **extra}
    return client.post(
        f"/agents/{agent_id}/sessions", headers=_headers(key), json=body
    )


def test_creates_a_session_and_binding(monkeypatch):
    """验证创建外部会话时会同时落库会话行与会话绑定。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write", "sessions:read"])

    response = _create(client, key, title="本地会话")

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["external_session_id"] == "local-abc"
    assert payload["agent_id"] == FINANCE_AGENT

    with Session(engine) as db:
        binding = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.external_session_id == "local-abc"
            )
        ).first()
        assert binding is not None
        assert db.get(ChatSession, binding.session_id) is not None


def test_repeated_external_session_id_reuses_the_same_session(monkeypatch):
    """验证重复上报同一 external_session_id 时只会复用同一个会话。

    deepchat 会重连重试,若每次 POST 都当作新建,控制台会堆满同名的幽灵会话。
    """
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    first = _create(client, key)
    second = _create(client, key)

    assert first.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    with Session(engine) as db:
        rows = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.external_session_id == "local-abc"
            )
        ).all()
        assert len(rows) == 1


def test_binding_is_scoped_to_the_credential(monkeypatch):
    """验证两个凭据可以使用相同本地会话 id 而不冲突。

    本地会话 id 由设备生成而非服务端,两台机器完全可能产生同一字符串。
    """
    client, _engine, token = _client(monkeypatch)
    first_key = _key(client, token, ["sessions:write"])
    second_key = _key(client, token, ["sessions:write"])

    first = _create(client, first_key)
    second = _create(client, second_key)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_requires_the_sessions_write_scope(monkeypatch):
    """验证缺少 sessions:write 作用域的凭据无法创建会话。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:read"])

    response = _create(client, key)

    assert response.status_code == 403


def test_rejects_an_unknown_agent(monkeypatch):
    """验证向不存在的 agent 创建会话时返回 404 与 AGENT_NOT_FOUND。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    response = _create(client, key, agent_id="agent_missing")

    assert response.status_code == 404
    assert response.json()["title"] == "AGENT_NOT_FOUND"


def _events(client, key, session_id, events):
    """向指定会话批量投递事件,返回接口响应。"""
    return client.post(
        f"/agents/{FINANCE_AGENT}/sessions/{session_id}/events:batch",
        headers=_headers(key),
        json={"events": events},
    )


def _message(event_id, content="hi", role="user"):
    """构造一条 message 类型的事件载荷。"""
    return {"type": "message", "event_id": event_id, "role": role, "content": content}


def test_appends_messages_as_ordinary_session_messages(monkeypatch):
    """验证事件落库后成为普通会话消息,内容与角色保持不变。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["id"]

    response = _events(client, key, session_id, [_message("e1", "你好"), _message("e2", "在", "assistant")])

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 2, "duplicates": 0}

    with Session(engine) as db:
        rows = db.exec(select(Message).where(Message.session_id == session_id)).all()
        assert {row.content for row in rows} == {"你好", "在"}


def test_a_resent_event_is_skipped_not_rejected(monkeypatch):
    """验证重复投递同一事件时只会跳过该条,而不是整批拒绝。

    重试会重发整批事件,若用批量级幂等键,只要有一条已存就会拒绝整批,
    连带丢掉随行携带的新事件。
    """
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["id"]

    _events(client, key, session_id, [_message("e1"), _message("e2")])
    second = _events(client, key, session_id, [_message("e1"), _message("e2"), _message("e3")])

    assert second.status_code == 200
    assert second.json() == {"accepted": 1, "duplicates": 2}


def test_an_identical_resend_of_a_full_batch_is_a_no_op(monkeypatch):
    """验证整批原样重发时不再新增消息,只统计重复数。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["id"]
    batch = [_message("e1"), _message("e2")]

    _events(client, key, session_id, batch)
    again = _events(client, key, session_id, batch)

    assert again.json() == {"accepted": 0, "duplicates": 2}
    with Session(engine) as db:
        assert len(db.exec(select(Message).where(Message.session_id == session_id)).all()) == 2


def test_records_tool_calls_and_usage(monkeypatch):
    """验证 tool_call 与 usage 事件分别落为 tool、system 消息并保留元数据。"""
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["id"]

    response = _events(
        client,
        key,
        session_id,
        [
            {
                "type": "tool_call",
                "event_id": "t1",
                "name": "staffdeck_expense_submit",
                "status": "error",
                "duration_ms": 120,
                "error_code": "TOOL_TIMEOUT",
            },
            {
                "type": "usage",
                "event_id": "u1",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        ],
    )

    assert response.json()["accepted"] == 2
    with Session(engine) as db:
        rows = db.exec(select(Message).where(Message.session_id == session_id)).all()
        tool = next(row for row in rows if row.role == "tool")
        assert tool.metadata_json["error_code"] == "TOOL_TIMEOUT"
        assert tool.metadata_json["duration_ms"] == 120
        usage = next(row for row in rows if row.role == "system")
        assert usage.metadata_json["total_tokens"] == 15


def test_rejects_an_unknown_session(monkeypatch):
    """验证向不存在的会话投递事件时返回 404。"""
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    response = _events(client, key, "session_missing", [_message("e1")])

    assert response.status_code == 404


def test_a_second_credential_cannot_append_to_the_first_credential_session(monkeypatch):
    """验证同租户的另一个凭据不能写入首个凭据的会话。

    归属校验必须经过绑定表,否则租户内任意 key 都能写进它叫得出名字的会话,
    一个泄露的读作用域就会变成写能力。
    """
    client, _engine, token = _client(monkeypatch)
    owner = _key(client, token, ["sessions:write"])
    other = _key(client, token, ["sessions:write"])
    session_id = _create(client, owner).json()["id"]

    response = _events(client, other, session_id, [_message("e1")])

    assert response.status_code == 404


def test_requires_the_sessions_write_scope_for_events(monkeypatch):
    """验证缺少 sessions:write 作用域的凭据无法投递事件。"""
    client, _engine, token = _client(monkeypatch)
    writer = _key(client, token, ["sessions:write"])
    reader = _key(client, token, ["sessions:read"])
    session_id = _create(client, writer).json()["id"]

    response = _events(client, reader, session_id, [_message("e1")])

    assert response.status_code == 403
