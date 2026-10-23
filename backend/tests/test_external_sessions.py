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
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override
    return TestClient(app), engine, token


def _key(client, token, scopes):
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
    return {"Authorization": f"Bearer {key}"}


def _create(client, key, *, external_session_id="local-abc", agent_id=FINANCE_AGENT, **extra):
    body = {"external_session_id": external_session_id, "agent_id": agent_id, **extra}
    return client.post("/external-sessions", headers=_headers(key), json=body)


def test_creates_a_session_and_binding(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write", "sessions:read"])

    response = _create(client, key, title="本地会话")

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["external_session_id"] == "local-abc"
    assert payload["agent_id"] == FINANCE_AGENT
    assert payload["created"] is True

    with Session(engine) as db:
        binding = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.external_session_id == "local-abc"
            )
        ).first()
        assert binding is not None
        assert db.get(ChatSession, binding.session_id) is not None


def test_repeated_external_session_id_reuses_the_same_session(monkeypatch):
    """One local conversation must not become two rows on the server.

    deepchat reconnects and retries; treating every POST as a create would fill
    the console with ghost sessions that share a title and nothing else.
    """
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    first = _create(client, key)
    second = _create(client, key)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["session_id"] == first.json()["session_id"]
    assert second.json()["created"] is False

    with Session(engine) as db:
        rows = db.exec(
            select(ExternalSessionBinding).where(
                ExternalSessionBinding.external_session_id == "local-abc"
            )
        ).all()
        assert len(rows) == 1


def test_binding_is_scoped_to_the_credential(monkeypatch):
    """Two credentials may use the same local id without colliding.

    A local session id is generated on a device, not by us: two machines can
    legitimately produce the same string.
    """
    client, _engine, token = _client(monkeypatch)
    first_key = _key(client, token, ["sessions:write"])
    second_key = _key(client, token, ["sessions:write"])

    first = _create(client, first_key)
    second = _create(client, second_key)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["session_id"] != second.json()["session_id"]


def test_requires_the_sessions_write_scope(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:read"])

    response = _create(client, key)

    assert response.status_code == 403


def test_rejects_an_unknown_agent(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    response = _create(client, key, agent_id="agent_missing")

    assert response.status_code == 404
    assert response.json()["title"] == "AGENT_NOT_FOUND"


def test_rejects_a_missing_external_session_id(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    response = client.post(
        "/external-sessions",
        headers=_headers(key),
        json={"agent_id": FINANCE_AGENT},
    )

    assert response.status_code == 422


def _events(client, key, session_id, events):
    return client.post(
        f"/external-sessions/{session_id}/events:batch",
        headers=_headers(key),
        json={"events": events},
    )


def _message(event_id, content="hi", role="user"):
    return {"type": "message", "event_id": event_id, "role": role, "content": content}


def test_appends_messages_as_ordinary_session_messages(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["session_id"]

    response = _events(client, key, session_id, [_message("e1", "你好"), _message("e2", "在", "assistant")])

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 2, "duplicates": 0}

    with Session(engine) as db:
        rows = db.exec(select(Message).where(Message.session_id == session_id)).all()
        assert {row.content for row in rows} == {"你好", "在"}


def test_a_resent_event_is_skipped_not_rejected(monkeypatch):
    """The whole batch is resent on retry, so duplicates must be filtered per event.

    A batch-level key would reject the retry outright the moment one event was
    already stored, losing every new event travelling with it.
    """
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["session_id"]

    _events(client, key, session_id, [_message("e1"), _message("e2")])
    second = _events(client, key, session_id, [_message("e1"), _message("e2"), _message("e3")])

    assert second.status_code == 200
    assert second.json() == {"accepted": 1, "duplicates": 2}


def test_an_identical_resend_of_a_full_batch_is_a_no_op(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["session_id"]
    batch = [_message("e1"), _message("e2")]

    _events(client, key, session_id, batch)
    again = _events(client, key, session_id, batch)

    assert again.json() == {"accepted": 0, "duplicates": 2}
    with Session(engine) as db:
        assert len(db.exec(select(Message).where(Message.session_id == session_id)).all()) == 2


def test_records_tool_calls_and_usage(monkeypatch):
    client, engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])
    session_id = _create(client, key).json()["session_id"]

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
    client, _engine, token = _client(monkeypatch)
    key = _key(client, token, ["sessions:write"])

    response = _events(client, key, "session_missing", [_message("e1")])

    assert response.status_code == 404


def test_a_second_credential_cannot_append_to_the_first_credential_session(monkeypatch):
    """Same tenant is not enough: ownership must run through the binding.

    Otherwise any key in the tenant could write into any conversation it can
    name, which turns a leaked read scope into a write capability.
    """
    client, _engine, token = _client(monkeypatch)
    owner = _key(client, token, ["sessions:write"])
    other = _key(client, token, ["sessions:write"])
    session_id = _create(client, owner).json()["session_id"]

    response = _events(client, other, session_id, [_message("e1")])

    assert response.status_code == 404


def test_requires_the_sessions_write_scope_for_events(monkeypatch):
    client, _engine, token = _client(monkeypatch)
    writer = _key(client, token, ["sessions:write"])
    reader = _key(client, token, ["sessions:read"])
    session_id = _create(client, writer).json()["session_id"]

    response = _events(client, reader, session_id, [_message("e1")])

    assert response.status_code == 403
