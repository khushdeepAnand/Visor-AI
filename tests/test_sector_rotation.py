"""Tests for sector rotation coverage and grouping (K3)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.sector_rotation import (  # noqa: E402
    _catalogue_counts,
    _sector_roster,
    sector_coverage,
    sector_rotation,
)


def _fresh_db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    path = handle.name

    def factory() -> sqlite3.Connection:
        return sqlite3.connect(path)

    connection = factory()
    connection.execute(
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT, symbol TEXT, exchange TEXT, country TEXT, sector TEXT)"
    )
    connection.commit()
    connection.close()
    return factory


def _seed(factory, rows):
    connection = factory()
    try:
        connection.executemany(
            "INSERT INTO symbols (name, symbol, exchange, country, sector) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def test_coverage_reports_partial_when_sector_column_is_thin():
    factory = _fresh_db()
    _seed(
        factory,
        [
            ("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy"),
            ("TCS", "TCS", "NSE", "IN", "Technology"),
        ],
    )
    coverage = sector_coverage(connection_factory=factory)
    assert coverage["catalogue"]["total"] == 2
    assert coverage["catalogue"]["sector_populated"] == 2
    assert coverage["catalogue"]["sector_populated_pct"] == 100.0
    assert coverage["partial_coverage"] is False


def test_coverage_honestly_flags_partial_and_zero_states():
    factory = _fresh_db()
    _seed(
        factory,
        [
            ("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy"),
            ("Unknown Inc", "UNKNOWN", "NSE", "IN", ""),
        ],
    )
    coverage = sector_coverage(connection_factory=factory)
    assert coverage["catalogue"]["sector_populated"] == 1
    assert coverage["catalogue"]["total"] == 2
    assert coverage["catalogue"]["sector_populated_pct"] == 50.0
    assert coverage["partial_coverage"] is True

    factory = _fresh_db()
    _seed(factory, [("Unknown Inc", "UNKNOWN", "NSE", "IN", "")])
    coverage = sector_coverage(connection_factory=factory)
    assert coverage["catalogue"]["sector_populated"] == 0
    assert coverage["partial_coverage"] is True
    assert coverage["catalogue"]["distinct_sectors"] == 0


def test_roster_groups_by_sector_and_ignores_unassigned():
    factory = _fresh_db()
    _seed(
        factory,
        [
            ("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy"),
            ("HDFC Bank", "HDFCBANK", "NSE", "IN", "Financials"),
            ("ICICI Bank", "ICICIBANK", "NSE", "IN", "Financials"),
            ("No Sector", "NOSEC", "NSE", "IN", " "),
        ],
    )
    roster = _sector_roster(connection_factory=factory)
    assert roster == {"Energy": ["RELIANCE"], "Financials": ["HDFCBANK", "ICICIBANK"]}


def test_rotation_uses_history_loader_and_discloses_partial_coverage():
    frames = {
        "RELIANCE": pd.DataFrame({"close": [100, 102, 101, 105, 107, 110]}, index=range(6)),
        "HDFCBANK": pd.DataFrame({"close": [50, 51, 52, 53, 54, 55]}, index=range(6)),
        "ICICIBANK": pd.DataFrame({"close": [x for x in range(6)]}, index=range(6)),
    }

    def loader(symbol):
        if symbol == "NOSEC":
            raise RuntimeError("searchless")
        return frames[symbol]

    factory = _fresh_db()
    _seed(
        factory,
        [
            ("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy"),
            ("HDFC Bank", "HDFCBANK", "NSE", "IN", "Financials"),
            ("ICICI Bank", "ICICIBANK", "NSE", "IN", "Financials"),
        ],
    )
    result = sector_rotation(connection_factory=factory, history_loader=loader)
    assert result["partial_coverage"] is False
    assert result["sector_count"] == 2
    by_name = {block["sector"]: block for block in result["sectors"]}
    assert by_name["Energy"]["avg_change_pct_5d"] == round((110 / 100 - 1) * 100, 3)
    assert by_name["Financials"]["symbol_count"] == 2
    assert by_name["Financials"]["avg_change_pct_5d"] == round((55 / 50 - 1) * 100, 3)
    assert any("PARTIAL" not in item for item in result["disclosures"])


def test_rotation_skips_unmeasurable_symbols_and_sectors():
    frames = {"RELIANCE": pd.DataFrame({"close": [100, 101]}, index=range(2))}

    def loader(_symbol):
        return frames.get(_symbol)

    factory = _fresh_db()
    _seed(factory, [("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy")])
    result = sector_rotation(connection_factory=factory, history_loader=loader)
    block = result["sectors"][0]
    assert block["measured_count"] == 0
    assert block["avg_change_pct_5d"] is None
    assert block["symbols"][0]["change_pct_5d"] is None


def test_rotation_flags_partial_when_roster_is_thin():
    factory = _fresh_db()
    _seed(
        factory,
        [
            ("Reliance Industries", "RELIANCE", "NSE", "IN", "Energy"),
            ("Unassigned", "NOSEC", "NSE", "IN", None),
        ],
    )

    def loader(symbol):
        return pd.DataFrame({"close": [100 + i for i in range(6)]}, index=range(6)) if symbol == "RELIANCE" else None

    result = sector_rotation(connection_factory=factory, history_loader=loader)
    assert result["partial_coverage"] is True
    assert result["coverage"]["catalogue"]["sector_populated_pct"] == 50.0


def test_catalogue_counts_handles_empty_table():
    factory = _fresh_db()
    counts = _catalogue_counts(connection_factory=factory)
    assert counts["total"] == 0
    assert counts["sector_populated"] == 0
    assert counts["sector_populated_pct"] == 0.0
    assert counts["distinct_sectors"] == 0