import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    """由 APP_SECRET 的 SHA-256 摘要构造 Fernet 实例，保证同一配置下密钥稳定。"""
    secret = get_settings().app_secret.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    """用 APP_SECRET 派生的密钥加密敏感配置值，返回可持久化的 token 字符串。"""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    """解密 encrypt_secret 生成的 token；空值原样返回空串，密钥不匹配时抛 ValueError。"""
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Secret cannot be decrypted with current APP_SECRET") from exc


def mask_secret(value: str) -> str:
    """生成脱敏展示串：空值返回空串，8 位及以下整体掩码，更长值保留前 3 位与后 4 位。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}-****{value[-4:]}"

