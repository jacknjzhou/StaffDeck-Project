"""D5 回流链路的容器级验证：真 HTTP + 真 PostgreSQL。

与 `manual_d5_backflow_e2e.py` 的区别，也是它存在的理由：

- `manual_d5_backflow_e2e.py` 用 `TestClient` + 内存 SQLite，验证的是**逻辑**。
  它在进程内直接调用 ASGI app，绕过了 nginx、uvicorn、连接池、PG 事务与迁移，
  所以 SQLite 上能过不代表容器里能过。
- 本脚本只用 `urllib` 打 `http://localhost:5173`，走 nginx → api 容器 → PG。验的是
  **部署形态**：迁移建出来的表结构、psycopg 下的事务行为、nginx 的 SSE/超时配置、
  角色租约下 api 是否真的可用。

一个具体的历史教训：本地 SQLite 上 `sqlmodel` 版本漂移不会暴露，而 PG 上
`UTCDateTime` 会直接拒绝 naive datetime。所以这两条链路必须都跑。

跑法（栈需先起）：
    cd packaging/docker && docker compose up -d
    python backend/tests/manual_d5_backflow_container_e2e.py
    # 自定义入口：STAFFDECK_BASE_URL=http://localhost:8080 ...
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("STAFFDECK_BASE_URL", "http://localhost:5173").rstrip("/")
TENANT = os.environ.get("STAFFDECK_TENANT", "tenant_demo")
ADMIN_USER = os.environ.get("STAFFDECK_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("STAFFDECK_ADMIN_PASSWORD", "admin")
FINANCE_AGENT = os.environ.get("STAFFDECK_AGENT_ID", "agent_f2828efc2a2a476d")
COMPOSE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "packaging", "docker")

_failures: list[str] = []


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    expect: int | None = None,
) -> tuple[int, object]:
    """发一个 JSON 请求，返回 (status, parsed_body)。

    `expect` 只断言状态码。刻意不 assert 在 _request 内部：调用方通常还想打印
    响应体，先抛异常会丢掉最有用的那部分上下文。
    """
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", "replace")

    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw

    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} -> HTTP {status} (expected {expect}): {raw[:400]}")
    return status, parsed


def check(label: str, condition: bool, detail: str = "") -> None:
    """记录一条断言。不中断——一次跑完把所有问题都暴露出来，比挨个试快。"""
    if condition:
        print(f"  [ok] {label}")
    else:
        print(f"  [FAIL] {label}{(' :: ' + detail) if detail else ''}")
        _failures.append(label)


def psql(sql: str) -> str:
    """直接在 PG 里核对，避免「API 说成功了」自证。"""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "staffdeck",
         "-d", "staffdeck", "-t", "-A", "-F", "|", "-c", sql],
        cwd=COMPOSE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def turn_events(suffix: str) -> list[dict]:
    """一轮完整对话：用户消息、助手消息、工具调用、用量。"""
    return [
        {
            "type": "message",
            "event_id": f"m:msg-user-{suffix}",
            "role": "user",
            "content": "查一下 A 类物料的库存",
            "created_at": "2026-10-24T01:00:00Z",
        },
        {
            "type": "message",
            "event_id": f"m:msg-assistant-{suffix}",
            "role": "assistant",
            "content": "A 类物料当前库存 1234 件，低于安全线。",
            "created_at": "2026-10-24T01:00:05Z",
        },
        {
            "type": "tool_call",
            "event_id": f"t:msg-assistant-{suffix}:1",
            "name": "staffdeck_inventory_lookup",
            "status": "ok",
            "duration_ms": 320,
            "created_at": "2026-10-24T01:00:03Z",
        },
        {
            "type": "usage",
            "event_id": f"u:msg-assistant-{suffix}",
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "created_at": "2026-10-24T01:00:05Z",
        },
    ]


def provision_credential(token: str, *, name: str, scopes: list[str]) -> tuple[str, str]:
    """建一个 api-client + 凭证，返回 (client_id, api_key)。"""
    _, client = _request(
        "POST", "/api/v1/api-clients", token=token, expect=201,
        body={"name": name, "scopes": ["*"]},
    )
    client_id = client["id"]
    _, credential = _request(
        "POST", f"/api/v1/api-clients/{client_id}/credentials", token=token, expect=201,
        body={"name": f"{name}-key", "scopes": scopes},
    )
    return client_id, credential["api_key"]


def main() -> int:
    print(f"target: {BASE}  tenant={TENANT}  agent={FINANCE_AGENT}")

    print("\n== 0. 部署形态先自检（这段失败就不用往下看了）==")
    status, health = _request("GET", "/api/health")
    check("health reachable through nginx", status == 200, str(health))
    check("health payload is ok", isinstance(health, dict) and health.get("status") == "ok", str(health))

    version = psql("select version_num from alembic_version;")
    check("alembic migration applied", version == "0001", f"got {version!r}")

    dialect = psql("select current_setting('server_version');")
    print(f"  postgres server_version = {dialect}")

    print("\n== 1. 管理员登录（拿 JWT 才能签发凭证）==")
    status, login = _request(
        "POST", "/api/auth/login",
        body={"tenant_id": TENANT, "username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    if status != 200:
        print(f"  login failed: HTTP {status} {login}")
        return 1
    token = login["token"]
    check("admin token issued", bool(token))
    print(f"  user = {login['user']['username']} role={login['user']['role']}")

    print("\n== 2. 签发回流凭证（deepchat 只用它，不用 admin token）==")
    client_id, api_key = provision_credential(
        token, name="dc-container", scopes=["sessions:write", "agents:read"]
    )
    check("api key looks like sd_live_", api_key.startswith("sd_live_"), api_key[:12])
    print(f"  client_id = {client_id}  key_prefix = {api_key[:16]}…")

    print("\n== 3. 建远端会话（客户端第一步，按 external_session_id 幂等）==")
    local_session = f"local-session-container-{os.getpid()}"
    status, created = _request(
        "POST", f"/api/v1/agents/{FINANCE_AGENT}/sessions", token=api_key,
        body={
            "external_session_id": local_session,
            "external_user_id": "user-container",
            "title": "容器联调",
            "metadata": {"source": "deepchat"},
        },
    )
    check("session created", status == 201, f"HTTP {status}: {created}")
    remote_session_id = created.get("id") if isinstance(created, dict) else None
    check("session id returned", bool(remote_session_id), str(created))
    if not remote_session_id:
        return 1
    print(f"  remote session id = {remote_session_id}")

    print("\n== 4. 重复建会话回到同一行（客户端缓存丢失后重来）==")
    _, again = _request(
        "POST", f"/api/v1/agents/{FINANCE_AGENT}/sessions", token=api_key, expect=201,
        body={
            "external_session_id": local_session,
            "external_user_id": "user-container",
            "metadata": {"source": "deepchat"},
        },
    )
    check("same row returned", again.get("id") == remote_session_id, f"{again.get('id')}")

    print("\n== 5. 首次上报整轮事件 ==")
    events_url = f"/api/v1/agents/{FINANCE_AGENT}/sessions/{remote_session_id}/events:batch"
    events = turn_events("container-1")
    _, first = _request("POST", events_url, token=api_key, body={"events": events})
    check("all 4 accepted", first == {"accepted": 4, "duplicates": 0}, str(first))

    print("\n== 6. 整批重发 + 1 条新增 ==")
    extended = events + [
        {
            "type": "message",
            "event_id": "m:msg-user-container-2",
            "role": "user",
            "content": "那 B 类呢？",
            "created_at": "2026-10-24T01:01:00Z",
        }
    ]
    _, second = _request("POST", events_url, token=api_key, body={"events": extended})
    check("only the new one accepted", second == {"accepted": 1, "duplicates": 4}, str(second))

    print("\n== 7. 同一批再发：全判重，库里不增 ==")
    _, third = _request("POST", events_url, token=api_key, body={"events": extended})
    check("everything deduplicated", third == {"accepted": 0, "duplicates": 5}, str(third))

    print("\n== 8. 直接查 PG，不信 API 的自我声明 ==")
    row_count = psql(f"select count(*) from sessions where id = '{remote_session_id}';")
    check("session row exists in PG", row_count == "1", f"count={row_count}")

    binding_count = psql(
        "select count(*) from external_session_bindings "
        f"where external_session_id = '{local_session}';"
    )
    check("exactly one binding", binding_count == "1", f"count={binding_count}")

    message_count = psql(
        f"select count(*) from messages where session_id = '{remote_session_id}';"
    )
    check("5 messages stored", message_count == "5", f"count={message_count}")

    roles = psql(
        "select string_agg(role, ',' order by role) from messages "
        f"where session_id = '{remote_session_id}';"
    )
    check("roles are sane", roles == "assistant,system,tool,user,user", roles)

    tool_name = psql(
        "select metadata_json::json->>'tool_name' from messages "
        f"where session_id = '{remote_session_id}' and role = 'tool';"
    )
    check("tool_name persisted", tool_name == "staffdeck_inventory_lookup", str(tool_name))

    total_tokens = psql(
        "select metadata_json::json->>'total_tokens' from messages "
        f"where session_id = '{remote_session_id}' and role = 'system';"
    )
    check("usage total_tokens persisted", total_tokens == "1280", str(total_tokens))

    print("\n== 9. 另一凭证不得写这个会话（binding 级鉴权）==")
    _, other_key = provision_credential(token, name="dc-container-other", scopes=["sessions:write"])
    status, foreign = _request("POST", events_url, token=other_key, body={"events": [events[0]]})
    check("foreign key rejected with 404", status == 404, f"HTTP {status}: {foreign}")

    print("\n== 10. 不存在的会话一律 404 ==")
    status, missing = _request(
        "POST",
        f"/api/v1/agents/{FINANCE_AGENT}/sessions/session_does_not_exist/events:batch",
        token=api_key,
        body={"events": [events[0]]},
    )
    check("unknown session 404", status == 404, f"HTTP {status}: {missing}")

    print("\n== 11. 空批次 / 坏凭证 ==")
    _, empty = _request("POST", events_url, token=api_key, body={"events": []})
    check("empty batch is a no-op", empty == {"accepted": 0, "duplicates": 0}, str(empty))

    status, _ = _request("POST", events_url, token="sd_live_not_a_real_key",
                         body={"events": [events[0]]})
    check("bad key is 401", status == 401, f"HTTP {status}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("D5 backflow container e2e: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
