"""CLI wrapper around :func:`app.db.migrations.run_migrations`.

Used by the Docker api-role entrypoint to apply Alembic migrations before the
uvicorn server starts. Resolves the database URL from ``STAFFDECK_DATABASE_URL``
and falls back to the configured settings value.

Usage::

    python -m app.db.migrations_cli upgrade
"""

from __future__ import annotations

import os
import sys

from app.config import get_settings
from app.db.migrations import run_migrations


def _resolve_url() -> str:
    url = os.environ.get("STAFFDECK_DATABASE_URL")
    if url:
        return url
    return get_settings().database_url


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "upgrade"
    if action != "upgrade":
        print(f"unsupported migrations action: {action}", file=sys.stderr)
        return 2
    url = _resolve_url()
    run_migrations(url)
    print("migrations applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
