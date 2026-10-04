"""Migrate the old root-level market.db catalogue into stockpilot.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from database import create_tables, get_connection

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "market.db"


def migrate() -> int:
    if not LEGACY.exists():
        print("No legacy market.db found; nothing to migrate.")
        return 0
    create_tables()
    legacy = sqlite3.connect(LEGACY)
    destination = get_connection()
    try:
        rows = legacy.execute(
            "SELECT name, symbol, exchange, country, sector FROM symbols"
        ).fetchall()
        destination.executemany(
            """
            INSERT INTO symbols(name, symbol, exchange, country, sector)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        destination.commit()
    except sqlite3.Error as error:
        raise RuntimeError(f"Legacy database migration failed: {error}") from error
    finally:
        legacy.close()
        destination.close()
    print(f"Migrated {len(rows)} catalogue rows. Remove {LEGACY.name} after verification.")
    return len(rows)


if __name__ == "__main__":
    migrate()
