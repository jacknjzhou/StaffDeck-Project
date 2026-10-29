from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError, reset_tzpath


def test_iana_timezone_available_without_system_database() -> None:
    """验证清空系统时区搜索路径后仍能凭 tzdata 包加载 IANA 时区。"""
    ZoneInfo.clear_cache()
    reset_tzpath(())
    try:
        timezone = ZoneInfo("Asia/Shanghai")
        assert timezone.key == "Asia/Shanghai"
    except ZoneInfoNotFoundError as exc:
        raise AssertionError("The tzdata package must provide IANA timezones on Windows") from exc
    finally:
        ZoneInfo.clear_cache()
        reset_tzpath(TZPATH)
