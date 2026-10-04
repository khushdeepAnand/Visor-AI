"""Risk and portfolio analytics for StockPilot AI.

All functions are deterministic, network-free and designed to be used by both
the FastAPI service and research tooling. Metrics are descriptive estimates, not guarantees of
future performance.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class PositionSizeResult:
    shares: int
    notional: float
    cash_risk: float
    allocation_fraction: float
    risk_fraction: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "shares": self.shares,
            "notional": round(self.notional, 2),
            "cash_risk": round(self.cash_risk, 2),
            "allocation_fraction": round(self.allocation_fraction, 6),
            "risk_fraction": round(self.risk_fraction, 6),
        }


def _clean_series(values: Iterable[float] | pd.Series, name: str) -> pd.Series:
    series = pd.Series(values, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if series.empty:
        raise ValueError(f"{name} must contain at least one finite value.")
    return series


def price_returns(prices: Iterable[float] | pd.Series) -> pd.Series:
    """Return clean simple returns from a strictly positive price/equity series."""

    series = _clean_series(prices, "Price series")
    if (series <= 0).any():
        raise ValueError("Price series must contain only positive values.")
    returns = series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty:
        raise ValueError("At least two price observations are required.")
    return returns


def calculate_drawdown(equity: Iterable[float] | pd.Series) -> pd.Series:
    series = _clean_series(equity, "Equity series")
    if (series <= 0).any():
        raise ValueError("Equity series must contain only positive values.")
    running_max = series.cummax()
    return series / running_max - 1.0


def historical_var(
    returns: Iterable[float] | pd.Series,
    confidence: float = 0.95,
) -> dict[str, float]:
    """Calculate historical Value-at-Risk and Conditional Value-at-Risk.

    Returned values are positive loss magnitudes expressed as decimal returns.
    """

    if not 0.5 < float(confidence) < 1.0:
        raise ValueError("Confidence must be between 0.5 and 1.0.")
    series = _clean_series(returns, "Returns")
    quantile = float(series.quantile(1.0 - confidence))
    tail = series[series <= quantile]
    cvar = float(tail.mean()) if not tail.empty else quantile
    return {
        "confidence": round(float(confidence), 4),
        "var": round(max(0.0, -quantile), 8),
        "cvar": round(max(0.0, -cvar), 8),
    }


def calculate_performance_metrics(
    equity: Iterable[float] | pd.Series,
    *,
    risk_free_rate: float = 0.06,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, float | int]:
    """Calculate institutional-style performance and downside-risk metrics."""

    if periods_per_year <= 0:
        raise ValueError("Periods per year must be positive.")
    series = _clean_series(equity, "Equity series")
    if len(series) < 2:
        raise ValueError("At least two equity observations are required.")
    if (series <= 0).any():
        raise ValueError("Equity series must contain only positive values.")

    returns = series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total_return = float(series.iloc[-1] / series.iloc[0] - 1.0)
    years = max((len(returns) / periods_per_year), 1.0 / periods_per_year)
    annualized_return = float((series.iloc[-1] / series.iloc[0]) ** (1.0 / years) - 1.0)
    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(periods_per_year)) if len(returns) > 1 else 0.0

    periodic_rf = (1.0 + float(risk_free_rate)) ** (1.0 / periods_per_year) - 1.0
    excess = returns - periodic_rf
    excess_std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    sharpe = float(excess.mean() / excess_std * math.sqrt(periods_per_year)) if excess_std > 0 else 0.0

    downside = np.minimum(excess.to_numpy(dtype=float), 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(periods_per_year))
    annualized_excess = float(excess.mean() * periods_per_year)
    sortino = annualized_excess / downside_deviation if downside_deviation > 0 else 0.0

    drawdown = calculate_drawdown(series)
    max_drawdown = abs(float(drawdown.min()))
    calmar = annualized_return / max_drawdown if max_drawdown > 0 else 0.0
    var_metrics = historical_var(returns, 0.95)

    positive = int((returns > 0).sum())
    negative = int((returns < 0).sum())
    hit_rate = positive / len(returns) if len(returns) else 0.0
    gains = float(returns[returns > 0].sum())
    losses = abs(float(returns[returns < 0].sum()))
    profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)

    return {
        "observations": int(len(series)),
        "total_return": round(total_return, 8),
        "annualized_return": round(annualized_return, 8),
        "annualized_volatility": round(annualized_volatility, 8),
        "sharpe_ratio": round(sharpe, 6),
        "sortino_ratio": round(float(sortino), 6),
        "max_drawdown": round(max_drawdown, 8),
        "calmar_ratio": round(float(calmar), 6),
        "hit_rate": round(float(hit_rate), 8),
        "positive_periods": positive,
        "negative_periods": negative,
        "profit_factor": round(float(profit_factor), 6) if math.isfinite(profit_factor) else 9999.0,
        "var_95": var_metrics["var"],
        "cvar_95": var_metrics["cvar"],
    }


def calculate_position_size(
    account_value: float,
    entry_price: float,
    stop_loss: float,
    *,
    risk_fraction: float = 0.01,
    max_allocation: float = 0.25,
) -> dict[str, Any]:
    """Calculate conservative whole-share position size from risk and allocation caps."""

    account_value = float(account_value)
    entry_price = float(entry_price)
    stop_loss = float(stop_loss)
    risk_fraction = float(risk_fraction)
    max_allocation = float(max_allocation)

    if account_value <= 0 or entry_price <= 0 or stop_loss <= 0:
        raise ValueError("Account value, entry price and stop loss must be positive.")
    if stop_loss >= entry_price:
        raise ValueError("For a long position, stop loss must be below entry price.")
    if not 0 < risk_fraction <= 0.10:
        raise ValueError("Risk fraction must be greater than 0 and at most 10%.")
    if not 0 < max_allocation <= 1.0:
        raise ValueError("Maximum allocation must be between 0 and 1.")

    per_share_risk = entry_price - stop_loss
    risk_budget = account_value * risk_fraction
    risk_limited_shares = math.floor(risk_budget / per_share_risk)
    allocation_limited_shares = math.floor(account_value * max_allocation / entry_price)
    shares = max(0, min(risk_limited_shares, allocation_limited_shares))
    notional = shares * entry_price
    result = PositionSizeResult(
        shares=shares,
        notional=notional,
        cash_risk=shares * per_share_risk,
        allocation_fraction=(notional / account_value) if account_value else 0.0,
        risk_fraction=((shares * per_share_risk) / account_value) if account_value else 0.0,
    )
    return result.as_dict()


def monte_carlo_projection(
    last_price: float,
    returns: Iterable[float] | pd.Series,
    *,
    horizon_days: int = 30,
    simulations: int = 2000,
    seed: int = 2026,
) -> dict[str, Any]:
    """Generate a reproducible geometric-Brownian price scenario distribution."""

    last_price = float(last_price)
    if last_price <= 0:
        raise ValueError("Last price must be positive.")
    if not 1 <= int(horizon_days) <= 756:
        raise ValueError("Horizon days must be between 1 and 756.")
    if not 100 <= int(simulations) <= 100_000:
        raise ValueError("Simulations must be between 100 and 100,000.")

    series = _clean_series(returns, "Returns")
    if len(series) < 2:
        raise ValueError("At least two returns are required.")
    log_returns = np.log1p(series.clip(lower=-0.999999))
    drift = float(log_returns.mean() - 0.5 * log_returns.var(ddof=1))
    volatility = float(log_returns.std(ddof=1))
    rng = np.random.default_rng(int(seed))
    shocks = rng.normal(drift, volatility, size=(int(horizon_days), int(simulations)))
    paths = last_price * np.exp(np.cumsum(shocks, axis=0))
    terminal = paths[-1]
    percentiles = np.percentile(terminal, [5, 25, 50, 75, 95])
    probability_up = float(np.mean(terminal > last_price))

    sampled_indexes = np.linspace(0, int(simulations) - 1, num=min(20, int(simulations)), dtype=int)
    sample_paths = paths[:, sampled_indexes]

    return {
        "last_price": round(last_price, 4),
        "horizon_days": int(horizon_days),
        "simulations": int(simulations),
        "drift_daily": round(drift, 10),
        "volatility_daily": round(volatility, 10),
        "probability_finish_above_start": round(probability_up, 6),
        "terminal_percentiles": {
            "p05": round(float(percentiles[0]), 4),
            "p25": round(float(percentiles[1]), 4),
            "p50": round(float(percentiles[2]), 4),
            "p75": round(float(percentiles[3]), 4),
            "p95": round(float(percentiles[4]), 4),
        },
        "sample_paths": [[round(float(value), 4) for value in row] for row in sample_paths.tolist()],
        "disclaimer": "Scenario simulation based on historical return distribution; not a forecast or guarantee.",
    }
