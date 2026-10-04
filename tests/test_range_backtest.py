"""Unit tests for the walk-forward backtest + naive-beat gate (Phase B item 7)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from forecasting.interval_forecast import forecast_range
from services.range_backtest import (
    MIN_GATE_SAMPLES,
    BacktestGateError,
    naive_beat_gate,
    normalize_frame,
    range_model_gate_report,
    walk_forward_metrics,
    walk_forward_origins,
)


def _frame(n: int = 200, start: float = 100.0, drift: float = 0.0, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05", periods=n, freq="B")
    noise = rng.normal(0.0, 0.6, n).cumsum()
    close = start + np.arange(n) * drift + noise
    close = np.maximum(close, 1.0)
    open_ = np.r_[close[0] + rng.normal(0, 0.2), close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.4, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.4, n))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": np.full(n, 1_000.0)},
        index=idx,
    )


def test_origins_are_expanding_and_chronological() -> None:
    origins = walk_forward_origins(250, 1, max_origins=20)
    assert len(origins) > 5
    assert origins == sorted(origins)
    # Origins step forward (never backwards/shuffled) and reach the newest bar.
    assert origins[-1] >= 240
    assert origins[0] >= 40


def test_origins_respect_horizon_and_min_train() -> None:
    origins = walk_forward_origins(100, 5, max_origins=50)
    assert all(origin >= 40 and origin + 5 <= 99 for origin in origins)


def test_origins_insufficient_history() -> None:
    assert walk_forward_origins(10, 1) == []


def test_normalize_frame_accepts_lowercase() -> None:
    frame = _frame(60)
    lower = frame.rename(columns={c: c.lower() for c in frame.columns})
    normalized = normalize_frame(lower)
    assert set(["open", "high", "low", "close"]).issubset(set(normalized.columns))
    assert isinstance(normalized.index, pd.DatetimeIndex)


def test_walk_forward_basic_accuracy_payload() -> None:
    frame = _frame(220)
    forecaster = lambda symbol, data: {  # noqa: E731
        "forecast": {
            "low": float(data["Close"].iloc[-1]) * 0.99,
            "median": float(data["Close"].iloc[-1]),
            "high": float(data["Close"].iloc[-1]) * 1.01,
        }
    }
    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=forecaster, horizon=1)
    assert metrics["usable_origins"] > 0
    assert metrics["origins"] == metrics["usable_origins"] + metrics["abstained_origins"]
    accuracy = metrics["accuracy"]
    assert accuracy["mase"] is not None
    assert accuracy["beats_naive_baseline"] in {True, False}
    assert 0.0 <= accuracy["empirical_coverage"] <= 1.0
    # Median == last close means no directional view, so no trades.
    assert metrics["trades"]["count"] == 0
    assert len(metrics["replay"]) == metrics["origins"]


def test_walk_forward_reports_range_containment_coverage() -> None:
    """The intraday containment metric (next-bar high/low inside the band) is
    reported per record and as an aggregate, always consistent with the replay."""
    frame = _frame(220)
    forecaster = lambda symbol, data: {  # noqa: E731
        "forecast": {
            "low": float(data["Close"].iloc[-1]) * 0.8,
            "median": float(data["Close"].iloc[-1]),
            "high": float(data["Close"].iloc[-1]) * 1.2,
        }
    }
    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=forecaster, horizon=1)
    accuracy = metrics["accuracy"]
    assert "range_containment_coverage" in accuracy
    assert 0.0 <= accuracy["range_containment_coverage"] <= 1.0
    assert accuracy["containment_denominator"] == metrics["usable_origins"]
    usable = [record for record in metrics["replay"] if record["usable"]]
    assert all("containment_hit" in record and "actual_range" in record for record in usable)
    # The aggregate equals the per-record count by construction.
    per_record = sum(1 for record in usable if record["containment_hit"])
    assert accuracy["containment_hits"] == per_record


def test_narrow_band_containment_is_stricter_than_close_coverage() -> None:
    """A band hugging the close can cover the close yet still be blown out
    intraday; containment must never exceed close coverage and should be low."""
    frame = _frame(220, start=100.0, drift=0.03)
    forecaster = lambda symbol, data: {  # noqa: E731
        "forecast": {
            "low": float(data["Close"].iloc[-1]) * 0.9995,
            "median": float(data["Close"].iloc[-1]),
            "high": float(data["Close"].iloc[-1]) * 1.0005,
        }
    }
    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=forecaster, horizon=1)
    accuracy = metrics["accuracy"]
    containment = accuracy["range_containment_coverage"]
    assert containment <= accuracy["empirical_coverage"] + 1e-9
    assert containment < 0.3


def test_trades_fill_next_open_with_costs() -> None:
    frame = _frame(220, start=100.0, drift=0.05)
    forecaster = lambda symbol, data: {  # noqa: E731
        "forecast": {
            "low": float(data["Close"].iloc[-1]) * 0.95,
            "median": float(data["Close"].iloc[-1]) * 1.05,
            "high": float(data["Close"].iloc[-1]) * 1.15,
        }
    }
    metrics = walk_forward_metrics(
        symbol="TEST",
        frame=frame,
        forecaster=forecaster,
        spread_bps=5.0,
        slippage_bps=2.0,
        charges_bps=1.0,
    )
    trades = metrics["trades"]
    assert trades["count"] > 0
    assert trades["costs"] >= 0
    # First trade must fill at (or above) the next open for a LONG.
    first = metrics["replay"][0]["trade"]
    assert first["direction"] == "LONG"
    assert first["entry_fill"] >= first["entry_open"]


def test_gate_passes_when_model_beats_naive() -> None:
    frame = _frame(220, drift=0.0)
    full_close = frame["Close"].to_numpy()

    def oracle(symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        # Deterministic holdout cheat: the replay harness exposes the origin at
        # data.index[-1]; a perfect median makes model error ~0 vs naive.
        origin = len(data) - 1
        target = float(full_close[origin + 1])
        return {"forecast": {"low": target * 0.99, "median": target, "high": target * 1.01}}

    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=oracle)
    # The perfect-cheat oracle lands 100% of actuals inside its band, which is
    # over-coverage for a nominal 80% interval. Pass the band that matches the
    # oracle so the naive-beat + sample-count axes are what this test verifies.
    gate = naive_beat_gate(metrics, nominal_coverage=1.0, coverage_tolerance=0.05)
    assert metrics["accuracy"]["beats_naive_baseline"] is True
    assert gate["passed"] is True
    assert "naive persistence" in gate["requirement"]
    assert gate["coverage_ok"] is True


def test_gate_allows_naive_tie_at_zero_floor() -> None:
    """Floor 0 means 'never worse than naive': a tie (0% improvement) passes."""
    metrics = {
        "origins": 12,
        "usable_origins": 12,
        "accuracy": {
            "empirical_coverage": 0.85,
            "mae_improvement_vs_naive_pct": 0.0,
            "beats_naive_baseline": False,
        },
    }
    gate = naive_beat_gate(metrics)
    assert gate["passed"] is True
    assert gate["beats_naive"] is False


def test_gate_enforces_calibration_band() -> None:
    """Ranges that under-cover or over-cover fail; calibrated coverage passes."""
    base_accuracy = {
        "mae_improvement_vs_naive_pct": 5.0,
        "beats_naive_baseline": True,
    }
    passing_gate = None
    for coverage, expected_passed in ((0.40, False), (0.85, True), (1.00, False)):
        metrics = {
            "origins": 12,
            "usable_origins": 12,
            "accuracy": {**base_accuracy, "empirical_coverage": coverage},
        }
        gate = naive_beat_gate(metrics)
        assert gate["passed"] is expected_passed, f"coverage={coverage}"
        if not expected_passed:
            assert any("coverage" in reason for reason in gate["reasons"])
        else:
            passing_gate = gate
    assert passing_gate is not None and passing_gate["coverage_ok"] is True


def test_gate_fails_when_model_loses_to_naive() -> None:
    frame = _frame(220, drift=0.0)

    def adversarial(symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        last = float(data["Close"].iloc[-1])
        return {"forecast": {"low": last * 0.9, "median": last * 1.4, "high": last * 1.9}}

    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=adversarial)
    gate = naive_beat_gate(metrics)
    assert gate["passed"] is False
    assert gate["reasons"]


def test_gate_requires_samples() -> None:
    frame = _frame(220)
    forecaster = lambda symbol, data: {  # noqa: E731
        "forecast": {"low": float(data["Close"].iloc[-1]) * 0.5, "median": float(data["Close"].iloc[-1]), "high": float(data["Close"].iloc[-1]) * 1.5}
    }
    metrics = walk_forward_metrics(symbol="TEST", frame=frame, forecaster=forecaster, max_origins=3)
    gate = naive_beat_gate(metrics, min_samples=MIN_GATE_SAMPLES)
    assert gate["passed"] is False
    assert any("required" in reason for reason in gate["reasons"])


def test_insufficient_history_raises() -> None:
    frame = _frame(20)
    with pytest.raises(BacktestGateError):
        walk_forward_metrics(symbol="TEST", frame=frame, forecaster=lambda s, d: {"forecast": {}})


def test_end_to_end_with_real_forecast_range() -> None:
    frame = _frame(120, drift=0.0, seed=11)
    report = range_model_gate_report(
        "TEST",
        frame,
        lambda symbol, data: forecast_range(
            symbol,
            data,
            confidence_level=0.80,
            training_window="3mo",
            timeframe="1D",
            horizons=(1,),
        ),
        horizon=1,
        max_origins=10,
    )
    assert report["symbol"] == "TEST"
    assert report["horizon"] == 1
    assert report["usable_origins"] >= 1
    assert report["gate"]["gate"] == "naive_beat_by_floor"
    assert report["gate"]["passed"] in {True, False}
    assert report["trades"]["filled_as"] == "next_bar_open"
    assert report["evidence"]["is_forecast"] is False