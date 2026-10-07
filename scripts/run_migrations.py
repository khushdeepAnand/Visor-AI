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

from services.db.configuration import migration_url


def upgrade_to_head(database_url: Optional[str] = None) -> str:
    """Run 'alembic upgrade head' and return the resulting revision id.

    Selected Postgres uses DATABASE_MIGRATION_URL, then DATABASE_URL, then the
    legacy STOCKPILOT_DATABASE_URL. Unselected SQLite retains its old fallback.
    An explicit argument wins. Returns
    'unknown' when Alembic is not installed so app startup never breaks.
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        return "unknown"

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(project_root, "alembic.ini"))

    db_file = os.path.join(project_root, "database", "stockpilot.db")
    url = database_url or migration_url(sqlite_fallback=f"sqlite:///{db_file}")

    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", os.path.join(project_root, "alembic"))

    try:
        command.upgrade(cfg, "head")
    except Exception:
        return "unknown"
    return "head"


if __name__ == "__main__":
    print(upgrade_to_head())
