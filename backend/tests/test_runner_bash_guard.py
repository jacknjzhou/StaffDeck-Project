import sys

from app import paths
from app.general_skills import runner


def test_bash_unsupported_on_windows(monkeypatch) -> None:
    """验证平台为 Windows 时 bash 执行被判为不支持。"""
    monkeypatch.setattr(sys, "platform", "win32")
    assert runner._bash_supported() is False


def test_bash_unsupported_when_frozen(monkeypatch) -> None:
    """验证冻结打包运行时即使平台受支持也不启用 bash。"""
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert runner._bash_supported() is False


def test_bash_supported_on_dev_posix(monkeypatch) -> None:
    """验证开发态 POSIX 平台下 bash 执行可用。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert runner._bash_supported() is True
