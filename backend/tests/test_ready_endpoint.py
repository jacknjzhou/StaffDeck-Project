"""就绪探针契约：数据库与角色租约任一不可用都必须返回 503。

探针是编排层的停机信号，因此这里刻意不启动 lifespan（``TestClient`` 不以
``with`` 使用）：探针必须只依赖进程内状态与一次轻量查询，不能通过它触发
后台线程、迁移或恢复任务。
"""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

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


def test_ready_reports_both_failures(monkeypatch):
    """两项同时失效时仍要分别上报，排障不能只看到笼统的 degraded。"""
    response = _client(monkeypatch, database_ok=False, lease_lost=True).get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": False, "lease": False}


def test_check_database_returns_false_when_connection_fails(monkeypatch):
    """连接层异常（拒绝连接、超时）必须收敛成 False，而不是让探针抛错。"""

    class _BrokenEngine:
        """模拟探针引擎在取连接时直接抛 OperationalError。"""

        def connect(self):
            raise OperationalError("SELECT 1", {}, RuntimeError("connection refused"))

    monkeypatch.setattr(main, "_probe_engine", _BrokenEngine())
    monkeypatch.setattr(main, "is_lease_lost", lambda: False)

    assert main._check_database() is False
    response = TestClient(main.app).get("/api/ready")
    assert response.status_code == 503
    assert response.json()["database"] is False


def test_probe_engine_does_not_share_the_app_pool():
    """探针必须用独立连接池，否则应用池耗尽时 readiness 会一起阻塞。"""
    probe = main._build_probe_engine()

    assert isinstance(probe.pool, NullPool)
    assert probe.url == main.engine.url
    assert probe.pool._pre_ping is False
