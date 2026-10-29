import base64
import hashlib

from app.security.auth import hash_password, verify_password


def _legacy_hash(password: str, app_secret: str = "test-secret-key-for-legacy") -> str:
    """复刻旧版 hash_password()：盐值由 APP_SECRET 派生。"""
    salt = hashlib.sha256(app_secret.encode("utf-8")).hexdigest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"


def test_same_password_produces_different_hashes() -> None:
    """验证同一密码两次哈希结果不同，说明每次哈希都使用随机盐。"""
    h1 = hash_password("mypassword")
    h2 = hash_password("mypassword")
    assert h1 != h2


def test_correct_password_accepted_and_wrong_rejected() -> None:
    """验证正确密码校验通过，错误密码校验返回 False。"""
    stored = hash_password("correcthorse")
    assert verify_password("correcthorse", stored) is True
    assert verify_password("wrongpassword", stored) is False


def test_legacy_hash_verifies_with_new_verify() -> None:
    """验证旧版算法生成的哈希仍能被新版校验逻辑正确识别。"""
    old_hash = _legacy_hash("oldpassword")
    assert verify_password("oldpassword", old_hash) is True
    assert verify_password("wrongpassword", old_hash) is False


def test_malformed_hash_returns_false() -> None:
    """验证哈希格式非法或为空串时校验返回 False 而不抛异常。"""
    assert verify_password("anypassword", "not-a-valid-hash") is False
    assert verify_password("anypassword", "") is False
