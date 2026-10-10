"""Programmatic Alembic migration runner.

Usage:
    from scripts.run_migrations import upgrade_to_head
    upgrade_to_head()               # existing SQLite fallback; optional selected Postgres
    upgrade_to_head("postgresql://...")  # explicit DSN
"""
from __future__ import annotations

import os
from typing import Optional

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db.configuration import migration_url, sqlalchemy_url


def upgrade_to_head(database_url: Optional[str] = None) -> str:
    """Run 'alembic upgrade head' and return the resulting revision id.

    An explicit argument wins. Failures propagate; a failed upgrade is never
    reported as successful. Known Supabase pooler endpoints are rejected.
    """
    from alembic import command
    from alembic.config import Config

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(project_root, "alembic.ini"))

    db_file = os.path.join(project_root, "database", "stockpilot.db")
    url = migration_url(configured_url=database_url, sqlite_fallback=f"sqlite:///{db_file}")

    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", os.path.join(project_root, "alembic"))

    command.upgrade(cfg, "head")
    return _current_revision(url)


def _current_revision(url: str) -> str:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    engine = create_engine(sqlalchemy_url(url), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            if revision is None:
                raise RuntimeError("Migration completed without a recorded revision")
            return revision
    finally:
        engine.dispose()


if __name__ == "__main__":
    from services.secure_secrets import load_windows_secure_secrets
    load_windows_secure_secrets()
    try:
        print(upgrade_to_head())
    except Exception:
        # Driver errors can include DSNs or SQL values; do not print them.
        print("Database migration failed; no success claimed. Check endpoint, grants and schema with the operator.", file=sys.stderr)
        raise SystemExit(1)
