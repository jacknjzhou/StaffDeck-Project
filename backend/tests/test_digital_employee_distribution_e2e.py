from __future__ import annotations

import io
import json
import zipfile

from deepchat_contract import validate_digital_employee
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import visible_general_skill_rows
from app.db.models import AgentProfile, User
from app.db.seed import seed_demo_data
from app.digital_employees.payload import build_employee_list
from app.digital_employees.skill_package import build_skill_zip
from app.public_api.auth import PublicPrincipal


def _seeded() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seed_demo_data(session)
    session.commit()
    return session


def _principal(db: Session) -> PublicPrincipal:
    admin = db.exec(select(User).where(User.username == "admin")).first()
    assert admin is not None
    return PublicPrincipal(tenant_id=admin.tenant_id, actor_user=admin, scopes=frozenset({"*"}))


def _distribution(db: Session) -> list[AgentProfile]:
    return list(db.exec(select(AgentProfile).where(AgentProfile.is_overall == False)).all())


def test_distribution_payload_is_json_serialisable_and_complete():
    session = _seeded()
    try:
        principal = _principal(session)
        payload = build_employee_list(principal, _distribution(session), session)
    finally:
        session.close()

    # 必须能原样过 JSON 往返且字节级稳定，否则 HTTP 层的 ETag 会抖动
    first = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    second = json.dumps(json.loads(first), ensure_ascii=False, sort_keys=True, default=str)
    assert first == second

    assert payload["configVersion"].startswith("v1:")
    for employee in payload["employees"]:
        validate_digital_employee(employee)
        assert employee["systemPrompt"].strip()
        assert employee["executionMode"] in {"local", "remote", "hybrid"}
        assert employee["configHash"].startswith("sha256:")


def test_every_distributed_skill_is_downloadable():
    session = _seeded()
    try:
        principal = _principal(session)
        seen = 0
        for agent in _distribution(session):
            for row in visible_general_skill_rows(session, principal.tenant_id, agent.id):
                seen += 1
                with zipfile.ZipFile(io.BytesIO(build_skill_zip(row))) as archive:
                    assert "SKILL.md" in archive.namelist()
                    assert archive.testzip() is None
        assert seen > 0, "seed data must ship at least one distributed skill"
    finally:
        session.close()


def test_tools_never_leak_credentials_into_the_payload():
    session = _seeded()
    try:
        principal = _principal(session)
        payload = build_employee_list(principal, _distribution(session), session)
    finally:
        session.close()

    # 只看键名：SOP 正文里合法地出现过「授权」这类业务词汇，值扫描会误报
    forbidden = {"headers_json", "auth_json", "credentials", "url", "authorization"}
    assert forbidden.isdisjoint(_all_keys(payload))

    blob = json.dumps(payload, ensure_ascii=False, default=str)
    assert "sd_live_" not in blob
    for employee in payload["employees"]:
        for tool in employee["tools"]:
            assert tool.startswith("staffdeck:")
            assert "://" not in tool


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item)
    return keys
