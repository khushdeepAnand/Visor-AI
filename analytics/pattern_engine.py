"""Transparent chart-pattern detection from OHLC data.

This module detects data-derived structures without claiming computer-vision
certainty. Scores are heuristic pattern-quality scores, not probabilities of a
profitable trade.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _prepare(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Pattern data must be a pandas DataFrame.")
    required = {"High", "Low", "Close"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError("Pattern data is missing: " + ", ".join(sorted(missing)))
    frame = data.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=list(required))
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    if len(frame) < 30:
        raise ValueError("At least 30 valid candles are required for pattern analysis.")
    return frame


def _pivot_indexes(series: pd.Series, window: int, mode: str) -> list[int]:
    values = series.to_numpy(dtype=float)
    indexes: list[int] = []
    for index in range(window, len(values) - window):
        region = values[index - window:index + window + 1]
        current = values[index]
        if mode == "high" and current >= np.nanmax(region):
            indexes.append(index)
        elif mode == "low" and current <= np.nanmin(region):
            indexes.append(index)
    return indexes


def _double_pattern(frame: pd.DataFrame, indexes: list[int], kind: str, tolerance: float) -> dict[str, Any] | None:
    if len(indexes) < 2:
        return None
    first, second = indexes[-2], indexes[-1]
    if second - first < 4:
        return None
    column = "High" if kind == "Double Top" else "Low"
    first_value = float(frame[column].iloc[first])
    second_value = float(frame[column].iloc[second])
    similarity = abs(first_value - second_value) / max(first_value, second_value)
    if similarity > tolerance:
        return None
    between = frame.iloc[first:second + 1]
    if kind == "Double Top":
        neckline = float(between["Low"].min())
        confirmation = float(frame["Close"].iloc[-1]) < neckline
        direction = "Bearish"
    else:
        neckline = float(between["High"].max())
        confirmation = float(frame["Close"].iloc[-1]) > neckline
        direction = "Bullish"
    score = max(0.0, min(1.0, 1.0 - similarity / tolerance))
    if confirmation:
        score = min(1.0, score + 0.15)
    return {
        "pattern": kind,
        "direction": direction,
        "quality_score": round(score, 4),
        "confirmed": bool(confirmation),
        "first_pivot_date": str(frame.index[first]),
        "second_pivot_date": str(frame.index[second]),
        "pivot_price": round((first_value + second_value) / 2.0, 4),
        "neckline": round(neckline, 4),
    }


def _triangle_pattern(frame: pd.DataFrame, lookback: int = 40) -> dict[str, Any] | None:
    recent = frame.tail(min(lookback, len(frame)))
    x = np.arange(len(recent), dtype=float)
    high_slope = float(np.polyfit(x, recent["High"].to_numpy(dtype=float), 1)[0])
    low_slope = float(np.polyfit(x, recent["Low"].to_numpy(dtype=float), 1)[0])
    mean_price = float(recent["Close"].mean())
    normalized_high = high_slope / mean_price
    normalized_low = low_slope / mean_price
    width_start = float(recent["High"].iloc[:5].mean() - recent["Low"].iloc[:5].mean())
    width_end = float(recent["High"].iloc[-5:].mean() - recent["Low"].iloc[-5:].mean())
    contracting = width_end < width_start * 0.85
    if not contracting:
        return None
    if normalized_high < -0.0002 and normalized_low > 0.0002:
        name, direction = "Symmetrical Triangle", "Neutral / breakout pending"
    elif abs(normalized_high) <= 0.0002 and normalized_low > 0.0002:
        name, direction = "Ascending Triangle", "Bullish bias"
    elif normalized_high < -0.0002 and abs(normalized_low) <= 0.0002:
        name, direction = "Descending Triangle", "Bearish bias"
    else:
        return None
    contraction = max(0.0, min(1.0, 1.0 - width_end / max(width_start, 1e-9)))
    return {
        "pattern": name,
        "direction": direction,
        "quality_score": round(0.45 + 0.5 * contraction, 4),
        "confirmed": False,
        "lookback_candles": len(recent),
    }


def _fair_value_gaps(frame: pd.DataFrame, limit: int = 5) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for index in range(2, len(frame)):
        first_high = float(frame["High"].iloc[index - 2])
        first_low = float(frame["Low"].iloc[index - 2])
        third_high = float(frame["High"].iloc[index])
        third_low = float(frame["Low"].iloc[index])
        if third_low > first_high:
            gaps.append({
                "pattern": "Bullish Fair Value Gap",
                "direction": "Bullish",
                "quality_score": round(min(1.0, (third_low - first_high) / first_high * 20), 4),
                "confirmed": True,
                "date": str(frame.index[index]),
                "zone_low": round(first_high, 4),
                "zone_high": round(third_low, 4),
            })
        elif third_high < first_low:
            gaps.append({
                "pattern": "Bearish Fair Value Gap",
                "direction": "Bearish",
                "quality_score": round(min(1.0, (first_low - third_high) / first_low * 20), 4),
                "confirmed": True,
                "date": str(frame.index[index]),
                "zone_low": round(third_high, 4),
                "zone_high": round(first_low, 4),
            })
    return gaps[-limit:]


def detect_chart_patterns(
    data: pd.DataFrame,
    *,
    pivot_window: int = 3,
    tolerance: float = 0.025,
) -> dict[str, Any]:
    """Detect pivots, double formations, triangles, breakouts and FVGs."""

    frame = _prepare(data)
    pivot_window = max(2, min(int(pivot_window), 10))
    tolerance = max(0.005, min(float(tolerance), 0.10))
    highs = _pivot_indexes(frame["High"], pivot_window, "high")
    lows = _pivot_indexes(frame["Low"], pivot_window, "low")
    patterns: list[dict[str, Any]] = []
    for candidate in (
        _double_pattern(frame, highs, "Double Top", tolerance),
        _double_pattern(frame, lows, "Double Bottom", tolerance),
        _triangle_pattern(frame),
    ):
        if candidate:
            patterns.append(candidate)
    patterns.extend(_fair_value_gaps(frame))

    lookback = min(60, len(frame) - 1)
    prior = frame.iloc[-lookback - 1:-1]
    latest_close = float(frame["Close"].iloc[-1])
    resistance = float(prior["High"].max())
    support = float(prior["Low"].min())
    breakout = "None"
    if latest_close > resistance:
        breakout = "Bullish breakout"
        patterns.append({
            "pattern": breakout,
            "direction": "Bullish",
            "quality_score": round(min(1.0, 0.6 + (latest_close / resistance - 1) * 20), 4),
            "confirmed": True,
            "level": round(resistance, 4),
        })
    elif latest_close < support:
        breakout = "Bearish breakdown"
        patterns.append({
            "pattern": breakout,
            "direction": "Bearish",
            "quality_score": round(min(1.0, 0.6 + (support / latest_close - 1) * 20), 4),
            "confirmed": True,
            "level": round(support, 4),
        })

    patterns.sort(key=lambda item: float(item.get("quality_score", 0.0)), reverse=True)
    return {
        "as_of": str(frame.index[-1]),
        "latest_close": round(latest_close, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "breakout_status": breakout,
        "pivot_high_count": len(highs),
        "pivot_low_count": len(lows),
        "patterns": patterns,
        "methodology": "Rule-based OHLC pattern detection using local pivots, regression slopes and three-candle gaps.",
        "disclaimer": "Pattern quality scores are heuristic structure scores, not probabilities of profit or investment advice.",
    }
