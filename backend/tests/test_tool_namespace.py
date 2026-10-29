from __future__ import annotations

import re

from app.digital_employees.tool_namespace import (
    MODEL_TOOL_NAME_MAX,
    TOOL_PREFIX,
    namespaced_tool_name,
    tool_name_from_namespace,
)


def test_prefix_matches_the_deepchat_tool_name_charset():
    """验证命名前缀只使用 deepchat 允许的字符集。

    名称不匹配 /^[a-zA-Z0-9_-]+$/ 的 MCP 工具会被 deepchat 丢弃，用冒号做分隔符
    会让整个工具被静默跳过，因此前缀必须是双方都接受的字符。
    """
    assert TOOL_PREFIX == "staffdeck_"


def test_a_slug_that_is_already_safe_keeps_its_readable_name():
    """验证本身安全的 slug 加上前缀后仍可读，并能反向还原。"""
    assert namespaced_tool_name("expense_submit") == "staffdeck_expense_submit"
    assert tool_name_from_namespace("staffdeck_expense_submit") == "expense_submit"


def test_every_namespaced_name_passes_the_deepchat_charset():
    """验证含特殊字符、中文与空串的 slug 生成的工具名都符合字符集要求。"""
    pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    for slug in (
        "expense.submit",
        "order.archive_query",
        "crm.lookup",
        "My Tool!",
        "工具.查询",
        "",
    ):
        assert pattern.match(namespaced_tool_name(slug)), slug


def test_a_lossy_slug_carries_a_digest_so_two_slugs_never_collide():
    """验证经有损映射的 slug 会附加摘要，使两个工具不会重名。

    `.` 转 `_` 是多对一映射，两个真实工具可能映射到同一名称；deepchat 以名称为键
    平铺存放工具，重名会静默丢掉其中一个，因此只给有损的一侧加摘要。
    """
    dotted = namespaced_tool_name("expense.submit")
    underscored = namespaced_tool_name("expense_submit")
    assert dotted != underscored
    assert dotted.startswith("staffdeck_expense_submit_")
    assert underscored == "staffdeck_expense_submit"


def test_the_digest_is_stable_for_the_same_slug():
    """验证同一 slug 多次生成的工具名保持一致。"""
    assert namespaced_tool_name("expense.submit") == namespaced_tool_name("expense.submit")


def test_distinct_lossy_slugs_stay_distinct():
    """验证经有损映射后不同 slug 仍得到不同工具名。"""
    assert namespaced_tool_name("a.b") != namespaced_tool_name("a:b")
    assert namespaced_tool_name("a b") != namespaced_tool_name("a.b")


def test_a_long_slug_is_truncated_but_keeps_its_digest():
    """验证超长 slug 被截断到长度上限，并靠摘要保持唯一。"""
    name = namespaced_tool_name("a" * 300)
    assert len(name) <= MODEL_TOOL_NAME_MAX
    assert name.startswith(TOOL_PREFIX)
    # Still unique: the digest is what tells two long slugs apart.
    assert namespaced_tool_name("a" * 299 + "b") != name


def test_reverse_lookup_returns_none_for_a_lossy_name():
    """验证有损名称无法反向还原时返回 None，调用方应改查工具目录。"""
    # A digest names the original but cannot reconstruct it; callers must
    # resolve through the tool catalog instead of parsing the name.
    assert tool_name_from_namespace(namespaced_tool_name("expense.submit")) is None
    assert tool_name_from_namespace(namespaced_tool_name("a" * 300)) is None


def test_reverse_lookup_rejects_a_foreign_prefix():
    """验证非本服务前缀与空字符串都不会解析出 slug。"""
    assert tool_name_from_namespace("other_expense_submit") is None
    assert tool_name_from_namespace("") is None
