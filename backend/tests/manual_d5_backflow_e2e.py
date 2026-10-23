"""D5 端到端联调：按 deepchat 客户端 `backflow.ts` 的真实请求形状打一遍回流链路。

要验的是「这条链路能不能自己跑通」，所以刻意不用现成的 session id：本地执行的
会话在服务端本来就没有记录，脚本必须从枚举目录开始，像客户端一样先建会话、再
追加事件。这也是这次修复的核心——之前只有事件端点，缺了建会话那一步，真实
客户端一上报就是 404。

跑法：
    cd backend && .venv/Scripts/python.exe tests/manual_d5_backflow_e2e.py
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.db.models import Message, User
from app.db.seed import seed_demo_data
from app.public_api.app import create_public_api_app
from app.security.auth import create_access_token

FINANCE_AGENT = "agent_f2828efc2a2a476d"
# 客户端 `BackflowEventSchema` 的上限就是 500，这里用同量级验证分块边界。
LOCAL_SESSION = "local-session-d5-e2e"


def _client() -> tuple[TestClient, dict[str, str], object]:
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
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {token}"}
    client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc", "scopes": ["*"]}
    ).json()["id"]
    key = client.post(
        f"/api-clients/{client_id}/credentials",
        headers=admin,
        json={"name": "d5", "scopes": ["sessions:write", "agents:read"]},
    ).json()["api_key"]
    return client, {"Authorization": f"Bearer {key}"}, engine


def _events() -> list[dict]:
    """一轮对话的事件：用户消息、助手消息、工具调用、用量。"""
    return [
        {
            "type": "message",
            "event_id": "m:msg-user-1",
            "role": "user",
            "content": "查一下 A 类物料的库存",
            "created_at": "2026-10-23T12:00:00Z",
        },
        {
            "type": "message",
            "event_id": "m:msg-assistant-2",
            "role": "assistant",
            "content": "A 类物料当前库存 1234 件，低于安全线。",
            "created_at": "2026-10-23T12:00:05Z",
        },
        {
            "type": "tool_call",
            "event_id": "t:msg-assistant-2:1",
            "name": "staffdeck_inventory_lookup",
            "status": "ok",
            "duration_ms": 320,
            "created_at": "2026-10-23T12:00:03Z",
        },
        {
            "type": "usage",
            "event_id": "u:msg-assistant-2",
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "created_at": "2026-10-23T12:00:05Z",
        },
    ]


def main() -> None:
    client, auth, engine = _client()

    print("\n== 1. 建远端会话（客户端第一步，按 external_session_id 幂等）==")
    created = client.post(
        f"/agents/{FINANCE_AGENT}/sessions",
        headers=auth,
        json={
            "external_session_id": LOCAL_SESSION,
            "external_user_id": "user-1",
            "title": "财务",
            "metadata": {"source": "deepchat"},
        },
    )
    print(f"  POST /sessions -> HTTP {created.status_code}")
    assert created.status_code == 201, created.text
    remote_session_id = created.json()["id"]
    print(f"  remote session id = {remote_session_id}")

    print("\n== 2. 重复建会话必须回到同一行（客户端缓存丢失/重启后重来）==")
    again = client.post(
        f"/agents/{FINANCE_AGENT}/sessions",
        headers=auth,
        json={
            "external_session_id": LOCAL_SESSION,
            "external_user_id": "user-1",
            "metadata": {"source": "deepchat"},
        },
    )
    print(f"  POST /sessions again -> HTTP {again.status_code}, id = {again.json()['id']}")
    assert again.json()["id"] == remote_session_id, "外部会话 id 复用时不得产生第二行"

    events_url = f"/agents/{FINANCE_AGENT}/sessions/{remote_session_id}/events:batch"

    print("\n== 3. 首次上报整轮事件 ==")
    first = client.post(events_url, headers=auth, json={"events": _events()})
    print(f"  POST events:batch -> HTTP {first.status_code} {first.json()}")
    assert first.json() == {"accepted": 4, "duplicates": 0}

    print("\n== 4. 整批重发 + 新增一条：只有新事件被接受 ==")
    resent = _events() + [
        {
            "type": "message",
            "event_id": "m:msg-user-3",
            "role": "user",
            "content": "那 B 类呢？",
            "created_at": "2026-10-23T12:01:00Z",
        }
    ]
    second = client.post(events_url, headers=auth, json={"events": resent})
    print(f"  POST events:batch -> HTTP {second.status_code} {second.json()}")
    assert second.json() == {"accepted": 1, "duplicates": 4}

    print("\n== 5. 同一批再发一次：全部判重，事件数不变 ==")
    third = client.post(events_url, headers=auth, json={"events": resent})
    print(f"  POST events:batch -> HTTP {third.status_code} {third.json()}")
    assert third.json() == {"accepted": 0, "duplicates": 5}

    print("\n== 6. 库内事件与字段 ==")
    with Session(engine) as db:
        rows = db.exec(select(Message).where(Message.session_id == remote_session_id)).all()
        print(f"  stored messages = {len(rows)}")
        assert len(rows) == 5, f"expected 5 rows, got {len(rows)}"
        roles = sorted(row.role for row in rows)
        print(f"  roles = {roles}")
        assert roles == ["assistant", "system", "tool", "user", "user"]

        tool_row = next(row for row in rows if row.role == "tool")
        print(f"  tool row: name={tool_row.metadata_json.get('tool_name')} "
              f"status={tool_row.metadata_json.get('status')} "
              f"duration_ms={tool_row.metadata_json.get('duration_ms')}")
        assert tool_row.metadata_json["tool_name"] == "staffdeck_inventory_lookup"
        assert tool_row.metadata_json["status"] == "ok"
        assert tool_row.content == "staffdeck_inventory_lookup"

        usage_row = next(row for row in rows if row.role == "system")
        print(f"  usage row: total_tokens={usage_row.metadata_json.get('total_tokens')}")
        assert usage_row.metadata_json["total_tokens"] == 1280
        assert usage_row.content == ""

    print("\n== 7. 另一凭证不得往这个会话写（binding 级鉴权）==")
    admin = None
    with Session(engine) as db:
        token = create_access_token(db.exec(select(User).where(User.username == "admin")).first())
    admin = {"Authorization": f"Bearer {token}"}
    other_client_id = client.post(
        "/api-clients", headers=admin, json={"name": "dc2", "scopes": ["*"]}
    ).json()["id"]
    other_key = client.post(
        f"/api-clients/{other_client_id}/credentials",
        headers=admin,
        json={"name": "d5-other", "scopes": ["sessions:write"]},
    ).json()["api_key"]
    foreign = client.post(
        events_url,
        headers={"Authorization": f"Bearer {other_key}"},
        json={"events": [_events()[0]]},
    )
    print(f"  POST events:batch with another key -> HTTP {foreign.status_code}")
    assert foreign.status_code == 404, "同租户的其他凭证也必须被拒"

    print("\n== 8. 不存在的会话一律 404 ==")
    missing = client.post(
        f"/agents/{FINANCE_AGENT}/sessions/session_does_not_exist/events:batch",
        headers=auth,
        json={"events": [_events()[0]]},
    )
    print(f"  POST events:batch to unknown session -> HTTP {missing.status_code}")
    assert missing.status_code == 404

    print("\n== 9. 空批次 ==")
    empty = client.post(events_url, headers=auth, json={"events": []})
    print(f"  POST events:batch (empty) -> HTTP {empty.status_code} {empty.json()}")
    assert empty.json() == {"accepted": 0, "duplicates": 0}

    print("\nD5 backflow e2e: OK")


if __name__ == "__main__":
    main()
