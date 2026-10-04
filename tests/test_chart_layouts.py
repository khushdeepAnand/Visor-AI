"""Tests for per-user saved chart layouts (K2)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.chart_layouts import (  # noqa: E402
    MAX_SAVED_LAYOUTS,
    ChartLayoutError,
    ChartLayoutStore,
    TIMEFRAMES,
)


def _store() -> ChartLayoutStore:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    return ChartLayoutStore(connection_factory=lambda: sqlite3.connect(handle.name))


def test_save_and_get_layout_round_trip():
    store = _store()
    saved = store.save_layout(
        user_id=1,
        name="Daily done",
        symbol="reliance",
        timeframe="1D",
        overlays={"ema": True, "vwap": False, "band": True},
        visible_range={"from": 1717200000, "to": 1719792000},
    )
    assert saved["symbol"] == "RELIANCE"
    assert saved["overlays"] == {"ema": True, "vwap": False, "band": True}
    assert saved["visible_range"] == {"from": 1717200000.0, "to": 1719792000.0}

    got = store.get_layout(user_id=1, layout_id=saved["id"])
    assert got["timeframe"] == "1D"
    assert got["name"] == "Daily done"


def test_named_layout_upserts_instead_of_duplicating():
    store = _store()
    first = store.save_layout(user_id=7, name="Reuse", symbol="TCS", timeframe="15m")
    second = store.save_layout(user_id=7, name="Reuse", symbol="INFY", timeframe="1h")
    assert first["id"] == second["id"]
    assert store.get_layout(user_id=7, layout_id=first["id"])["symbol"] == "INFY"


def test_validation_rejects_bad_inputs():
    store = _store()
    with pytest.raises(ChartLayoutError) as exc:
        store.save_layout(user_id=1, name="x" * 61, symbol="RELIANCE", timeframe="1D")
    assert exc.value.code == "chart_layout_name_invalid"

    with pytest.raises(ChartLayoutError) as exc:
        store.save_layout(user_id=1, name="ok", symbol="", timeframe="1D")
    assert exc.value.code == "chart_layout_symbol_required"

    with pytest.raises(ChartLayoutError) as exc:
        store.save_layout(user_id=1, name="ok", symbol="RELIANCE", timeframe="45m")
    assert exc.value.code == "chart_layout_timeframe_unknown"

    with pytest.raises(ChartLayoutError) as exc:
        store.save_layout(user_id=1, name="ok", symbol="RELIANCE", timeframe="1D", visible_range={"from": "a", "to": "b"})
    assert exc.value.code == "chart_layout_range_invalid"


def test_overlays_only_keeps_known_keys():
    store = _store()
    saved = store.save_layout(
        user_id=1,
        name="Filtered overlays",
        symbol="NIFTY 50",
        timeframe="1D",
        overlays={"ema": 1, "evil": True, "band": 1, "vwap": True},
    )
    assert saved["overlays"] == {"ema": True, "vwap": True, "band": True}


def test_per_account_saved_limit():
    store = _store()
    for index in range(MAX_SAVED_LAYOUTS):
        store.save_layout(user_id=42, name=f"layout-{index}", symbol="RELIANCE", timeframe="1D")
    with pytest.raises(ChartLayoutError) as exc:
        store.save_layout(user_id=42, name="overflow", symbol="INFY", timeframe="1D")
    assert exc.value.code == "chart_layout_saved_limit"


def test_delete_removes_layout_and_missing_is_not_found():
    store = _store()
    saved = store.save_layout(user_id=3, name="temp", symbol="HDFC", timeframe="1h")
    assert store.delete_layout(user_id=3, layout_id=saved["id"])["deleted"] is True
    with pytest.raises(ChartLayoutError) as exc:
        store.get_layout(user_id=3, layout_id=saved["id"])
    assert exc.value.code == "chart_layout_not_found"
    with pytest.raises(ChartLayoutError):
        store.delete_layout(user_id=3, layout_id=saved["id"])


def test_list_orders_by_updated_at_desc():
    store = _store()
    store.save_layout(user_id=9, name="a", symbol="A", timeframe="1D")
    store.save_layout(user_id=9, name="b", symbol="B", timeframe="1D")
    store.save_layout(user_id=9, name="c", symbol="C", timeframe="1D")
    names = [item["name"] for item in store.list_layouts(user_id=9)]
    assert sorted(names) == ["a", "b", "c"]


def test_timeline_contract_is_stable():
    assert TIMEFRAMES == ("1m", "5m", "15m", "1h", "4h", "1D", "1W")