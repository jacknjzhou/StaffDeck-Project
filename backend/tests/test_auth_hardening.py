"""认证与输入边界加固:登录限速、OIDC 租户绑定与上传体积预检。"""

import asyncio
import json

import fakeredis
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.auth as auth_api
import app.api.chat as chat_api
from app.api.auth import _login_identity_key, _rate_limit_login
from app.api.chat import _reject_oversized_upload
from app.config import Settings
from app.core.body_limit import (
    CHAT_UPLOAD_BODY_LIMIT,
    REJECT_OVERSIZED_DETAIL,
    BodySizeLimitMiddleware,
)
from app.db import get_session
from app.db.models import Tenant, User
from app.security.auth import create_access_token, get_current_user, hash_password


@pytest.fixture(autouse=True)
def _reset_login_rate_limit_state() -> None:
    """每个用例前后清空限速窗口与 Redis 客户端缓存,避免跨用例串扰。"""
    auth_api._reset_login_rate_limit_state()
    yield
    auth_api._reset_login_rate_limit_state()


def _use_settings(monkeypatch, **overrides) -> Settings:
    """构造测试设置并让 `app.config.get_settings` 返回它。"""
    values = {"database_url": "sqlite://", "app_secret": "test-secret", "redis_url": ""}
    values.update(overrides)
    settings = Settings(**values)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


def _upload_scope(content_length: bytes | None, path: str = "/api/chat/attachments") -> dict:
    """构造一个上传请求的 ASGI scope,按需带上 content-length 头。"""
    headers = [] if content_length is None else [(b"content-length", content_length)]
    return {"type": "http", "method": "POST", "path": path, "headers": headers}


def test_login_identity_key_is_per_ip_and_user() -> None:
    """限速键同时包含来源与用户名,避免只按 IP 被 NAT 放大或只按用户被绕过。"""
    assert _login_identity_key("10.0.0.7", "admin") == "login:10.0.0.7:admin"


def test_oversized_content_length_is_rejected_before_read() -> None:
    """声明长度超限时直接 413,不读取 body。"""
    with pytest.raises(HTTPException) as exc:
        _reject_oversized_upload(10, limit=5)
    assert exc.value.status_code == 413


def test_content_length_at_or_below_limit_is_accepted() -> None:
    """长度恰好等于上限或未声明长度时放行,真正的大小校验留给分片读取。"""
    _reject_oversized_upload(5, limit=5)
    _reject_oversized_upload(None, limit=5)


def test_body_limit_middleware_rejects_before_calling_downstream() -> None:
    """超限上传必须由中间件在 FastAPI 解析 multipart 前拒绝,且绝不读取 body。"""
    sent: list[dict] = []

    async def receive() -> dict:
        raise AssertionError("oversized upload must be rejected before receive")

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("downstream app must not run for oversized uploads")

    middleware = BodySizeLimitMiddleware(downstream)
    oversized = str(CHAT_UPLOAD_BODY_LIMIT + 1).encode()
    asyncio.run(middleware(_upload_scope(oversized), receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    body = b"".join(item["body"] for item in sent if item["type"] == "http.response.body")
    assert json.loads(body)["detail"]


def test_body_limit_middleware_passes_normal_upload() -> None:
    """未超限的上传原样交给下游应用。"""
    called: list[str] = []

    async def downstream(scope, receive, send):
        called.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        return None

    middleware = BodySizeLimitMiddleware(downstream)
    asyncio.run(middleware(_upload_scope(b"128"), receive, send))

    assert called == ["/api/chat/attachments"]


def test_body_limit_middleware_ignores_other_routes_and_missing_header() -> None:
    """其他路由与未声明 content-length 的请求直接放行。"""
    called: list[str] = []

    async def downstream(scope, receive, send):
        called.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        return None

    middleware = BodySizeLimitMiddleware(downstream)
    oversized = str(CHAT_UPLOAD_BODY_LIMIT + 1).encode()
    asyncio.run(
        middleware(_upload_scope(oversized, path="/api/auth/login"), receive, send)
    )
    asyncio.run(middleware(_upload_scope(None), receive, send))

    assert called == ["/api/auth/login", "/api/chat/attachments"]


def test_rate_limit_blocks_after_limit_in_memory(monkeypatch) -> None:
    """未配置 Redis 时进程内窗口仍然生效,超限返回 429。"""
    _use_settings(monkeypatch, login_rate_limit_per_minute=2)
    identity = "login:10.0.0.7:admin"
    for _ in range(2):
        _rate_limit_login(identity)

    with pytest.raises(HTTPException) as exc:
        _rate_limit_login(identity)
    assert exc.value.status_code == 429
    assert exc.value.detail == "too many login attempts"
    assert exc.value.headers == {"Retry-After": "60"}


def test_rate_limit_keys_are_independent(monkeypatch) -> None:
    """不同来源或不同用户名的计数互不影响。"""
    _use_settings(monkeypatch, login_rate_limit_per_minute=1)
    _rate_limit_login("login:10.0.0.7:admin")
    _rate_limit_login("login:10.0.0.7:other")
    _rate_limit_login("login:10.0.0.8:admin")


def test_rate_limit_uses_redis_fixed_window(monkeypatch) -> None:
    """配置 Redis 时用 INCR + EXPIRE 共享固定窗口计数。"""
    _use_settings(
        monkeypatch,
        redis_url="redis://localhost:6379/0",
        login_rate_limit_per_minute=2,
    )
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth_api, "_build_login_redis_client", lambda url: fake)
    identity = "login:10.0.0.7:admin"
    key = f"staffdeck:login:{identity}"

    _rate_limit_login(identity)
    _rate_limit_login(identity)
    assert fake.get(key) == "2"
    assert fake.ttl(key) > 0

    with pytest.raises(HTTPException) as exc:
        _rate_limit_login(identity)
    assert exc.value.status_code == 429
    assert fake.get(key) == "3"
    # 超限时也要保证窗口有 TTL,不能留下永久键。
    assert fake.ttl(key) > 0


def test_rate_limit_repairs_missing_redis_ttl(monkeypatch) -> None:
    """已有计数但丢失 TTL 的键在下一次登录时补上过期时间。"""
    _use_settings(
        monkeypatch,
        redis_url="redis://localhost:6379/0",
        login_rate_limit_per_minute=3,
    )
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(auth_api, "_build_login_redis_client", lambda url: fake)
    identity = "login:10.0.0.7:admin"
    key = f"staffdeck:login:{identity}"
    fake.set(key, "1")
    assert fake.ttl(key) == -1

    _rate_limit_login(identity)

    assert fake.get(key) == "2"
    assert fake.ttl(key) > 0


def test_rate_limit_falls_back_to_memory_when_redis_errors(monkeypatch, caplog) -> None:
    """Redis 故障时降级到进程内窗口并记 warning,登录接口不能因此 500。"""
    _use_settings(
        monkeypatch,
        redis_url="redis://localhost:6379/0",
        login_rate_limit_per_minute=1,
    )

    class BrokenRedis:
        def incr(self, key: str) -> int:
            raise ConnectionError("redis down")

    monkeypatch.setattr(auth_api, "_build_login_redis_client", lambda url: BrokenRedis())
    identity = "login:10.0.0.7:admin"
    with caplog.at_level("WARNING"):
        _rate_limit_login(identity)
    assert "falling back to memory" in caplog.text

    with pytest.raises(HTTPException) as exc:
        _rate_limit_login(identity)
    assert exc.value.status_code == 429


def _chat_client(monkeypatch) -> tuple[TestClient, User]:
    """构建只挂载聊天路由、带演示租户与登录用户的测试客户端。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        user = User(
            id="user_web",
            tenant_id="tenant_demo",
            username="zhangsan",
            password_hash=hash_password("secret"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)

    test_app = FastAPI()
    test_app.include_router(chat_api.router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    return TestClient(test_app), user


def test_upload_rejects_per_file_oversize(monkeypatch) -> None:
    """即使中间件放行,端点内的单文件大小校验仍返回 413(纵深防御)。"""
    monkeypatch.setattr(chat_api, "MAX_CHAT_ATTACHMENT_BYTES", 4)
    client, user = _chat_client(monkeypatch)
    response = client.post(
        "/api/chat/attachments",
        params={"tenant_id": "tenant_demo"},
        files={"files": ("big.txt", b"0123456789", "text/plain")},
        headers={"Authorization": f"Bearer {create_access_token(user)}"},
    )
    assert response.status_code == 413


def _auth_client(monkeypatch, *, limit: int) -> TestClient:
    """构建只挂载认证路由、带演示租户与账号的测试客户端。"""
    _use_settings(monkeypatch, login_rate_limit_per_minute=limit)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            User(
                id="user_login",
                tenant_id="tenant_demo",
                username="zhangsan",
                password_hash=hash_password("secret"),
            )
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(auth_api.router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    return TestClient(test_app)


def test_login_endpoint_returns_429_after_limit(monkeypatch) -> None:
    """端到端:错误密码也计数,超过阈值后登录接口返回 429 而非 401。"""
    client = _auth_client(monkeypatch, limit=2)
    payload = {"tenant_id": "tenant_demo", "username": "zhangsan", "password": "wrong"}
    assert client.post("/api/auth/login", json=payload).status_code == 401
    assert client.post("/api/auth/login", json=payload).status_code == 401
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 429
    assert response.json()["detail"] == "too many login attempts"
    assert response.headers["retry-after"] == "60"


def test_login_endpoint_counts_successful_logins(monkeypatch) -> None:
    """成功登录同样消耗预算:只统计失败的计数器无法通过本用例。"""
    client = _auth_client(monkeypatch, limit=2)
    payload = {"tenant_id": "tenant_demo", "username": "zhangsan", "password": "secret"}
    assert client.post("/api/auth/login", json=payload).status_code == 200
    assert client.post("/api/auth/login", json=payload).status_code == 200
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_main_app_wires_cors_outside_body_limit() -> None:
    """CORS 必须比体积预检更外层,413 响应才会带上跨域头。"""
    from app import main as main_module

    names = [middleware.cls.__name__ for middleware in main_module.app.user_middleware]
    # 断言相对顺序:完整套件会经 single_port_app 追加中间件,绝对下标不稳定。
    assert names.index("CORSMiddleware") < names.index("BodySizeLimitMiddleware")


def test_real_app_rejects_oversized_upload_before_route(monkeypatch) -> None:
    """真实 TestClient 上传超限请求由中间件 413,路由与鉴权依赖都不执行。"""
    from app import main as main_module
    import app.core.body_limit as body_limit

    monkeypatch.setattr(body_limit, "CHAT_UPLOAD_BODY_LIMIT", 64)

    def _must_not_run() -> None:
        raise AssertionError("attachment route must not run for oversized uploads")

    main_module.app.dependency_overrides[get_current_user] = _must_not_run
    try:
        response = TestClient(main_module.app).post(
            "/api/chat/attachments",
            params={"tenant_id": "tenant_demo"},
            files={"files": ("big.txt", b"x" * 1024, "text/plain")},
            headers={"Origin": "http://localhost:5173"},
        )
    finally:
        main_module.app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 413
    assert response.json()["detail"] == REJECT_OVERSIZED_DETAIL
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
