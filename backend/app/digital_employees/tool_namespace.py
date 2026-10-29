"""分发载荷与 MCP 目录共用的工具名命名空间。

两个出口必须产出逐字节相同的名字（deepchat 用它比对会话级白名单），
因此映射只在这里定义一次，而不是在两处各推导一遍。

名字不直接采用 ``staffdeck:{slug}`` 的原因：deepchat 会丢弃名字不匹配
``/^[a-zA-Z0-9_-]+$/`` 的 MCP 工具（``main/mcp/toolManager.ts``），
OpenAI 兼容 API 也拒绝同一字符集之外的函数名。冒号会让所有远程工具
静默消失，真实 slug 中的点（``expense.submit``）同理。

把 ``.`` 归一化成 ``_`` 是多对一映射，所以需要归一化的名字会带上原始
slug 的短摘要；本就安全且不超长的常见 slug 保留可读形式。
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
    """取 slug 的 SHA-256 前 8 位十六进制摘要，作为归一化后防撞名的短后缀。"""
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


def namespaced_tool_name(slug: str) -> str:
    """把工具 slug 映射成 deepchat 与模型都能接受的唯一工具名。

    结果是确定性的：同一 slug 永远得到同一个名字，重新发布目录逐字节一致。
    已安全且不超长的 slug 保留可读形式，其余追加原始 slug 的摘要避免撞名。
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
    """拼出带摘要后缀的工具名，正文按 MODEL_TOOL_NAME_MAX 的剩余预算截断。"""
    suffix = f"_{_digest(original)}"
    budget = MODEL_TOOL_NAME_MAX - len(TOOL_PREFIX) - len(suffix)
    return f"{TOOL_PREFIX}{normalized[:budget]}{suffix}"


def tool_name_from_namespace(name: str) -> str | None:
    """在映射无信息损失时反解出原始工具 slug，带摘要后缀的名字返回 None。

    摘要只能标识原始 slug 而无法还原它；解析真实调用应到员工拿到的目录里查名字，
    不要靠字符串手术反推 slug。
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
