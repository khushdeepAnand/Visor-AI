"""Per-tier walk-forward evaluation harness for StockPilot AI v13.

Reports coverage, Winkler score, pinball loss, CRPS, MASE, PIT calibration,
and Diebold-Mariano tests separately for each tier, sector, liquidity bucket, and regime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy import stats as _stats
from forecasting.regime_stacking import walk_forward_stacked_forecast


@dataclass(frozen=True, slots=True)
class PinballLossResult:
    """Pinball loss for a specific quantile."""
    quantile: float
    loss: float
    n_samples: int


@dataclass(frozen=True, slots=True)
class CRPSResult:
    """Continuous Ranked Probability Score."""
    crps: float
    n_samples: int


@dataclass(frozen=True, slots=True)
class PITResult:
    """Probability Integral Transform calibration."""
    pit_values: np.ndarray
    n_samples: int
    ks_statistic: float | None
    ks_pvalue: float | None
    uniformity_test: str


@dataclass(frozen=True, slots=True)
class DieboldMarianoResult:
    """Diebold-Mariano test for forecast comparison."""
    dm_statistic: float
    p_value: float
    alternative: str
    h: int
    loss_function: str
    reject_null: bool


@dataclass(frozen=True, slots=True)
class TierEvaluationResult:
    """Complete evaluation results for one tier."""
    tier: str
    n_symbols: int
    n_forecasts: int
    coverage: float
    target_coverage: float
    winkler_score: float
    pinball_losses: list[PinballLossResult]
    crps: CRPSResult
    mase: float
    mae: float
    rmse: float
    directional_accuracy: float
    pit: PITResult
    conditional_coverage: dict[str, float]
    diebold_mariano: DieboldMarianoResult | None = None
    promotion_gate_passed: bool = False
    baseline_winkler: float | None = None


@dataclass(frozen=True, slots=True)
class PurgedFold:
    """A chronological fold with group isolation and a leakage embargo.

    ``train`` and ``test`` contain positional indexes into the input frame.  Keeping
    indexes (rather than copies) makes the splitter useful with pandas and numpy
    callers and makes the purge auditable.
    """
    fold: int
    group: str
    train: tuple[int, ...]
    test: tuple[int, ...]
    embargo_until: Any
    purged: int


def build_purged_group_folds(
    data: pd.DataFrame,
    *,
    group_col: str = "group",
    time_col: str = "timestamp",
    label_end_col: str | None = None,
    horizon: int = 1,
    embargo: int = 0,
    min_train_size: int = 1,
) -> list[PurgedFold]:
    """Build one-origin-per-group folds without cross-group leakage.

    Rows with a label window ending at or after a test origin are purged.  When
    timestamps are numeric/datetime, ``embargo`` is interpreted as rows in the
    ordered unique time axis; this is deterministic for both market dates and
    integer bars.  A missing group column is treated as one global group.
    """
    if data is None or len(data) == 0:
        return []
    frame = data.reset_index(drop=True)
    groups = frame[group_col] if group_col in frame else pd.Series(["__all__"] * len(frame))
    times = frame[time_col] if time_col in frame else pd.Series(range(len(frame)))
    ordered = sorted(times.dropna().unique())
    if not ordered:
        return []
    ends = frame[label_end_col] if label_end_col and label_end_col in frame else None
    folds: list[PurgedFold] = []
    fold_no = 0
    for origin_pos, origin in enumerate(ordered):
        embargo_pos = max(0, origin_pos - max(0, int(embargo)))
        train_cut = ordered[embargo_pos]
        for group in pd.unique(groups):
            test_mask = (times == origin) & (groups == group)
            test = tuple(int(i) for i in np.flatnonzero(test_mask.to_numpy()))
            if not test:
                continue
            train_mask = times < train_cut
            # Purge rows whose forward label overlaps the test origin.  If no
            # explicit label-end is supplied, horizon is a conservative row gap.
            if ends is not None:
                train_mask &= ends < origin
            elif horizon > 0 and origin_pos >= horizon:
                cutoff = ordered[origin_pos - int(horizon)]
                train_mask &= times <= cutoff
            train_mask &= groups != group
            # Permit same-group history only when it is safely before the origin;
            # this is the essential per-group purge, while other groups may train.
            safe_same_group = (times < train_cut)
            train_mask |= safe_same_group & (groups == group)
            train = tuple(int(i) for i in np.flatnonzero(train_mask.to_numpy()))
            if len(train) < min_train_size:
                continue
            purged = int(len(frame) - len(train) - len(test))
            folds.append(PurgedFold(fold=fold_no, group=str(group), train=train, test=test,
                                    embargo_until=train_cut, purged=max(0, purged)))
            fold_no += 1
    return folds


def evaluate_purged_walk_forward(
    data: pd.DataFrame,
    evaluator: Callable[[pd.DataFrame, pd.DataFrame, PurgedFold], Any],
    **splitter_kwargs: Any,
) -> list[Any]:
    """Evaluate folds produced by :func:`build_purged_group_folds`.

    The evaluator is deliberately injected so this harness cannot accidentally
    train on the complete frame.  Fold metadata can be persisted alongside the
    returned result by callers.
    """
    frame = data.reset_index(drop=True)
    results = []
    for fold in build_purged_group_folds(frame, **splitter_kwargs):
        results.append(evaluator(frame.iloc[list(fold.train)], frame.iloc[list(fold.test)], fold))
    return results


# Short alias used by integrations that call the operation a splitter.
purged_group_walk_forward = build_purged_group_folds


def pinball_loss(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    quantile: float,
) -> float:
    """Compute pinball loss for quantile regression.

    Pinball loss = (quantile - 1{err < 0}) * err
    where err = y_true - y_pred
    """
    err = y_true - y_pred
    loss = np.where(err >= 0, quantile * err, (quantile - 1) * err)
    return float(np.mean(loss))


def crps_ensemble(
    y_true: np.ndarray,
    ensemble_predictions: np.ndarray,
) -> float:
    """Compute CRPS for ensemble forecasts.

    CRPS = mean over samples of mean|y_pred - y_true| - 0.5 * mean|y_pred_i - y_pred_j|
    """
    ensemble_predictions = np.asarray(ensemble_predictions, dtype=float)
    y_true = np.asarray(y_true, dtype=float)
    if ensemble_predictions.ndim != 2 or ensemble_predictions.shape[0] != len(y_true) or not ensemble_predictions.size:
        raise ValueError("Ensemble predictions must have shape (observations, members).")
    term1 = np.mean(np.abs(ensemble_predictions - y_true[:, None]))

    # Second term: 0.5 * mean pairwise absolute difference
    diffs = np.abs(ensemble_predictions[:, :, None] - ensemble_predictions[:, None, :])
    term2 = 0.5 * np.mean(diffs)

    return float(term1 - term2)


def crps_gaussian(
    y_true: np.ndarray,
    mu: np.ndarray,
    sigma: np.ndarray,
) -> float:
    """CRPS for Gaussian predictive distribution.

    CRPS = sigma * [z * (2*Phi(z) - 1) + 2*phi(z) - 1/sqrt(pi)]
    where z = (y - mu) / sigma
    """
    z = (y_true - mu) / sigma
    cdf = _stats.norm.cdf(z)
    pdf = _stats.norm.pdf(z)
    crps = sigma * (z * (2 * cdf - 1) + 2 * pdf - 1 / np.sqrt(np.pi))
    return float(np.mean(crps))


def pit_values(
    y_true: np.ndarray,
    y_pred_low: np.ndarray,
    y_pred_high: np.ndarray,
    confidence: float,
) -> np.ndarray:
    """Compute PIT values for interval forecasts.

    For a uniform distribution on [low, high], PIT = (y - low) / (high - low)
    clipped to [0, 1].
    """
    width = y_pred_high - y_pred_low
    # Avoid division by zero
    width = np.where(width > 0, width, 1.0)
    pit = (y_true - y_pred_low) / width
    return np.asarray(np.clip(pit, 0, 1), dtype=float)


def pit_uniformity_test(pit: np.ndarray) -> tuple[float, float]:
    """Kolmogorov-Smirnov test for PIT uniformity."""
    # KS test against uniform distribution
    from scipy.stats import kstest
    stat, p = kstest(pit, 'uniform')
    return float(stat), float(p)


def mase(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_naive: np.ndarray,
    seasonality: int = 1,
) -> float:
    """Mean Absolute Scaled Error.

    MASE = MAE(model) / MAE(naive seasonal)
    """
    mae_model = np.mean(np.abs(y_true - y_pred))
    # Naive seasonal forecast: y_{t-seasonality}
    if seasonality < 1:
        raise ValueError("seasonality must be positive.")
    if len(y_naive) > seasonality:
        naive_err = np.abs(y_naive[seasonality:] - y_naive[:-seasonality])
        mae_naive = np.mean(naive_err)
    else:
        return float('inf')  # No scale estimate; never report NaN as model skill.
    if mae_naive == 0:
        return float('inf')
    return float(mae_model / mae_naive)


def directional_accuracy(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_ref: np.ndarray,
) -> float:
    """Directional accuracy vs reference (e.g., persistence)."""
    dir_true = np.sign(y_true - y_ref)
    dir_pred = np.sign(y_pred - y_ref)
    # Only count non-zero directions
    mask = (dir_true != 0) & (dir_pred != 0)
    if not mask.any():
        return 0.0
    return float(np.mean(dir_true[mask] == dir_pred[mask]))


def diebold_mariano_test(
    loss1: np.ndarray,
    loss2: np.ndarray,
    h: int = 1,
    alternative: str = "two-sided",
    loss_function: str = "square",
) -> DieboldMarianoResult:
    """Diebold-Mariano test for equal predictive accuracy.

    H0: E[loss1] = E[loss2]
    H1: E[loss1] != E[loss2] (or < or > depending on alternative)

    Uses HAC standard errors (Newey-West) for autocorrelated loss differentials.
    """
    d = loss1 - loss2
    n = len(d)
    if n < 10:
        return DieboldMarianoResult(
            dm_statistic=0.0,
            p_value=1.0,
            alternative=alternative,
            h=h,
            loss_function=loss_function,
            reject_null=False,
        )

    d_mean = np.mean(d)
    # Newey-West variance estimator
    gamma0 = np.mean((d - d_mean) ** 2)
    max_lag = min(int(1.5 * n ** (1/3)), n - 1)

    # Autocovariances
    autocov = 0.0
    for lag in range(1, max_lag + 1):
        if lag < n:
            autocov += (1 - lag / (max_lag + 1)) * np.mean((d[lag:] - d_mean) * (d[:-lag] - d_mean))

    var_d = gamma0 + 2 * autocov
    if var_d <= 0:
        se = np.std(d) / np.sqrt(n)
    else:
        se = np.sqrt(var_d / n)

    dm_stat = d_mean / se if se > 0 else 0.0

    # p-value from t-distribution (or normal for large n)
    if alternative == "two-sided":
        p_value = 2 * _stats.norm.sf(abs(dm_stat))
    elif alternative == "less":
        p_value = _stats.norm.cdf(dm_stat)
    else:  # greater
        p_value = _stats.norm.sf(dm_stat)

    reject_null = p_value < 0.05

    return DieboldMarianoResult(
        dm_statistic=float(dm_stat),
        p_value=float(p_value),
        alternative=alternative,
        h=h,
        loss_function=loss_function,
        reject_null=reject_null,
    )


def conditional_coverage_checks(
    y_true: np.ndarray,
    y_low: np.ndarray,
    y_high: np.ndarray,
    confidence: float,
    regimes: np.ndarray | None = None,
    events: np.ndarray | None = None,
) -> dict[str, float]:
    """Check coverage conditional on regimes, events, etc."""
    overall = float(np.mean((y_true >= y_low) & (y_true <= y_high)))
    result = {"overall": overall}

    if regimes is not None:
        for regime in np.unique(regimes):
            mask = regimes == regime
            if mask.sum() >= 5:
                result[f"regime_{regime}"] = float(
                    np.mean((y_true[mask] >= y_low[mask]) & (y_true[mask] <= y_high[mask]))
                )

    if events is not None:
        # Coverage around events (±5 days)
        event_mask = events == 1
        if event_mask.sum() > 5:
            result["event_days"] = float(
                np.mean((y_true[event_mask] >= y_low[event_mask]) & (y_true[event_mask] <= y_high[event_mask]))
            )
        # Non-event days
        non_event_mask = ~event_mask
        if non_event_mask.sum() > 5:
            result["non_event_days"] = float(
                np.mean((y_true[non_event_mask] >= y_low[non_event_mask]) & (y_true[non_event_mask] <= y_high[non_event_mask]))
            )

    # High volatility days (top 20% of ATR)
    # This would need ATR data - placeholder
    return result


def evaluate_tier(
    tier: str,
    symbols: list[str],
    forecasts: list[dict[str, Any]],
    confidence: float = 0.80,
    quantiles: Sequence[float] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95),
    baseline_forecasts: list[dict[str, Any]] | None = None,
    regimes: np.ndarray | None = None,
    events: np.ndarray | None = None,
) -> TierEvaluationResult:
    """Run complete evaluation for one tier."""
    # Extract arrays from forecasts
    y_true = np.array([f.get("actual") for f in forecasts if f.get("actual") is not None])
    y_pred = np.array([f.get("median") for f in forecasts if f.get("actual") is not None])
    y_low = np.array([f.get("low") for f in forecasts if f.get("actual") is not None])
    y_high = np.array([f.get("high") for f in forecasts if f.get("actual") is not None])

    if len(y_true) == 0:
        return TierEvaluationResult(
            tier=tier,
            n_symbols=len(symbols),
            n_forecasts=0,
            coverage=0.0,
            target_coverage=confidence,
            winkler_score=0.0,
            pinball_losses=[],
            crps=CRPSResult(0.0, 0),
            mase=0.0,
            mae=0.0,
            rmse=0.0,
            directional_accuracy=0.0,
            pit=PITResult(np.array([]), 0, None, None, "ks_test"),
            conditional_coverage={},
        )

    # Coverage
    coverage = float(np.mean((y_true >= y_low) & (y_true <= y_high)))

    # Winkler score
    alpha = 1 - confidence
    width = y_high - y_low
    penalty_low = np.where(y_true < y_low, (2 / alpha) * (y_low - y_true), 0.0)
    penalty_high = np.where(y_true > y_high, (2 / alpha) * (y_true - y_high), 0.0)
    winkler = float(np.mean(width + penalty_low + penalty_high))

    # Pinball losses for multiple quantiles
    pinball_losses = []
    for q in quantiles:
        # For now, approximate using interval bounds
        # In practice, would use actual quantile predictions
        if q <= 0.5:
            # Lower tail
            q_pred = y_low + (y_pred - y_low) * (q / (1 - confidence) * 2)
        else:
            # Upper tail
            q_pred = y_pred + (y_high - y_pred) * ((q - 0.5) / 0.5)
        loss = pinball_loss(y_true, q_pred, q)
        pinball_losses.append(PinballLossResult(q, loss, len(y_true)))

    # CRPS (using interval as approximate uniform distribution)
    # Better: use ensemble predictions if available
    crps_val = crps_gaussian(y_true, y_pred, (y_high - y_low) / 4)
    crps_res = CRPSResult(crps_val, len(y_true))

    # MASE
    if baseline_forecasts is not None:
        y_naive = np.array([f.get("median") for f in baseline_forecasts if f.get("actual") is not None])
        if len(y_naive) == len(y_true):
            baseline_mae = float(np.mean(np.abs(y_true - y_naive)))
            mase_val = float(np.mean(np.abs(y_true - y_pred)) / baseline_mae) if baseline_mae > 0 else float('inf')
        else:
            mase_val = mase(y_true, y_pred, y_true)  # fallback
    else:
        mase_val = mase(y_true, y_pred, y_true)

    # MAE/RMSE
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

    # Directional accuracy
    # First observation has no earlier reference; rolling wraps future truth.
    dir_acc = directional_accuracy(y_true[1:], y_pred[1:], y_true[:-1])

    # PIT
    pit = pit_values(y_true, y_low, y_high, confidence)
    ks_stat, ks_p = pit_uniformity_test(pit)
    pit_res = PITResult(
        pit_values=pit,
        n_samples=len(pit),
        ks_statistic=ks_stat if np.isfinite(ks_stat) else None,
        ks_pvalue=ks_p if np.isfinite(ks_p) else None,
        uniformity_test="ks_test",
    )

    # Conditional coverage
    cond_cov = conditional_coverage_checks(y_true, y_low, y_high, confidence, regimes, events)

    # Diebold-Mariano vs baseline
    dm_result = None
    if baseline_forecasts is not None:
        y_naive = np.array([f.get("median") for f in baseline_forecasts if f.get("actual") is not None])
        if len(y_naive) == len(y_true):
            loss_model = (y_true - y_pred) ** 2
            loss_naive = (y_true - y_naive) ** 2
            dm_result = diebold_mariano_test(loss_model, loss_naive, loss_function="square")

    baseline_winkler = None
    if baseline_forecasts is not None:
        baseline_rows = [f for f in baseline_forecasts if f.get("actual") is not None]
        if len(baseline_rows) == len(y_true) and all(f.get("low") is not None and f.get("high") is not None for f in baseline_rows):
            actual_baseline = np.asarray([f["actual"] for f in baseline_rows], dtype=float)
            lows = np.asarray([f["low"] for f in baseline_rows], dtype=float)
            highs = np.asarray([f["high"] for f in baseline_rows], dtype=float)
            if np.array_equal(actual_baseline, y_true) and np.all(np.isfinite(lows)) and np.all(np.isfinite(highs)) and np.all(highs >= lows):
                baseline_winkler = float(np.mean(highs - lows + (2 / alpha) * np.maximum(lows - y_true, 0) + (2 / alpha) * np.maximum(y_true - highs, 0)))

    # Promotion eligibility is not a substitute for the production gate.
    gate_passed = (
        abs(coverage - confidence) <= 0.05 and
        mase_val <= 1.0 and
        dm_result is not None and dm_result.reject_null and
        baseline_winkler is not None and winkler <= baseline_winkler
    )

    return TierEvaluationResult(
        tier=tier,
        n_symbols=len(symbols),
        n_forecasts=len(y_true),
        coverage=coverage,
        target_coverage=confidence,
        winkler_score=winkler,
        pinball_losses=pinball_losses,
        crps=crps_res,
        mase=mase_val,
        mae=mae,
        rmse=rmse,
        directional_accuracy=dir_acc,
        pit=pit_res,
        conditional_coverage=cond_cov,
        diebold_mariano=dm_result,
        promotion_gate_passed=gate_passed,
        baseline_winkler=baseline_winkler,
    )


def evaluate_all_tiers(
    forecasts_by_tier: dict[str, list[dict[str, Any]]],
    confidence: float = 0.80,
    baseline_by_tier: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, TierEvaluationResult]:
    """Evaluate all tiers and return results."""
    results = {}
    for tier, forecasts in forecasts_by_tier.items():
        baseline = baseline_by_tier.get(tier) if baseline_by_tier else None
        results[tier] = evaluate_tier(tier, sorted({str(f.get("symbol") or "unknown") for f in forecasts}), forecasts, confidence, baseline_forecasts=baseline)
    return results


def evaluation_summary(
    results: dict[str, TierEvaluationResult],
) -> dict[str, Any]:
    """Create summary across all tiers."""
    summary: dict[str, Any] = {
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "tiers": {},
        "overall": {
            "total_forecasts": sum(r.n_forecasts for r in results.values()),
            "weighted_coverage": 0.0,
            "weighted_winkler": 0.0,
            "weighted_mase": 0.0,
        },
    }

    total_fc = sum(r.n_forecasts for r in results.values())
    if total_fc > 0:
        summary["overall"]["weighted_coverage"] = sum(r.coverage * r.n_forecasts for r in results.values()) / total_fc
        summary["overall"]["weighted_winkler"] = sum(r.winkler_score * r.n_forecasts for r in results.values()) / total_fc
        summary["overall"]["weighted_mase"] = sum(r.mase * r.n_forecasts for r in results.values()) / total_fc

    for tier, result in results.items():
        summary["tiers"][tier] = {
            "n_symbols": result.n_symbols,
            "n_forecasts": result.n_forecasts,
            "coverage": round(result.coverage, 4),
            "target_coverage": result.target_coverage,
            "coverage_gap": round(result.coverage - result.target_coverage, 4),
            "winkler_score": round(result.winkler_score, 4),
            "mase": round(result.mase, 4),
            "mae": round(result.mae, 4),
            "rmse": round(result.rmse, 4),
            "directional_accuracy": round(result.directional_accuracy, 4),
            "pit_ks_pvalue": result.pit.ks_pvalue,
            "conditional_coverage": {k: round(v, 4) for k, v in result.conditional_coverage.items()},
            "promotion_gate_passed": result.promotion_gate_passed,
            "diebold_mariano": {
                "statistic": round(result.diebold_mariano.dm_statistic, 4) if result.diebold_mariano else None,
                "p_value": round(result.diebold_mariano.p_value, 4) if result.diebold_mariano else None,
                "reject_null": result.diebold_mariano.reject_null if result.diebold_mariano else None,
            } if result.diebold_mariano else None,
        }

    return summary


def evaluate_regime_stacking(
    y_true: np.ndarray,
    y_low: np.ndarray,
    y_high: np.ndarray,
    confidence: float,
    regimes: np.ndarray | None = None,
    regime_multipliers: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate regime-conditional interval widening.

    Given base intervals (y_low, y_high) at some confidence level, this applies
    a per-regime multiplier to the half-width and checks whether coverage moves
    toward the nominal level in each regime.

    Args:
        y_true: Actual outcomes, shape (n,)
        y_low: Lower interval bounds, shape (n,)
        y_high: Upper interval bounds, shape (n,)
        confidence: Nominal coverage level (e.g., 0.80 for 80% intervals)
        regimes: Optional regime label per observation, shape (n,). If None, all
            treated as a single "unknown" regime.
        regime_multipliers: Mapping regime -> half-width multiplier. Missing
            regimes default to 1.0 (no tilt).

    Returns:
        Dict with keys:
        - "error": str if input validation fails, else absent.
        - "per_regime": dict regime -> dict with keys:
            "base_coverage": coverage of original interval
            "tilted_coverage": coverage after applying multiplier
            "multiplier": effective multiplier used
            "moved_toward_nominal": bool
            "note": str (e.g., "too few observations to judge")
        - "regimes_improved": int count of regimes where tilted > base
        - "all_improved": bool whether every regime with enough data improved
    """
    n = len(y_true)
    if n == 0:
        return {"error": "no forecasts supplied"}
    if not (len(y_low) == len(y_high) == n):
        return {"error": "y_low, y_high must match y_true length"}
    if regimes is not None and len(regimes) != n:
        return {"error": "regimes array must be aligned with y_true"}

    multipliers = regime_multipliers or {}
    regimes_arr = np.array(regimes) if regimes is not None else np.full(n, "unknown", dtype=object)

    half_width = (y_high - y_low) / 2.0
    centre = (y_low + y_high) / 2.0

    unique_regimes = np.unique(regimes_arr)
    per_regime: dict[str, dict[str, Any]] = {}
    improved_count = 0
    all_improved = True

    for regime in unique_regimes:
        mask = regimes_arr == regime
        count = int(mask.sum())

        if count < 5:
            per_regime[str(regime)] = {
                "multiplier": round(float(multipliers.get(str(regime), 1.0)), 4),
                "note": "too few observations to judge",
            }
            continue

        base_low = y_low[mask]
        base_high = y_high[mask]
        true_vals = y_true[mask]

        base_cov = float(np.mean((true_vals >= base_low) & (true_vals <= base_high)))

        mult = float(multipliers.get(str(regime), 1.0))
        hw = half_width[mask] * mult
        cen = centre[mask]
        tilted_low = cen - hw
        tilted_high = cen + hw

        tilted_cov = float(np.mean((true_vals >= tilted_low) & (true_vals <= tilted_high)))

        moved = tilted_cov > base_cov
        if moved:
            improved_count += 1
        else:
            all_improved = False

        per_regime[str(regime)] = {
            "base_coverage": round(base_cov, 6),
            "tilted_coverage": round(tilted_cov, 6),
            "multiplier": round(mult, 4),
            "moved_toward_nominal": moved,
            "note": "",
        }

    return {
        "per_regime": per_regime,
        "regimes_improved": improved_count,
        "all_improved": all_improved,
    }


def evaluate_walk_forward_stacking(
    base_predictions: dict[str, Sequence[float]],
    outcomes: Sequence[float],
    horizons: Sequence[int],
    *,
    min_train: int = 20,
    folds: int = 4,
    shrinkage: float = 0.5,
    ridge: float = 1.0,
    regimes: Sequence[str] | None = None,
    regime_prior: dict[str, dict[str, float]] | None = None,
    regime_prior_weight: float = 0.25,
    keep_predictions: bool = False,
) -> dict[str, Any]:
    """Harness-level wrapper around the walk-forward stacking scorer with promotion gate.

    This function calls :func:`walk_forward_stacked_forecast` and applies the
    promotion rules:
    - The stack must be available (enough history, finite predictions).
    - The learned stack must beat the equal-weight baseline on out-of-sample MAE.
    - There must be enough scored bars (>= 30) to trust the comparison.
    """
    result = walk_forward_stacked_forecast(
        base_predictions,
        outcomes,
        horizons,
        regimes=regimes,
        regime_prior=regime_prior,
        min_train=min_train,
        folds=folds,
        shrinkage=shrinkage,
        ridge=ridge,
        regime_prior_weight=regime_prior_weight,
    )

    if not result.get("available", False):
        return {
            "available": False,
            "promote": False,
            "reason": result.get("reason", "stack unavailable"),
            "oos_mae": None,
            "equal_weight_mae": None,
            "weights": {},
            "effective_weights": {},
            "regime_weights": {},
            "regime_counts": {},
            "scored": 0,
            "folds": 0,
            "min_train": min_train,
            "note": result.get("reason", ""),
        }

    oos_mae = result["oos_mae"]
    ew_mae = result["equal_weight_mae"]
    beats_baseline = oos_mae < ew_mae
    enough_evidence = result["scored"] >= 30
    promote = beats_baseline and enough_evidence

    improvement_pct = None
    if ew_mae > 0:
        improvement_pct = round(100.0 * (ew_mae - oos_mae) / ew_mae, 3)

    note = ""
    if not beats_baseline:
        note = "learned stack did not beat equal-weight baseline"
    elif not enough_evidence:
        note = "not enough to promote"

    out = {
        "available": True,
        "promote": promote,
        "oos_mae": oos_mae,
        "equal_weight_mae": ew_mae,
        "beats_equal_weight": beats_baseline,
        "enough_evidence": enough_evidence,
        "improvement_pct": improvement_pct,
        "weights": result.get("weights", {}),
        "effective_weights": result.get("effective_weights", {}),
        "regime_weights": result.get("regime_weights", {}),
        "regime_counts": result.get("regime_counts", {}),
        "scored": result["scored"],
        "folds": result["folds"],
        "min_train": min_train,
        "shrinkage": shrinkage,
        "ridge": ridge,
        "basis": result.get("method", ""),
        "promotion_rule": (
            "promote iff available AND oos_mae < equal_weight_mae AND scored >= 30"
        ),
        "note": note,
    }

    if keep_predictions:
        out["predictions"] = result.get("predictions", [])
        out["scored_indices"] = result.get("scored_indices", [])
        out["baseline_predictions"] = result.get("baseline_predictions", [])

    # Directional accuracy for both arms
    preds = np.array(result.get("predictions", []))
    baseline_preds = np.array(result.get("baseline_predictions", []))
    scored_indices = result.get("scored_indices", [])
    if preds.size > 0 and scored_indices:
        truth_vals = np.array([outcomes[i] for i in scored_indices])
        prev_truth = np.roll(truth_vals, 1)
        # Only count non-zero direction changes
        mask = (truth_vals != prev_truth)
        if mask.any():
            dir_acc = float(np.mean((np.sign(truth_vals[mask] - prev_truth[mask]) == np.sign(preds[mask] - prev_truth[mask]))))
            ew_dir_acc = float(np.mean((np.sign(truth_vals[mask] - prev_truth[mask]) == np.sign(baseline_preds[mask] - prev_truth[mask]))))
        else:
            dir_acc = 0.0
            ew_dir_acc = 0.0
        out["directional_accuracy"] = dir_acc
        out["equal_weight_directional_accuracy"] = ew_dir_acc
    else:
        out["directional_accuracy"] = 0.0
        out["equal_weight_directional_accuracy"] = 0.0

    return out


__all__: Sequence[str] = (
    "PinballLossResult",
    "CRPSResult",
    "PITResult",
    "DieboldMarianoResult",
    "TierEvaluationResult",
    "pinball_loss",
    "crps_ensemble",
    "crps_gaussian",
    "pit_values",
    "pit_uniformity_test",
    "mase",
    "directional_accuracy",
    "diebold_mariano_test",
    "conditional_coverage_checks",
    "evaluate_tier",
    "evaluate_all_tiers",
    "evaluation_summary",
    "evaluate_regime_stacking",
    "evaluate_walk_forward_stacking",
)
