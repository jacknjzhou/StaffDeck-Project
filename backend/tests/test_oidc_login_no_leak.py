"""守卫:test_oidc_login.py 不得跨测试污染 User.model_fields。

test_oidc_login.py 曾全局改写 User 的 created_at/updated_at default_factory
且不恢复(fix round 1 评审发现)。pytest 按文件名字母序收集:本文件名排在
test_oidc_login.py 之后,同进程运行时若恢复逻辑缺失/失效,下面的断言会失败。

单独运行本文件时断言平凡通过——必须与 test_oidc_login.py 同进程按序运行,
验证命令见 task-8-report.md 的 Fix Round 1 一节。
"""

from app.db.models import User, utc_now


def test_user_timestamp_default_factory_restored_to_original():
    """验证其他测试改写 User 时间戳 factory 后已恢复为原始 utc_now。"""
    for field_name in ("created_at", "updated_at"):
        factory = User.model_fields[field_name].default_factory
        assert factory is utc_now, (
            f"User.model_fields[{field_name!r}].default_factory 被其他测试改写且未恢复"
        )
        # 行为校验:原始 utc_now 返回 naive datetime(泄漏的补丁返回 tz-aware)
        assert factory().tzinfo is None
