from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config

from alembic import command

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def run_migrations(database_url: str) -> None:
    """Run `alembic upgrade head` against the given URL."""
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    os.environ["STAFFDECK_DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")
