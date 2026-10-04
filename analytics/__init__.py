"""Quantitative analytics services for StockPilot AI."""

from analytics.backtest_engine import compare_strategies, run_moving_average_strategy
from analytics.risk_engine import (
    calculate_performance_metrics,
    calculate_position_size,
    historical_var,
    monte_carlo_projection,
)

__all__ = [
    "calculate_performance_metrics",
    "calculate_position_size",
    "compare_strategies",
    "historical_var",
    "monte_carlo_projection",
    "run_moving_average_strategy",
]
