from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.backtest_engine import compare_strategies, run_moving_average_strategy, run_rsi_strategy
from analytics.risk_engine import (
    calculate_performance_metrics,
    calculate_position_size,
    historical_var,
    monte_carlo_projection,
    price_returns,
)


def _market_frame(rows=260):
    rng = np.random.default_rng(123)
    dates = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = 100 + np.linspace(0, 25, rows) + np.sin(np.arange(rows) / 8) * 4
    return pd.DataFrame(
        {
            "Open": close + rng.normal(0, 0.2, rows),
            "High": close + 1.2,
            "Low": close - 1.2,
            "Close": close,
            "Volume": rng.integers(100_000, 500_000, rows),
        },
        index=dates,
    )


def test_performance_metrics_include_downside_risk():
    equity = pd.Series([100, 102, 101, 105, 103, 110, 112], dtype=float)
    metrics = calculate_performance_metrics(equity, risk_free_rate=0.0, periods_per_year=252)
    assert metrics["total_return"] == pytest.approx(0.12)
    assert metrics["max_drawdown"] > 0
    assert "sortino_ratio" in metrics
    assert "cvar_95" in metrics


def test_historical_var_returns_positive_loss_magnitudes():
    result = historical_var(pd.Series([-0.10, -0.05, 0.01, 0.02, 0.03]), confidence=0.80)
    assert result["var"] >= 0
    assert result["cvar"] >= result["var"]


def test_position_size_obeys_risk_and_allocation_caps():
    result = calculate_position_size(100_000, 100, 95, risk_fraction=0.01, max_allocation=0.20)
    assert result["shares"] == 200
    assert result["notional"] == 20_000
    assert result["cash_risk"] == 1_000


def test_position_size_rejects_invalid_long_stop():
    with pytest.raises(ValueError, match="below entry"):
        calculate_position_size(100_000, 100, 105)


def test_monte_carlo_is_reproducible_and_bounded():
    returns = pd.Series(np.linspace(-0.02, 0.025, 100))
    first = monte_carlo_projection(100, returns, horizon_days=10, simulations=500, seed=7)
    second = monte_carlo_projection(100, returns, horizon_days=10, simulations=500, seed=7)
    assert first["terminal_percentiles"] == second["terminal_percentiles"]
    assert 0 <= first["probability_finish_above_start"] <= 1
    assert len(first["sample_paths"]) == 10


def test_price_returns_requires_positive_prices():
    with pytest.raises(ValueError, match="positive"):
        price_returns([100, 0, 101])


def test_moving_average_backtest_is_no_lookahead_and_cost_aware():
    result = run_moving_average_strategy(_market_frame(), fast_period=10, slow_period=30, transaction_cost_bps=25)
    assert result["strategy"] == "Moving Average Crossover"
    assert result["equity_curve"][0]["position"] == 0.0
    assert result["metrics"]["transaction_cost_bps"] == 25
    assert "shifted by one bar" in result["methodology"]
    assert len(result["equity_curve"]) == 260


def test_rsi_backtest_and_comparison_contract():
    frame = _market_frame()
    rsi = run_rsi_strategy(frame)
    comparison = compare_strategies(frame)
    assert rsi["strategy"] == "RSI Mean Reversion"
    assert len(comparison["ranking"]) == 2
    assert {item["strategy"] for item in comparison["ranking"]} == {
        "Moving Average Crossover",
        "RSI Mean Reversion",
    }
