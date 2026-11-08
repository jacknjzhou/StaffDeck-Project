"""就绪探针契约：数据库与角色租约任一不可用都必须返回 503。

探针是编排层的停机信号，因此这里刻意不启动 lifespan（``TestClient`` 不以
``with`` 使用）：探针必须只依赖进程内状态与一次轻量查询，不能通过它触发
后台线程、迁移或恢复任务。
"""

from fastapi.testclient import TestClient

from app import main


def _client(monkeypatch, *, database_ok: bool, lease_lost: bool) -> TestClient:
    """构造探针客户端，按用例替换数据库探测与租约状态。"""
    monkeypatch.setattr(main, "_check_database", lambda: database_ok)
    monkeypatch.setattr(main, "is_lease_lost", lambda: lease_lost)
    return TestClient(main.app)


def test_ready_returns_ok_when_database_and_lease_are_healthy(monkeypatch):
    """数据库可连通且租约在握时返回 200，并同时报告两项健康。"""
    response = _client(monkeypatch, database_ok=True, lease_lost=False).get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "lease": True}


def test_ready_reports_database_failure(monkeypatch):
    """数据库不可用时 readiness 必须 503，而不是恒返回 ok。"""
    response = _client(monkeypatch, database_ok=False, lease_lost=False).get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": False, "lease": True}


def test_ready_reports_lost_lease(monkeypatch):
    """角色租约确认丢失时 readiness 必须 503，即使数据库仍然可连通。"""
    response = _client(monkeypatch, database_ok=True, lease_lost=True).get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": True, "lease": False}
