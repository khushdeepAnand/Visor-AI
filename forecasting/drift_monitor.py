"""Rolling prediction-quality monitoring and drift auto-adaptation.

The monitor only evaluates forecasts whose realised outcome has been settled
(the authoritative, immutable ledger rows). It reports, per
``(symbol, timeframe, horizon)`` group:

- rolling empirical coverage vs the nominal confidence level over a sliding
  window, plus the gap;
- MASE relative to naive persistence, computed honestly from consecutive
  settled outcomes in the same group (the naive error for row ``i`` is
  ``|actual_i - actual_{i-1}|``, so no baseline is invented when outcomes are
  missing);
- directional accuracy against the same persistence reference.

``detect_drift`` raises a flag when the rolling coverage falls more than the
tolerance below nominal, when MASE reaches 1.0 (the model is no better than
no-change), or when directional accuracy decays below a threshold. The result
is consumed by the scheduler's auto-retrain job and by the admin quality
endpoint; nothing here changes a published forecast on its own.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

#: Sliding window used for the rolling coverage statistic.
DEFAULT_WINDOW = 20

#: Fewer settled outcomes than this means "not enough evidence to judge".
MIN_SETTLED_SAMPLES = 6

#: Coverage gap tolerance before drift is called.
COVERAGE_TOLERANCE = 0.10

#: MASE at/above 1.0 means the model is no better than naive persistence.
MASE_THRESHOLD = 1.0

#: Directional accuracy below this (with >= 10 samples) flags decay.
DIRECTIONAL_ACCURACY_THRESHOLD = 0.45


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sort_key(row: dict[str, Any]) -> tuple[int, str | int, int]:
    for key in ("target_timestamp", "origin_timestamp", "created_at"):
        value = row.get(key)
        if value:
            return (0, str(value), int(row.get("id") or 0))
    return (1, 0, int(row.get("id") or 0))


def compute_group_quality(rows: Iterable[dict[str, Any]], *, window: int = DEFAULT_WINDOW, min_settled: int = MIN_SETTLED_SAMPLES) -> dict[str, Any]:
    """Rolling coverage, MASE and directional accuracy for one group."""
    settled = [dict(row) for row in rows if row.get("actual_price") is not None]
    settled.sort(key=_sort_key)
    symbol = str(settled[0].get("symbol") or "?") if settled else "?"
    timeframe = str(settled[0].get("timeframe") or "") if settled else ""
    horizon = int(settled[0].get("horizon") or settled[0].get("horizon_sessions") or 1) if settled else 1

    base: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "horizon": horizon,
        "settled_samples": len(settled),
        "state": "insufficient_data",
        "window": int(window),
        "rolling_coverage": None,
        "nominal_coverage": None,
        "coverage_gap": None,
        "mase": None,
        "model_mae": None,
        "naive_mae": None,
        "directional_accuracy": None,
        "naive_directional_accuracy": None,
        "drift_detected": False,
        "drift_reasons": [],
        "severity": None,
    }
    if len(settled) == 0:
        base["message"] = "No settled outcomes are available for this group."
        return base

    recent = settled[-int(window):]
    coverage_hits = [1.0 if float(row["coverage_hit"]) else 0.0 for row in recent if row.get("coverage_hit") is not None]
    nominals = [float(row.get("confidence_level") or 0.8) for row in recent if row.get("coverage_hit") is not None]
    if coverage_hits:
        rolling = sum(coverage_hits) / len(coverage_hits)
        nominal = sum(nominals) / len(nominals)
        base["rolling_coverage"] = round(rolling, 6)
        base["nominal_coverage"] = round(nominal, 6)
        base["coverage_gap"] = round(rolling - nominal, 6)

    # MASE + directional accuracy over consecutive settled outcomes.
    model_errors: list[float] = []
    naive_errors: list[float] = []
    direction_correct = 0
    direction_decidable = 0
    naive_direction_correct = 0
    naive_direction_decidable = 0
    for index, row in enumerate(settled):
        actual = _finite(row.get("actual_price"))
        median = _finite(row.get("forecast_median"))
        low = _finite(row.get("forecast_low"))
        high = _finite(row.get("forecast_high"))
        if actual is None:
            continue
        if median is not None:
            model_errors.append(abs(actual - median))
        if index > 0:
            previous = _finite(settled[index - 1].get("actual_price"))
            if previous is not None:
                naive_errors.append(abs(actual - previous))
                # Actual direction vs persistence (previous settled outcome).
                actual_direction = 1 if actual > previous else (-1 if actual < previous else 0)
                # Model direction: braced range midpoint vs median.
                if median is not None and low is not None and high is not None and high > low:
                    midpoint = (low + high) / 2.0
                    model_direction = 1 if median > midpoint else (-1 if median < midpoint else 0)
                    if model_direction != 0 and actual_direction != 0:
                        direction_decidable += 1
                        direction_correct += int(model_direction == actual_direction)
                if index > 1:
                    prior = _finite(settled[index - 2].get("actual_price"))
                    if prior is not None and previous != prior:
                        naive_direction = 1 if previous > prior else -1
                        if actual_direction != 0:
                            naive_direction_decidable += 1
                            naive_direction_correct += int(naive_direction == actual_direction)

    if model_errors:
        base["model_mae"] = round(sum(model_errors) / len(model_errors), 6)
    if naive_errors:
        base["naive_mae"] = round(sum(naive_errors) / len(naive_errors), 6)
        if sum(naive_errors) > 0:
            base["mase"] = round(sum(model_errors) / sum(naive_errors), 6) if model_errors else None
    if direction_decidable:
        base["directional_accuracy"] = round(direction_correct / direction_decidable, 6)
    if naive_direction_decidable:
        base["naive_directional_accuracy"] = round(naive_direction_correct / naive_direction_decidable, 6)

    if len(settled) >= min_settled:
        base["state"] = "stable"
    else:
        base["state"] = "insufficient_data"
        base["message"] = f"Only {len(settled)} settled outcomes are available; at least {min_settled} are needed for a drift call."
    return base


def detect_drift(
    quality: dict[str, Any],
    *,
    min_settled: int = MIN_SETTLED_SAMPLES,
    coverage_tolerance: float = COVERAGE_TOLERANCE,
    mase_threshold: float = MASE_THRESHOLD,
    direction_threshold: float = DIRECTIONAL_ACCURACY_THRESHOLD,
) -> dict[str, Any]:
    """Decide drift for one group's quality payload (mutates nothing)."""
    reasons: list[str] = []
    if int(quality.get("settled_samples") or 0) < min_settled:
        return {
            "drift_detected": False,
            "severity": None,
            "drift_reasons": [],
            "message": "Not enough settled outcomes to judge drift.",
        }
    gap = _finite(quality.get("coverage_gap"))
    if gap is not None and gap < -abs(coverage_tolerance):
        reasons.append("rolling coverage fell below nominal by more than the tolerance")
    mase = _finite(quality.get("mase"))
    if mase is not None and mase >= mase_threshold:
        reasons.append("MASE reached 1.0 (no better than naive persistence)")
    direction = _finite(quality.get("directional_accuracy"))
    if direction is not None and direction < direction_threshold and int(quality.get("settled_samples") or 0) >= 10:
        reasons.append("directional accuracy decayed below threshold")
    return {
        "drift_detected": bool(reasons),
        "severity": "high" if reasons and (mase is not None and mase >= mase_threshold or (gap is not None and gap < -abs(coverage_tolerance))) else ("low" if reasons else None),
        "drift_reasons": reasons,
    }


def quality_dashboard(rows: Iterable[dict[str, Any]], *, window: int = DEFAULT_WINDOW, min_settled: int = MIN_SETTLED_SAMPLES, now: datetime | None = None) -> dict[str, Any]:
    """Group settled ledger rows by (symbol, timeframe, horizon) and judge each."""
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        timeframe = str(row.get("timeframe") or "")
        horizon = int(row.get("horizon") or row.get("horizon_sessions") or 1)
        groups.setdefault((symbol, timeframe, horizon), []).append(row)

    entries: list[dict[str, Any]] = []
    for (symbol, timeframe, horizon), group_rows in groups.items():
        quality = compute_group_quality(group_rows, window=window, min_settled=min_settled)
        drift = detect_drift(quality, min_settled=min_settled)
        quality.update(drift)
        if quality["state"] == "insufficient_data" and int(quality.get("settled_samples") or 0) == 0:
            quality["state"] = "insufficient_data"
        elif quality["state"] == "stable" and quality["drift_detected"]:
            quality["state"] = "drift"
        entries.append(quality)

    entries.sort(key=lambda entry: (entry["symbol"], entry["timeframe"], entry["horizon"]))
    drift_groups = [entry for entry in entries if entry["drift_detected"]]
    return {
        "groups": entries,
        "group_count": len(entries),
        "drift_active_groups": len(drift_groups),
        "policy": {
            "window": int(window),
            "min_settled": min_settled,
            "coverage_tolerance": COVERAGE_TOLERANCE,
            "mase_threshold": MASE_THRESHOLD,
            "directional_accuracy_threshold": DIRECTIONAL_ACCURACY_THRESHOLD,
            "naive_baseline": "persistence (previous settled outcome)",
            "note": "Drift is decided only from authoritative settled outcomes; pending and manually-settled rows are excluded.",
        },
        "last_evaluated": (now or datetime.now(timezone.utc)).isoformat(),
    }


__all__: Sequence[str] = (
    "COVERAGE_TOLERANCE",
    "DEFAULT_WINDOW",
    "DIRECTIONAL_ACCURACY_THRESHOLD",
    "MASE_THRESHOLD",
    "MIN_SETTLED_SAMPLES",
    "compute_group_quality",
    "detect_drift",
    "quality_dashboard",
)
