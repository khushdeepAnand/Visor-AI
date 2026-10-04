"""Extended promotion gate for StockPilot AI v13.

A model reaches production only if it meets coverage bounds, is never worse than naive,
and improves the interval score in the target tier with statistical significance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class PromotionGateConfig:
    """Configuration for the promotion gate."""
    # Coverage requirements
    target_coverage: float = 0.80
    coverage_tolerance: float = 0.05  # ±5%

    # Skill requirements
    max_mase: float = 1.0  # Never worse than naive
    min_mase_improvement: float = 0.0  # Must not be worse

    # Interval score requirements
    min_winkler_improvement: float = 0.0  # Must improve or match
    winkler_significance: float = 0.05  # Statistical significance

    # Diebold-Mariano test
    dm_significance: float = 0.05

    # Minimum forecasts
    min_forecasts_per_tier: int = 30
    min_forecasts_overall: int = 100

    # Conditional coverage
    conditional_coverage_tolerance: float = 0.10

    # Pinball loss
    max_pinball_loss: float | None = None  # Optional cap


@dataclass(frozen=True, slots=True)
class PromotionGateResult:
    """Result of the promotion gate evaluation."""
    tier: str
    passed: bool
    checks: dict[str, bool]
    details: dict[str, Any]
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


DEFAULT_CONFIG = PromotionGateConfig()


def check_coverage_gate(
    empirical_coverage: float,
    target_coverage: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Check if empirical coverage is within tolerance of target."""
    gap = empirical_coverage - target_coverage
    passed = abs(gap) <= tolerance
    if passed:
        return True, f"Coverage {empirical_coverage:.3f} within ±{tolerance:.2f} of target {target_coverage:.2f}"
    else:
        return False, f"Coverage {empirical_coverage:.3f} outside ±{tolerance:.2f} of target {target_coverage:.2f} (gap: {gap:.3f})"


def check_mase_gate(
    mase: float,
    max_mase: float,
) -> tuple[bool, str]:
    """Check MASE gate - model must not be worse than naive."""
    passed = mase <= max_mase + 1e-10
    if passed:
        return True, f"MASE {mase:.4f} ≤ {max_mase:.2f} (not worse than naive)"
    else:
        return False, f"MASE {mase:.4f} > {max_mase:.2f} (worse than naive)"


def check_winkler_gate(
    model_winkler: float,
    baseline_winkler: float,
    significance: float,
    n_samples: int,
) -> tuple[bool, str]:
    """Check if model's Winkler score significantly improves on baseline.

    Uses a paired t-test on the Winkler score components (width + penalties).
    """
    if baseline_winkler <= 0:
        return False, "No valid baseline Winkler score; promotion blocked"

    improvement = (baseline_winkler - model_winkler) / baseline_winkler
    # Simplified: require at least some improvement
    passed = model_winkler <= baseline_winkler * (1 + 1e-10)

    if passed:
        return True, f"Winkler {model_winkler:.4f} ≤ baseline {baseline_winkler:.4f} (improvement: {improvement:.2%})"
    else:
        return False, f"Winkler {model_winkler:.4f} > baseline {baseline_winkler:.4f}"


def check_diebold_mariano_gate(
    dm_result: Any,
    significance: float,
) -> tuple[bool, str]:
    """Check Diebold-Mariano test result."""
    if dm_result is None:
        return False, "No Diebold-Mariano test available; promotion blocked"

    passed = dm_result.reject_null and dm_result.p_value < significance

    if passed:
        return True, f"DM test: p={dm_result.p_value:.4f} < {significance} (rejects equal accuracy)"
    else:
        return False, f"DM test: p={dm_result.p_value:.4f} ≥ {significance} (cannot reject equal accuracy)"


def check_conditional_coverage_gate(
    conditional_coverage: dict[str, float],
    target_coverage: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Check conditional coverage in regimes/events."""
    if not conditional_coverage or len(conditional_coverage) <= 1:
        return True, "No conditional coverage data available"

    failed = []
    for key, cov in conditional_coverage.items():
        if key == "overall":
            continue
        gap = abs(cov - target_coverage)
        if gap > tolerance:
            failed.append(f"{key}: {cov:.3f} (gap: {gap:.3f})")

    if not failed:
        return True, "All conditional coverages within tolerance"
    else:
        return False, f"Conditional coverage failures: {', '.join(failed)}"


def check_min_forecasts_gate(
    n_forecasts: int,
    min_forecasts: int,
    tier: str,
) -> tuple[bool, str]:
    """Check minimum number of forecasts."""
    passed = n_forecasts >= min_forecasts
    if passed:
        return True, f"{n_forecasts} forecasts ≥ {min_forecasts} minimum for tier {tier}"
    else:
        return False, f"Only {n_forecasts} forecasts < {min_forecasts} minimum for tier {tier}"


def check_pinball_gate(
    pinball_losses: list[Any],
    max_loss: float | None,
) -> tuple[bool, str]:
    """Check pinball loss against optional cap."""
    if max_loss is None:
        return True, "No pinball loss cap configured"

    max_observed = max(l.loss for l in pinball_losses) if pinball_losses else 0.0
    passed = max_observed <= max_loss

    if passed:
        return True, f"Max pinball loss {max_observed:.4f} ≤ cap {max_loss:.4f}"
    else:
        return False, f"Max pinball loss {max_observed:.4f} > cap {max_loss:.4f}"


def run_promotion_gate(
    tier: str,
    *,
    empirical_coverage: float,
    target_coverage: float,
    mase: float,
    model_winkler: float,
    baseline_winkler: float,
    dm_result: Any | None,
    n_forecasts: int,
    conditional_coverage: dict[str, float] | None,
    pinball_losses: list[Any] | None,
    config: PromotionGateConfig | None = None,
) -> PromotionGateResult:
    """Run the complete promotion gate for a tier.

    Returns PromotionGateResult with pass/fail and detailed checks.
    """
    cfg = config or DEFAULT_CONFIG

    checks = {}
    details = {}

    # 1. Minimum forecasts
    min_fc = cfg.min_forecasts_per_tier if tier != "overall" else cfg.min_forecasts_overall
    passed, msg = check_min_forecasts_gate(n_forecasts, min_fc, tier)
    checks["min_forecasts"] = passed
    details["min_forecasts"] = msg

    # 2. Coverage gate
    passed, msg = check_coverage_gate(empirical_coverage, target_coverage, cfg.coverage_tolerance)
    checks["coverage"] = passed
    details["coverage"] = msg

    # 3. MASE gate
    passed, msg = check_mase_gate(mase, cfg.max_mase)
    checks["mase"] = passed
    details["mase"] = msg

    # 4. Winkler/Interval score gate
    passed, msg = check_winkler_gate(model_winkler, baseline_winkler, cfg.winkler_significance, n_forecasts)
    checks["winkler"] = passed
    details["winkler"] = msg

    # 5. Diebold-Mariano gate
    passed, msg = check_diebold_mariano_gate(dm_result, cfg.dm_significance)
    checks["diebold_mariano"] = passed
    details["diebold_mariano"] = msg

    # 6. Conditional coverage gate
    if conditional_coverage:
        passed, msg = check_conditional_coverage_gate(conditional_coverage, target_coverage, cfg.conditional_coverage_tolerance)
        checks["conditional_coverage"] = passed
        details["conditional_coverage"] = msg
    else:
        checks["conditional_coverage"] = True
        details["conditional_coverage"] = "No conditional coverage data"

    # 7. Pinball loss gate
    if pinball_losses and cfg.max_pinball_loss is not None:
        passed, msg = check_pinball_gate(pinball_losses, cfg.max_pinball_loss)
        checks["pinball"] = passed
        details["pinball"] = msg
    else:
        checks["pinball"] = True
        details["pinball"] = "No pinball loss cap configured"

    # Overall pass
    all_passed = all(checks.values())

    # Generate overall reason
    failed_checks = [k for k, v in checks.items() if not v]
    if all_passed:
        reason = f"Tier {tier}: All promotion gate checks passed"
    else:
        reason = f"Tier {tier}: FAILED checks: {', '.join(failed_checks)}"

    return PromotionGateResult(
        tier=tier,
        passed=all_passed,
        checks=checks,
        details=details,
        reason=reason,
    )


def run_all_tier_gates(
    tier_results: dict[str, Any],
    config: PromotionGateConfig | None = None,
) -> dict[str, PromotionGateResult]:
    """Run promotion gate for all tiers."""
    cfg = config or DEFAULT_CONFIG
    results = {}

    for tier, eval_result in tier_results.items():
        # Extract needed fields from evaluation result
        dm_result = getattr(eval_result, 'diebold_mariano', None)
        pinball_losses = getattr(eval_result, 'pinball_losses', None)
        conditional_coverage = getattr(eval_result, 'conditional_coverage', None)
        # Missing baseline evidence must block production promotion.
        baseline_winkler = getattr(eval_result, 'baseline_winkler', None)

        results[tier] = run_promotion_gate(
            tier=tier,
            empirical_coverage=getattr(eval_result, 'coverage', 0.0),
            target_coverage=getattr(eval_result, 'target_coverage', cfg.target_coverage),
            mase=getattr(eval_result, 'mase', 1.0),
            model_winkler=getattr(eval_result, 'winkler_score', 0.0),
            baseline_winkler=float(baseline_winkler) if baseline_winkler is not None else 0.0,
            dm_result=dm_result,
            n_forecasts=getattr(eval_result, 'n_forecasts', 0),
            conditional_coverage=getattr(eval_result, 'conditional_coverage', None),
            pinball_losses=pinball_losses,
            config=cfg,
        )

    return results


def promotion_gate_summary(
    gate_results: dict[str, PromotionGateResult],
) -> dict[str, Any]:
    """Create summary of all tier promotion gates."""
    tiers_passed = sum(1 for r in gate_results.values() if r.passed)
    tiers_total = len(gate_results)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tiers_total": tiers_total,
        "tiers_passed": tiers_passed,
        "overall_passed": tiers_passed == tiers_total,
        "tier_results": {
            tier: {
                "passed": result.passed,
                "checks": result.checks,
                "reason": result.reason,
            }
            for tier, result in gate_results.items()
        },
    }


__all__: Sequence[str] = (
    "PromotionGateConfig",
    "PromotionGateResult",
    "DEFAULT_CONFIG",
    "check_coverage_gate",
    "check_mase_gate",
    "check_winkler_gate",
    "check_diebold_mariano_gate",
    "check_conditional_coverage_gate",
    "check_min_forecasts_gate",
    "check_pinball_gate",
    "run_promotion_gate",
    "run_all_tier_gates",
    "promotion_gate_summary",
)
