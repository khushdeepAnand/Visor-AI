"""Volatility-first models for StockPilot AI v13.

Implements:
- HAR-RV (Heterogeneous Autoregressive Realized Volatility)
- GARCH-family (GJR-GARCH, EGARCH for leverage effects)
- Range-based estimators from OHLC (Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang)
- Realized variance from intraday bars (when available)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

try:
    from arch import arch_model
    ARCH_AVAILABLE = True
except Exception:
    ARCH_AVAILABLE = False

try:
    from statsmodels.tsa.arima.model import ARIMA
    STATSMODELS_AVAILABLE = True
except Exception:
    STATSMODELS_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class VolatilityForecast:
    expected_daily_vol_pct: float
    expected_range_pct: float
    horizon_days: int
    model: str
    confidence_level: float
    lower_vol_pct: float | None = None
    upper_vol_pct: float | None = None
    components: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class RangeEstimates:
    parkinson: float
    garman_klass: float
    rogers_satchell: float
    yang_zhang: float
    close_to_close: float


@dataclass(frozen=True, slots=True)
class VolatilityEnsemble:
    """Named, auditable member forecasts (no network/model loading)."""
    forecast: VolatilityForecast
    members: dict[str, float]
    promoted_members: tuple[str, ...]


def _to_log_returns(close: pd.Series) -> np.ndarray:
    series = pd.Series(close, dtype=float).dropna()
    if len(series) < 2:
        return np.array([], dtype=float)
    return np.diff(np.log(series.to_numpy(dtype=float)))


def _to_ohlc_arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    open_ = pd.to_numeric(frame["Open"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(frame["High"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(frame["Low"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float)
    return open_, high, low, close


def realized_volatility(close: pd.Series, window: int = 20) -> float:
    """Close-to-close realized volatility (annualized)."""
    log_ret = _to_log_returns(close)
    if len(log_ret) < window:
        return 0.0
    return float(np.std(log_ret[-window:], ddof=1) * math.sqrt(252) * 100)


def range_based_estimators(frame: pd.DataFrame) -> RangeEstimates:
    """Compute four range-based volatility estimators from OHLC data.

    All estimators are annualized and expressed as percentage.
    """
    open_, high, low, close = _to_ohlc_arrays(frame)

    valid = (
        (open_ > 0) & (high > 0) & (low > 0) & (close > 0) &
        (high >= np.maximum(open_, close)) & (low <= np.minimum(open_, close))
    )
    open_ = open_[valid]
    high = high[valid]
    low = low[valid]
    close = close[valid]

    if len(close) < 2:
        zero = 0.0
        return RangeEstimates(zero, zero, zero, zero, zero)

    cc_ret = np.diff(np.log(close))
    close_to_close = float(np.std(cc_ret, ddof=1) * math.sqrt(252) * 100)

    hl_ratio = np.log(high / low)
    parkinson = float(np.sqrt(np.mean(hl_ratio**2) / (4 * math.log(2))) * math.sqrt(252) * 100)

    co_ratio = np.log(close / open_)
    garman_klass = float(
        np.sqrt(np.mean(0.5 * hl_ratio**2 - (2 * math.log(2) - 1) * co_ratio**2)) * math.sqrt(252) * 100
    )

    rogers_satchell = float(
        np.sqrt(np.mean(
            np.log(high / close) * np.log(high / open_) +
            np.log(low / close) * np.log(low / open_)
        )) * math.sqrt(252) * 100
    )

    overnight = np.log(open_[1:] / close[:-1])
    overnight_var = float(np.var(overnight, ddof=1)) if len(overnight) > 1 else 0.0
    open_close = np.log(close / open_)
    open_close_var = float(np.var(open_close, ddof=1))
    yang_zhang = float(
        math.sqrt(overnight_var + 0.5 * garman_klass / 252 * 252) * math.sqrt(252) * 100
    ) if garman_klass > 0 else 0.0

    return RangeEstimates(
        parkinson=max(parkinson, 0.01),
        garman_klass=max(garman_klass, 0.01),
        rogers_satchell=max(rogers_satchell, 0.01),
        yang_zhang=max(yang_zhang, 0.01),
        close_to_close=max(close_to_close, 0.01),
    )


def realized_variance_intraday(bars_5m: pd.DataFrame, session_bars: int = 75) -> float:
    """Realized variance from 5-minute bars within a trading session.

    NSE session: 09:15-15:30 = 375 minutes = 75 five-minute bars.
    """
    if bars_5m is None or len(bars_5m) < session_bars:
        return 0.0
    close = pd.to_numeric(bars_5m["Close"], errors="coerce").dropna().to_numpy(dtype=float)
    if len(close) < 2:
        return 0.0
    log_ret = np.diff(np.log(close))
    rv = float(np.sum(log_ret**2))
    return max(rv, 0.0)


def har_rv_forecast(
    daily_rv: np.ndarray,
    horizon: int = 1,
    *,
    daily_window: int = 22,
    weekly_window: int = 66,
    monthly_window: int = 264,
) -> float:
    """HAR-RV forecast: RV_{t+1} = beta0 + beta_d * RV_d + beta_w * RV_w + beta_m * RV_m.

    Uses OLS on log(RV) for stability. Returns annualized vol %.
    """
    if len(daily_rv) < monthly_window + 10:
        return 0.0

    rv = pd.Series(daily_rv, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(rv) < monthly_window + 10:
        return 0.0

    rv_d = rv.rolling(daily_window).mean()
    rv_w = rv.rolling(weekly_window).mean()
    rv_m = rv.rolling(monthly_window).mean()

    df = pd.DataFrame({"rv": rv, "rv_d": rv_d, "rv_w": rv_w, "rv_m": rv_m}).dropna()
    if len(df) < 30:
        return float(rv.iloc[-1] * math.sqrt(252) * 100)

    y = np.log(df["rv"].values[1:])
    X = np.column_stack([
        np.ones(len(df) - 1),
        np.log(df["rv_d"].values[:-1]),
        np.log(df["rv_w"].values[:-1]),
        np.log(df["rv_m"].values[:-1]),
    ])

    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        last_X = np.array([1.0, math.log(df["rv_d"].iloc[-1]), math.log(df["rv_w"].iloc[-1]), math.log(df["rv_m"].iloc[-1])])
        pred_log_rv = float(last_X @ beta)
        pred_rv = math.exp(pred_log_rv)
        return float(math.sqrt(pred_rv * horizon) * math.sqrt(252) * 100)
    except Exception:
        return float(rv.iloc[-1] * math.sqrt(252) * 100)


def garch_forecast(
    returns: np.ndarray,
    horizon: int = 1,
    *,
    model_type: str = "GJR-GARCH",
    p: int = 1,
    q: int = 1,
) -> VolatilityForecast | None:
    """GARCH-family forecast using arch library.

    model_type: "GARCH", "GJR-GARCH", "EGARCH"
    """
    if not ARCH_AVAILABLE or len(returns) < 100:
        return None

    try:
        if model_type == "GARCH":
            am = arch_model(returns * 100, vol="Garch", p=p, q=q, dist="normal")
        elif model_type == "GJR-GARCH":
            am = arch_model(returns * 100, vol="Garch", p=p, o=1, q=q, dist="normal")
        elif model_type == "EGARCH":
            am = arch_model(returns * 100, vol="EGARCH", p=p, o=1, q=q, dist="normal")
        else:
            am = arch_model(returns * 100, vol="Garch", p=1, o=1, q=1, dist="normal")

        res = am.fit(update_freq=0, show_warning=False, disp="off")
        forecast = res.forecast(horizon=horizon, reindex=False)
        var = forecast.variance.values[-1, -1]
        daily_vol_pct = float(math.sqrt(var))
        return VolatilityForecast(
            expected_daily_vol_pct=daily_vol_pct,
            expected_range_pct=daily_vol_pct * 2.0 * math.sqrt(horizon),
            horizon_days=horizon,
            model=model_type,
            confidence_level=0.68,
        )
    except Exception:
        return None


def garch_vol_forecast(
    close: pd.Series,
    horizon: int = 1,
    model_type: str = "GJR-GARCH",
) -> VolatilityForecast | None:
    """Convenience wrapper: compute returns from close and call garch_forecast."""
    returns = _to_log_returns(close)
    if len(returns) < 100:
        return None
    return garch_forecast(returns, horizon=horizon, model_type=model_type)


def ewma_volatility(returns: np.ndarray, lambda_: float = 0.94) -> float:
    """RiskMetrics EWMA volatility (annualized %)."""
    if len(returns) == 0:
        return 0.0
    var = float(returns[0]**2)
    for r in returns:
        var = lambda_ * var + (1 - lambda_) * r**2
    return float(math.sqrt(var) * math.sqrt(252) * 100)


def composite_volatility_forecast(
    frame: pd.DataFrame,
    *,
    intraday_bars_5m: pd.DataFrame | None = None,
    horizon: int = 1,
    confidence: float = 0.68,
) -> VolatilityForecast:
    """Best-effort composite volatility forecast using all available estimators."""
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(close) < 30:
        return VolatilityForecast(
            expected_daily_vol_pct=0.0,
            expected_range_pct=0.0,
            horizon_days=horizon,
            model="insufficient_data",
            confidence_level=confidence,
        )

    returns = _to_log_returns(close)
    range_est = range_based_estimators(frame)

    components = {
        "close_to_close": realized_volatility(close, 20),
        "parkinson": range_est.parkinson,
        "garman_klass": range_est.garman_klass,
        "yang_zhang": range_est.yang_zhang,
        "ewma": ewma_volatility(returns),
    }

    if intraday_bars_5m is not None and len(intraday_bars_5m) >= 75:
        rv_intraday = realized_variance_intraday(intraday_bars_5m)
        components["realized_intraday"] = float(math.sqrt(rv_intraday) * math.sqrt(252) * 100)

    garch_result = garch_vol_forecast(close, horizon=horizon, model_type="GJR-GARCH")
    if garch_result:
        components["gjr_garch"] = garch_result.expected_daily_vol_pct

    # HAR must be fit on a time series of realized variance, not on the
    # cross-sectional component list (the old implementation silently did the
    # latter and therefore almost always returned zero).
    rv_series = returns ** 2
    har_result = har_rv_forecast(rv_series, horizon=horizon)
    if har_result > 0:
        components["har_rv"] = har_result

    valid = [v for v in components.values() if v > 0]
    if not valid:
        expected = 0.0
    else:
        expected = float(np.median(valid))

    z_score = {0.50: 0.674, 0.68: 1.0, 0.80: 1.28, 0.90: 1.645, 0.95: 1.96}.get(confidence, 1.28)
    expected_range = expected * z_score * math.sqrt(horizon)

    return VolatilityForecast(
        expected_daily_vol_pct=round(expected, 3),
        expected_range_pct=round(expected_range, 3),
        horizon_days=horizon,
        model="composite",
        confidence_level=confidence,
        components={k: round(v, 3) for k, v in components.items()},
    )


def volatility_ensemble(
    frame: pd.DataFrame,
    *,
    intraday_bars_5m: pd.DataFrame | None = None,
    horizon: int = 1,
    confidence: float = 0.68,
    foundation_member: Any = None,
    promoted_members: Sequence[str] = (),
) -> VolatilityEnsemble:
    """Build explicit offline members and combine only approved members.

    ``foundation_member`` is an optional callable accepting the OHLC frame and
    horizon. It is never downloaded or invoked unless supplied by the caller;
    its output is merely a candidate until named in ``promoted_members``.
    """
    base = composite_volatility_forecast(frame, intraday_bars_5m=intraday_bars_5m,
                                         horizon=horizon, confidence=confidence)
    members = dict(base.components or {})
    if foundation_member is not None:
        try:
            value = float(foundation_member(frame, horizon))
            if np.isfinite(value) and value > 0:
                members["foundation"] = value
        except Exception:
            # Optional candidates must not make the production path fail.
            pass
    approved = tuple(name for name in promoted_members if name in members)
    selected = [members[name] for name in approved] or [v for k, v in members.items() if k != "foundation"]
    expected = float(np.median(selected)) if selected else base.expected_daily_vol_pct
    z_score = {0.50: 0.674, 0.68: 1.0, 0.80: 1.28, 0.90: 1.645, 0.95: 1.96}.get(confidence, 1.28)
    forecast = VolatilityForecast(round(expected, 3), round(expected * z_score * math.sqrt(horizon), 3),
                                  horizon, "approved_ensemble", confidence,
                                  components={k: round(v, 3) for k, v in members.items()})
    return VolatilityEnsemble(forecast, {k: round(v, 3) for k, v in members.items()}, approved)


def volatility_scorecard(
    frame: pd.DataFrame,
    *,
    intraday_bars_5m: pd.DataFrame | None = None,
    horizons: Sequence[int] = (1, 5, 10, 20),
    confidence: float = 0.68,
) -> dict[str, Any]:
    """Produce a volatility forecast scorecard for multiple horizons."""
    scorecard: dict[str, Any] = {}
    for h in horizons:
        fc = composite_volatility_forecast(frame, intraday_bars_5m=intraday_bars_5m, horizon=h, confidence=confidence)
        scorecard[f"{h}d"] = {
            "expected_daily_vol_pct": fc.expected_daily_vol_pct,
            "expected_range_pct": fc.expected_range_pct,
            "model": fc.model,
            "components": fc.components,
        }
    return {
        "horizons": scorecard,
        "confidence_level": confidence,
        "disclosure": "Volatility is the most predictable component of markets. Range forecasts derived from volatility are more reliable than directional forecasts.",
    }


__all__: Sequence[str] = (
    "VolatilityForecast",
    "VolatilityEnsemble",
    "RangeEstimates",
    "realized_volatility",
    "range_based_estimators",
    "realized_variance_intraday",
    "har_rv_forecast",
    "garch_forecast",
    "garch_vol_forecast",
    "ewma_volatility",
    "composite_volatility_forecast",
    "volatility_ensemble",
    "volatility_scorecard",
    "ARCH_AVAILABLE",
)
