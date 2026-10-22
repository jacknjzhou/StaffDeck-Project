# pytest 只自动加载 conftest.py；这里转发 conftest_pg 中的 PG fixture 使其对全部用例可见。
from conftest_pg import pg_url  # noqa: F401
