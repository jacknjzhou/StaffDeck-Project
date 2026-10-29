from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config

from alembic import command

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def run_migrations(database_url: str) -> None:
    """把数据库结构升级到最新版本，即对给定 URL 执行 `alembic upgrade head`。

    连接串经环境变量 STAFFDECK_DATABASE_URL 传给 alembic，覆盖其默认配置。
    """
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    os.environ["STAFFDECK_DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")
