"""Tool-name namespace shared by the distribution payload and the MCP catalog.

The two surfaces must emit byte-equal names (deepchat matches its session-level
allowlist against them), so the mapping lives here rather than being derived
twice.

Why the name is not simply ``staffdeck:{slug}``: deepchat drops every MCP tool
whose name fails ``/^[a-zA-Z0-9_-]+$/`` (``main/mcp/toolManager.ts``), and
OpenAI-compatible APIs reject function names outside the same set. A colon
would make every remote tool silently disappear, and dots in real slugs
(``expense.submit``) would go the same way.

Normalising ``.`` to ``_`` is many-to-one, so names that need normalising carry
a short digest of the original slug. Everyday slugs that are already safe keep
their readable form.
"""

from __future__ import annotations

import hashlib
import re

TOOL_PREFIX = "staffdeck_"

# OpenAI-compatible function names are capped at 64 characters. Deepchat does
# not truncate, it drops the tool, so the ceiling has to hold on our side.
MODEL_TOOL_NAME_MAX = 64

_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_UNSAFE_RUN = re.compile(r"[^A-Za-z0-9_-]+")
_DIGEST_LENGTH = 8


def _digest(slug: str) -> str:
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


def namespaced_tool_name(slug: str) -> str:
    """Map a tool slug onto the single name both deepchat and the model accept.

    Deterministic: the same slug always yields the same name, so a re-published
    catalog is byte-identical. Tool slugs that are already safe and short keep a
    readable form; anything else gains a digest so distinct slugs cannot collide
    after normalisation.
    """
    if _SAFE_SLUG.match(slug):
        candidate = f"{TOOL_PREFIX}{slug}"
        if len(candidate) <= MODEL_TOOL_NAME_MAX:
            return candidate
        # Too long: fall through to the digested form so truncation stays unique.
        return _with_digest(slug, slug)

    normalized = _UNSAFE_RUN.sub("_", slug).strip("_") or "tool"
    return _with_digest(normalized, slug)


def _with_digest(normalized: str, original: str) -> str:
    suffix = f"_{_digest(original)}"
    budget = MODEL_TOOL_NAME_MAX - len(TOOL_PREFIX) - len(suffix)
    return f"{TOOL_PREFIX}{normalized[:budget]}{suffix}"


def tool_name_from_namespace(name: str) -> str | None:
    """Reverse :func:`namespaced_tool_name` when the mapping was lossless.

    Returns ``None`` for digested names — a digest names the original but cannot
    reconstruct it. Callers resolving a live call must look the name up in the
    catalog the employee was given, never re-derive the slug by string surgery.
    """
    if not name.startswith(TOOL_PREFIX):
        return None
    bare = name[len(TOOL_PREFIX) :]
    if not bare or not _SAFE_SLUG.match(bare):
        return None
    # A trailing digest means the original slug had characters we had to rewrite.
    if re.search(rf"_[0-9a-f]{{{_DIGEST_LENGTH}}}$", bare):
        return None
    return bare
