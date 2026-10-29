import os

import pytest


@pytest.fixture(scope="session")
def pg_url() -> str:
    """提供集成测试所用的真实 PostgreSQL 连接地址，未配置时跳过相关用例。"""
    url = os.environ.get("STAFFDECK_TEST_PG_URL", "")
    if not url:
        pytest.skip("STAFFDECK_TEST_PG_URL not set; PostgreSQL integration skipped")
    return url
