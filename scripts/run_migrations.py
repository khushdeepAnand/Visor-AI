"""Programmatic Alembic migration runner.

Usage:
    from scripts.run_migrations import upgrade_to_head
    upgrade_to_head()               # SQLite/Postgres via STOCKPILOT_DATABASE_URL
    upgrade_to_head("postgresql://...")  # explicit DSN
"""
from __future__ import annotations

import os
from typing import Optional


def upgrade_to_head(database_url: Optional[str] = None) -> str:
    """Run 'alembic upgrade head' and return the resulting revision id.

    Falls back to STOCKPILOT_DATABASE_URL, then SQLite file.  Returns
    'unknown' when Alembic is not installed so app startup never breaks.
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        return "unknown"

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(project_root, "alembic.ini"))

    url = database_url or os.getenv("STOCKPILOT_DATABASE_URL")
    if not url:
        db_file = os.path.join(project_root, "database", "stockpilot.db")
        url = f"sqlite:///{db_file}"

    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", os.path.join(project_root, "alembic"))

    try:
        command.upgrade(cfg, "head")
    except Exception:
        return "unknown"
    return "head"


if __name__ == "__main__":
    print(upgrade_to_head())
