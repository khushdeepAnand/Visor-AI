"""Tests for the user-defined technical screener and saved screens."""

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

from services.screener import (  # noqa: E402
    MAX_FILTERS,
    MIN_SESSIONS,
    SCREENER_FIELDS,
    ScreenerError,
    ScreenerStore,
    compute_metrics,
    describe_fields,
    run_screen,
    validate_filters,
)


def _frame(sessions: int, *, start: float = 100.0, step: float = 1.0, volume: float = 1000.0) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=sessions, freq="B")
    closes = [start + step * index for index in range(sessions)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": [value - 0.5 for value in closes],
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [volume] * sessions,
        }
    )


def _loader(mapping):
    def loader(symbol: str) -> pd.DataFrame:
        if symbol not in mapping:
            raise KeyError(symbol)
        return mapping[symbol]

    return loader


def _store() -> ScreenerStore:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    return ScreenerStore(connection_factory=lambda: sqlite3.connect(handle.name))


# -- field catalogue -------------------------------------------------------


def test_field_catalogue_documents_every_field():
    fields = describe_fields()
    assert len(fields) == len(SCREENER_FIELDS)
    for entry in fields:
        assert entry["basis"], f"{entry['name']} has no stated basis"
        assert entry["sessions_required"] >= 1


# -- filter validation -----------------------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters([{"field": "secret_sauce", "op": "gt", "value": 1}])
    assert excinfo.value.code == "screener_field_unknown"


def test_unknown_operator_is_rejected():
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters([{"field": "rsi_14", "op": "approximately", "value": 50}])
    assert excinfo.value.code == "screener_operator_unknown"


def test_between_requires_ordered_bounds():
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters([{"field": "rsi_14", "op": "between", "low": 70, "high": 30}])
    assert excinfo.value.code == "screener_bounds_invalid"


def test_non_numeric_value_is_rejected():
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters([{"field": "rsi_14", "op": "gt", "value": "oversold"}])
    assert excinfo.value.code == "screener_value_invalid"


def test_empty_and_oversized_filter_sets_are_rejected():
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters([])
    assert excinfo.value.code == "screener_filters_required"
    too_many = [{"field": "rsi_14", "op": "gt", "value": index} for index in range(MAX_FILTERS + 1)]
    with pytest.raises(ScreenerError) as excinfo:
        validate_filters(too_many)
    assert excinfo.value.code == "screener_filters_too_many"


# -- metric computation ----------------------------------------------------


def test_metrics_require_their_own_window():
    short = compute_metrics("SHORT", _frame(80))
    assert short["available"] is True
    assert short["metrics"]["sma_50"] is not None
    assert short["metrics"]["sma_200"] is None, "SMA 200 must not be approximated from 80 sessions"

    long_history = compute_metrics("LONG", _frame(260))
    assert long_history["metrics"]["sma_200"] is not None


def test_short_history_is_excluded_with_a_reason():
    result = compute_metrics("NEWLIST", _frame(MIN_SESSIONS - 1))
    assert result["available"] is False
    assert result["reason"] == "insufficient_history"


def test_missing_history_is_excluded():
    assert compute_metrics("NOTHING", None)["reason"] == "history_unavailable"


def test_uptrend_metrics_are_arithmetically_correct():
    metrics = compute_metrics("UP", _frame(120, start=100.0, step=1.0))["metrics"]
    # Closes run 100..219; the last close is the 52-week high in this window.
    assert metrics["last_close"] == 219.0
    assert metrics["distance_from_52w_high_pct"] == pytest.approx(0.0, abs=0.01)
    assert metrics["close_vs_sma20_pct"] > 0
    assert metrics["rsi_14"] is not None and metrics["rsi_14"] > 90


def test_volume_ratio_uses_the_symbols_own_baseline():
    frame = _frame(80, volume=500.0)
    frame.loc[frame.index[-1], "volume"] = 2500.0
    metrics = compute_metrics("SPIKE", frame)["metrics"]
    # 20-session mean = (19 * 500 + 2500) / 20 = 600 -> 2500 / 600.
    assert metrics["volume_vs_20d_avg"] == pytest.approx(2500 / 600, abs=0.01)


# -- screening -------------------------------------------------------------


def test_screen_matches_only_symbols_satisfying_every_filter():
    mapping = {
        "UP": _frame(120, start=100.0, step=1.0),
        "DOWN": _frame(120, start=300.0, step=-1.0),
    }
    result = run_screen(
        symbols=["UP", "DOWN"],
        history_loader=_loader(mapping),
        filters=[{"field": "close_vs_sma20_pct", "op": "gt", "value": 0}],
    )
    assert [row["symbol"] for row in result["matches"]] == ["UP"]
    assert result["coverage"]["evaluated"] == 2
    assert result["coverage"]["matched"] == 1


def test_symbols_missing_a_required_metric_are_excluded_not_failed():
    # 260 sessions supports SMA 200; 80 sessions cannot, and must be excluded
    # rather than compared against a shorter average.
    mapping = {"UP": _frame(260), "SHORTISH": _frame(80)}
    result = run_screen(
        symbols=["UP", "SHORTISH"],
        history_loader=_loader(mapping),
        filters=[{"field": "close_vs_sma200_pct", "op": "gt", "value": -100}],
    )
    excluded = {row["symbol"]: row for row in result["coverage"]["excluded"]}
    assert excluded["SHORTISH"]["reason"] == "metric_unavailable"
    assert "close_vs_sma200_pct" in excluded["SHORTISH"]["fields"]
    assert [row["symbol"] for row in result["matches"]] == ["UP"]


def test_history_failures_are_reported_per_symbol():
    result = run_screen(
        symbols=["UP", "MISSING"],
        history_loader=_loader({"UP": _frame(120)}),
        filters=[{"field": "last_close", "op": "gt", "value": 0}],
    )
    reasons = {row["symbol"]: row["reason"] for row in result["coverage"]["excluded"]}
    assert reasons["MISSING"] == "history_unavailable"


def test_between_and_sorting_and_limit_behave():
    mapping = {
        "A": _frame(120, start=100.0, step=1.0),
        "B": _frame(120, start=100.0, step=2.0),
        "C": _frame(120, start=100.0, step=3.0),
    }
    result = run_screen(
        symbols=["A", "B", "C"],
        history_loader=_loader(mapping),
        filters=[{"field": "last_close", "op": "between", "low": 0, "high": 100000}],
        sort_by="last_close",
        descending=True,
        limit=2,
    )
    assert [row["symbol"] for row in result["matches"]] == ["C", "B"]
    assert result["truncated"] is True


def test_unknown_sort_field_is_rejected():
    with pytest.raises(ScreenerError) as excinfo:
        run_screen(
            symbols=["A"],
            history_loader=_loader({"A": _frame(120)}),
            filters=[{"field": "last_close", "op": "gt", "value": 1}],
            sort_by="alpha_score",
        )
    assert excinfo.value.code == "screener_sort_unknown"


def test_screen_requires_symbols():
    with pytest.raises(ScreenerError) as excinfo:
        run_screen(
            symbols=[],
            history_loader=_loader({}),
            filters=[{"field": "last_close", "op": "gt", "value": 1}],
        )
    assert excinfo.value.code == "screener_symbols_required"


def test_screen_output_is_labelled_as_descriptive():
    result = run_screen(
        symbols=["A"],
        history_loader=_loader({"A": _frame(120)}),
        filters=[{"field": "last_close", "op": "gt", "value": 1}],
    )
    assert result["evidence"]["is_forecast"] is False
    assert result["evidence"]["is_recommendation"] is False
    assert any("not a trade idea" in text.lower() for text in result["disclosures"])
    match = result["matches"][0]
    forbidden = {"score", "rating", "signal_strength", "target_price", "recommendation"}
    assert forbidden.isdisjoint(match.keys())


# -- saved screens ---------------------------------------------------------


def test_saved_screen_round_trip():
    store = _store()
    saved = store.save_screen(
        user_id=7,
        name="Above 20 DMA",
        filters=[{"field": "close_vs_sma20_pct", "op": "gt", "value": 0}],
        sort_by="change_pct_5d",
        symbols=["reliance", "tcs"],
    )
    assert saved["name"] == "Above 20 DMA"
    assert saved["symbols"] == ["RELIANCE", "TCS"]
    assert saved["sort"] == {"field": "change_pct_5d", "descending": True}

    listed = store.list_screens(user_id=7)
    assert len(listed) == 1
    fetched = store.get_screen(user_id=7, screen_id=saved["id"])
    assert fetched["filters"][0]["field"] == "close_vs_sma20_pct"


def test_saved_screens_are_scoped_to_one_user():
    store = _store()
    mine = store.save_screen(
        user_id=1,
        name="Mine",
        filters=[{"field": "rsi_14", "op": "lt", "value": 30}],
    )
    assert store.list_screens(user_id=2) == []
    with pytest.raises(ScreenerError) as excinfo:
        store.get_screen(user_id=2, screen_id=mine["id"])
    assert excinfo.value.code == "screener_not_found"
    with pytest.raises(ScreenerError):
        store.delete_screen(user_id=2, screen_id=mine["id"])


def test_saving_the_same_name_replaces_rather_than_duplicates():
    store = _store()
    store.save_screen(user_id=3, name="Momentum", filters=[{"field": "rsi_14", "op": "gt", "value": 60}])
    store.save_screen(user_id=3, name="Momentum", filters=[{"field": "rsi_14", "op": "gt", "value": 70}])
    screens = store.list_screens(user_id=3)
    assert len(screens) == 1
    assert screens[0]["filters"][0]["value"] == 70


def test_saved_screen_rejects_bad_names_and_filters():
    store = _store()
    with pytest.raises(ScreenerError) as excinfo:
        store.save_screen(user_id=4, name="  ", filters=[{"field": "rsi_14", "op": "gt", "value": 60}])
    assert excinfo.value.code == "screener_name_invalid"
    with pytest.raises(ScreenerError) as excinfo:
        store.save_screen(user_id=4, name="Bad field", filters=[{"field": "nope", "op": "gt", "value": 1}])
    assert excinfo.value.code == "screener_field_unknown"


def test_deleting_a_screen_removes_it():
    store = _store()
    saved = store.save_screen(user_id=5, name="Temp", filters=[{"field": "rsi_14", "op": "gt", "value": 60}])
    assert store.delete_screen(user_id=5, screen_id=saved["id"])["deleted"] is True
    assert store.list_screens(user_id=5) == []
