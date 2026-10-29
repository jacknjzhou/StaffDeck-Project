from __future__ import annotations

import io
import zipfile

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import GeneralSkill
from app.db.seed import seed_demo_data
from app.digital_employees.skill_package import build_skill_zip, skill_zip_filename


def _session() -> Session:
    """创建内存 SQLite 会话并写入演示数据，避免用例依赖外部数据库。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _published(session: Session) -> GeneralSkill:
    """取出首个已发布的通用技能，作为技能包打包用例的输入。"""
    row = session.exec(select(GeneralSkill).where(GeneralSkill.status == "published")).first()
    assert row is not None
    return row


def test_zip_contains_skill_markdown_at_the_root():
    """验证压缩包根部有非空 SKILL.md，条目均为相对路径且压缩包校验通过。"""
    session = _session()
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(_published(session)))) as archive:
        names = archive.namelist()
        assert "SKILL.md" in names
        assert archive.read("SKILL.md").decode("utf-8").strip()
        assert not any(name.startswith("/") for name in names)
        assert archive.testzip() is None


def test_zip_ships_every_declared_skill_file():
    """验证技能声明的每个文件都出现在压缩包中。"""
    session = _session()
    row = _published(session)
    declared = {item.get("path") for item in (row.skill_files_json or []) if item.get("path")}
    if not declared:
        declared = {"SKILL.md"}
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert declared.issubset(set(archive.namelist()))


def test_zip_falls_back_to_markdown_when_files_are_missing():
    """验证文件清单为空时回退为只打包技能 Markdown 原文。"""
    session = _session()
    row = _published(session)
    row.skill_files_json = []
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert "SKILL.md" in archive.namelist()
        assert archive.read("SKILL.md").decode("utf-8") == row.skill_markdown


def test_zip_rejects_path_traversal_entries():
    """验证含 .. 的路径穿越条目被丢弃，同时正常文件仍会打包。"""
    session = _session()
    row = _published(session)
    row.skill_files_json = [
        {"path": "../../etc/passwd", "content": "nope"},
        {"path": "SKILL.md", "content": "---\nname: x\n---\n# x"},
    ]
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        names = archive.namelist()
        assert all(".." not in name for name in names)
        assert "etc/passwd" not in names
        assert "SKILL.md" in names


def test_skill_zip_filename_is_filesystem_safe():
    """验证下载文件名会剔除路径与非法字符，空名称回退为 skill.zip。"""
    assert skill_zip_filename("expense-policy_v1") == "expense-policy_v1.zip"
    assert skill_zip_filename("../../evil slug") == "evilslug.zip"
    assert skill_zip_filename("") == "skill.zip"
