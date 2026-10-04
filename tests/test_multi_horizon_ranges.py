"""Multi-horizon (1/3/5/10 session) direct range forecasts.

A different direct regressor is trained and calibrated per horizon, each with
its own untouched test fold, so a 10-session range is never the 1-session
range rescaled by a guess.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from forecasting.interval_forecast import (
    DEFAULT_HORIZONS,
    HORIZON_SESSIONS,
    _horizon_consistency,
    _target_timestamp,
    forecast_range,
)


def _frame(rows: int = 460) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-06-01", periods=rows, freq="B")
    phase = np.arange(rows) * 2 * np.pi / 8
    close = 150 + 15 * np.sin(phase) + rng.normal(0, 0.1, rows)
    open_ = close + rng.normal(0, 0.05, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.3,
            "Low": np.minimum(open_, close) - 0.3,
            "Close": close,
            "Volume": 1_000_000 + np.sin(phase + 1) * 100_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )


TEST_SYMBOL = "NONEXISTENT"  # not in F&O so circuit limits don't clip model median


def test_default_call_keeps_single_horizon_contract():
    result = forecast_range(TEST_SYMBOL, _frame(), confidence_level=0.80, training_window="1y", timeframe="1D")
    assert result["horizon"]["bars"] == 1
    assert result["horizon"]["sessions"] == 1
    assert result["multi_horizon"]["computed"] == [1]
    assert result["multi_horizon"]["requested"] == [1]
    assert result["forecast"]["low"] <= result["forecast"]["median"] <= result["forecast"]["high"]


def test_full_ladder_is_computed_with_own_targets():
    result = forecast_range(
        TEST_SYMBOL,
        _frame(),
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
        horizons=DEFAULT_HORIZONS,
    )
    multi = result["multi_horizon"]
    assert multi["computed"] == [1, 3, 5, 10]
    assert multi["primary"] == 1
    assert multi["unavailable"] == []
    targets = [entry["target_timestamp"] for entry in multi["horizons"]]
    assert len(set(targets)) == 4
    assert targets[0] < targets[1] < targets[2] < targets[3]
    for entry in multi["horizons"]:
        assert entry["sessions"] in HORIZON_SESSIONS
        assert entry["forecast"]["low"] <= entry["forecast"]["median"] <= entry["forecast"]["high"]
        assert entry["validation"]["samples"] > 0
        assert "empirical_coverage" in entry["validation"]
        assert "beats_naive_baseline" in entry["validation"]
        assert entry["width_pct"] is not None


def test_ladder_target_matches_session_rule():
    result = forecast_range(
        TEST_SYMBOL,
        _frame(),
        confidence_level=0.80,
        training_window="5y",
        timeframe="1D",
        horizons=DEFAULT_HORIZONS,
    )
    latest = result["feature_timestamp"]
    ladder = {entry["sessions"]: entry["target_timestamp"] for entry in result["multi_horizon"]["horizons"]}
    for horizon in HORIZON_SESSIONS:
        assert ladder[horizon] == _target_timestamp(latest, "1D", sessions=horizon)


def test_short_history_degrades_ladder_instead_of_guessing():
    result = forecast_range(
        TEST_SYMBOL,
        _frame(rows=36),
        confidence_level=0.80,
        training_window="1mo",
        timeframe="1D",
        horizons=DEFAULT_HORIZONS,
    )
    multi = result["multi_horizon"]
    assert 1 in multi["computed"]
    assert 10 not in multi["computed"]
    unavailable_sessions = {entry["sessions"] for entry in multi["unavailable"]}
    assert 10 in unavailable_sessions
    assert all(entry["reason"] for entry in multi["unavailable"])


def test_primary_block_matches_top_level_contract():
    result = forecast_range(
        TEST_SYMBOL,
        _frame(),
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
        horizons=DEFAULT_HORIZONS,
    )
    primary = result["multi_horizon"]["horizons"][0]
    assert primary["sessions"] == 1
    assert primary["forecast"]["low"] == result["forecast"]["low"]
    assert primary["forecast"]["median"] == result["forecast"]["median"]
    assert primary["forecast"]["high"] == result["forecast"]["high"]
    assert primary["target_timestamp"] == result["target_timestamp"]


def test_horizon_consistency_flags_direction_path_and_width_conflicts():
    runs = {
        1: {"median": 101.0, "range_width_pct": 0.04, "abstained": False},
        5: {"median": 98.0, "range_width_pct": 0.03, "abstained": False},
        10: {"median": 104.0, "range_width_pct": 0.05, "abstained": False},
    }

    signal = _horizon_consistency(runs, 100.0)

    assert signal["available"] is True
    assert signal["level"] == "low"
    assert signal["directional_agreement"] is False
    assert signal["median_path_monotonic"] is False
    assert signal["uncertainty_width_monotonic"] is False
    assert set(signal["flags"]) == {
        "direction_conflict",
        "median_path_reversal",
        "interval_narrows_with_horizon",
    }


def test_horizon_consistency_reports_supportive_monotonic_ladder():
    runs = {
        1: {"median": 100.5, "range_width_pct": 0.02, "abstained": False},
        5: {"median": 102.0, "range_width_pct": 0.04, "abstained": False},
        10: {"median": 103.0, "range_width_pct": 0.06, "abstained": False},
    }

    signal = _horizon_consistency(runs, 100.0)

    assert signal["score"] == 100
    assert signal["level"] == "high"
    assert signal["signal"] == "supportive"
    assert signal["flags"] == []
