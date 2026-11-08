"""生产配置门禁：生产环境必须显式提供强 APP_SECRET，开发环境保留默认值。"""

import pytest

from app.config import Settings

DEFAULT_SECRET = "change-me-in-development"


def _settings(monkeypatch: pytest.MonkeyPatch, **kwargs) -> Settings:
    """构造隔离的配置：先清除宿主环境变量，避免环境覆盖显式参数。"""
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return Settings(_env_file=None, **kwargs)


def test_production_rejects_default_secret(monkeypatch):
    """生产环境使用内置默认密钥时必须拒绝启动。"""
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(
            monkeypatch,
            environment="production",
            app_secret=DEFAULT_SECRET,
        )


def test_production_rejects_short_secret(monkeypatch):
    """生产环境的 APP_SECRET 少于 32 字符时拒绝启动。"""
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(monkeypatch, environment="production", app_secret="short-secret")


def test_production_accepts_strong_secret(monkeypatch):
    """生产环境使用足够长的随机密钥时可以构造配置。"""
    settings = _settings(monkeypatch, environment="production", app_secret="x" * 48)
    assert settings.is_production is True


def test_development_allows_default_secret(monkeypatch):
    """开发环境保留默认密钥，避免本地启动被阻断。"""
    settings = _settings(monkeypatch, environment="development")
    assert settings.is_production is False
    assert settings.app_secret == DEFAULT_SECRET
