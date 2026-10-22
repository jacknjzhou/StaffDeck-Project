import os

import pytest


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Real PostgreSQL URL for integration tests; skip when not provided."""
    url = os.environ.get("STAFFDECK_TEST_PG_URL", "")
    if not url:
        pytest.skip("STAFFDECK_TEST_PG_URL not set; PostgreSQL integration skipped")
    return url
