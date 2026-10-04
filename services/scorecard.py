"""Public scorecard service for StockPilot AI v13.

Builds a transparent, real-time track record for the whole universe.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from database import get_settled_rows_for_quality
from forecasting.drift_monitor import compute_group_quality
from forecasting.model_promotion import active_promotion_receipt


def build_public_scorecard(
    *,
    tier_filter: str | None = None,
    horizon_filter: int | None = None,
    limit_symbols: int = 500,
) -> dict[str, Any]:
    """Build the public rolling scorecard from settled outcomes."""
    # Use the actual immutable ledger, excluding manual/stale/demo outcomes.
    # Never construct an imaginary settled_forecasts table or inferred tier.
    rows = []
    for row in get_settled_rows_for_quality(limit=10000):
        try:
            payload = json.loads(row.pop("payload_json", None) or "{}")
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        row["tier"] = payload.get("tier") or (payload.get("data_sufficiency") or {}).get("tier") or "unknown"
        regime = payload.get("regime") or {}
        row["regime"] = regime.get("regime", "unknown") if isinstance(regime, dict) else str(regime)
        if tier_filter and row["tier"] != tier_filter.upper():
            continue
        if horizon_filter and row["horizon_sessions"] != horizon_filter:
            continue
        rows.append(row)
        if len(rows) >= max(1, min(int(limit_symbols), 5000)):
            break
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["symbol"], row["timeframe"], str(row["horizon_sessions"])), []).append(row)
    for group in groups.values():
        quality = compute_group_quality(group)
        for row in group:
            row["mase"] = quality["mase"]
            row["mae"] = quality["model_mae"]
            row["directional_accuracy"] = quality["directional_accuracy"]

    if not rows:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "No settled forecasts available yet.",
            "tiers": {},
            "overall": {
                "total_forecasts": 0,
                "coverage": None,
                "winkler_score": None,
                "mase": None,
            },
            "conditional_coverage": {},
            "promotion_gates": {},
            "model_actions": {
                "auto_widened": 0,
                "retired": 0,
            },
        }

    # Group by tier
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tier = row["tier"] or "unknown"
        by_tier.setdefault(tier, []).append(dict(row))

    tier_results = {}
    all_forecasts = []

    for tier, forecasts in by_tier.items():
        if horizon_filter:
            forecasts = [f for f in forecasts if f["horizon_sessions"] == horizon_filter]

        if not forecasts:
            continue

        n = len(forecasts)
        coverage = sum(f["coverage_hit"] for f in forecasts) / n
        winkler = sum(f["winkler_score"] for f in forecasts) / n
        mase_vals = [f["mase"] for f in forecasts if f["mase"] is not None]
        mase = sum(mase_vals) / len(mase_vals) if mase_vals else None
        mae_vals = [f["mae"] for f in forecasts if f["mae"] is not None]
        mae = sum(mae_vals) / len(mae_vals) if mae_vals else None
        dir_acc_vals = [f["directional_accuracy"] for f in forecasts if f["directional_accuracy"] is not None]
        dir_acc = sum(dir_acc_vals) / len(dir_acc_vals) if dir_acc_vals else None

        # Conditional coverage by regime
        regimes: dict[str, list[dict[str, Any]]] = {}
        for f in forecasts:
            reg = f["regime"] or "unknown"
            regimes.setdefault(reg, []).append(f)

        conditional = {}
        for reg, reg_forecasts in regimes.items():
            if len(reg_forecasts) >= 5:
                cov = sum(f["coverage_hit"] for f in reg_forecasts) / len(reg_forecasts)
                conditional[reg] = round(cov, 4)

        tier_results[tier] = {
            "n_forecasts": n,
            "coverage": round(coverage, 4),
            "target_coverage": round(sum(float(f["confidence_level"]) for f in forecasts) / n, 4),
            "winkler_score": round(winkler, 4),
            "mase": round(mase, 4) if mase is not None else None,
            "mae": round(mae, 4) if mae is not None else None,
            "directional_accuracy": round(dir_acc, 4) if dir_acc is not None else None,
            "conditional_coverage": conditional,
        }
        all_forecasts.extend(forecasts)

    # Overall
    total = len(all_forecasts)
    overall = {
        "total_forecasts": total,
        "coverage": round(sum(f["coverage_hit"] for f in all_forecasts) / total, 4) if total else None,
        "winkler_score": round(sum(f["winkler_score"] for f in all_forecasts) / total, 4) if total else None,
        "mase": None,
        "mae": None,
    }
    if all_forecasts:
        mase_vals = [f["mase"] for f in all_forecasts if f["mase"] is not None]
        if mase_vals:
            overall["mase"] = round(sum(mase_vals) / len(mase_vals), 4)
        mae_vals = [f["mae"] for f in all_forecasts if f["mae"] is not None]
        if mae_vals:
            overall["mae"] = round(sum(mae_vals) / len(mae_vals), 4)

    # Only the real promotion receipt can claim a passed production gate.
    receipt = active_promotion_receipt() or {}
    actual_tiers = receipt.get("tier_results") or {}
    promotion_gates = {}
    for tier, res in tier_results.items():
        recorded = actual_tiers.get(tier, {}) if isinstance(actual_tiers, dict) else {}
        promotion_gates[tier] = {
            "passed": bool(receipt.get("passed") and recorded.get("passed")),
            "coverage_within_tolerance": abs(res["coverage"] - res["target_coverage"]) <= 0.05,
            "mase_not_worse_than_naive": res["mase"] is not None and res["mase"] <= 1.0,
            "basis": "production promotion receipt; rolling metrics alone cannot authorize promotion",
        }

    # Model actions (would come from drift monitor)
    model_actions = {
        "auto_widened": 0,
        "retired": 0,
    }

    # Conditional coverage overall
    all_conditional: dict[str, list[float]] = {}
    for tier, res in tier_results.items():
        for reg, cov in res["conditional_coverage"].items():
            all_conditional.setdefault(reg, []).append(cov)

    conditional_overall = {
        reg: round(sum(vals) / len(vals), 4)
        for reg, vals in all_conditional.items()
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Public rolling scorecard for StockPilot AI forecast models. "
            "Models are research-only, not investment advice. "
            "Coverage is measured against each saved nominal interval. "
            "MASE uses consecutive authoritative outcomes within symbol/timeframe/horizon groups. "
            "Missing tier metadata is unknown; insufficient evidence is not a passed promotion gate."
        ),
        "tiers": tier_results,
        "overall": overall,
        "conditional_coverage": conditional_overall,
        "promotion_gates": promotion_gates,
        "model_actions": model_actions,
    }
