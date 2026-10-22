from __future__ import annotations

import pytest
from deepchat_contract import validate_digital_employee

from app.digital_employees.contract import clamp_items, clamp_text, zod_len


def _minimal(**overrides):
    payload = {
        "id": "agent-1",
        "name": "财务",
        "role": "报销管家",
        "description": "",
        "avatar": None,
        "capabilities": [],
        "greeting": None,
        "systemPrompt": "# 角色\n你是财务数字员工。",
        "sops": [],
        "knowledgeBaseIds": [],
        "skillIds": [],
        "tools": [],
        "enabled": True,
        "updatedAt": "2026-10-22T09:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_contract_accepts_minimal_employee():
    validate_digital_employee(_minimal())


def test_contract_rejects_over_length_strings_and_extra_items():
    # 守卫的守卫：fixture 本身必须真的会拦住违规，否则金样测试形同虚设
    with pytest.raises(AssertionError, match="at /name: .*too long"):
        validate_digital_employee(_minimal(name="名" * 51))
    with pytest.raises(AssertionError, match="at /tools"):
        validate_digital_employee(_minimal(tools=[f"t{i}" for i in range(51)]))
    with pytest.raises(AssertionError, match="at /sops/0/content: .*too long"):
        validate_digital_employee(_minimal(sops=[{"title": "t", "content": "x" * 20001}]))


def test_contract_allows_unknown_extension_fields():
    validate_digital_employee(_minimal(executionMode="hybrid", configHash="sha256:abc"))


def test_zod_len_counts_utf16_code_units():
    assert zod_len("财务") == 2
    # emoji 在 UTF-16 里占两个 code unit，JS String.length === 2
    assert zod_len("🙂") == 2
    assert zod_len("a🙂b") == 4


def test_clamp_text_truncates_by_utf16_units():
    text, truncated = clamp_text("a" * 5, 5)
    assert (text, truncated) == ("a" * 5, False)
    text, truncated = clamp_text("a" * 6, 5)
    assert (text, truncated) == ("a" * 5, True)
    # 不得把一个代理对裁成半个字符
    text, truncated = clamp_text("🙂" * 3, 5)
    assert truncated is True
    assert zod_len(text) <= 5
    assert text == "🙂" * 2


def test_clamp_items_reports_overflow():
    kept, overflowed = clamp_items([1, 2, 3], 2)
    assert kept == [1, 2]
    assert overflowed is True
    kept, overflowed = clamp_items([1], 2)
    assert (kept, overflowed) == ([1], False)
