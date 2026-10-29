from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mock import router
from app.security.internal_service import INTERNAL_SERVICE_HEADER, internal_service_token


def test_mock_api_rejects_anonymous_and_invalid_internal_requests() -> None:
    """验证 mock API 拒绝匿名请求与无效内部服务令牌，两种情况均返回 401。"""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = {"order_id": "ARCHIVE-1001"}

    assert client.post("/api/mock/order/archive-query", json=payload).status_code == 401
    assert client.post(
        "/api/mock/order/archive-query",
        json=payload,
        headers={INTERNAL_SERVICE_HEADER: "invalid"},
    ).status_code == 401


def test_mock_api_accepts_internal_service_token() -> None:
    """验证携带合法内部服务令牌时请求返回 200 并命中订单数据。"""
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/mock/order/archive-query",
        json={"order_id": "ARCHIVE-1001"},
        headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
    )

    assert response.status_code == 200
    assert response.json()["found"] is True
