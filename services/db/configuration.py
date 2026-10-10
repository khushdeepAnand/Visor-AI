"""Optional PostgreSQL URL selection. Merely configuring URLs never switches SQLite."""
from __future__ import annotations

import os
from urllib.parse import urlsplit


def postgres_selected() -> bool:
    return (os.getenv("DB_BACKEND") or os.getenv("STOCKPILOT_DB_TYPE") or "sqlite").lower() in {"postgres", "postgresql"}


def postgres_url(*, migration: bool = False) -> str | None:
    """Call only on a selected Postgres path; retain the legacy single-URL alias."""
    names = ("DATABASE_MIGRATION_URL", "DATABASE_URL", "STOCKPILOT_DATABASE_URL") if migration else (
        "DATABASE_URL", "STOCKPILOT_DATABASE_URL", "DATABASE_MIGRATION_URL")
    return next((os.environ[name] for name in names if os.getenv(name)), None)


def migration_url(*, configured_url: str | None = None, sqlite_fallback: str) -> str:
    """An explicitly supplied Postgres migration URL wins; SQLite fallback is unchanged."""
    if configured_url and configured_url.startswith(("postgresql://", "postgresql+", "postgres://")):
        return _direct_url(configured_url)
    if postgres_selected():
        url = postgres_url(migration=True)
        if not url:
            raise ValueError("PostgreSQL migration URL required when Postgres is selected")
        return _direct_url(url)
    return sqlite_fallback


def _direct_url(url: str) -> str:
    """Reject known Supavisor transaction/session endpoints for schema changes."""
    parsed = urlsplit(url)
    if parsed.port == 6543 or (parsed.hostname or "").endswith(".pooler.supabase.com"):
        raise ValueError("Migrations require a direct PostgreSQL endpoint, not a Supabase pooler URL")
    return url


def sqlalchemy_url(url: str) -> str:
    """Pin the installed synchronous driver across SQLAlchemy 2.0/2.1 defaults."""
    if url.startswith(("postgresql://", "postgres://")):
        return "postgresql+psycopg2://" + url.split("://", 1)[1]
    return url
