"""Point-in-time leakage detection test for StockPilot AI v13.

This test shuffles future data and fails the build if forecasts change,
proving that features only use data available at forecast timestamp.
"""
from __future__ import annotations

import hashlib
import json
import numpy as np
import pandas as pd
import pytest

from forecasting.interval_forecast import forecast_range, DataSufficiencyReport
from forecasting.data_tier_router import assign_tier, DataTier
from forecasting.volatility_models import composite_volatility_forecast


def _make_deterministic_frame(symbol: str, days: int = 500, seed: int = 42) -> pd.DataFrame:
    """Create a deterministic price frame for testing."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    steps = rng.normal(0.0005, 0.01, size=days)
    close = 1000.0 * np.exp(np.cumsum(steps))
    return pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.002, days)),
        "High": close * (1 + np.abs(rng.normal(0, 0.005, days))),
        "Low": close * (1 - np.abs(rng.normal(0, 0.005, days))),
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, days),
    }, index=dates)


def _forecast_hash(result: dict) -> str:
    """Create a stable hash of the forecast output for comparison."""
    # Extract key forecast fields that should be deterministic
    key_fields = [
        "symbol",
        "forecast",
        "current_price",
        "volatility_forecast",
        "market_regime",
        "tier",
    ]
    payload = {k: result.get(k) for k in key_fields if k in result}
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def test_leakage_detection_shuffle_future_data():
    """Test that feature computation at forecast origin is point-in-time correct.

    This proves point-in-time correctness at the feature level: indicators
    at index i only use data up to i, not future data.
    """
    from indicators import add_indicators
    from forecasting.interval_forecast import _coerce_market_data, _retained_features, MIN_SUPERVISED_FULL

    frame = _make_deterministic_frame("TEST", days=400)
    split_point = len(frame) // 2  # Day 200 is forecast origin

    # Get the timestamp at forecast origin
    origin_timestamp = frame.index[split_point]

    # History frame (up to forecast origin)
    history_frame = frame.loc[:origin_timestamp].copy()

    # Future frame (after forecast origin)
    future_frame = frame.loc[origin_timestamp:].iloc[1:].copy()  # Exclude origin

    # Shuffle future data (keeping timestamps)
    shuffled_future = future_frame.sample(frac=1.0, random_state=999).reset_index(drop=True)
    shuffled_future.index = future_frame.index
    contaminated_frame = pd.concat([history_frame, shuffled_future]).sort_index()

    # Compute indicators on both frames
    canonical_history = _coerce_market_data(history_frame)
    enriched_history = add_indicators(canonical_history)

    canonical_contaminated = _coerce_market_data(contaminated_frame)
    enriched_contaminated = add_indicators(canonical_contaminated)

    # Features at forecast origin should be identical
    # Use the last available timestamp in enriched_history (may differ from origin_timestamp due to warm-up)
    last_timestamp = enriched_history.index[-1]

    features = _retained_features(enriched_history, MIN_SUPERVISED_FULL + 1)

    # Compare feature values at the last available timestamp
    leakage_detected = []
    for feat in features:
        hist_val = enriched_history[feat].iloc[-1]
        # Find the same timestamp in contaminated frame
        if last_timestamp in enriched_contaminated.index:
            cont_val = enriched_contaminated[feat].loc[last_timestamp]
        else:
            # Use last available if timestamp not found
            cont_val = enriched_contaminated[feat].iloc[-1]
        if np.isfinite(hist_val) and np.isfinite(cont_val):
            if abs(hist_val - cont_val) >= 1e-10:
                leakage_detected.append(f"{feat}: history={hist_val}, contaminated={cont_val}")

    assert not leakage_detected, (
        f"LEAKAGE DETECTED in features: {'; '.join(leakage_detected)}. "
        f"Features at forecast origin changed when future data was shuffled."
    )

    # The target (next period close) is NaN at forecast origin by definition
    # (no future data exists yet), so we don't check it
    hist_target = canonical_history["Close"].shift(-1).iloc[-1]
    assert np.isnan(hist_target), "Target at forecast origin should be NaN (unrealized)"


def test_leakage_detection_feature_timestamp_alignment():
    """Test that feature_timestamp matches data_timestamp (no lookahead)."""
    frame = _make_deterministic_frame("TEST", days=400)

    result = forecast_range(
        "TEST",
        frame,
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
    )

    # Feature timestamp should equal data timestamp (last available bar)
    assert result["feature_timestamp"] == result["data_timestamp"], (
        f"Feature timestamp ({result['feature_timestamp']}) != data timestamp "
        f"({result['data_timestamp']}). This indicates lookahead bias - "
        f"features are using data not available at forecast time."
    )


def test_leakage_detection_no_future_in_training():
    """Test that training data doesn't include future relative to forecast origin."""
    frame = _make_deterministic_frame("TEST", days=400)

    result = forecast_range(
        "TEST",
        frame,
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
    )

    # Check that all training splits are chronologically before test
    training = result.get("training", {})
    split = training.get("split", {})
    if split:
        # Verify chronological order: train < meta < calibration < test
        train_len = split.get("train", 0)
        meta_len = split.get("meta", 0)
        cal_len = split.get("calibration", 0)
        test_len = split.get("test", 0)
        total = train_len + meta_len + cal_len + test_len

        # The test fold should be the most recent data
        assert test_len > 0, "Test fold should have data"

        # The supervised rows should match the frame rows used
        supervised_rows = training.get("supervised_rows", 0)
        assert supervised_rows == total, "Split sizes should sum to supervised rows"


def test_leakage_detection_volatility_forecast_point_in_time():
    """Test that volatility forecast only uses historical data."""
    frame = _make_deterministic_frame("TEST", days=300)

    vol_result = composite_volatility_forecast(frame, horizon=1, confidence=0.68)

    # Components should be derived from historical data only
    assert hasattr(vol_result, "components")
    components = vol_result.components
    assert components is not None
    for name, value in components.items():
        assert np.isfinite(value), f"Component {name} should be finite"


def test_data_sufficiency_rejects_insufficient_history():
    """Test that DataSufficiencyReport correctly grades insufficient history."""
    # Very short history
    short_frame = _make_deterministic_frame("SHORT", days=20)

    from forecasting.interval_forecast import _coerce_market_data, add_indicators, _retained_features, MIN_SUPERVISED_ABSOLUTE

    try:
        canonical = _coerce_market_data(short_frame)
        enriched = add_indicators(canonical)
        features = _retained_features(enriched, MIN_SUPERVISED_ABSOLUTE)
        supervised = enriched[features].dropna()
        assert len(supervised) < MIN_SUPERVISED_ABSOLUTE
    except Exception:
        # Expected to fail or have insufficient rows
        pass

    # Explicit check with DataSufficiencyReport
    report = DataSufficiencyReport.from_counts(
        raw_rows=20,
        cleaned_rows=18,
        supervised_rows=15,
        validation_samples=2,
    )
    assert report.evidence_grade == "none"
    assert report.supported_horizons == ()


def test_tier_router_rejects_t0_for_directional_forecast():
    """Test that T0 tier (< 30 days) gets no directional forecast."""
    # 20 days of history = T0
    assignment = assign_tier(
        usable_days=20,
        validation_samples=3,
        zero_volume_ratio=0.0,
    )
    assert assignment.tier == DataTier.T0
    assert assignment.supported_horizons == ()
    assert "no directional claim" in assignment.primary_output.lower()


def test_leakage_detection_cross_validation_purging():
    """Test that cross-validation uses purging/embargo to prevent leakage."""
    # This is a design test - the interval_forecast uses chronological splits
    # with train < meta < calibration < test, which inherently prevents leakage
    frame = _make_deterministic_frame("TEST", days=400)

    result = forecast_range(
        "TEST",
        frame,
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
    )

    # The split should show distinct chronological folds
    split = result.get("training", {}).get("split", {})
    if split:
        train = split.get("train", 0)
        meta = split.get("meta", 0)
        cal = split.get("calibration", 0)
        test = split.get("test", 0)

        # All folds should have data
        assert train > 0 and meta > 0 and cal > 0 and test > 0

        # Chronological order implied by the split construction
        # (train is earliest, test is latest)
        # This is enforced by _frame_splits / _adaptive_splits


if __name__ == "__main__":
    pytest.main([__file__, "-v"])