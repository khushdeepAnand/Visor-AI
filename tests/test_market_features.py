"""Tests for market features module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.market_features import (
    compute_market_features,
    market_features_to_dict,
    evaluate_feature_importance,
    _rolling_beta,
    _rolling_corr,
)


def _make_frame(days: int, start: float = 1000.0, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV frame."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    steps = rng.normal(0.0005, 0.01, size=days)
    close = start * np.exp(np.cumsum(steps))
    return pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.002, days)),
        "High": close * (1 + np.abs(rng.normal(0, 0.005, days))),
        "Low": close * (1 - np.abs(rng.normal(0, 0.005, days))),
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, days),
    }, index=dates)


def test_rolling_beta():
    """Test rolling beta computation."""
    rng = np.random.default_rng(42)
    market = pd.Series(rng.normal(0.001, 0.01, 100))
    stock = pd.Series(1.5 * market + rng.normal(0, 0.005, 100))

    beta = _rolling_beta(stock, market, 20)
    assert beta is not None
    assert 1.0 < beta < 2.0  # Should be close to 1.5

    # Test with insufficient data
    beta_short = _rolling_beta(stock[:10], market[:10], 20)
    assert beta_short is None


def test_rolling_corr():
    """Test rolling correlation computation."""
    rng = np.random.default_rng(42)
    s1 = pd.Series(rng.normal(0, 1, 100))
    s2 = pd.Series(0.8 * s1 + rng.normal(0, 0.5, 100))

    corr = _rolling_corr(s1, s2, 20)
    assert corr is not None
    assert 0.5 < corr < 1.0


def test_compute_market_features_basic():
    """Test basic market features computation."""
    symbol_frame = _make_frame(100)
    nifty_frame = _make_frame(100, start=20000.0, seed=99)

    features = compute_market_features(
        symbol_frame=symbol_frame,
        nifty_frame=nifty_frame,
    )

    assert features.nifty_return_1d is not None
    assert features.nifty_return_5d is not None
    assert features.nifty_return_20d is not None
    assert features.beta_nifty_20d is not None
    assert features.beta_nifty_60d is not None
    assert features.rolling_corr_nifty_20d is not None


def test_compute_market_features_with_sector():
    """Test market features with sector data."""
    symbol_frame = _make_frame(100)
    nifty_frame = _make_frame(100, start=20000.0, seed=99)
    sector_frame = _make_frame(100, start=5000.0, seed=123)

    features = compute_market_features(
        symbol_frame=symbol_frame,
        nifty_frame=nifty_frame,
        sector_frame=sector_frame,
    )

    assert features.sector_return_1d is not None
    assert features.sector_return_5d is not None
    assert features.sector_momentum_20d is not None
    assert features.relative_strength_nifty_20d is not None


def test_compute_market_features_with_vix():
    """Test market features with VIX data."""
    symbol_frame = _make_frame(100)
    vix_series = pd.Series(np.random.uniform(10, 30, 100), index=symbol_frame.index)

    features = compute_market_features(
        symbol_frame=symbol_frame,
        vix_series=vix_series,
    )

    assert features.vix_level is not None
    assert features.vix_change_1d is not None
    assert features.vix_change_5d is not None
    assert features.vix_percentile_252d is None  # Not enough data for 252d


def test_compute_market_features_with_options():
    """Test market features with option chain data."""
    symbol_frame = _make_frame(100)
    option_chain = {
        "available": True,
        "expected_moves": [{"move_pct": 2.5}],
        "oi_change_pct": 5.2,
        "pcr": 1.3,
    }

    features = compute_market_features(
        symbol_frame=symbol_frame,
        option_chain=option_chain,
    )

    assert features.expected_move_atm == 2.5
    assert features.oi_change_pct == 5.2
    assert features.pcr == 1.3


def test_compute_market_features_with_futures():
    """Test market features with futures data."""
    symbol_frame = _make_frame(100)
    futures_data = {
        "rollover_pct": 85.5,
        "basis_pct": 0.8,
        "max_pain_distance_pct": 1.2,
    }

    features = compute_market_features(
        symbol_frame=symbol_frame,
        futures_data=futures_data,
    )

    assert features.rollover_pct == 85.5
    assert features.basis_pct == 0.8
    assert features.max_pain_distance_pct == 1.2


def test_compute_market_features_with_flows():
    """Test market features with flow data."""
    symbol_frame = _make_frame(100)
    flow_data = {
        "fii_net_buy_cr": 1250.5,
        "dii_net_buy_cr": -800.3,
        "delivery_pct": 55.5,
        "bulk_deals_count": 3,
        "block_deals_count": 1,
        "promoter_pledge_change_pct": -0.5,
    }

    features = compute_market_features(
        symbol_frame=symbol_frame,
        flow_data=flow_data,
    )

    assert features.fii_net_buy_cr == 1250.5
    assert features.dii_net_buy_cr == -800.3
    assert features.delivery_pct == 55.5
    assert features.bulk_deals_count == 3
    assert features.block_deals_count == 1
    assert features.promoter_pledge_change_pct == -0.5


def test_market_features_to_dict():
    """Test conversion to dictionary."""
    symbol_frame = _make_frame(100)
    nifty_frame = _make_frame(100, start=20000.0, seed=99)

    features = compute_market_features(
        symbol_frame=symbol_frame,
        nifty_frame=nifty_frame,
    )

    d = market_features_to_dict(features)

    assert "market_context" in d
    assert "sector_context" in d
    assert "systematic_risk" in d
    assert "vix" in d
    assert "options_implied" in d
    assert "derivatives_positioning" in d
    assert "flows" in d
    assert "disclosure" in d


def test_evaluate_feature_importance():
    """Test feature importance evaluation."""
    # Test admitted feature
    result = evaluate_feature_importance(
        feature_names=["vix_level", "beta_nifty_20d"],
        feature_values=[15.5, 1.2],
        interval_score_improvement=0.005,
        min_improvement=0.001,
    )
    assert result["admitted"] is True
    assert "above" in result["reason"]

    # Test rejected feature
    result = evaluate_feature_importance(
        feature_names=["news_sentiment"],
        feature_values=[0.3],
        interval_score_improvement=0.0005,
        min_improvement=0.001,
    )
    assert result["admitted"] is False
    assert "below" in result["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])