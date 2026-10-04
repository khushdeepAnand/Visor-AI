"""Data-tier router for StockPilot AI v13.

Replaces the single pipeline with an explicit data-tier router. Tiers are based
on usable, clean, adjusted trading days (not calendar age).

Tiers:
- T0: < 30 days  (fresh IPO / new listing) -> Volatility band only; no directional claim
- T1: 30-120 days (recent IPO, relisted) -> Calibrated range, wide; abstain on direction
- T2: 120-500 days (young company) -> Range + probability, limited direction
- T3: 500-2,500 days (mature mid/large cap) -> Full stack
- T4: 2,500+ days, liquid (blue chips, index members) -> Full stack + options-implied + multi-regime
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

import numpy as np
import pandas as pd


class DataTier(Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


TIER_THRESHOLDS = {
    DataTier.T0: 30,
    DataTier.T1: 120,
    DataTier.T2: 500,
    DataTier.T3: 2500,
    DataTier.T4: float("inf"),
}

TIER_DESCRIPTIONS = {
    DataTier.T0: "Fresh IPO or new listing (< 30 usable trading days)",
    DataTier.T1: "Recent IPO or relisted (30-120 usable trading days)",
    DataTier.T2: "Young company (120-500 usable trading days)",
    DataTier.T3: "Mature mid/large cap (500-2,500 usable trading days)",
    DataTier.T4: "Blue chip / index member (2,500+ usable trading days, liquid)",
}

TIER_PRIMARY_OUTPUT = {
    DataTier.T0: "Volatility band only; no directional claim",
    DataTier.T1: "Calibrated range, wide; abstain on direction",
    DataTier.T2: "Range + probability, limited direction",
    DataTier.T3: "Full stack (per-stock CQR + pooled + regime routing + stacking)",
    DataTier.T4: "Full stack + options-implied features + multi-regime models",
}

TIER_MODEL_STACK = {
    DataTier.T0: ["peer_prior", "ipo_specific_prior", "shrinkage"],
    DataTier.T1: ["pooled_cross_sectional", "volatility_model", "peer_transfer"],
    DataTier.T2: ["per_stock_gbm_quantiles", "pooled_blend", "volatility_model"],
    DataTier.T3: ["per_stock_cqr", "pooled", "regime_routing", "stacking"],
    DataTier.T4: ["per_stock_cqr", "pooled", "regime_routing", "stacking", "options_implied", "multi_regime"],
}

EVIDENCE_GRADES = ("A", "B", "C", "none")


@dataclass(frozen=True, slots=True)
class TierAssignment:
    tier: DataTier
    usable_days: int
    evidence_grade: str
    reason: str
    primary_output: str
    model_stack: list[str]
    supported_horizons: tuple[int, ...]
    liquidity_bucket: str
    corporate_action_flag: bool
    surveillance_flag: bool


def _liquidity_bucket(median_turnover: float | None, zero_volume_ratio: float) -> str:
    if median_turnover is None or not np.isfinite(median_turnover):
        return "unknown"
    if median_turnover >= 5e7 and zero_volume_ratio < 0.01:
        return "high"
    if median_turnover >= 1e7 and zero_volume_ratio < 0.05:
        return "normal"
    return "low"


def _corporate_action_flag(status: str) -> bool:
    return status in {"review_required", "detected_unadjusted"}


def _surveillance_flag(surveillance: str | None) -> bool:
    if surveillance is None:
        return False
    s = str(surveillance).upper()
    return any(flag in s for flag in ("ASM", "GSM", "SURVEILLANCE", "GRADED"))


def assign_tier(
    *,
    usable_days: int,
    validation_samples: int,
    median_turnover: float | None = None,
    zero_volume_ratio: float = 0.0,
    corporate_action_status: str = "not_assessed",
    surveillance_status: str | None = None,
    is_fno: bool = False,
    options_implied_available: bool = False,
) -> TierAssignment:
    """Assign a data tier based on usable history and evidence quality."""
    if usable_days < 0:
        raise ValueError("usable_days cannot be negative")

    if usable_days < TIER_THRESHOLDS[DataTier.T0]:
        tier = DataTier.T0
    elif usable_days < TIER_THRESHOLDS[DataTier.T1]:
        tier = DataTier.T1
    elif usable_days < TIER_THRESHOLDS[DataTier.T2]:
        tier = DataTier.T2
    elif usable_days < TIER_THRESHOLDS[DataTier.T3]:
        tier = DataTier.T3
    else:
        tier = DataTier.T4

    liquidity = _liquidity_bucket(median_turnover, zero_volume_ratio)
    ca_flag = _corporate_action_flag(corporate_action_status)
    surv_flag = _surveillance_flag(surveillance_status)

    if tier == DataTier.T0:
        evidence_grade = "none"
        reason = f"Only {usable_days} usable trading days; below minimum for any calibrated range."
        supported_horizons: tuple[int, ...] = ()
    elif tier == DataTier.T1:
        if validation_samples >= 18:
            evidence_grade = "C"
        elif validation_samples >= 5:
            evidence_grade = "C"
        else:
            evidence_grade = "none"
        reason = f"{usable_days} usable trading days; limited validation evidence ({validation_samples} samples)."
        supported_horizons = (1,) if validation_samples >= 5 else ()
    elif tier == DataTier.T2:
        if validation_samples >= 36:
            evidence_grade = "B"
        elif validation_samples >= 18:
            evidence_grade = "C"
        else:
            evidence_grade = "none"
        reason = f"{usable_days} usable trading days; moderate validation evidence ({validation_samples} samples)."
        supported_horizons = (1, 5) if validation_samples >= 18 else (1,)
    elif tier == DataTier.T3:
        if validation_samples >= 100:
            evidence_grade = "A"
        elif validation_samples >= 36:
            evidence_grade = "B"
        elif validation_samples >= 18:
            evidence_grade = "C"
        else:
            evidence_grade = "none"
        reason = f"{usable_days} usable trading days; substantial validation evidence ({validation_samples} samples)."
        supported_horizons = (1, 5, 10, 20) if validation_samples >= 36 else (1, 5, 10)
    else:
        if validation_samples >= 200 and options_implied_available and is_fno:
            evidence_grade = "A"
        elif validation_samples >= 100:
            evidence_grade = "A"
        elif validation_samples >= 36:
            evidence_grade = "B"
        elif validation_samples >= 18:
            evidence_grade = "C"
        else:
            evidence_grade = "none"
        reason = f"{usable_days} usable trading days; extensive validation evidence ({validation_samples} samples)."
        supported_horizons = (1, 5, 10, 20, 60) if validation_samples >= 100 else (1, 5, 10, 20)

    if ca_flag:
        reason += " Corporate action review required."
    if surv_flag:
        reason += " Surveillance flag active."

    return TierAssignment(
        tier=tier,
        usable_days=usable_days,
        evidence_grade=evidence_grade,
        reason=reason,
        primary_output=TIER_PRIMARY_OUTPUT[tier],
        model_stack=TIER_MODEL_STACK[tier],
        supported_horizons=supported_horizons,
        liquidity_bucket=liquidity,
        corporate_action_flag=ca_flag,
        surveillance_flag=surv_flag,
    )


def tier_to_dict(assignment: TierAssignment) -> dict[str, Any]:
    return {
        "tier": assignment.tier.value,
        "usable_trading_days": assignment.usable_days,
        "evidence_grade": assignment.evidence_grade,
        "reason": assignment.reason,
        "primary_output": assignment.primary_output,
        "model_stack": assignment.model_stack,
        "supported_horizons": list(assignment.supported_horizons),
        "liquidity_bucket": assignment.liquidity_bucket,
        "corporate_action_review_required": assignment.corporate_action_flag,
        "surveillance_flag": assignment.surveillance_flag,
    }


def describe_all_tiers() -> dict[str, Any]:
    return {
        "tiers": [
            {
                "tier": tier.value,
                "usable_days_range": f"{TIER_THRESHOLDS[prev] if prev != DataTier.T0 else 0}-{TIER_THRESHOLDS[tier] if tier != DataTier.T4 else '∞'}",
                "description": TIER_DESCRIPTIONS[tier],
                "primary_output": TIER_PRIMARY_OUTPUT[tier],
                "model_stack": TIER_MODEL_STACK[tier],
            }
            for prev, tier in zip(
                [DataTier.T0, DataTier.T0, DataTier.T1, DataTier.T2, DataTier.T3],
                [DataTier.T0, DataTier.T1, DataTier.T2, DataTier.T3, DataTier.T4],
            )
        ],
        "evidence_grades": list(EVIDENCE_GRADES),
    }


__all__: Sequence[str] = (
    "DataTier",
    "TierAssignment",
    "TIER_THRESHOLDS",
    "TIER_DESCRIPTIONS",
    "TIER_PRIMARY_OUTPUT",
    "TIER_MODEL_STACK",
    "assign_tier",
    "tier_to_dict",
    "describe_all_tiers",
)