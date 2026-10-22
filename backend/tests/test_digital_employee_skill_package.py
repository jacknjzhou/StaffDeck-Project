from __future__ import annotations

import io
import zipfile

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import GeneralSkill
from app.db.seed import seed_demo_data
from app.digital_employees.skill_package import build_skill_zip, skill_zip_filename


def _session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _published(session: Session) -> GeneralSkill:
    row = session.exec(select(GeneralSkill).where(GeneralSkill.status == "published")).first()
    assert row is not None
    return row


def test_zip_contains_skill_markdown_at_the_root():
    session = _session()
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(_published(session)))) as archive:
        names = archive.namelist()
        assert "SKILL.md" in names
        assert archive.read("SKILL.md").decode("utf-8").strip()
        assert not any(name.startswith("/") for name in names)
        assert archive.testzip() is None


def test_zip_ships_every_declared_skill_file():
    session = _session()
    row = _published(session)
    declared = {item.get("path") for item in (row.skill_files_json or []) if item.get("path")}
    if not declared:
        declared = {"SKILL.md"}
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert declared.issubset(set(archive.namelist()))


def test_zip_falls_back_to_markdown_when_files_are_missing():
    session = _session()
    row = _published(session)
    row.skill_files_json = []
    with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
        assert "SKILL.md" in archive.namelist()
        assert archive.read("SKILL.md").decode("utf-8") == row.skill_markdown


def test_zip_rejects_path_traversal_entries():
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
    assert skill_zip_filename("expense-policy_v1") == "expense-policy_v1.zip"
    assert skill_zip_filename("../../evil slug") == "evilslug.zip"
    assert skill_zip_filename("") == "skill.zip"
