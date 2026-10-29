from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "deepchat_digital_employee.schema.json"
)
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def validate_digital_employee(payload: dict[str, Any]) -> None:
    """校验 StaffDeck 载荷满足 deepchat 的 zod 契约。

    失败时抛 AssertionError 并附带首个违规字段的 JSON 指针；这样长度类回归
    能直接指出问题字段，而不是把整个员工对象抛给人看。
    """
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    pointer = "/" + "/".join(str(part) for part in first.path)
    raise AssertionError(f"digitalEmployee contract violation at {pointer}: {first.message}")
