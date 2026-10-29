from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import paths
from app.db import seed
from app.db.models import MCPServer


def _seed_session():
    """建内存 SQLite 会话（StaticPool 复用单连接）并建表，供种子数据落库断言。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_stdio_mcp_command_is_bundled_python_when_frozen(monkeypatch, tmp_path) -> None:
    """验证打包（冻结）运行时 stdio_demo 的 command 指向随包分发的 Python。"""
    bundled = tmp_path / "runtime" / "bin" / "python3"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("")
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(seed, "_stdio_mcp_python", lambda: str(bundled))
    with _seed_session() as db:
        seed._seed_mcp_servers(db)
        db.commit()
        row = db.exec(select(MCPServer).where(MCPServer.name == "stdio_demo")).first()
        assert row is not None
        assert row.command == str(bundled)


def test_stdio_mcp_command_is_sys_executable_in_dev(monkeypatch) -> None:
    """验证开发（非冻结）模式下 stdio_demo 的 command 使用当前解释器 sys.executable。"""
    import sys
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    with _seed_session() as db:
        seed._seed_mcp_servers(db)
        db.commit()
        row = db.exec(select(MCPServer).where(MCPServer.name == "stdio_demo")).first()
        assert row.command == sys.executable
