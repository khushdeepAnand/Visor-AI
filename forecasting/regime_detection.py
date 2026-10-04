"""Regime detection and routing for StockPilot AI v13.

Detects market regimes (trend/mean-reversion, low/high volatility, market-wide stress)
and routes/weights models per regime with regime-specific calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

import numpy as np
import pandas as pd


class MarketRegime(Enum):
    BULL_TREND_LOW_VOL = "bull_trend_low_vol"
    BULL_TREND_HIGH_VOL = "bull_trend_high_vol"
    BEAR_TREND_LOW_VOL = "bear_trend_low_vol"
    BEAR_TREND_HIGH_VOL = "bear_trend_high_vol"
    SIDEWAYS_LOW_VOL = "sideways_low_vol"
    SIDEWAYS_HIGH_VOL = "sideways_high_vol"
    CRISIS = "crisis"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class RegimeState:
    regime: MarketRegime
    trend: str
    volatility: str
    stress: str
    probability: float
    adx: float | None
    atr_percentile: float | None
    vix_level: float | None
    nifty_drawdown_pct: float | None
    basis: str


def _safe_row(enriched: pd.DataFrame, columns: Sequence[str]) -> dict[str, float]:
    row = enriched.iloc[-1] if not enriched.empty else None
    out: dict[str, float] = {}
    for col in columns:
        val = float("nan")
        if row is not None and col in enriched.columns:
            try:
                cand = float(row[col])
            except (TypeError, ValueError):
                cand = float("nan")
            val = cand if np.isfinite(cand) else float("nan")
        out[col] = val
    return out


def _atr_percentile(enriched: pd.DataFrame, lookback: int = 90) -> float | None:
    if "ATR_Pct" not in enriched.columns:
        return None
    series = pd.Series(enriched["ATR_Pct"], dtype=float).dropna()
    if len(series) < 5:
        return None
    tail = series.iloc[-lookback:]
    if len(tail) < 5:
        return None
    latest = float(tail.iloc[-1])
    return float((tail < latest).mean())


def _trend_signals(enriched: pd.DataFrame) -> tuple[int, list[str]]:
    cols = ["Close", "SMA_20", "SMA_50", "EMA_20", "EMA_50", "MACD_Histogram"]
    row = _safe_row(enriched, cols)
    score = 0
    signals: list[str] = []
    if np.isfinite(row.get("SMA_20", np.nan)):
        above = row["Close"] > row["SMA_20"]
        score += int(above)
        signals.append(f"close {'above' if above else 'below'} SMA20")
    if np.isfinite(row.get("SMA_50", np.nan)):
        above = row["Close"] > row["SMA_50"]
        score += int(above)
        signals.append(f"close {'above' if above else 'below'} SMA50")
    if np.isfinite(row.get("EMA_20", np.nan)) and np.isfinite(row.get("EMA_50", np.nan)):
        above = row["EMA_20"] > row["EMA_50"]
        score += int(above)
        signals.append(f"EMA20 {'above' if above else 'below'} EMA50")
    if np.isfinite(row.get("MACD_Histogram", np.nan)):
        pos = row["MACD_Histogram"] > 0
        score += int(pos)
        signals.append(f"MACD histogram {'positive' if pos else 'negative'}")
    return score, signals


def _market_context(
    market_close: pd.Series | None,
    vix_level: float | None,
) -> tuple[str, float | None, float | None]:
    if market_close is None or len(market_close) < 50:
        return "unknown", None, None
    close = pd.Series(market_close, dtype=float).dropna()
    peak = close.expanding().max()
    drawdown = float((close.iloc[-1] - peak.iloc[-1]) / peak.iloc[-1] * 100)
    returns = close.pct_change().dropna()
    recent_vol = float(returns.iloc[-20:].std() * np.sqrt(252) * 100) if len(returns) >= 20 else None
    if drawdown < -20:
        stress = "crisis"
    elif drawdown < -10:
        stress = "elevated"
    elif drawdown < -5:
        stress = "moderate"
    else:
        stress = "low"
    return stress, drawdown, vix_level


def detect_regime(
    enriched: pd.DataFrame,
    *,
    market_close: pd.Series | None = None,
    vix_level: float | None = None,
) -> RegimeState:
    """Rule-based regime detection from indicators at the latest bar."""
    required = ["Close", "SMA_20", "SMA_50", "ADX", "ATR_Pct", "RSI", "MACD_Histogram"]
    row = _safe_row(enriched, required)

    has_close = np.isfinite(row.get("Close", np.nan))
    has_anchor = np.isfinite(row.get("SMA_20", np.nan)) or np.isfinite(row.get("SMA_50", np.nan))
    if not (has_close and has_anchor):
        return RegimeState(
            regime=MarketRegime.UNCLASSIFIED,
            trend="unknown",
            volatility="unknown",
            stress="unknown",
            probability=0.0,
            adx=None,
            atr_percentile=None,
            vix_level=vix_level,
            nifty_drawdown_pct=None,
            basis="insufficient indicator history for a regime label.",
        )

    trend_score, trend_signals = _trend_signals(enriched)
    adx = row.get("ADX", np.nan)
    trending = np.isfinite(adx) and adx >= 25.0
    rsi = row.get("RSI", np.nan)

    if trend_score >= 3:
        trend = "strong_bullish" if (trending and (np.isnan(rsi) or rsi > 55)) else "mild_bullish"
    elif trend_score <= 1:
        trend = "strong_bearish" if (trending and (np.isnan(rsi) or rsi < 45)) else "mild_bearish"
    else:
        trend = "sideways"

    atr_pct = _atr_percentile(enriched)
    if atr_pct is None:
        volatility = "unknown"
    elif atr_pct >= 0.80:
        volatility = "high"
    elif atr_pct <= 0.20:
        volatility = "low"
    else:
        volatility = "normal"

    stress, nifty_dd, vix = _market_context(market_close, vix_level)

    if stress == "crisis":
        regime = MarketRegime.CRISIS
    elif trend in ("strong_bullish", "mild_bullish") and volatility == "high":
        regime = MarketRegime.BULL_TREND_HIGH_VOL
    elif trend in ("strong_bullish", "mild_bullish") and volatility == "low":
        regime = MarketRegime.BULL_TREND_LOW_VOL
    elif trend in ("strong_bearish", "mild_bearish") and volatility == "high":
        regime = MarketRegime.BEAR_TREND_HIGH_VOL
    elif trend in ("strong_bearish", "mild_bearish") and volatility == "low":
        regime = MarketRegime.BEAR_TREND_LOW_VOL
    elif trend == "sideways" and volatility == "high":
        regime = MarketRegime.SIDEWAYS_HIGH_VOL
    elif trend == "sideways" and volatility == "low":
        regime = MarketRegime.SIDEWAYS_LOW_VOL
    else:
        regime = MarketRegime.UNCLASSIFIED

    prob = 0.5
    if regime != MarketRegime.UNCLASSIFIED:
        prob = 0.7 if trending else 0.6
        if stress in ("elevated", "crisis"):
            prob = min(0.9, prob + 0.2)

    basis_parts = [
        f"trend_score={trend_score}/4",
        f"ADX={adx:.1f}" if np.isfinite(adx) else "ADX=nan",
        f"ATR_pctile={atr_pct:.2f}" if atr_pct is not None else "ATR_pctile=nan",
        f"stress={stress}",
    ]
    if vix_level is not None:
        basis_parts.append(f"India_VIX={vix_level:.1f}")
    if nifty_dd is not None:
        basis_parts.append(f"Nifty_DD={nifty_dd:.1f}%")

    return RegimeState(
        regime=regime,
        trend=trend,
        volatility=volatility,
        stress=stress,
        probability=round(prob, 2),
        adx=round(adx, 2) if np.isfinite(adx) else None,
        atr_percentile=round(atr_pct, 3) if atr_pct is not None else None,
        vix_level=vix_level,
        nifty_drawdown_pct=round(nifty_dd, 2) if nifty_dd is not None else None,
        basis="; ".join(basis_parts),
    )


def regime_router(
    regime: MarketRegime,
    *,
    available_models: Sequence[str],
) -> dict[str, float]:
    """Return model weights for the detected regime."""
    weights: dict[str, float] = {m: 1.0 for m in available_models}

    if regime == MarketRegime.CRISIS:
        weights["pooled_cross_sectional"] = weights.get("pooled_cross_sectional", 0) * 1.5
        weights["volatility_model"] = weights.get("volatility_model", 0) * 1.5
        weights["per_stock_cqr"] = weights.get("per_stock_cqr", 0) * 0.5
    elif regime in (MarketRegime.BULL_TREND_HIGH_VOL, MarketRegime.BEAR_TREND_HIGH_VOL):
        weights["volatility_model"] = weights.get("volatility_model", 0) * 1.3
        weights["regime_adaptive"] = weights.get("regime_adaptive", 0) * 1.2
    elif regime in (MarketRegime.BULL_TREND_LOW_VOL, MarketRegime.BEAR_TREND_LOW_VOL):
        weights["per_stock_cqr"] = weights.get("per_stock_cqr", 0) * 1.2
        weights["trend_following"] = weights.get("trend_following", 0) * 1.2
    elif regime in (MarketRegime.SIDEWAYS_LOW_VOL, MarketRegime.SIDEWAYS_HIGH_VOL):
        weights["mean_reversion"] = weights.get("mean_reversion", 0) * 1.3
        weights["volatility_model"] = weights.get("volatility_model", 0) * 1.1

    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    return weights


def regime_to_dict(state: RegimeState) -> dict[str, Any]:
    return {
        "regime": state.regime.value,
        "trend": state.trend,
        "volatility": state.volatility,
        "stress": state.stress,
        "probability": state.probability,
        "adx": state.adx,
        "atr_percentile": state.atr_percentile,
        "vix_level": state.vix_level,
        "nifty_drawdown_pct": state.nifty_drawdown_pct,
        "basis": state.basis,
    }


__all__: Sequence[str] = (
    "MarketRegime",
    "RegimeState",
    "detect_regime",
    "regime_router",
    "regime_to_dict",
)