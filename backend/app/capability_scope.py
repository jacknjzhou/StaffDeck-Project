from __future__ import annotations

from typing import Literal, TypeAlias


CapabilityScope: TypeAlias = Literal["general", "sop_specific"]

GENERAL_CAPABILITY_SCOPE: CapabilityScope = "general"
SOP_SPECIFIC_CAPABILITY_SCOPE: CapabilityScope = "sop_specific"


def normalize_capability_scope(value: object) -> CapabilityScope:
    """归一化持久化或导入的能力范围取值，避免历史脏数据导致旧记录不可用。

    仅识别 `sop_specific`，其余任何取值都回退为 `general`。
    """

    if value == SOP_SPECIFIC_CAPABILITY_SCOPE:
        return SOP_SPECIFIC_CAPABILITY_SCOPE
    return GENERAL_CAPABILITY_SCOPE
