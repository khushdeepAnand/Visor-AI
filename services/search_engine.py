"""Fast, parameterized search over the consolidated symbol catalogue."""

from __future__ import annotations

import pandas as pd

from services.db.factory import dao_session


def _ensure_catalogue() -> None:
    with dao_session() as factory:
        row = factory.db.fetchone("SELECT COUNT(*) AS n FROM symbols")
        count = row["n"] if row else 0
    if count == 0:
        from build_market_database import build
        build()


def search_symbol(query, limit=25):
    query = str(query or "").strip()
    if not query:
        return pd.DataFrame()
    _ensure_catalogue()
    limit = max(1, min(int(limit), 100))
    with dao_session() as factory:
        connection = factory.db
        rows = connection.fetchall(connection.sql("""
            SELECT name, symbol, exchange, country, sector
            FROM symbols
            WHERE LOWER(name) LIKE LOWER(?) OR LOWER(symbol) LIKE LOWER(?)
            ORDER BY
                CASE WHEN LOWER(symbol) = LOWER(?) THEN 0
                     WHEN LOWER(name) = LOWER(?) THEN 1
                     WHEN LOWER(symbol) LIKE LOWER(?) THEN 2
                     ELSE 3 END,
                name ASC
            LIMIT ?
            """), (f"%{query}%", f"%{query}%", query, query, f"{query}%", limit))
        return pd.DataFrame(rows, columns=["name", "symbol", "exchange", "country", "sector"])


def get_popular(limit=20):
    with dao_session() as factory:
        rows = factory.db.fetchall(factory.db.sql("SELECT name, symbol, exchange FROM symbols ORDER BY id LIMIT ?"), (max(1, min(int(limit), 100)),))
        return pd.DataFrame(rows, columns=["name", "symbol", "exchange"])


def get_symbol(symbol):
    with dao_session() as factory:
        rows = factory.db.fetchall(factory.db.sql("SELECT * FROM symbols WHERE LOWER(symbol)=LOWER(?) LIMIT 1"), (str(symbol or "").strip(),))
        return pd.DataFrame(rows, columns=["id", "name", "symbol", "exchange", "country", "sector"])


def symbol_exists(symbol):
    with dao_session() as factory:
        row = factory.db.fetchone(factory.db.sql(
            "SELECT 1 FROM symbols WHERE LOWER(symbol)=LOWER(?) LIMIT 1"),
            (str(symbol or "").strip(),),
        )
        return row is not None
