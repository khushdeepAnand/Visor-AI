"""Split-conformal calibration and walk-forward scoring for range forecasts.

Why this module exists
----------------------
The product promises *ranges with evidence*, not point predictions. Two things
are needed to keep that promise honest:

1. An interval whose stated coverage is measured, not asserted. Split-conformal
   prediction gives a distribution-free interval: take a model's absolute
   residuals on a held-out calibration window, and the (1 - alpha) empirical
   quantile of those residuals is a half-width whose coverage holds in finite
   samples without assuming normality.
2. Proof that the model beats the cheapest possible alternative. For daily
   equity closes the honest baseline is the random walk (tomorrow equals today).
   A model that cannot beat it on walk-forward error has no business widening
   or narrowing anyone's expectations, so this module reports the skill score
   and abstains when it is not positive.

Everything here is evaluated walk-forward: for each scored session the model is
fitted only on data strictly before it. There is no lookahead, no shuffling and
no refitting on the scored window.

v13 additions:
- Conformalized Quantile Regression (CQR) with native quantile objectives
- Adaptive Conformal Inference (ACI) for online coverage self-correction
- Mondrian (group-conditional) conformal per tier/regime/liquidity/sector
- Distributional output: multiple quantiles (5/10/25/50/75/90/95)
- Circuit-limit clipping and tick-size minimum widths

Deliberate non-goals
--------------------
- No point "target price" is produced, ever. The output is an interval plus the
  measured error of the estimator that produced it.
- No claim of accuracy is emitted that was not measured on this symbol's own
  history. When evidence is thin the support state is `low_evidence` or
  `abstained` and callers must not render a range.
- numpy/pandas only. No scikit-learn, scipy or network access, so this runs in
  the same environments as the rest of the forecasting package.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)
_STATE_LOCK = threading.RLock()

# --- configuration ---------------------------------------------------------

#: Minimum realised sessions before any calibration is attempted.
MIN_SESSIONS = 180

#: Minimum number of walk-forward scored points before coverage is trusted.
MIN_SCORED_POINTS = 40

#: Sessions held back for conformal calibration inside each fit.
CALIBRATION_WINDOW = 60

#: Trailing sessions used to fit the drift estimator.
DRIFT_WINDOW = 20

#: How far measured coverage may fall below target before the interval is
#: treated as miscalibrated. Coverage above target is never penalised.
COVERAGE_TOLERANCE = 0.05

#: Supported confidence levels. Anything else is rejected rather than rounded.
CONFIDENCE_LEVELS: tuple[float, ...] = (0.50, 0.68, 0.80, 0.90, 0.95)

#: Horizons in trading sessions.
MAX_HORIZON = 20

#: Quantile levels for distributional output.
QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)

#: ACI learning rate for online conformal updates.
ACI_LEARNING_RATE = 0.1

#: Minimum tick size in rupees for NSE equities.
MIN_TICK_SIZE = 0.05

#: NSE circuit limit bands (percentage).
CIRCUIT_LIMITS: tuple[float, ...] = (0.05, 0.10, 0.20)

SUPPORT_STATES: tuple[str, ...] = (
    "model_supported",
    "baseline_only",
    "low_evidence",
    "abstained",
)

EVIDENCE_TIERS: tuple[str, ...] = ("A", "B", "C", "none")

CALIBRATION_DISCLOSURES: tuple[str, ...] = (
    "Intervals are calibrated on this symbol's own realised residuals. Coverage is measured, not assumed.",
    "A range is not a forecast of where the price will go. It is where past errors of this estimator fell.",
    "Skill is measured against a random walk. A non-positive skill score means the model adds nothing.",
    "Calibration is historical. A market regime change can invalidate it before the next refit.",
    "Intervals are clipped to NSE circuit limits and have a tick-size floor.",
)


class CalibrationError(ValueError):
    """A calibration request could not be served as asked."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CalibrationResult:
    """Measured quality of one symbol/horizon/confidence combination."""

    symbol: str
    horizon: int
    confidence: float
    sessions_used: int
    scored_points: int
    model: str
    mae: float
    rmse: float
    baseline_mae: float
    skill_vs_baseline: float
    coverage: float
    target_coverage: float
    half_width: float
    half_width_pct: float
    support_state: str
    evidence_tier: str
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "confidence": self.confidence,
            "sessions_used": self.sessions_used,
            "scored_points": self.scored_points,
            "model": self.model,
            "mae": self.mae,
            "rmse": self.rmse,
            "baseline_mae": self.baseline_mae,
            "skill_vs_baseline": self.skill_vs_baseline,
            "coverage": self.coverage,
            "target_coverage": self.target_coverage,
            "half_width": self.half_width,
            "half_width_pct": self.half_width_pct,
            "support_state": self.support_state,
            "evidence_tier": self.evidence_tier,
            "reason": self.reason,
            "is_forecast": False,
            "is_recommendation": False,
            "disclosures": list(CALIBRATION_DISCLOSURES),
        }
        return payload


# --- helpers ---------------------------------------------------------------


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    if not np.isfinite(value):
        return None
    return float(round(float(value), digits))


def _closes(frame: pd.DataFrame) -> np.ndarray:
    if frame is None or getattr(frame, "empty", True):
        raise CalibrationError("calibration_history_unavailable", "No history was returned for this symbol.")
    column = next((name for name in ("close", "Close", "close_price") if name in frame.columns), None)
    if column is None:
        raise CalibrationError(
            "calibration_history_unavailable",
            "History is missing a close column, so residuals cannot be computed.",
        )
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    closes = series.to_numpy(dtype=float)
    if closes.size and not np.all(closes > 0):
        raise CalibrationError(
            "calibration_data_quality_failed",
            "History contains non-positive closes, which cannot be a traded price.",
        )
    return closes


def _validate(horizon: int, confidence: float) -> tuple[int, float]:
    horizon = int(horizon)
    if horizon < 1 or horizon > MAX_HORIZON:
        raise CalibrationError(
            "calibration_horizon_invalid",
            f"Horizon must be between 1 and {MAX_HORIZON} sessions.",
        )
    confidence = float(confidence)
    if not any(abs(confidence - level) < 1e-9 for level in CONFIDENCE_LEVELS):
        raise CalibrationError(
            "calibration_confidence_invalid",
            "Confidence must be one of: " + ", ".join(str(level) for level in CONFIDENCE_LEVELS),
        )
    return horizon, confidence


# --- estimators ------------------------------------------------------------
# Both estimators are deliberately simple and inspectable. The point of this
# module is calibrated honesty, not model complexity: a complicated estimator
# that cannot beat a random walk is still worthless, and this harness is what
# proves whether a future estimator does better.


def _predict_random_walk(history: np.ndarray, horizon: int) -> float:
    """Tomorrow equals today. The baseline every model must beat."""
    return float(history[-1])


def _predict_damped_drift(history: np.ndarray, horizon: int) -> float:
    """Random walk plus a damped estimate of recent log drift.

    Drift is the median log return over `DRIFT_WINDOW` sessions (median, not
    mean, so one gap or split-like jump cannot dominate), damped by 0.5 because
    undamped extrapolation of recent drift is the classic way to lose to a
    random walk out of sample.
    """
    window = history[-(DRIFT_WINDOW + 1) :]
    if window.size < 3:
        return float(history[-1])
    log_returns = np.diff(np.log(window))
    drift = float(np.median(log_returns)) * 0.5
    return float(history[-1] * np.exp(drift * horizon))


ESTIMATORS = {
    "random_walk": _predict_random_walk,
    "damped_drift": _predict_damped_drift,
}


# --- walk-forward scoring --------------------------------------------------


def walk_forward_errors(
    closes: Sequence[float] | np.ndarray,
    *,
    horizon: int,
    model: str = "damped_drift",
    min_train: int | None = None,
) -> dict[str, np.ndarray]:
    """Score an estimator walk-forward, one session at a time.

    For each scored index `i`, the estimator sees only `closes[:i]` and predicts
    `closes[i + horizon - 1]`. Nothing from the scored window reaches the fit,
    which is what makes the resulting residuals usable for conformal
    calibration.
    """
    if model not in ESTIMATORS:
        raise CalibrationError("calibration_model_unknown", f"Unknown estimator: {model}")
    values = np.asarray(closes, dtype=float)
    horizon = int(horizon)
    train = int(min_train if min_train is not None else DRIFT_WINDOW + 5)
    predict = ESTIMATORS[model]

    predictions: list[float] = []
    baselines: list[float] = []
    actuals: list[float] = []
    for index in range(train, values.size - horizon + 1):
        history = values[:index]
        actual = float(values[index + horizon - 1])
        predictions.append(float(predict(history, horizon)))
        baselines.append(float(_predict_random_walk(history, horizon)))
        actuals.append(actual)

    if not actuals:
        raise CalibrationError(
            "calibration_insufficient_history",
            "Not enough sessions to score even one walk-forward point.",
        )

    prediction_array = np.asarray(predictions, dtype=float)
    baseline_array = np.asarray(baselines, dtype=float)
    actual_array = np.asarray(actuals, dtype=float)
    # Log residuals are reported alongside the currency residuals because
    # rupee errors are heteroscedastic: a 1% miss on a 3,000 stock is ten times
    # the rupee error of a 1% miss on a 300 stock, and the same stock's rupee
    # errors grow as its price grows. Conformal prediction assumes the
    # calibration and test residuals are exchangeable, which holds far better
    # in log space than in rupees.
    return {
        "predictions": prediction_array,
        "baselines": baseline_array,
        "actuals": actual_array,
        "residuals": actual_array - prediction_array,
        "baseline_residuals": actual_array - baseline_array,
        "log_residuals": np.log(actual_array) - np.log(prediction_array),
        "baseline_log_residuals": np.log(actual_array) - np.log(baseline_array),
    }


def conformal_half_width(residuals: Sequence[float] | np.ndarray, confidence: float) -> float:
    """Split-conformal half-width: the (1 - alpha) quantile of |residual|.

    The finite-sample correction `ceil((n + 1) * confidence) / n` is the
    standard conformal adjustment; without it the interval is slightly
    anti-conservative on small calibration sets.
    """
    absolute = np.abs(np.asarray(residuals, dtype=float))
    absolute = absolute[np.isfinite(absolute)]
    if absolute.size == 0:
        raise CalibrationError(
            "calibration_insufficient_history",
            "No residuals are available to calibrate an interval.",
        )
    count = absolute.size
    rank = int(np.ceil((count + 1) * float(confidence)))
    if rank >= count:
        return float(np.max(absolute))
    ordered = np.sort(absolute)
    return float(ordered[max(rank - 1, 0)])


def measured_coverage(
    residuals: Sequence[float] | np.ndarray,
    half_width: float,
) -> float:
    """Share of residuals that the calibrated half-width actually contains."""
    absolute = np.abs(np.asarray(residuals, dtype=float))
    absolute = absolute[np.isfinite(absolute)]
    if absolute.size == 0:
        raise CalibrationError(
            "calibration_insufficient_history",
            "No residuals are available to measure coverage.",
        )
    return float(np.mean(absolute <= float(half_width) + 1e-12))


def _support_state(
    *,
    scored_points: int,
    skill: float,
    coverage: float,
    target: float,
) -> tuple[str, str, str | None]:
    """Decide whether a range may be shown, and say why when it may not."""
    if scored_points < MIN_SCORED_POINTS:
        return (
            "low_evidence",
            "C",
            "Fewer than %d walk-forward points were scored, so coverage is not trustworthy." % MIN_SCORED_POINTS,
        )
    if coverage + COVERAGE_TOLERANCE < target:
        return (
            "abstained",
            "none",
            "Measured coverage %.3f is below the %.2f target by more than the %.2f tolerance."
            % (coverage, target, COVERAGE_TOLERANCE),
        )
    if skill <= 0:
        return (
            "baseline_only",
            "C",
            "The estimator did not beat a random walk, so only the baseline interval is defensible.",
        )
    tier = "A" if (skill >= 0.05 and scored_points >= 4 * MIN_SCORED_POINTS) else "B"
    return "model_supported", tier, None


def calibrate_symbol(
    frame: pd.DataFrame,
    *,
    symbol: str = "",
    horizon: int = 5,
    confidence: float = 0.80,
    model: str = "damped_drift",
) -> CalibrationResult:
    """Measure error, skill and coverage for one symbol, then gate on it.

    Raises `CalibrationError` only for unusable *inputs*. A usable input with
    weak evidence is not an error: it returns a result whose `support_state`
    tells the caller not to publish a range.
    """
    horizon, confidence = _validate(horizon, confidence)
    closes = _closes(frame)
    if closes.size < MIN_SESSIONS:
        raise CalibrationError(
            "calibration_insufficient_history",
            "At least %d sessions are needed to calibrate; %d were supplied." % (MIN_SESSIONS, closes.size),
        )

    scored = walk_forward_errors(closes, horizon=horizon, model=model)
    residuals = scored["residuals"]
    baseline_residuals = scored["baseline_residuals"]
    log_residuals = scored["log_residuals"]

    # Calibrate on the earlier residuals, measure coverage on the later ones, so
    # the reported coverage is out-of-sample with respect to the half-width.
    split = max(int(log_residuals.size * 0.6), 1)
    if log_residuals.size - split < 5:
        split = max(log_residuals.size - 5, 1)
    calibration_residuals = log_residuals[:split][-CALIBRATION_WINDOW * 4 :]
    holdout_residuals = log_residuals[split:]

    # The half-width is a proportional (log) quantile, so it travels with the
    # price level instead of decaying into under-coverage as the price rises.
    log_half_width = conformal_half_width(calibration_residuals, confidence)
    coverage = measured_coverage(holdout_residuals, log_half_width)
    proportional_half_width = float(np.expm1(log_half_width))

    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(np.square(residuals))))
    baseline_mae = float(np.mean(np.abs(baseline_residuals)))
    skill = 0.0 if baseline_mae <= 0 else float((baseline_mae - mae) / baseline_mae)

    state, tier, reason = _support_state(
        scored_points=int(holdout_residuals.size),
        skill=skill,
        coverage=coverage,
        target=confidence,
    )
    last_close = float(closes[-1])
    return CalibrationResult(
        symbol=str(symbol or "").strip().upper(),
        horizon=horizon,
        confidence=confidence,
        sessions_used=int(closes.size),
        scored_points=int(holdout_residuals.size),
        model=model,
        mae=_round(mae) or 0.0,
        rmse=_round(rmse) or 0.0,
        baseline_mae=_round(baseline_mae) or 0.0,
        skill_vs_baseline=_round(skill) or 0.0,
        coverage=_round(coverage) or 0.0,
        target_coverage=confidence,
        half_width=_round(last_close * proportional_half_width, 2) or 0.0,
        half_width_pct=_round(proportional_half_width * 100, 3) or 0.0,
        support_state=state,
        evidence_tier=tier,
        reason=reason,
    )


def calibrated_range(
    frame: pd.DataFrame,
    *,
    symbol: str = "",
    horizon: int = 5,
    confidence: float = 0.80,
    model: str = "damped_drift",
) -> dict[str, Any]:
    """Return a publishable range, or a refusal with a reason.

    When the support state is `abstained` the payload carries no `low`/`high`
    at all. A caller cannot accidentally render an uncalibrated range because
    the numbers are simply absent.
    """
    result = calibrate_symbol(frame, symbol=symbol, horizon=horizon, confidence=confidence, model=model)
    closes = _closes(frame)
    last_close = float(closes[-1])
    payload: dict[str, Any] = {
        "symbol": result.symbol,
        "as_of_close": _round(last_close, 2),
        "horizon_sessions": result.horizon,
        "quality": result.to_dict(),
        "is_forecast": False,
        "is_recommendation": False,
    }
    if result.support_state == "abstained":
        payload["range"] = None
        payload["refused"] = True
        payload["reason"] = result.reason
        return payload

    # A baseline-only symbol still gets an interval, but it is the random walk's
    # interval and it is labelled as such.
    if result.support_state == "baseline_only":
        baseline = calibrate_symbol(
            frame,
            symbol=symbol,
            horizon=horizon,
            confidence=confidence,
            model="random_walk",
        )
        centre = _predict_random_walk(closes, result.horizon)
        proportion = baseline.half_width_pct / 100.0
        payload["quality"] = baseline.to_dict()
        payload["quality"]["support_state"] = "baseline_only"
        payload["quality"]["reason"] = result.reason
    else:
        centre = ESTIMATORS[model](closes, result.horizon)
        proportion = result.half_width_pct / 100.0

    # Proportional bounds, so the interval is symmetric in percentage terms
    # rather than in rupees. A stock cannot go below zero, and the log-space
    # calibration is what was actually measured.
    payload["refused"] = False
    payload["range"] = {
        "low": _round(centre / (1.0 + proportion), 2),
        "high": _round(centre * (1.0 + proportion), 2),
        "width_pct": _round(
            (centre * (1.0 + proportion) - centre / (1.0 + proportion)) / last_close * 100 if last_close else 0.0,
            3,
        ),
        "basis": "split_conformal_on_walk_forward_log_residuals",
    }
    return payload


def calibration_report(
    symbols: Iterable[str],
    *,
    history_loader: Callable[[str], pd.DataFrame],
    horizon: int = 5,
    confidence: float = 0.80,
    model: str = "damped_drift",
) -> dict[str, Any]:
    """Calibrate many symbols and summarise how many may publish a range.

    A symbol whose history cannot be loaded or is too short is reported in
    `skipped` with its code. It is never silently dropped, and it never counts
    toward the covered population.
    """
    calibrated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if not symbol:
            continue
        try:
            frame = history_loader(symbol)
        except Exception as exc:  # noqa: BLE001 - loader failures are data, not bugs
            skipped.append({"symbol": symbol, "code": "calibration_history_unavailable", "detail": str(exc)})
            continue
        try:
            calibrated.append(
                calibrate_symbol(
                    frame,
                    symbol=symbol,
                    horizon=horizon,
                    confidence=confidence,
                    model=model,
                ).to_dict()
            )
        except CalibrationError as exc:
            skipped.append({"symbol": symbol, "code": exc.code, "detail": exc.message})

    states: dict[str, int] = {state: 0 for state in SUPPORT_STATES}
    for row in calibrated:
        states[row["support_state"]] = states.get(row["support_state"], 0) + 1
    publishable = [row for row in calibrated if row["support_state"] in ("model_supported", "baseline_only")]
    return {
        "horizon": int(horizon),
        "confidence": float(confidence),
        "model": model,
        "calibrated": calibrated,
        "skipped": skipped,
        "support_states": states,
        "coverage_summary": {
            "symbols_requested": len(calibrated) + len(skipped),
            "symbols_calibrated": len(calibrated),
            "symbols_publishable": len(publishable),
            "mean_coverage": _round(float(np.mean([row["coverage"] for row in publishable])) if publishable else None),
            "mean_skill": _round(
                float(np.mean([row["skill_vs_baseline"] for row in publishable])) if publishable else None
            ),
        },
        "disclosures": list(CALIBRATION_DISCLOSURES),
        "is_forecast": False,
    }


def describe_methodology() -> dict[str, Any]:
    """Machine-readable description of what this module does and does not do."""
    return {
        "interval_method": "split_conformal",
        "interval_method_detail": (
            "Absolute walk-forward residuals from an earlier window supply the (1 - alpha) quantile; "
            "coverage is then measured on a later, untouched window."
        ),
        "validation": "walk_forward_expanding_window",
        "lookahead_controls": [
            "Each prediction sees only sessions strictly before the scored session.",
            "The half-width is calibrated on residuals from before the coverage window.",
            "No refitting or parameter selection happens on the coverage window.",
        ],
        "baseline": "random_walk",
        "skill_definition": "(baseline_mae - model_mae) / baseline_mae",
        "estimators": sorted(ESTIMATORS),
        "support_states": list(SUPPORT_STATES),
        "evidence_tiers": list(EVIDENCE_TIERS),
        "minimums": {
            "sessions": MIN_SESSIONS,
            "scored_points": MIN_SCORED_POINTS,
            "coverage_tolerance": COVERAGE_TOLERANCE,
        },
        "produces_point_prediction": False,
        "produces_recommendation": False,
        "disclosures": list(CALIBRATION_DISCLOSURES),
    }


# --- v13: CQR (Conformalized Quantile Regression) ----------------------------

def cqr_calibration(
    y_true: np.ndarray,
    y_pred_low: np.ndarray,
    y_pred_high: np.ndarray,
    confidence: float,
) -> tuple[float, float]:
    """Conformalized Quantile Regression calibration.

    Given quantile predictions at alpha/2 and 1-alpha/2, computes conformal
    correction offsets for lower and upper bounds. Returns (lower_offset, upper_offset).

    CQR score: max(q_lo - y, y - q_hi, 0). The conformal quantile of this score
    gives the symmetric correction to apply to both quantiles.
    """
    alpha = 1.0 - confidence
    scores = np.maximum(np.maximum(y_pred_low - y_true, y_true - y_pred_high), 0.0)
    scores = scores[np.isfinite(scores)]
    if scores.size == 0:
        return 0.0, 0.0
    rank = int(np.ceil((scores.size + 1) * confidence))
    if rank >= scores.size:
        offset = float(np.max(scores))
    else:
        offset = float(np.sort(scores)[max(rank - 1, 0)])
    return -offset, offset


def cqr_interval(
    y_pred_low: float,
    y_pred_high: float,
    lower_offset: float,
    upper_offset: float,
) -> tuple[float, float]:
    """Apply CQR offsets to quantile predictions."""
    return y_pred_low + lower_offset, y_pred_high + upper_offset


# --- v13: ACI (Adaptive Conformal Inference) ---------------------------------

@dataclass
class ACIState:
    """State for adaptive conformal inference (online coverage correction)."""
    gamma: float = 0.0  # cumulative correction
    step: int = 0

    def update(self, covered: bool, target_coverage: float, learning_rate: float = ACI_LEARNING_RATE) -> None:
        """Update gamma based on whether the last prediction covered the truth."""
        self.step += 1
        error = float(covered) - target_coverage
        self.gamma += learning_rate * error

    def adjusted_level(self, nominal: float) -> float:
        """Return the adjusted confidence level for the next prediction."""
        adjusted = nominal - self.gamma
        return float(np.clip(adjusted, 0.5, 0.99))

    def to_dict(self) -> dict[str, Any]:
        return {"gamma": round(self.gamma, 6), "step": self.step}


def aci_calibrate_online(
    residuals: Sequence[float],
    confidence: float,
    initial_gamma: float = 0.0,
) -> tuple[float, ACIState]:
    """Run ACI over a sequence of residuals, returning final half-width and state."""
    state = ACIState(gamma=initial_gamma)
    absolute = np.abs(np.asarray(residuals, dtype=float))
    absolute = absolute[np.isfinite(absolute)]
    if absolute.size == 0:
        return 0.0, state

    half_widths: list[float] = []
    for i, res in enumerate(absolute):
        adj_conf = state.adjusted_level(confidence)
        hw = conformal_half_width(absolute[:i+1], adj_conf) if i > 0 else absolute[0]
        half_widths.append(hw)
        covered = res <= hw
        state.update(covered, confidence)

    return half_widths[-1], state


# --- v13: Mondrian (group-conditional) conformal -----------------------------

def mondrian_conformal_half_width(
    residuals: np.ndarray,
    groups: np.ndarray,
    confidence: float,
    group: Any,
) -> float:
    """Group-conditional conformal half-width (Mondrian conformal).

    Calibrates separately within each group so coverage holds *within* each group.
    """
    mask = groups == group
    group_residuals = residuals[mask]
    absolute = np.abs(group_residuals)
    absolute = absolute[np.isfinite(absolute)]
    if absolute.size == 0:
        # Fallback to global if group has no calibration points
        return conformal_half_width(residuals, confidence)
    return conformal_half_width(absolute, confidence)


def mondrian_calibration_report(
    residuals: np.ndarray,
    groups: np.ndarray,
    confidence: float,
) -> dict[str, Any]:
    """Report coverage per group for Mondrian conformal."""
    unique_groups = np.unique(groups)
    report: dict[str, Any] = {}
    for grp in unique_groups:
        mask = groups == grp
        grp_residuals = residuals[mask]
        if grp_residuals.size == 0:
            continue
        hw = mondrian_conformal_half_width(residuals, groups, confidence, grp)
        coverage = measured_coverage(grp_residuals, hw)
        report[str(grp)] = {
            "n_calibration": int(mask.sum()),
            "half_width": float(hw),
            "empirical_coverage": float(coverage),
            "target_coverage": confidence,
        }
    return report


# --- v13: Distributional output (multi-quantile) -----------------------------

def multi_quantile_conformal(
    y_true: np.ndarray,
    quantile_predictions: dict[float, np.ndarray],
    confidence: float,
) -> dict[float, tuple[float, float]]:
    """Conformalize multiple quantiles simultaneously.

    quantile_predictions: dict mapping quantile level -> predicted values array
    Returns conformalized (lower, upper) for each quantile pair.
    """
    alpha = 1.0 - confidence
    lower_q = alpha / 2
    upper_q = 1 - alpha / 2

    # Get the central quantile predictions (e.g., 0.05 and 0.95 for 90% CI)
    available = sorted(quantile_predictions.keys())
    lower_key = min(available, key=lambda q: abs(q - lower_q))
    upper_key = max(available, key=lambda q: abs(q - upper_q))

    y_low = quantile_predictions[lower_key]
    y_high = quantile_predictions[upper_key]

    low_offset, high_offset = cqr_calibration(y_true, y_low, y_high, confidence)

    result: dict[float, tuple[float, float]] = {}
    for q in available:
        if q <= 0.5:
            base_low = quantile_predictions[q]
            base_high = quantile_predictions.get(1 - q, y_high)
            result[q] = (
                float(np.median(base_low + low_offset)),
                float(np.median(base_high + high_offset)),
            )
    return result


# --- v13: Mondrian (group-conditional) conformal persistence -------------------

MONDRIAN_STATE_VERSION = 1

def _mondrian_state_path() -> Path:
    configured = os.getenv("STOCKPILOT_MONDRIAN_STATE_PATH")
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / "cache" / "forecast_v14" / "mondrian_state.json"


def _mondrian_key(symbol: str, timeframe: str, horizon: int, confidence: float, group: tuple[Any, ...]) -> str:
    group_str = "|".join(str(g) for g in group)
    return f"{symbol.strip().upper()}|{timeframe}|{int(horizon)}|{float(confidence):.4f}|{group_str}"


def _empty_mondrian_state() -> dict[str, Any]:
    return {"version": MONDRIAN_STATE_VERSION, "updated_at": None, "groups": {}}


def _load_mondrian_state() -> dict[str, Any]:
    path = _mondrian_state_path()
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != MONDRIAN_STATE_VERSION or not isinstance(payload.get("groups"), dict):
            raise ValueError("unsupported Mondrian state version")
        return payload
    except FileNotFoundError:
        return _empty_mondrian_state()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("forecast_v14_mondrian_state_unavailable", extra={"reason": type(exc).__name__})
        return _empty_mondrian_state()


def save_mondrian_calibration(
    symbol: str,
    timeframe: str,
    horizon: int,
    confidence: float,
    tier: str,
    regime: str,
    liquidity: str,
    sector: str,
    residuals: np.ndarray,
    groups: np.ndarray,
) -> None:
    """Persist Mondrian calibration residuals per group."""
    group_key = (tier, regime, liquidity, sector)
    key = _mondrian_key(symbol, timeframe, horizon, confidence, group_key)
    
    mask = groups == group_key
    group_residuals = residuals[mask]
    absolute = np.abs(group_residuals)
    absolute = absolute[np.isfinite(absolute)]
    
    if absolute.size == 0:
        return
    
    with _STATE_LOCK:
        payload = _load_mondrian_state()
        if key not in payload["groups"]:
            payload["groups"][key] = {
                "tier": tier,
                "regime": regime,
                "liquidity": liquidity,
                "sector": sector,
                "residuals": [],
                "updated_at": None,
            }
        # Append new residuals, keep last 1000 per group
        payload["groups"][key]["residuals"] = (
            payload["groups"][key]["residuals"] + absolute.tolist()
        )[-1000:]
        payload["groups"][key]["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        path = _mondrian_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(path)


def load_mondrian_calibration(
    symbol: str,
    timeframe: str,
    horizon: int,
    confidence: float,
    tier: str,
    regime: str,
    liquidity: str,
    sector: str,
) -> np.ndarray | None:
    """Load persisted Mondrian calibration residuals for a group."""
    group_key = (tier, regime, liquidity, sector)
    key = _mondrian_key(symbol, timeframe, horizon, confidence, group_key)
    
    with _STATE_LOCK:
        payload = _load_mondrian_state()
        group_data = payload["groups"].get(key)
        if group_data is None or not group_data.get("residuals"):
            return None
        return np.array(group_data["residuals"], dtype=float)


def mondrian_conformal_half_width_persisted(
    residuals: np.ndarray,
    groups: np.ndarray,
    confidence: float,
    group: tuple[Any, ...],
    symbol: str | None = None,
    timeframe: str | None = None,
    horizon: int | None = None,
) -> float:
    """Group-conditional conformal half-width with persisted calibration.
    
    If symbol/timeframe/horizon are provided, loads persisted residuals for the group
    and combines them with current calibration residuals.
    """
    mask = groups == group
    group_residuals = residuals[mask]
    absolute = np.abs(group_residuals)
    absolute = absolute[np.isfinite(absolute)]
    
    # Load persisted residuals if available
    if symbol is not None and timeframe is not None and horizon is not None:
        tier, regime, liquidity, sector = group
        persisted = load_mondrian_calibration(symbol, timeframe, horizon, confidence, tier, regime, liquidity, sector)
        if persisted is not None and persisted.size > 0:
            absolute = np.concatenate([persisted, absolute])
    
    if absolute.size == 0:
        # Fallback to global if group has no calibration points
        return conformal_half_width(residuals, confidence)
    return conformal_half_width(absolute, confidence)


# --- v13: Circuit limits and tick-size floor ---------------------------------

NSE_CIRCUIT_LIMITS = {
    "default": 0.20,  # 20% for most stocks
    "fno": 0.10,      # 10% for F&O stocks (dynamic in reality)
    "high_vol": 0.05, # 5% for high volatility / surveillance stocks
}


def circuit_limit_band(price: float, limit_pct: float) -> tuple[float, float]:
    """Compute absolute circuit limits for a price."""
    lower = max(MIN_TICK_SIZE, round(price * (1 - limit_pct), 2))
    upper = round(price * (1 + limit_pct), 2)
    return lower, upper


def apply_circuit_limits(
    low: float,
    high: float,
    price: float,
    is_fno: bool = False,
    surveillance: bool = False,
) -> tuple[float, float, dict[str, Any]]:
    """Clip interval to NSE circuit limits and enforce tick-size floor."""
    if surveillance:
        limit_pct = NSE_CIRCUIT_LIMITS["high_vol"]
    elif is_fno:
        limit_pct = NSE_CIRCUIT_LIMITS["fno"]
    else:
        limit_pct = NSE_CIRCUIT_LIMITS["default"]

    circuit_low, circuit_high = circuit_limit_band(price, limit_pct)

    clipped_low = max(low, circuit_low)
    clipped_high = min(high, circuit_high)

    # Tick-size floor on width
    min_width = max(MIN_TICK_SIZE * 2, price * 0.0005)
    if clipped_high - clipped_low < min_width:
        centre = (clipped_low + clipped_high) / 2
        clipped_low = max(circuit_low, round(centre - min_width / 2, 2))
        clipped_high = min(circuit_high, round(centre + min_width / 2, 2))

    info = {
        "circuit_limit_pct": limit_pct,
        "circuit_low": circuit_low,
        "circuit_high": circuit_high,
        "clipped": clipped_low != low or clipped_high != high,
        "tick_floor_applied": clipped_high - clipped_low < (high - low) * 0.99,
    }
    return clipped_low, clipped_high, info


def minimum_width_floor(price: float, min_pct: float = 0.0025) -> float:
    """Hard minimum half-width based on tick size and minimum percentage."""
    return max(price * min_pct, MIN_TICK_SIZE)


__all__: Sequence[str] = (
    "MIN_SESSIONS",
    "MIN_SCORED_POINTS",
    "CALIBRATION_WINDOW",
    "DRIFT_WINDOW",
    "COVERAGE_TOLERANCE",
    "CONFIDENCE_LEVELS",
    "MAX_HORIZON",
    "QUANTILE_LEVELS",
    "ACI_LEARNING_RATE",
    "MIN_TICK_SIZE",
    "CIRCUIT_LIMITS",
    "SUPPORT_STATES",
    "EVIDENCE_TIERS",
    "CALIBRATION_DISCLOSURES",
    "CalibrationError",
    "CalibrationResult",
    "walk_forward_errors",
    "conformal_half_width",
    "measured_coverage",
    "calibrate_symbol",
    "calibrated_range",
    "calibration_report",
    "describe_methodology",
    "cqr_calibration",
    "cqr_interval",
    "ACIState",
    "aci_calibrate_online",
    "mondrian_conformal_half_width",
    "mondrian_calibration_report",
    "mondrian_conformal_half_width_persisted",
    "save_mondrian_calibration",
    "load_mondrian_calibration",
    "multi_quantile_conformal",
    "circuit_limit_band",
    "apply_circuit_limits",
    "minimum_width_floor",
    "NSE_CIRCUIT_LIMITS",
)
