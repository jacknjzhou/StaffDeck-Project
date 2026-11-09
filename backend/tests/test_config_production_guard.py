"""生产配置门禁：生产环境必须显式提供强 APP_SECRET，开发环境保留默认值。"""

from pathlib import Path

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


@pytest.mark.parametrize(
    "secret",
    [
        "change-me-long-random-replace-with-32-chars-min",
        "changeme-changeme-changeme-changeme",
        "replace-me-please-with-a-real-secret-value",
        "example-secret-example-secret-0123",
        "ExAmPlE-Secret-0123456789abcdef",
    ],
)
def test_production_rejects_placeholder_secrets(monkeypatch, secret):
    """模板/占位片段即使长度达标也必须被拒绝：复制 .env.example 不能启动生产。"""
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(monkeypatch, environment="production", app_secret=secret)


def test_production_rejects_packaged_env_example_secret(monkeypatch):
    """打包模板里的 STAFFDECK_APP_SECRET 原样复制到生产环境必须拒绝启动。"""
    example = Path(__file__).resolve().parents[2] / "packaging/docker/.env.example"
    value = next(
        line.split("=", 1)[1].strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.startswith("STAFFDECK_APP_SECRET=")
    )
    with pytest.raises(ValueError, match="APP_SECRET"):
        _settings(monkeypatch, environment="production", app_secret=value)


def test_production_accepts_strong_secret(monkeypatch):
    """生产环境使用足够长的随机密钥时可以构造配置。"""
    settings = _settings(monkeypatch, environment="production", app_secret="x" * 48)
    assert settings.is_production is True


def test_development_allows_default_secret(monkeypatch):
    """开发环境保留默认密钥，避免本地启动被阻断。"""
    settings = _settings(monkeypatch, environment="development")
    assert settings.is_production is False
    assert settings.app_secret == DEFAULT_SECRET
