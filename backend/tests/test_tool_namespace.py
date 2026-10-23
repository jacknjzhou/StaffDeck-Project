from __future__ import annotations

import re

from app.digital_employees.tool_namespace import (
    MODEL_TOOL_NAME_MAX,
    TOOL_PREFIX,
    namespaced_tool_name,
    tool_name_from_namespace,
)


def test_prefix_matches_the_deepchat_tool_name_charset():
    """deepchat drops any MCP tool whose name fails /^[a-zA-Z0-9_-]+$/.

    A colon would get the whole tool silently skipped, so the namespace
    separator has to be a character both sides accept.
    """
    assert TOOL_PREFIX == "staffdeck_"


def test_a_slug_that_is_already_safe_keeps_its_readable_name():
    assert namespaced_tool_name("expense_submit") == "staffdeck_expense_submit"
    assert tool_name_from_namespace("staffdeck_expense_submit") == "expense_submit"


def test_every_namespaced_name_passes_the_deepchat_charset():
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
    """`.` -> `_` is many-to-one: two real tools can map onto the same name.

    Deepchat keeps tools in one flat map keyed by name, so a collision would
    silently drop one of them. Only the lossy side gets a digest, which keeps
    everyday tool names readable.
    """
    dotted = namespaced_tool_name("expense.submit")
    underscored = namespaced_tool_name("expense_submit")
    assert dotted != underscored
    assert dotted.startswith("staffdeck_expense_submit_")
    assert underscored == "staffdeck_expense_submit"


def test_the_digest_is_stable_for_the_same_slug():
    assert namespaced_tool_name("expense.submit") == namespaced_tool_name("expense.submit")


def test_distinct_lossy_slugs_stay_distinct():
    assert namespaced_tool_name("a.b") != namespaced_tool_name("a:b")
    assert namespaced_tool_name("a b") != namespaced_tool_name("a.b")


def test_a_long_slug_is_truncated_but_keeps_its_digest():
    name = namespaced_tool_name("a" * 300)
    assert len(name) <= MODEL_TOOL_NAME_MAX
    assert name.startswith(TOOL_PREFIX)
    # Still unique: the digest is what tells two long slugs apart.
    assert namespaced_tool_name("a" * 299 + "b") != name


def test_reverse_lookup_returns_none_for_a_lossy_name():
    # A digest names the original but cannot reconstruct it; callers must
    # resolve through the tool catalog instead of parsing the name.
    assert tool_name_from_namespace(namespaced_tool_name("expense.submit")) is None
    assert tool_name_from_namespace(namespaced_tool_name("a" * 300)) is None


def test_reverse_lookup_rejects_a_foreign_prefix():
    assert tool_name_from_namespace("other_expense_submit") is None
    assert tool_name_from_namespace("") is None
