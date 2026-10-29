import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from app.capabilities.errors import CapabilityErrorInfo

SCHEMA_DIR = Path(__file__).parents[1] / "app" / "capabilities" / "schemas"


def load_schema(name: str) -> dict[str, object]:
    """读取 app/capabilities/schemas 下的 JSON Schema 文件并解析为字典。"""
    return json.loads((SCHEMA_DIR / name).read_text())


def test_knowledge_request_schema_rejects_unknown_top_level_fields() -> None:
    """验证知识检索请求 schema 拒绝未声明的顶层字段，避免凭据越界传递。"""
    schema = load_schema("knowledge.search.request.v1.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = {
        "context": {
            "user_id": "user-1",
            "agent_id": "agent-1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "channel": "web",
        },
        "query": "policy",
    }
    assert not list(validator.iter_errors(valid))
    invalid = {**valid, "provider_secret": "must-not-cross-contract"}
    assert list(validator.iter_errors(invalid))


def test_knowledge_request_schema_requires_minimal_session_turn_context() -> None:
    """验证上下文必填字段缺失或混入操作级幂等键时，请求 schema 会报错。"""
    schema = load_schema("knowledge.search.request.v1.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = {
        "context": {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "user_id": "user-1",
            "agent_id": "agent-1",
            "channel": "web",
        },
        "query": "policy",
    }
    assert not list(validator.iter_errors(valid))
    missing_agent = {
        **valid,
        "context": {
            key: value for key, value in valid["context"].items() if key != "agent_id"
        },
    }
    assert list(validator.iter_errors(missing_agent))

    with_operation_idempotency = {
        **valid,
        "context": {**valid["context"], "idempotency_key": "turn-1:search"},
    }
    assert list(validator.iter_errors(with_operation_idempotency))


def test_knowledge_result_schema_allows_namespaced_extensions_only() -> None:
    """验证检索结果 schema 只接受符合命名空间规范的 extensions 键。"""
    schema = load_schema("knowledge.search.result.v1.json")
    validator = Draft202012Validator(schema)
    valid = {
        "query_id": "q-1",
        "items": [],
        "outcome": "complete",
        "warnings": [],
        "extensions": {"vendor_x": {"rerank": 0.9}},
    }
    assert not list(validator.iter_errors(valid))
    invalid = {**valid, "extensions": {"Vendor-X": {}}}
    assert list(validator.iter_errors(invalid))


def test_provider_error_schema_requires_retryability() -> None:
    """验证 provider 错误 schema 强制要求 retryable，缺失即校验失败。"""
    schema = load_schema("provider.error.v1.json")
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors({"error_code": "X"}))


def test_provider_error_schema_rejects_reserved_extension_namespaces() -> None:
    """验证 provider 错误 schema 禁止使用 core、staffdeck 等保留扩展命名空间。"""
    schema = load_schema("provider.error.v1.json")
    validator = Draft202012Validator(schema)
    valid = {
        "error_code": "X",
        "message": "failed",
        "retryable": False,
        "request_id": "req-1",
        "extensions": {"vendor_x": {}},
    }
    assert not list(validator.iter_errors(valid))
    for namespace in ("core", "staffdeck"):
        invalid = {**valid, "extensions": {namespace: {}}}
        assert list(validator.iter_errors(invalid))


def test_python_provider_error_has_one_wire_error_code_mapping() -> None:
    """验证 Python 侧 CapabilityErrorInfo 的输出可直接通过错误 schema 校验。"""
    schema = load_schema("provider.error.v1.json")
    validator = Draft202012Validator(schema)
    payload = CapabilityErrorInfo(
        code="KNOWLEDGE_TIMEOUT",
        message="timeout",
        retryable=True,
        request_id="req-1",
    ).to_payload()
    assert not list(validator.iter_errors(payload))
