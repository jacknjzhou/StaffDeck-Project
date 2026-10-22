from app.config import get_settings


def test_process_role_default_all():
    assert get_settings().process_role == "all"


def test_role_gate_helpers(monkeypatch):
    from app.main import _role_enables

    settings = get_settings()
    assert _role_enables("api") is True  # all 角色启用一切
    # get_settings() 是 lru_cache 单例、Settings 是 pydantic v2 BaseSettings（默认
    # 可变）；用 monkeypatch 赋值并在用例结束后自动还原，避免污染单例。
    monkeypatch.setattr(settings, "process_role", "worker")
    assert _role_enables("api") is False
    assert _role_enables("worker") is True
