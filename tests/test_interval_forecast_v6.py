from __future__ import annotations
import numpy as np
import pytest
from forecasting.interval_forecast import _drift_status, _quantile, _target_timestamp, _winkler


def test_conformal_quantile_is_conservative():
    values=np.array([1,2,3,4,5],dtype=float)
    assert _quantile(values,.8) >= 4


def test_winkler_rewards_coverage_and_penalizes_miss():
    inside=_winkler(np.array([100.]),np.array([95.]),np.array([105.]),.2)
    outside=_winkler(np.array([115.]),np.array([95.]),np.array([105.]),.2)
    assert inside == 10
    assert outside > inside


def test_drift_monitor_flags_corrupted_recent_errors():
    residuals=np.r_[np.full(80,.5),np.full(20,10.0)]
    result=_drift_status(residuals,20)
    assert result["drift_detected"] is True
    assert result["status"] == "drift"


def test_drift_monitor_stable_for_consistent_errors():
    residuals=np.linspace(.4,.6,100)
    assert _drift_status(residuals,20)["drift_detected"] is False


def test_target_timestamp_skips_nse_weekends_and_holidays():
    assert _target_timestamp("2026-01-23T00:00:00", "1D") == "2026-01-27T00:00:00"
    assert _target_timestamp("2026-01-23T15:30:00+05:30", "5m") == "2026-01-27T09:20:00+05:30"


def test_range_model_beats_naive_on_fixed_predictable_fold():
    """Model-quality regression guard on a deterministic, leakage-safe fold."""
    import pandas as pd
    from forecasting.interval_forecast import forecast_range

    rng = np.random.default_rng(42)
    rows = 420
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    phase = np.arange(rows) * 2 * np.pi / 8
    close = 150 + 15 * np.sin(phase) + rng.normal(0, 0.1, rows)
    open_ = close + rng.normal(0, 0.05, rows)
    frame = pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.3,
            "Low": np.minimum(open_, close) - 0.3,
            "Close": close,
            "Volume": 1_000_000 + np.sin(phase + 1) * 100_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )
    # A raw trailing row with an invalid close must not become the reference
    # price or timestamp; all forecast inputs come from one cleaned frame.
    invalid_timestamp = idx[-1] + pd.offsets.BDay()
    frame.loc[invalid_timestamp] = {
        "Open": 9999.0,
        "High": 9999.0,
        "Low": 9999.0,
        "Close": np.nan,
        "Volume": 1_000_000.0,
    }
    result = forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y", timeframe="1D")
    validation = result["validation"]
    assert validation["beats_naive_baseline"] is True
    assert validation["mae_improvement_vs_naive_pct"] > 50
    assert result["forecast"]["low"] <= result["forecast"]["median"] <= result["forecast"]["high"]
    assert result["current_price"] == round(float(close[-1]), 2)
    assert result["data_timestamp"] == idx[-1].isoformat()
    assert result["feature_timestamp"] == idx[-1].isoformat()
    assert set(result["training"]["split"]) == {"train", "meta", "calibration", "test"}
    assert result["validation"]["evaluated_prediction"] == "published_blended_prediction"
    assert result["methods"]["quantile_regression"]["families"] == []
    assert result["methods"]["quantile_regression"]["request_fit"] is False
    assert result["next_day_evidence"]["available"] is True
    assert result["next_day_evidence"]["calibration"]["samples"] == validation["samples"]
    assert result["next_day_evidence"]["specialist_status"] == "research_only_pending_next_day_promotion"


@pytest.mark.live
def test_live_interval_coverage_against_nominal_provider_fold():
    """Manual real-data quality gate; requires configured broker credentials.

    Run with: STOCKPILOT_LIVE_TEST_SYMBOL=RELIANCE pytest -m live -q
    """
    import os
    from services.market_data.manager import MANAGER
    from forecasting.interval_forecast import forecast_range

    symbol = os.getenv("STOCKPILOT_LIVE_TEST_SYMBOL")
    if not symbol:
        pytest.skip("Set STOCKPILOT_LIVE_TEST_SYMBOL and broker credentials for the live model-quality gate.")
    frame = MANAGER.get_history(symbol, timeframe="1D", window="5y")
    result = forecast_range(symbol, frame, confidence_level=0.80, training_window="5y", timeframe="1D")
    coverage = result["validation"]["empirical_coverage"]
    assert 0.75 <= coverage <= 0.85
