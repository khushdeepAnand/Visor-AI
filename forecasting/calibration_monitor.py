"""Rolling interval-forecast quality monitoring.

The monitor only evaluates forecasts whose realized target has been settled.
It reports empirical coverage and Winkler score without inventing observations
for pending forecasts.
"""
from __future__ import annotations

from typing import Any, Iterable


def summarize_interval_quality(rows: Iterable[dict[str, Any]], *, tolerance: float = 0.05) -> dict[str, Any]:
    settled = [row for row in rows if row.get("coverage_hit") is not None and row.get("winkler_score") is not None]
    if not settled:
        return {
            "settled_forecasts": 0,
            "empirical_coverage": None,
            "nominal_coverage": None,
            "mean_winkler_score": None,
            "coverage_gap": None,
            "state": "insufficient_data",
            "message": "No settled forecasts are available; interval calibration cannot be evaluated yet.",
        }
    empirical = sum(float(row["coverage_hit"]) for row in settled) / len(settled)
    nominal_values = [float(row.get("confidence_level") or 0.8) for row in settled]
    nominal = sum(nominal_values) / len(nominal_values)
    winkler = sum(float(row["winkler_score"]) for row in settled) / len(settled)
    gap = empirical - nominal
    state = "stable"
    if gap < -abs(tolerance):
        state = "under_coverage"
    elif gap > abs(tolerance):
        state = "over_coverage"
    return {
        "settled_forecasts": len(settled),
        "empirical_coverage": round(empirical, 6),
        "nominal_coverage": round(nominal, 6),
        "mean_winkler_score": round(winkler, 6),
        "coverage_gap": round(gap, 6),
        "state": state,
        "tolerance": abs(tolerance),
    }


def group_interval_quality(rows: Iterable[dict[str, Any]], *, tolerance: float = 0.05) -> dict[str, Any]:
    materialized = list(rows)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in materialized:
        key = (str(row.get("training_window") or "unknown"), str(row.get("timeframe") or "unknown"))
        grouped.setdefault(key, []).append(row)
    return {
        "overall": summarize_interval_quality(materialized, tolerance=tolerance),
        "by_window_timeframe": [
            {"training_window": key[0], "timeframe": key[1], **summarize_interval_quality(values, tolerance=tolerance)}
            for key, values in sorted(grouped.items())
        ],
    }


def group_by_horizon(rows: Iterable[dict[str, Any]], *, tolerance: float = 0.05) -> list[dict[str, Any]]:
    """Per-horizon calibration summary from settled ledger rows.

    Legacy rows recorded before multi-horizon support carry no ``horizon``
    column and are counted as the 1-session group so old evidence stays useful.
    """
    materialized = list(rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in materialized:
        key = str(row.get("horizon") or row.get("horizon_sessions") or "1")
        grouped.setdefault(key, []).append(row)
    return [
        {"horizon": key, **summarize_interval_quality(values, tolerance=tolerance)}
        for key, values in sorted(grouped.items(), key=lambda item: (int(item[0]) if str(item[0]).isdigit() else 1, item[0]))
    ]
