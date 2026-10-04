"""Regime-adaptive interval widths.

Width is localised conformal: residuals are normalised by the volatility
observable at each calibration origin and the published half-width re-scales
the conformal quantile by today's realized volatility. A volatility burst must
widen the corridor instead of keeping a calm-period width that would silently
lose coverage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from forecasting.interval_forecast import forecast_range


def _series(close: np.ndarray) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    rows = close.shape[0]
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    open_ = close + rng.normal(0, 0.02, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.1,
            "Low": np.minimum(open_, close) - 0.1,
            "Close": close,
            "Volume": 1_000_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )


def _calm() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    return _series(100.0 + rng.normal(0, 0.05, 420))


def _burst() -> pd.DataFrame:
    rng = np.random.default_rng(4)
    calm = rng.normal(0, 0.05, 395)
    tail = rng.normal(0, 2.0, 25)  # volatility burst in the most recent 25 sessions
    return _series(np.r_[100.0 + calm, 100.0 + tail])


def _run(frame: pd.DataFrame) -> dict:
    return forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y", timeframe="1D", horizons=(1,))


def _half_width(result: dict) -> float:
    return float(result["methods"]["range_anchor"]["half_width_points"])


def _sigma_now(result: dict) -> float:
    return float(result["methods"]["range_anchor"]["realized_sigma_points"])


def test_volatility_burst_widens_published_corridor():
    calm = _run(_calm())
    burst = _run(_burst())
    # The envelope must react to the regime that is actually observable now.
    assert _sigma_now(burst) > _sigma_now(calm) * 5.0
    assert _half_width(burst) > _half_width(calm) * 2.0


def test_pure_level_shift_scales_width_proportionally():
    calm = _run(_calm())
    scaled = _run(_series(_calm()["Close"].to_numpy(dtype=float) * 1.1))
    assert _sigma_now(scaled) / _sigma_now(calm) > 0.9
    ratio = _half_width(scaled) / _half_width(calm)
    assert 0.9 < ratio < 1.3


def test_interval_never_narrowed_below_numerical_floor():
    calm = _run(_calm())
    low, high = calm["forecast"]["low"], calm["forecast"]["high"]
    price = calm["current_price"]
    assert (high - low) / price >= 0.0025  # MIN_RANGE_HALF_PCT * 2


def test_weights_favour_recent_same_regime_calibration_points():
    from forecasting.interval_forecast import _calibration_weights

    scales = np.array([1.0, 1.0, 10.0, 1.0], dtype=float)
    weights = _calibration_weights(scales, recent_scale=1.1)
    assert weights.shape == (4,)
    assert weights.sum() > 0.99 and weights.sum() <= 1.01
    # Same-regime, more-recent points carry more weight than the distant burst point.
    assert weights[3] > weights[2]
    assert weights[3] > weights[0]