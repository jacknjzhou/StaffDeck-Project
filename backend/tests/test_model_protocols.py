from __future__ import annotations

from types import MappingProxyType

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import ModelConfig
from app.llm.model_config_resolver import (
    resolve_model_config_for_runtime,
    resolve_model_config_for_verification,
)
from app.llm.client import _normalize_extra_body
from app.llm.model_protocols import (
    ModelApiProtocol,
    available_model_protocols,
    model_config_fingerprint,
    normalize_chat_protocol_options,
    resolve_api_protocol,
)


def _session() -> Session:
    """创建绑定内存 SQLite 的会话，让每个用例使用独立的模型配置库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _row(**overrides) -> ModelConfig:
    """构造字段齐全的模型配置行，便于用例只覆盖关心的字段。"""
    values = {
        "id": "model_a",
        "tenant_id": "tenant_a",
        "name": "Chat",
        "api_key_encrypted": "encrypted",
        "model": "model-a",
        "api_protocol": "openai_chat_completions",
        "protocol_options_json": {
            "openai_chat_completions": {"thinking": {"type": "disabled"}}
        },
        "extra_body_json": {"legacy_vendor_flag": True},
        "trust_status": "legacy_trusted",
        "enabled": True,
    }
    values.update(overrides)
    return ModelConfig(**values)


def test_protocol_boundary_accepts_only_chat_compatibility() -> None:
    """验证兼容提供商缺省映射为 chat completions，其他提供商报 MODEL_PROVIDER_UNSUPPORTED。"""
    assert resolve_api_protocol(None, "openai_compatible") == (
        ModelApiProtocol.OPENAI_CHAT_COMPLETIONS
    )
    with pytest.raises(HTTPException) as exc_info:
        resolve_api_protocol(None, "anthropic")
    assert exc_info.value.detail == "MODEL_PROVIDER_UNSUPPORTED"


def test_chat_thinking_options_are_strictly_typed() -> None:
    """验证 thinking 选项只接受白名单字段，多余字段报 MODEL_PROTOCOL_OPTIONS_INVALID。"""
    assert normalize_chat_protocol_options(
        {"thinking": {"type": "disabled", "clear_thinking": True}}
    ) == {"thinking": {"type": "disabled", "clear_thinking": True}}
    with pytest.raises(HTTPException) as exc_info:
        normalize_chat_protocol_options({"thinking": {"type": "disabled", "vendor": 1}})
    assert exc_info.value.detail == "MODEL_PROTOCOL_OPTIONS_INVALID"


def test_fingerprint_normalizes_equivalent_base_urls() -> None:
    """验证 base_url 的大小写、默认端口与尾斜杠差异不改变配置指纹。"""
    common = {
        "api_protocol": "openai_chat_completions",
        "model": "model-a",
        "key_revision": 1,
        "protocol_options": {},
        "security_revision": 1,
    }
    assert model_config_fingerprint(base_url="HTTPS://EXAMPLE.COM:443/v1/", **common) == (
        model_config_fingerprint(base_url="https://example.com/v1", **common)
    )


def test_runtime_resolver_preserves_legacy_chat_options_as_read_only() -> None:
    """验证运行时解析把旧版 chat 选项规整为只读映射，并保留 legacy_extra_body。"""
    with _session() as db:
        db.add(_row())
        db.commit()

        resolved = resolve_model_config_for_runtime(db, "tenant_a", "model_a")

        assert isinstance(resolved.protocol_options, MappingProxyType)
        assert _normalize_extra_body(resolved.protocol_options) == {
            "thinking": {"type": "disabled"}
        }
        assert resolved.legacy_extra_body["legacy_vendor_flag"] is True
        with pytest.raises(TypeError):
            resolved.protocol_options["new"] = True  # type: ignore[index]


def test_verification_resolver_preserves_provider_extra_body() -> None:
    """验证校验解析在模型未启用时仍可读取，并原样保留提供商 extra_body。"""
    with _session() as db:
        db.add(
            _row(
                trust_status="unverified",
                enabled=False,
                verification_attempt_id="attempt_a",
                verification_attempt_status="verifying",
                extra_body_json={"chat_template_kwargs": {"enable_thinking": False}},
            )
        )
        db.commit()

        resolved = resolve_model_config_for_verification(
            db, "tenant_a", "model_a", "attempt_a"
        )

        assert _normalize_extra_body(resolved.legacy_extra_body) == {
            "chat_template_kwargs": {"enable_thinking": False}
        }


def test_runtime_resolver_rejects_unverified_but_verification_resolver_allows_it() -> None:
    """验证运行时解析拒绝未验证模型（MODEL_CONFIG_DISABLED），校验解析允许同一条记录。"""
    with _session() as db:
        db.add(
            _row(
                trust_status="unverified",
                enabled=False,
                verification_attempt_id="attempt_a",
                verification_attempt_status="verifying",
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            resolve_model_config_for_runtime(db, "tenant_a", "model_a")
        assert exc_info.value.detail == "MODEL_CONFIG_DISABLED"

        resolved = resolve_model_config_for_verification(
            db, "tenant_a", "model_a", "attempt_a"
        )
        assert resolved.id == "model_a"


def test_verified_runtime_requires_matching_fingerprint() -> None:
    """验证已验证模型的指纹过期时要求重新校验（MODEL_CONFIG_VERIFICATION_REQUIRED）。"""
    with _session() as db:
        db.add(_row(trust_status="verified", verified_fingerprint="stale"))
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            resolve_model_config_for_runtime(db, "tenant_a", "model_a")
        assert exc_info.value.detail == "MODEL_CONFIG_VERIFICATION_REQUIRED"


def test_all_implemented_protocols_are_available() -> None:
    """验证已实现的四种协议全部出现在可用协议列表中，且顺序固定。"""
    assert available_model_protocols() == [
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "gemini_generate_content",
    ]


def test_snapshot_model_config_preserves_anthropic_protocol_and_options() -> None:
    """验证快照保留 Anthropic 协议枚举，且不放大最小输出 token 上限。"""
    from app.llm.model_config_resolver import snapshot_model_config

    row = _row(
        api_protocol="anthropic_messages",
        protocol_options_json={"anthropic_messages": {}},
        extra_body_json={},
    )

    snapshot = snapshot_model_config(row, min_output_tokens=16_384)

    assert snapshot.api_protocol is ModelApiProtocol.ANTHROPIC_MESSAGES
    assert snapshot.max_output_tokens == row.max_output_tokens
