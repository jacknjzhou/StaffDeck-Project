import os

import pytest

import desktop_launcher


@pytest.fixture(autouse=True)
def _clean_runtime_env():
    """保存并清空运行时环境变量，测试结束后恢复，避免用例间相互污染。"""
    keys = (
        "TOOL_BASE_URL",
        "CORS_ORIGINS",
        "ULTRARAG_PORT",
        "ULTRARAG_PORT_RANGE_START",
        "ULTRARAG_PORT_RANGE_END",
        "ULTRARAG_DOTENV",
    )
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_launcher_sets_tool_base_url_to_actual_port(monkeypatch) -> None:
    """验证启动器按实际监听端口写入 TOOL_BASE_URL，并同步补全 CORS 来源。"""
    monkeypatch.delenv("TOOL_BASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    desktop_launcher.apply_runtime_env({"app": "single_port_app:app", "host": "127.0.0.1", "port": 6123})
    assert os.environ["TOOL_BASE_URL"] == "http://127.0.0.1:6123"
    assert "http://127.0.0.1:6123" in os.environ["CORS_ORIGINS"]


def test_launcher_respects_user_tool_base_url(monkeypatch) -> None:
    """验证用户显式设置的 TOOL_BASE_URL 不会被启动器覆盖。"""
    monkeypatch.setenv("TOOL_BASE_URL", "http://example.com")
    desktop_launcher.apply_runtime_env({"app": "single_port_app:app", "host": "127.0.0.1", "port": 6123})
    assert os.environ["TOOL_BASE_URL"] == "http://example.com"


def test_config_reads_new_port_after_apply(monkeypatch) -> None:
    """验证应用运行时环境后配置能读到新端口，不残留旧缓存值。"""
    from app.config import get_settings
    monkeypatch.delenv("TOOL_BASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    desktop_launcher.apply_runtime_env({"app": "single_port_app:app", "host": "127.0.0.1", "port": 6123})
    get_settings.cache_clear()
    try:
        assert get_settings().normalized_tool_base_url.endswith(":6123")
    finally:
        get_settings.cache_clear()


def test_cors_includes_new_port_origin(monkeypatch) -> None:
    """验证 CORS 白名单包含新端口对应的来源地址。"""
    from app.config import get_settings
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    desktop_launcher.apply_runtime_env({"app": "single_port_app:app", "host": "127.0.0.1", "port": 6123})
    get_settings.cache_clear()
    try:
        assert "http://127.0.0.1:6123" in get_settings().cors_origin_list
    finally:
        get_settings.cache_clear()
