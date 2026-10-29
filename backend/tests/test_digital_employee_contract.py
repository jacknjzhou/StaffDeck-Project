from __future__ import annotations

import pytest
from deepchat_contract import validate_digital_employee

from app.digital_employees.contract import clamp_items, clamp_text, zod_len


def _minimal(**overrides):
    """构造最小可用的数字员工契约载荷，并允许用 overrides 覆盖任意字段。"""
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
        "executionMode": "local",
    }
    payload.update(overrides)
    return payload


def test_contract_accepts_minimal_employee():
    """验证只含必填字段的最小数字员工载荷能通过契约校验。"""
    validate_digital_employee(_minimal())


def test_contract_rejects_over_length_strings_and_extra_items():
    """验证超长字符串和超出数量上限的列表会被契约拒绝。"""
    # 守卫的守卫：fixture 本身必须真的会拦住违规，否则金样测试形同虚设
    with pytest.raises(AssertionError, match="at /name: .*too long"):
        validate_digital_employee(_minimal(name="名" * 51))
    with pytest.raises(AssertionError, match="at /tools"):
        validate_digital_employee(_minimal(tools=[f"t{i}" for i in range(51)]))
    with pytest.raises(AssertionError, match="at /sops/0/content: .*too long"):
        validate_digital_employee(_minimal(sops=[{"title": "t", "content": "x" * 20001}]))


def test_contract_requires_execution_mode():
    """验证缺少 executionMode 时契约会报错，防止下游静默采用默认模式。"""
    # The zod field has .default('local'), which only fires on a missing key
    # at parse time; StaffDeck must always send it explicitly so the mode is
    # never silently reinterpreted downstream.
    payload = _minimal()
    del payload["executionMode"]
    with pytest.raises(AssertionError, match="executionMode"):
        validate_digital_employee(payload)


def test_contract_validates_the_staffdeck_extension_fields():
    """验证 StaffDeck 扩展字段（技能下载地址、配置哈希等）的长度与枚举校验生效。"""
    validate_digital_employee(
        _minimal(
            executionMode="remote",
            configHash="sha256:" + "a" * 64,
            skills=[
                {
                    "id": "gs-1",
                    "name": "staffdeck-api",
                    "downloadUrl": "https://sd/api/v1/digital-employees/a:export-skill?slug=x",
                }
            ],
            knowledgePackagesUrl="https://sd/api/v1/digital-employees/a/knowledge-packages",
        )
    )
    with pytest.raises(AssertionError, match="at /executionMode"):
        validate_digital_employee(_minimal(executionMode="distributed"))
    with pytest.raises(AssertionError, match="at /configHash: .*too long"):
        validate_digital_employee(_minimal(configHash="x" * 129))
    with pytest.raises(AssertionError, match="downloadUrl"):
        validate_digital_employee(
            _minimal(skills=[{"id": "gs-1", "name": "n", "downloadUrl": "u" * 513}])
        )
    with pytest.raises(AssertionError, match="at /skills"):
        validate_digital_employee(
            _minimal(
                skills=[
                    {"id": f"gs-{i}", "name": "n", "downloadUrl": "https://sd/x"} for i in range(21)
                ]
            )
        )


def test_contract_allows_unknown_extension_fields():
    """验证契约对未知扩展字段保持宽容，不会因前向兼容字段而拒绝。"""
    validate_digital_employee(_minimal(executionMode="hybrid", someFutureField={"a": 1}))


def test_zod_len_counts_utf16_code_units():
    """验证 zod_len 按 UTF-16 code unit 计数，与 JS String.length 对齐。"""
    assert zod_len("财务") == 2
    # emoji 在 UTF-16 里占两个 code unit，JS String.length === 2
    assert zod_len("🙂") == 2
    assert zod_len("a🙂b") == 4


def test_clamp_text_truncates_by_utf16_units():
    """验证 clamp_text 按 UTF-16 单元截断，且不会把代理对裁成半个字符。"""
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
    """验证 clamp_items 保留前 N 项，并通过溢出标记告知被截断。"""
    kept, overflowed = clamp_items([1, 2, 3], 2)
    assert kept == [1, 2]
    assert overflowed is True
    kept, overflowed = clamp_items([1], 2)
    assert (kept, overflowed) == ([1], False)
