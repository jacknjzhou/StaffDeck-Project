from app import paths


def test_not_frozen_by_default() -> None:
    """验证源码运行环境下 is_frozen() 默认返回 False。"""
    assert paths.is_frozen() is False


def test_app_root_points_to_backend() -> None:
    """验证 app_root() 指向包含 single_port_app.py 的 backend 目录。"""
    assert (paths.app_root() / "single_port_app.py").exists()


def test_resource_dir_dev_equals_app_root() -> None:
    """验证开发态资源目录与 app 根目录一致。"""
    assert paths.resource_dir() == paths.app_root()


def test_user_data_dir_is_absolute_and_exists() -> None:
    """验证用户数据目录为绝对路径且已创建。"""
    d = paths.user_data_dir()
    assert d.is_absolute()
    assert d.exists()
