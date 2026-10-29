import pytest

from app.channels.crypto import decrypt_channel_secret, encrypt_channel_secret
from app.config import get_settings


def test_encrypt_decrypt_roundtrip() -> None:
    """验证渠道密钥加密后与明文不同，且能解密回原文。"""
    token = encrypt_channel_secret("ilinkbot_token_123")
    assert token != "ilinkbot_token_123"
    assert decrypt_channel_secret(token) == "ilinkbot_token_123"


def test_key_derivation_stable_across_calls() -> None:
    """验证相同明文两次加密的密文不同，但都能解密回原文，即密钥派生稳定。"""
    first = encrypt_channel_secret("same-secret")
    second = encrypt_channel_secret("same-secret")
    # Fernet 带随机 IV,密文不同但密钥派生稳定,都能解回明文
    assert decrypt_channel_secret(first) == "same-secret"
    assert decrypt_channel_secret(second) == "same-secret"


def test_channel_secret_change_makes_old_token_undecryptable(monkeypatch) -> None:
    """验证 channel_secret 变更后，此前加密的密文无法再解密。"""
    token = encrypt_channel_secret("same-secret")
    changed = get_settings().model_copy(update={"channel_secret": "another-secret"})
    monkeypatch.setattr("app.channels.crypto.get_settings", lambda: changed)
    with pytest.raises(Exception):
        decrypt_channel_secret(token)
