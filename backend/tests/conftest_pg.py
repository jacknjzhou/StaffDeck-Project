import os

import pytest


@pytest.fixture(scope="session")
def pg_url() -> str:
    """提供集成测试所用的真实 PostgreSQL 连接地址。

    本地未配置 ``STAFFDECK_TEST_PG_URL`` 时跳过相关用例；CI 里必须显式配置（工作流
    自带 postgres 服务），缺失即判失败，避免 PG 路径静默退化成「全部跳过」。
    """
    url = os.environ.get("STAFFDECK_TEST_PG_URL", "")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("STAFFDECK_TEST_PG_URL missing in CI")
        pytest.skip("STAFFDECK_TEST_PG_URL not set; PostgreSQL integration skipped")
    return url
