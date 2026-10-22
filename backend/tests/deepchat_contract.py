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
    """Assert a StaffDeck payload satisfies deepchat's zod contract.

    Raises AssertionError with the JSON pointer of the first violation, so a
    length regression names the offending field instead of the whole employee.
    """
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    pointer = "/" + "/".join(str(part) for part in first.path)
    raise AssertionError(f"digitalEmployee contract violation at {pointer}: {first.message}")
