"""Fast, parameterized search over the consolidated symbol catalogue."""

from __future__ import annotations

import pandas as pd

from database import get_connection


def _ensure_catalogue() -> None:
    connection = get_connection()
    try:
        count = connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    finally:
        connection.close()
    if count == 0:
        from build_market_database import build
        build()


def search_symbol(query, limit=25):
    query = str(query or "").strip()
    if not query:
        return pd.DataFrame()
    _ensure_catalogue()
    limit = max(1, min(int(limit), 100))
    connection = get_connection()
    try:
        return pd.read_sql_query(
            """
            SELECT name, symbol, exchange, country, sector
            FROM symbols
            WHERE name LIKE ? COLLATE NOCASE OR symbol LIKE ? COLLATE NOCASE
            ORDER BY
                CASE WHEN symbol = ? COLLATE NOCASE THEN 0
                     WHEN name = ? COLLATE NOCASE THEN 1
                     WHEN symbol LIKE ? COLLATE NOCASE THEN 2
                     ELSE 3 END,
                name ASC
            LIMIT ?
            """,
            connection,
            params=(f"%{query}%", f"%{query}%", query, query, f"{query}%", limit),
        )
    finally:
        connection.close()


def get_popular(limit=20):
    connection = get_connection()
    try:
        return pd.read_sql_query(
            "SELECT name, symbol, exchange FROM symbols ORDER BY id LIMIT ?",
            connection,
            params=(max(1, min(int(limit), 100)),),
        )
    finally:
        connection.close()


def get_symbol(symbol):
    connection = get_connection()
    try:
        return pd.read_sql_query(
            "SELECT * FROM symbols WHERE symbol = ? COLLATE NOCASE LIMIT 1",
            connection,
            params=(str(symbol or "").strip(),),
        )
    finally:
        connection.close()


def symbol_exists(symbol):
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT 1 FROM symbols WHERE symbol = ? COLLATE NOCASE LIMIT 1",
            (str(symbol or "").strip(),),
        ).fetchone()
        return row is not None
    finally:
        connection.close()
