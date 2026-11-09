"""pytest 全局入口：在导入任何 app 模块之前隔离 HOME 与 XDG 目录。

运行时准备代码（``app.runtime_env``、``app.general_skills`` 等）在**导入期**就会向
``~/Library/Application Support``（macOS）等用户目录写锁文件与技能缓存。受限沙箱里
这些写入会失败，表现为与业务无关的 ``runtime_env`` / ``general_skills`` 锁错误。

pytest 在收集并导入测试模块之前先导入本 conftest，因此隔离必须放在模块级、而不是
session 级 fixture 中：fixture 执行时 app 模块已经被导入，目录写入早已发生。

隔离规则：
- ``STAFFDECK_TEST_HOME`` 显式指定时优先使用（便于本地复现/排查）。
- 否则新建进程级临时目录；若 ``HOME`` 已指向本文件创建的目录（重复导入）则复用，
  保证幂等。
- ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_CACHE_HOME`` 一律指向该临时目录，
  不沿用调用方的配置：外部 XDG 目录同样会收到运行期写入。
- 无法创建临时目录时直接 ``pytest.exit``，不带着半隔离的环境继续跑。
- 临时 HOME 只在测试进程存活期间有效，进程退出后不主动清理（``mkdtemp`` 语义），
  残留目录可安全删除。
- 仅改测试进程的环境变量，不触碰生产代码路径。
"""

import os
import tempfile

import pytest

_TEST_HOME_PREFIX = "staffdeck-test-home-"

_test_home = os.environ.get("STAFFDECK_TEST_HOME")
if not _test_home:
    _existing_home = os.environ.get("HOME", "")
    if os.path.basename(_existing_home).startswith(_TEST_HOME_PREFIX):
        _test_home = _existing_home
    else:
        try:
            _test_home = tempfile.mkdtemp(prefix=_TEST_HOME_PREFIX)
        except OSError as exc:
            pytest.exit(f"cannot create isolated HOME: {exc}", returncode=1)

os.environ["HOME"] = _test_home
os.environ["XDG_CONFIG_HOME"] = os.path.join(_test_home, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(_test_home, ".local", "share")
os.environ["XDG_CACHE_HOME"] = os.path.join(_test_home, ".cache")

# pytest 只自动加载 conftest.py；这里转发 conftest_pg 中的 PG fixture 使其对全部用例可见。
from conftest_pg import pg_url  # noqa: E402,F401
