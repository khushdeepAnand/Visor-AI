"""Range-first forecasting for StockPilot AI v14.

This module is the production forecast surface.  It combines:
* a leakage-safe stacked ensemble over reusable base regressors,
* direct multi-horizon forecasting (1/3/5/10/20 sessions ahead) where every
  horizon is an independent leakage-safe pipeline with its own untouched
  test fold,
* data-tier router (T0-T4) selecting model stacks per evidence level,
* volatility-first models: HAR-RV, GARCH-family, range-based estimators,
* Conformalized Quantile Regression (CQR) with native quantile objectives,
* Adaptive Conformal Inference (ACI) for online coverage self-correction,
* Mondrian (group-conditional) conformal per tier/regime/liquidity/sector,
* regime detection and routing with regime-specific calibration,
* pooled panel model with stock embeddings, hierarchical shrinkage, peer transfer,
* market/sector/derivatives/flow/event features (admitted only if they improve OOS interval score),
* distributional output: multiple quantiles (5/10/25/50/75/90/95) and fan charts,
* circuit-limit clipping and tick-size minimum widths,
* interval coverage + Winkler scoring, CRPS, pinball loss, MASE, PIT calibration,
* Diebold-Mariano tests for statistical significance,
* a lightweight rolling drift check.

The published interval is anchored to recent realized volatility and conformal
residuals. It is never narrowed to look useful: excessively wide ranges are
published with an explicit low-utility status.

No public API consumer receives a naked point forecast: the canonical response
is ``{low, median, high, confidence_level}``; longer horizons are published
under ``multi_horizon`` with the same contract per horizon. Every response
includes tier, evidence grade, and reason for full transparency.
"""
import logging
import math
import os
import threading
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
from scipy import stats as _stats
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error

import prediction as legacy_models
from indicators import add_indicators
from services.market_calendar import CLOSE as MARKET_CLOSE, IST, OPEN as MARKET_OPEN, resolve_year_calendar

# v13 imports
from forecasting.data_tier_router import DataTier, TierAssignment, assign_tier, tier_to_dict
from forecasting.volatility_models import (
    composite_volatility_forecast,
    realized_variance_intraday,
    range_based_estimators,
    volatility_scorecard,
)
from forecasting.regime_detection import MarketRegime, RegimeState, detect_regime, regime_router, regime_to_dict
from forecasting.conformal_calibration import (
    cqr_calibration,
    cqr_interval,
    aci_calibrate_online,
    ACIState,
    mondrian_conformal_half_width,
    multi_quantile_conformal,
    apply_circuit_limits,
    minimum_width_floor,
    save_mondrian_calibration,
    load_mondrian_calibration,
    mondrian_conformal_half_width_persisted,
)
from forecasting.pooled_cross_section import (
    build_ipo_features,
    find_ipo_peers,
    peer_transfer_prior,
    blended_forecast_with_peer_prior,
    hierarchical_shrinkage_weight,
)
from forecasting.v14_integration import collect_context, cqr_canary_status, read_aci_state

LOGGER = logging.getLogger(__name__)

TRAINING_WINDOWS = {"1w", "1mo", "3mo", "1y", "5y"}
WINDOW_TIMEFRAME_DEFAULTS = {
    "1w": "1m",
    "1mo": "5m",
    "3mo": "15m",
    "1y": "1D",
    "5y": "1D",
}

#: Session ladder every forecast is trained and calibrated for.  Each horizon
#: is a separate direct forecaster with its own chronological folds, so a
#: longer-horizon range is never the 1-session range rescaled by a guess.
HORIZON_SESSIONS: tuple[int, ...] = (1, 3, 5, 10)
#: Horizons requested by product surfaces unless a caller narrows the ladder.
DEFAULT_HORIZONS: tuple[int, ...] = HORIZON_SESSIONS

#: Full-pipeline supervised-row threshold.  Below this the forecast degrades to
#: the low-data ladder (fewer features, shorter warm-up) instead of failing.
MIN_SUPERVISED_FULL = 120
#: Absolute floor of usable supervised rows below which no forecast is produced.
MIN_SUPERVISED_ABSOLUTE = 30

#: Numerical floor only. There is deliberately no maximum interval-width cap.
MIN_RANGE_HALF_PCT = 0.0025
#: A wide interval remains published, but is labelled low utility at this point.
LOW_UTILITY_RANGE_PCT = 0.20
VOL_TAIL_BARS = 90

#: Published-width widening applied to low-data runs so a thin history cannot
#: masquerade as precision. Applied to the live width and replayed uniformly at
#: every test origin, so reported calibration stays honest.
LOW_DATA_WIDTH_FACTOR = 1.25

#: Reporting-only import-time snapshot of the CQR rollout fraction (0..1).
#: The live decision is resolved per request by
#: ``forecasting.v14_integration.canary_percentage`` so an operator can widen
#: or roll back the canary without a redeploy.
CQR_CANARY_PCT = float(os.getenv("STOCKPILOT_CQR_CANARY_PCT", "0.1"))

#: Minimum number of forecasts required in each arm before statistical comparison
CQR_CANARY_MIN_PER_ARM = 30

# --- Enhancement metrics tracking ---
_enhancement_metrics = {
    "total_calls": 0,
    "tier_router_failures": 0,
    "volatility_failures": 0,
    "regime_failures": 0,
    "cqr_failures": 0,
    "aci_failures": 0,
    "mondrian_failures": 0,
    "ipo_peer_failures": 0,
    "circuit_clip_failures": 0,
    "fan_chart_failures": 0,
    "width_floor_failures": 0,
    "partial_degradations": 0,
    "full_degradations": 0,
}
_enhancement_metrics_lock = threading.Lock()


def _increment_metric(key: str) -> None:
    with _enhancement_metrics_lock:
        _enhancement_metrics[key] = _enhancement_metrics.get(key, 0) + 1


def get_enhancement_metrics() -> dict[str, int]:
    """Get current enhancement metrics for monitoring."""
    with _enhancement_metrics_lock:
        return dict(_enhancement_metrics)


def reset_enhancement_metrics() -> None:
    """Reset enhancement metrics (for testing)."""
    with _enhancement_metrics_lock:
        for k in _enhancement_metrics:
            _enhancement_metrics[k] = 0

#: Confidence-score component weights (documented in docs/prediction-engine.md).
#: The total is a 0..100 sum of measured evidence, calibration agreement,
#: measured skill over naive persistence, model agreement, and data quality.
ASSESSMENT_CONFIDENCE_WEIGHTS = {
    "evidence": 40.0,
    "calibration": 25.0,
    "skill": 15.0,
    "agreement": 10.0,
    "data_quality": 10.0,
}

#: Model-agreement word thresholds over the dispersion score in [0, 1].
ASSESSMENT_AGREEMENT_HIGH = 0.75
ASSESSMENT_AGREEMENT_MODERATE = 0.45

#: Statuses under which the derived statistics (probability, expected return,
#: expected volatility, agreement) may be published. Blocked statuses release
#: context only (regime, data quality, confidence, explanation).
ASSESSMENT_PUBLISHABLE = frozenset({"model_supported", "baseline_only", "low_evidence", "available"})


@dataclass(slots=True)
class IntervalMetrics:
    coverage: float
    nominal_coverage: float
    winkler_score: float
    mae: float
    rmse: float
    samples: int
    mae_pct: float
    median_absolute_error: float
    direction_balanced_accuracy: float
    average_width: float
    average_width_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "empirical_coverage": round(self.coverage, 4),
            "nominal_coverage": round(self.nominal_coverage, 4),
            "winkler_score": round(self.winkler_score, 4),
            "mae": round(self.mae, 4),
            "rmse": round(self.rmse, 4),
            "samples": self.samples,
            "mae_pct": round(self.mae_pct, 4),
            "median_absolute_error": round(self.median_absolute_error, 4),
            "direction_balanced_accuracy": round(self.direction_balanced_accuracy, 4),
            "average_width": round(self.average_width, 4),
            "average_width_pct": round(self.average_width_pct, 4),
        }


@dataclass(frozen=True, slots=True)
class DataSufficiencyReport:
    """Observable evidence tier based only on usable rows and test samples."""

    raw_rows: int
    cleaned_rows: int
    supervised_rows: int
    validation_samples: int
    evidence_grade: str
    summary: str
    unique_sessions: int = 0
    missingness_ratio: float = 0.0
    zero_volume_ratio: float = 0.0
    duplicate_rows: int = 0
    corporate_action_status: str = "not_assessed"
    liquidity_proxy: str = "unknown"
    supported_horizons: tuple[str, ...] = ()

    @classmethod
    def from_counts(
        cls,
        *,
        raw_rows: int,
        cleaned_rows: int,
        supervised_rows: int,
        validation_samples: int,
        unique_sessions: int = 0,
        missingness_ratio: float = 0.0,
        zero_volume_ratio: float = 0.0,
        duplicate_rows: int = 0,
        corporate_action_status: str = "not_assessed",
        liquidity_proxy: str = "unknown",
    ) -> "DataSufficiencyReport":
        if supervised_rows >= 240 and validation_samples >= 36:
            grade = "A"
            summary = "Substantial history and a substantial unseen test sample are available."
        elif supervised_rows >= 120 and validation_samples >= 18:
            grade = "B"
            summary = "Adequate history and an unseen test sample are available, but evidence is not extensive."
        elif supervised_rows >= MIN_SUPERVISED_ABSOLUTE and validation_samples >= 5:
            grade = "C"
            summary = "Only limited history and a small unseen test sample are available."
        else:
            grade = "none"
            summary = "There is not enough usable history or unseen validation evidence."
        horizons = supported_horizons(supervised_rows) if grade != "none" else ()
        return cls(
            raw_rows, cleaned_rows, supervised_rows, validation_samples, grade, summary,
            unique_sessions, round(missingness_ratio, 6), round(zero_volume_ratio, 6),
            duplicate_rows, corporate_action_status, liquidity_proxy, horizons,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_rows": self.raw_rows,
            "cleaned_rows": self.cleaned_rows,
            "supervised_rows": self.supervised_rows,
            "validation_samples": self.validation_samples,
            "unique_sessions": self.unique_sessions,
            "missingness_ratio": self.missingness_ratio,
            "zero_volume_ratio": self.zero_volume_ratio,
            "duplicate_rows": self.duplicate_rows,
            "corporate_action_status": self.corporate_action_status,
            "liquidity_proxy": self.liquidity_proxy,
            "supported_horizons": list(self.supported_horizons),
            "evidence_grade": self.evidence_grade,
            "summary": self.summary,
            "thresholds": {
                "A": {"supervised_rows": 240, "validation_samples": 36},
                "B": {"supervised_rows": 120, "validation_samples": 18},
                "C": {"supervised_rows": MIN_SUPERVISED_ABSOLUTE, "validation_samples": 5},
            },
        }


def _quantile(value: np.ndarray, q: float) -> float:
    if value.size == 0:
        return 0.0
    # "higher" keeps conformal coverage conservative in finite samples.
    return float(np.quantile(value, q, method="higher"))


def _weighted_quantile(values: np.ndarray, q: float, weights: np.ndarray) -> float:
    """Conservative weighted quantile for finite-sample conformal calibration.

    ``q`` is the finite-sample-corrected level (n+1 rounding already applied).
    Picks the smallest order statistic whose cumulative weight reaches the
    level, mirroring the "higher" behaviour of :func:`_quantile`.
    """
    if values.size == 0:
        return 0.0
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    sorted_values = np.asarray(values, dtype=float)[order]
    sorted_weights = np.asarray(weights, dtype=float)[order]
    cumulative = np.cumsum(sorted_weights)
    total = float(cumulative[-1])
    if not np.isfinite(total) or total <= 0:
        return float(np.quantile(sorted_values, q, method="higher"))
    target = min(max(float(q), 0.0), 1.0) * total * (1.0 - 1e-12)
    index = int(np.searchsorted(cumulative, target, side="left"))
    index = min(index, sorted_values.size - 1)
    return float(sorted_values[index])


def _calibration_weights(scales: np.ndarray, recent_scale: float) -> np.ndarray:
    """Recency times regime-similarity weights for localised conformal scores.

    Calibration points that are older, or that were observed in a volatility
    regime far from the current one, matter less for the published width.
    Similarity is floored so a handful of historical points can always anchor
    the interval instead of letting one lucky recent point dominate it.
    """
    n = scales.size
    if n == 0:
        return np.array([], dtype=float)
    recency = 0.98 ** np.arange(n - 1, -1, -1, dtype=float)
    safe_scales = np.where(np.asarray(scales, dtype=float) > 0, np.asarray(scales, dtype=float), np.nan)
    reference = float(recent_scale) if recent_scale > 0 else float(np.nanmedian(safe_scales)) if np.isfinite(np.nanmedian(safe_scales)) else np.nan
    if not np.isfinite(reference) or reference <= 0:
        similarity = np.ones(n, dtype=float)
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            similarity = np.exp(-np.abs(np.log(safe_scales / reference)))
        similarity = np.where(np.isfinite(similarity), similarity, 1.0)
    weights = recency * np.maximum(similarity, 0.1)
    total = float(np.sum(weights))
    if not np.isfinite(total) or total <= 0:
        return np.full(n, 1.0 / n, dtype=float)
    return weights / total


def _winkler(actual: np.ndarray, low: np.ndarray, high: np.ndarray, alpha: float) -> float:
    width = high - low
    penalty_low = np.where(actual < low, (2 / alpha) * (low - actual), 0.0)
    penalty_high = np.where(actual > high, (2 / alpha) * (actual - high), 0.0)
    return float(np.mean(width + penalty_low + penalty_high))


class _PersistenceRegressor:
    """Last-observed-close baseline as a first-class stack member.

    Letting the meta-learner down-weight deterministic features in favour of
    persistence keeps a noisy ensemble from ever being much worse than the
    naive baseline, while genuine signal can still improve on it.
    """

    def fit(self, X: pd.DataFrame, y: Any) -> "_PersistenceRegressor":
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(X["Close"], dtype=float)


def _frame_splits(n: int) -> tuple[slice, slice, slice, slice]:
    """Distinct chronological train/meta/calibration/test folds."""
    if n < MIN_SUPERVISED_FULL:
        raise legacy_models.InsufficientDataError("At least 120 complete feature rows are required for the full range pipeline.")
    test_size = max(18, int(n * 0.15))
    calibration_size = max(18, int(n * 0.15))
    meta_size = max(18, int(n * 0.15))
    train_end = n - meta_size - calibration_size - test_size
    meta_end = train_end + meta_size
    calibration_end = meta_end + calibration_size
    if train_end < 40:
        raise legacy_models.InsufficientDataError("Not enough chronological rows for distinct train/meta/calibration/test folds.")
    return (
        slice(0, train_end),
        slice(train_end, meta_end),
        slice(meta_end, calibration_end),
        slice(calibration_end, n),
    )


def _adaptive_splits(n: int) -> tuple[slice, slice, slice, slice]:
    """Chronological splits that degrade gracefully for shorter histories."""
    if n < MIN_SUPERVISED_ABSOLUTE:
        raise legacy_models.InsufficientDataError(
            f"At least {MIN_SUPERVISED_ABSOLUTE} complete feature rows are required for a trustable range forecast."
        )
    if n >= MIN_SUPERVISED_FULL:
        return _frame_splits(n)
    test_size = max(5, int(n * 0.15))
    calibration_size = max(5, int(n * 0.15))
    meta_size = max(5, int(n * 0.15))
    train_end = n - meta_size - calibration_size - test_size
    meta_end = train_end + meta_size
    calibration_end = meta_end + calibration_size
    if train_end < 10:
        raise legacy_models.InsufficientDataError(
            "Not enough chronological rows to validate a range forecast reliably."
        )
    return (
        slice(0, train_end),
        slice(train_end, meta_end),
        slice(meta_end, calibration_end),
        slice(calibration_end, n),
    )


def _retained_features(enriched: pd.DataFrame, min_rows: int) -> list[str]:
    """Keep only features whose warm-up fits the available history.

    Features whose indicator warm-up (e.g. SMA_100) exceeds the available
    history can never contribute a supervised row, so they are dropped
    progressively until enough usable rows remain.  This is what lets
    newly-listed or thinly-traded stocks receive a trustable prediction.
    """
    if len(enriched) == 0:
        raise legacy_models.InsufficientDataError("No market history is available for forecasting.")
    tail = enriched[legacy_models.FEATURE_COLUMNS].notna().iloc[::-1]
    runs = tail.cumprod().sum()
    ordered = list(runs.sort_values(ascending=False).index)
    chosen: list[str] = []
    for count in range(len(ordered), 0, -1):
        subset = ordered[:count]
        rows = enriched[subset].dropna(how="any")
        if len(rows) >= min_rows:
            chosen = subset
            break
    if not chosen:
        chosen = ordered[: max(6, min(12, len(ordered)))]
    if not chosen:
        raise legacy_models.InsufficientDataError(
            "Not enough complete indicator history to construct a supervised forecast frame."
        )
    return chosen


def _supervised_from_enriched(enriched: pd.DataFrame, features: list[str], horizon: int = 1) -> pd.DataFrame:
    """Feature rows at bar t with the close ``horizon`` sessions ahead as target."""
    frame = enriched[features].copy()
    frame[legacy_models.TARGET_COLUMN] = enriched["Close"].shift(-max(1, int(horizon)))
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame.dropna(how="any", inplace=True)
    return frame


def supported_horizons(supervised_rows: int, requested: Sequence[int] = HORIZON_SESSIONS) -> tuple[str, ...]:
    """Horizons this history can honestly support under the full evidence rule.

    A horizon only counts as supported when, after dropping the ``h`` rows the
    horizon consumes at the tail, the remaining supervised rows still clear the
    full-pipeline threshold.  Short histories therefore degrade to fewer
    horizons instead of publishing longer ranges with no test evidence.
    """
    supported: list[str] = []
    for horizon in requested:
        horizon = max(1, int(horizon))
        if supervised_rows >= MIN_SUPERVISED_FULL + horizon or (horizon == 1 and supervised_rows >= MIN_SUPERVISED_ABSOLUTE):
            if str(horizon) not in supported:
                supported.append(str(horizon))
    return tuple(supported)


def _latest_feature_row(enriched: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """The most recent fully-observable feature row, scanning back if needed."""
    subset = enriched[features].dropna(how="any")
    if subset.empty:
        raise legacy_models.InsufficientDataError(
            "The latest feature row is incomplete; use a longer history window."
        )
    row = subset.iloc[-1]
    return pd.DataFrame(
        [{c: legacy_models.safe_float(row.get(c), default=np.nan) for c in features}],
        columns=features,
        index=[row.name],
    )


def _coerce_market_data(data: pd.DataFrame) -> pd.DataFrame:
    """Light cleaning that keeps short histories usable.

    The legacy ``validate_market_data`` gate demands 80 raw rows, which
    silently excludes newly-listed and thinly-traded instruments.  Here we do
    the same numeric coercion and row hygiene without that arbitrary floor; row
    sufficiency is enforced later by the supervised-frame minimum.
    """
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        raise legacy_models.InsufficientDataError("Stock data is required and must not be empty.")
    cleaned = data[legacy_models.RAW_COLUMNS].copy()
    for column in legacy_models.RAW_COLUMNS:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned.replace([np.inf, -np.inf], np.nan, inplace=True)
    cleaned = cleaned.loc[~cleaned.index.duplicated(keep="last")]
    cleaned.sort_index(inplace=True)
    cleaned.dropna(subset=legacy_models.RAW_COLUMNS, inplace=True)
    cleaned = cleaned[
        (cleaned["Open"] > 0)
        & (cleaned["High"] > 0)
        & (cleaned["Low"] > 0)
        & (cleaned["Close"] > 0)
        & (cleaned["Volume"] >= 0)
        & (cleaned["High"] >= cleaned[["Open", "Close", "Low"]].max(axis=1))
        & (cleaned["Low"] <= cleaned[["Open", "Close", "High"]].min(axis=1))
    ]
    if len(cleaned) < 10:
        raise legacy_models.InsufficientDataError("At least 10 valid historical records are required for range forecasting.")
    return cleaned


def _recent_sigma(close: pd.Series) -> float:
    """Robust 1-bar volatility scale from the recent realized move distribution."""
    series = pd.Series(close, dtype=float).dropna()
    if len(series) < 3:
        return 0.0
    tail = series.iloc[-VOL_TAIL_BARS:]
    moves = tail.diff().abs().dropna()
    if len(moves) == 0:
        return 0.0
    median_move = float(moves.median())
    sigma = max(median_move / 0.6745, float(moves.std(ddof=0)))
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = float(moves.std(ddof=0))
    return float(sigma)


def _sigma_at(canonical: pd.DataFrame, origin: Any) -> float:
    """MAD volatility observable at or before ``origin``.

    ``canonical.loc[timestamp, "Close"]`` returns a scalar on a unique daily
    index, which collapsed the calibration scales to their fallback and left
    the conformal scores in absolute-price units (a rupee-squared width). This
    helper slices the trailing window up to the origin instead, so the MAD
    scale is always measured on a real price series. Returns ``0.0`` when
    there are fewer than 3 prior bars so callers can fall back safely.
    """
    origin_ts = pd.Timestamp(origin)
    if len(canonical.index) == 0 or origin_ts < canonical.index[0] or origin_ts > canonical.index[-1]:
        return 0.0
    window = canonical.loc[:origin_ts].iloc[-VOL_TAIL_BARS:]
    return _recent_sigma(window["Close"])


def _interval_half_width(conformal_q: float, recent_sigma: float, reference_price: float, z_score: float) -> float:
    """Apply the published width rule without an evidence-destroying maximum cap."""
    floor = max(float(reference_price) * MIN_RANGE_HALF_PCT, 0.01)
    candidates = [float(conformal_q), float(z_score) * float(recent_sigma), floor]
    finite = [value for value in candidates if math.isfinite(value) and value > 0]
    return max(finite, default=floor)


def _timestamp(value: Any) -> str:
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def _target_timestamp(origin: Any, timeframe: str, sessions: int = 1) -> str:
    """Return the bar end ``sessions`` sessions ahead, skipping closed NSE days."""
    timestamp = pd.Timestamp(origin)
    aware = timestamp.tzinfo is not None
    local = timestamp.tz_convert(IST) if aware else timestamp.tz_localize(IST)

    def is_session(day: Any) -> bool:
        value = pd.Timestamp(day).date()
        calendar = resolve_year_calendar(value.year)
        return value in calendar["special_sessions"] or (value.weekday() < 5 and value not in calendar["holidays"])

    def following_session(day: Any) -> pd.Timestamp:
        candidate = pd.Timestamp(day).normalize() + pd.Timedelta(days=1)
        while not is_session(candidate):
            candidate += pd.Timedelta(days=1)
        return candidate

    steps = max(1, int(sessions))
    if timeframe in {"1D", "1W"}:
        bar_sessions = 1 if timeframe == "1D" else 5
        time_of_day = local - local.normalize()
        target = local
        for _ in range(steps * bar_sessions):
            target = following_session(target) + time_of_day
    else:
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(timeframe, 0)
        session_open = pd.Timedelta(hours=MARKET_OPEN.hour, minutes=MARKET_OPEN.minute)
        session_close = pd.Timedelta(hours=MARKET_CLOSE.hour, minutes=MARKET_CLOSE.minute)
        target = local
        for _ in range(steps):
            if not is_session(target) or target - target.normalize() < session_open:
                base = target.normalize() if is_session(target) else following_session(target)
                target = base + session_open + pd.Timedelta(minutes=minutes)
            else:
                candidate = target + pd.Timedelta(minutes=minutes)
                if candidate - candidate.normalize() > session_close:
                    target = following_session(target) + session_open + pd.Timedelta(minutes=minutes)
                else:
                    target = candidate
    if aware:
        return target.tz_convert(timestamp.tzinfo).isoformat()
    return target.tz_localize(None).isoformat()


def _evaluate_published_predictions(
    actual: np.ndarray,
    published: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    *,
    confidence_level: float,
    references: np.ndarray | None = None,
) -> IntervalMetrics:
    """Score exactly the blended point and bounds produced by the published rule."""
    actual_values = np.asarray(actual, dtype=float)
    published_values = np.asarray(published, dtype=float)
    low_values = np.asarray(low, dtype=float)
    high_values = np.asarray(high, dtype=float)
    reference_values = np.asarray(references, dtype=float) if references is not None else np.roll(actual_values, 1)
    absolute_errors = np.abs(actual_values - published_values)
    valid_scale = np.abs(actual_values) > 1e-12
    widths = high_values - low_values
    direction_actual = np.sign(actual_values - reference_values)
    direction_predicted = np.sign(published_values - reference_values)
    direction_classes = np.unique(direction_actual)
    direction_score = float(np.mean([
        np.mean(direction_predicted[direction_actual == value] == value)
        for value in direction_classes
    ])) if len(direction_classes) else 0.0
    alpha = 1.0 - confidence_level
    return IntervalMetrics(
        coverage=float(np.mean((actual_values >= low_values) & (actual_values <= high_values))),
        nominal_coverage=confidence_level,
        winkler_score=_winkler(actual_values, low_values, high_values, alpha),
        mae=float(mean_absolute_error(actual_values, published_values)),
        rmse=float(math.sqrt(mean_squared_error(actual_values, published_values))),
        samples=len(actual_values),
        mae_pct=float(np.mean(absolute_errors[valid_scale] / np.abs(actual_values[valid_scale])) * 100.0) if valid_scale.any() else 0.0,
        median_absolute_error=float(np.median(absolute_errors)),
        direction_balanced_accuracy=direction_score,
        average_width=float(np.mean(widths)),
        average_width_pct=float(np.mean(widths[valid_scale] / np.abs(actual_values[valid_scale])) * 100.0) if valid_scale.any() else 0.0,
    )


class _EtsRegressor:
    """Exponential smoothing of the horizon target, fitted on train only.

    A deterministic classical baseline for low-history instruments. ``predict``
    returns the fitted one-step-ahead level (or the training mean when the
    smoother fails to converge), so it can never see calibration or test rows.
    """

    def __init__(self, trend: str | None = "add") -> None:
        self.trend = trend
        self._level: float | None = None

    def fit(self, X: pd.DataFrame, y: Any) -> "_EtsRegressor":
        values = np.asarray(y, dtype=float)
        values = values[np.isfinite(values)]
        if len(values) == 0:
            self._level = None
            return self
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing

            if self.trend:
                model = ExponentialSmoothing(values, trend=self.trend, damped_trend=True)
            else:
                model = SimpleExpSmoothing(values)
            fitted = model.fit()
            forecast = float(np.asarray(fitted.forecast(1), dtype=float)[0])
            self._level = forecast if math.isfinite(forecast) else float(np.mean(values))
        except Exception:
            self._level = float(np.mean(values))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        level = self._level if self._level is not None else float("nan")
        return np.asarray([level] * len(X), dtype=float)


def _fit_base_models(X: pd.DataFrame, y: pd.Series, *, low_data: bool = False) -> dict[str, Any]:
    from sklearn.exceptions import ConvergenceWarning
    # Reuse the established model implementations instead of re-deriving them.
    names = ["Linear Regression", "ElasticNet", "Random Forest", "Gradient Boosting"]
    models = {}
    for name in names:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                models[name] = legacy_models._train_model_by_name(name, X, y)
        except ConvergenceWarning:
            # An unoptimised member is not evidence. Remaining members and the
            # persistence anchor are evaluated as the exact published blend.
            continue
    models["Naive Persistence"] = _PersistenceRegressor()

    # Low-data runs join the classical challengers the research recommends for
    # thin histories: a ridge and exponential smoothing, both fit strictly on
    # the train fold. They do not run on the full pipeline where they would
    # only add noise to the stack.
    if low_data:
        try:
            models["Ridge"] = Ridge(alpha=5.0, random_state=42).fit(X, y)
        except Exception:
            pass
        models["ETS"] = _EtsRegressor(trend="add").fit(X, y)

    # Advanced boosters are default dependencies (LightGBM, CatBoost). They join
    # the stack when the installed wheel is available on the current platform.
    try:
        from lightgbm import LGBMRegressor
        model = LGBMRegressor(n_estimators=180, max_depth=-1, learning_rate=0.035, num_leaves=31, random_state=42, verbosity=-1)
        model.fit(X, y)
        models["LightGBM"] = model
    except Exception:
        pass
    try:
        from catboost import CatBoostRegressor
        model = CatBoostRegressor(iterations=160, depth=6, learning_rate=0.04, loss_function="RMSE", verbose=False, random_seed=42, allow_writing_files=False)
        model.fit(X, y)
        models["CatBoost"] = model
    except Exception:
        pass
    return models


def _matrix_predictions(models: dict[str, Any], X: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    names = list(models)
    matrix = np.column_stack([np.asarray(models[name].predict(X), dtype=float) for name in names])
    return names, matrix


def _fit_quantile_families(X: pd.DataFrame, y: pd.Series, q_low: float, q_high: float) -> dict[str, dict[str, Any]]:
    """Fit direct quantile models, including native booster objectives when available."""
    families: dict[str, dict[str, Any]] = {
        "GradientBoosting": {
            "low": GradientBoostingRegressor(loss="quantile", alpha=q_low, n_estimators=120, max_depth=3, learning_rate=0.04, random_state=42),
            "median": GradientBoostingRegressor(loss="quantile", alpha=0.5, n_estimators=120, max_depth=3, learning_rate=0.04, random_state=42),
            "high": GradientBoostingRegressor(loss="quantile", alpha=q_high, n_estimators=120, max_depth=3, learning_rate=0.04, random_state=42),
        }
    }
    try:
        from lightgbm import LGBMRegressor
        families["LightGBM"] = {
            key: LGBMRegressor(objective="quantile", alpha=alpha, n_estimators=90, learning_rate=0.04, num_leaves=25, random_state=42, verbosity=-1)
            for key, alpha in (("low", q_low), ("median", 0.5), ("high", q_high))
        }
    except Exception:
        pass
    fitted: dict[str, dict[str, Any]] = {}
    for family, models in families.items():
        try:
            for model in models.values():
                model.fit(X, y)
            fitted[family] = models
        except Exception:
            # A platform-specific booster failure must not take down the production
            # forecast; sklearn quantile regression remains the portable baseline.
            if family == "GradientBoosting":
                raise
    return fitted


def _arima_baseline(close: pd.Series) -> dict[str, Any]:
    try:
        from statsmodels.tsa.arima.model import ARIMA
        from statsmodels.tools.sm_exceptions import ConvergenceWarning, EstimationWarning
        series = pd.Series(close, dtype=float).dropna()
        # ARIMA uses observation order only here. Passing values avoids
        # Statsmodels inferring an exchange-calendar frequency and warning on
        # otherwise valid business-date indices.
        subset = series.iloc[-min(len(series), 500):].to_numpy(dtype=float)
        # An unstable initialization/fit is not evidence for a publishable
        # challenger. Treat it as unavailable instead of hiding its warnings.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            warnings.simplefilter("error", EstimationWarning)
            model = ARIMA(subset, order=(2, 1, 2)).fit()
            if not model.mle_retvals.get("converged", False):
                return {"available": False, "reason": "arima_fit_not_converged"}
        point = float(np.asarray(model.forecast(steps=1), dtype=float)[0])
        return {"available": True, "forecast": round(point, 4), "order": [2, 1, 2]}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _drift_status(residuals: np.ndarray, recent_count: int = 20) -> dict[str, Any]:
    values = np.abs(np.asarray(residuals, dtype=float))
    if values.size < 40:
        return {"status": "insufficient_history", "drift_detected": False}
    recent_count = min(recent_count, max(10, values.size // 4))
    historical = values[:-recent_count]
    recent = values[-recent_count:]
    hist_mean = float(np.mean(historical))
    hist_std = float(np.std(historical))
    recent_mean = float(np.mean(recent))
    threshold = hist_mean + 2.0 * hist_std
    drift = recent_mean > max(threshold, hist_mean * 1.35)
    return {
        "status": "drift" if drift else "stable",
        "drift_detected": bool(drift),
        "historical_abs_error_mean": round(hist_mean, 4),
        "recent_abs_error_mean": round(recent_mean, 4),
        "threshold": round(threshold, 4),
        "method": "recent absolute error vs historical mean + 2σ",
    }


def _resolve_horizons(requested: Sequence[int] | None) -> tuple[int, ...]:
    """Normalise a caller horizon ladder to a sorted tuple of positive ints."""
    if requested is None:
        return (1,)
    try:
        values = sorted({max(1, int(horizon)) for horizon in requested})
    except (TypeError, ValueError):
        raise ValueError("horizons must be a sequence of positive integers.") from None
    return tuple(values) if values else (1,)


def _horizon_consistency(runs: dict[int, dict[str, Any]], current_price: float) -> dict[str, Any]:
    """Compare independently fitted horizons without altering their outputs."""
    if not (math.isfinite(current_price) and current_price > 0):
        return {
            "available": False,
            "score": None,
            "level": "unavailable",
            "signal": "insufficient_horizons",
            "summary": "A valid reference price is required for a consistency check.",
            "flags": [],
            "basis": "No cross-horizon inference is made without a valid reference price.",
        }
    usable: list[tuple[int, float, float]] = []
    for horizon, run in sorted(runs.items()):
        median = float(run.get("median") or 0.0)
        width = float(run.get("range_width_pct") or 0.0) * 100.0
        if bool(run.get("abstained")) or not all(math.isfinite(value) for value in (median, width)):
            continue
        usable.append((horizon, ((median / current_price) - 1.0) * 100.0, width))

    if len(usable) < 2:
        return {
            "available": False,
            "score": None,
            "level": "unavailable",
            "signal": "insufficient_horizons",
            "summary": "At least two released horizons are required for a consistency check.",
            "flags": [],
            "basis": "No cross-horizon inference is made from a single released corridor.",
        }

    move_tolerance_pct = 0.25
    signs = {
        1 if return_pct > move_tolerance_pct else -1 if return_pct < -move_tolerance_pct else 0
        for _, return_pct, _ in usable
    }
    material_signs = signs - {0}
    directional_agreement = len(material_signs) <= 1

    far_return = usable[-1][1]
    expected_sign = 1 if far_return > move_tolerance_pct else -1 if far_return < -move_tolerance_pct else 0
    path_violations: list[dict[str, Any]] = []
    for previous, current in zip(usable, usable[1:]):
        previous_horizon, previous_return, _ = previous
        current_horizon, current_return, _ = current
        reverses_path = (
            expected_sign > 0 and current_return < previous_return - move_tolerance_pct
        ) or (
            expected_sign < 0 and current_return > previous_return + move_tolerance_pct
        )
        if reverses_path:
            path_violations.append({"from": previous_horizon, "to": current_horizon})

    width_violations: list[dict[str, Any]] = []
    for previous, current in zip(usable, usable[1:]):
        previous_horizon, _, previous_width = previous
        current_horizon, _, current_width = current
        if current_width < previous_width * 0.95:
            width_violations.append({"from": previous_horizon, "to": current_horizon})

    flags: list[str] = []
    if not directional_agreement:
        flags.append("direction_conflict")
    if path_violations:
        flags.append("median_path_reversal")
    if width_violations:
        flags.append("interval_narrows_with_horizon")
    score = max(
        0,
        100
        - (40 if not directional_agreement else 0)
        - min(30, 15 * len(path_violations))
        - min(20, 10 * len(width_violations)),
    )
    level = "high" if score >= 80 else "moderate" if score >= 55 else "low"
    signal = "supportive" if level == "high" else "caution" if level == "moderate" else "weak"
    if not flags:
        summary = "Released horizons agree on direction and show no material path or uncertainty-width conflict."
    else:
        explanations = {
            "direction_conflict": "released horizons point in materially different directions",
            "median_path_reversal": "the median path reverses as the horizon extends",
            "interval_narrows_with_horizon": "a longer-horizon interval is materially narrower",
        }
        summary = "Cross-horizon caution: " + "; ".join(explanations[flag] for flag in flags) + "."
    return {
        "available": True,
        "score": score,
        "level": level,
        "signal": signal,
        "summary": summary,
        "directional_agreement": directional_agreement,
        "median_path_monotonic": not path_violations,
        "uncertainty_width_monotonic": not width_violations,
        "flags": flags,
        "path_violations": path_violations,
        "width_violations": width_violations,
        "horizons_compared": [horizon for horizon, _, _ in usable],
        "basis": "Compares released direct-horizon medians with a 0.25% material-move tolerance and flags interval narrowing greater than 5%; it does not change any forecast.",
    }


# ---------------------------------------------------------------------------
# Statistical assessment helpers (probability, agreement, regime, confidence,
# data quality, explanation). Every number here is derived from validated
# output or indicator data observable at the latest bar; nothing is hardcoded.
# ---------------------------------------------------------------------------


def _safe_recent_row(enriched: pd.DataFrame, columns: Sequence[str]) -> dict[str, float]:
    """Last-row values for indicator columns, NaN-safe."""
    row = enriched.iloc[-1] if not enriched.empty else None
    out: dict[str, float] = {}
    for column in columns:
        value = float("nan")
        if row is not None and column in enriched.columns:
            try:
                candidate = float(row[column])
            except (TypeError, ValueError):
                candidate = float("nan")
            value = candidate if math.isfinite(candidate) else float("nan")
        out[column] = value
    return out


def _member_directional_vote(matrix: np.ndarray, reference: float) -> tuple[float, int]:
    """Fraction of ensemble members whose next-bar point sits above the reference."""
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[1] == 0:
        return 0.5, 0
    if not (math.isfinite(reference) and reference > 0):
        return 0.5, 0
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.5, 0
    upward = float(np.mean(finite > reference))
    return upward, int(values.shape[1])


def _calibrated_probability(raw_vote: float, directional_skill: float | None, *, floor: float = 0.05, ceiling: float = 0.95) -> float:
    """Calibrate a raw member vote with out-of-sample directional skill.

    ``directional_skill`` is (balanced directional accuracy - 0.5) measured on
    the untouched test fold.  Skill 0 keeps the raw vote; positive skill
    amplifies it; negative skill shrinks it toward 50%.  The result is clipped
    so the system never claims a certainty it did not measure.
    """
    skill = float(directional_skill) if directional_skill is not None else 0.0
    skill = max(-0.5, min(0.5, skill))
    factor = max(0.1, 1.0 + 2.0 * skill)
    probability = 0.5 + (float(raw_vote) - 0.5) * factor
    return float(max(floor, min(ceiling, probability)))


def _model_agreement_report(matrix: np.ndarray, reference: float, expected_vol_pct: float | None) -> dict[str, Any]:
    """Ensemble spread relative to the expected one-bar move.

    ``dispersion_pct`` is the median absolute deviation of the member
    next-bar predictions as a percentage of the reference price.  The score is
    ``1 - dispersion / max(3 * expected volatility, 0.05%)`` clipped to [0, 1]:
    members that already disagree by more than three typical moves contribute
    no usable agreement.
    """
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[1] < 2:
        return {"available": False, "reason": "fewer than two members in the stack."}
    finite = values[np.isfinite(values)]
    if finite.size < 2:
        return {"available": False, "reason": "member predictions were not all finite."}
    if not (math.isfinite(reference) and reference > 0):
        return {"available": False, "reason": "reference price unavailable."}
    dispersion_pct = float(np.median(np.abs(finite - np.median(finite))) / reference * 100.0)
    vol_basis = max(3.0 * (float(expected_vol_pct) if expected_vol_pct is not None else 0.0), 0.05)
    score = float(max(0.0, min(1.0, 1.0 - dispersion_pct / max(vol_basis, 1e-9))))
    level = (
        "high" if score >= ASSESSMENT_AGREEMENT_HIGH
        else "moderate" if score >= ASSESSMENT_AGREEMENT_MODERATE
        else "low"
    )
    return {
        "available": True,
        "score": round(score, 4),
        "level": level,
        "dispersion_pct": round(dispersion_pct, 4),
        "member_count": int(values.shape[1]),
        "basis": "median absolute deviation of member next-bar predictions as % of the reference price, normalised by 3x expected one-bar volatility.",
    }


def _volatility_percentile(atr_pct: Sequence[float]) -> float | None:
    """Percentile of today's ATR% within its own recent history (0..1)."""
    series = pd.Series(atr_pct, dtype=float).dropna()
    if len(series) < 5:
        return None
    tail = series.iloc[-90:]
    if len(tail) < 5 or not math.isfinite(float(tail.iloc[-1])):
        return None
    latest = float(tail.iloc[-1])
    return float((tail < latest).mean())


def _market_regime_report(enriched: pd.DataFrame) -> dict[str, Any]:
    """Rule-based regime from indicators observable at the latest bar only.

    Trend score = (close > SMA20) + (close > SMA50) + (EMA20 > EMA50) +
    (MACD histogram > 0), so it ranges 0..4.  ADX >= 25 marks a trending
    regime; otherwise the directional lean is treated as movement inside a
    range.  Volatility is the percentile of today's ATR% in its own recent
    history.  Nothing here uses future bars, so it is leakage-safe by
    construction.
    """
    columns = ["Close", "SMA_20", "SMA_50", "EMA_20", "EMA_50", "MACD_Histogram", "ADX", "ATR_Pct", "RSI"]
    row = _safe_recent_row(enriched, columns)
    has_close = math.isfinite(row["Close"])
    has_anchor = math.isfinite(row["SMA_20"]) or math.isfinite(row["SMA_50"])
    if not (has_close and has_anchor):
        return {"available": False, "reason": "insufficient indicator history for a regime label.", "label": "unclassified"}

    trend_score = 0
    signals: list[str] = []
    if math.isfinite(row["SMA_20"]):
        trend_score += int(row["Close"] > row["SMA_20"])
        signals.append(f"close{' above' if row['Close'] > row['SMA_20'] else ' below'} 20-day average")
    if math.isfinite(row["SMA_50"]):
        trend_score += int(row["Close"] > row["SMA_50"])
        signals.append(f"close{' above' if row['Close'] > row['SMA_50'] else ' below'} 50-day average")
    if math.isfinite(row["EMA_20"]) and math.isfinite(row["EMA_50"]):
        trend_score += int(row["EMA_20"] > row["EMA_50"])
        signals.append(f"20-day EMA{' above' if row['EMA_20'] > row['EMA_50'] else ' below'} 50-day EMA")
    if math.isfinite(row["MACD_Histogram"]):
        trend_score += int(row["MACD_Histogram"] > 0)
        signals.append(f"MACD histogram {'positive' if row['MACD_Histogram'] > 0 else 'negative'}")

    adx = row["ADX"] if math.isfinite(row["ADX"]) else float("nan")
    trending = math.isfinite(adx) and adx >= 25.0
    rsi = row["RSI"] if math.isfinite(row["RSI"]) else None
    if trend_score >= 3:
        trend = "strong_bullish" if (trending and (rsi is None or rsi > 55)) else "mild_bullish"
    elif trend_score <= 1:
        trend = "strong_bearish" if (trending and (rsi is None or rsi < 45)) else "mild_bearish"
    else:
        trend = "sideways"

    percentile = _volatility_percentile(enriched["ATR_Pct"].tolist()) if "ATR_Pct" in enriched.columns else None
    if percentile is None:
        volatility = "unknown"
    elif percentile >= 0.80:
        volatility = "high"
    elif percentile <= 0.20:
        volatility = "low"
    else:
        volatility = "normal"

    label = {
        "strong_bullish": "Strong bullish trend",
        "mild_bullish": "Mild bullish trend",
        "sideways": "Sideways",
        "mild_bearish": "Mild bearish trend",
        "strong_bearish": "Strong bearish trend",
    }[trend]
    if volatility != "unknown":
        label += f" / {volatility.capitalize()} volatility"

    return {
        "available": True,
        "label": label,
        "trend": trend,
        "volatility": volatility,
        "volatility_percentile": round(percentile, 3) if percentile is not None else None,
        "trend_score": trend_score,
        "adx": round(adx, 2) if math.isfinite(adx) else None,
        "signals": signals,
        "basis": "close-vs-SMA20/SMA50, EMA20-vs-EMA50, MACD histogram sign, ADX>=25 trend filter, ATR% percentile for volatility.",
    }


def _data_quality_report(
    *,
    missingness_ratio: float,
    zero_volume_ratio: float,
    duplicate_rows: int,
    feature_aligned: bool,
    corporate_action_status: str,
) -> dict[str, Any]:
    """0..1 data-quality score from observable input defects only."""
    score = 1.0
    notes: list[str] = []
    if missingness_ratio > 0:
        score -= 1.0 * float(missingness_ratio)
        if missingness_ratio > 0.025:
            notes.append(f"{missingness_ratio:.1%} rows rejected")
    if zero_volume_ratio > 0:
        score -= 1.5 * float(zero_volume_ratio)
        if zero_volume_ratio > 0.05:
            notes.append(f"{zero_volume_ratio:.1%} zero-volume bars")
    if duplicate_rows:
        score -= 0.5
        notes.append(f"{duplicate_rows} duplicate rows")
    if not feature_aligned:
        score -= 0.25
        notes.append("feature snapshot misaligned")
    if corporate_action_status == "review_required":
        score -= 0.25
        notes.append("possible corporate action needs review")
    score = float(max(0.0, min(1.0, score)))
    level = "strong" if score >= 0.9 else "limited" if score >= 0.7 else "blocked"
    return {
        "score": round(score, 3),
        "level": level,
        "notes": notes,
        "basis": "1 - missingness - 1.5*zero-volume ratio - duplicate/misalignment/corporate-action penalties, clipped to [0,1].",
    }


def _explanation_reasons(
    enriched: pd.DataFrame,
    regime: dict[str, Any],
    agreement: dict[str, Any] | None,
    expected_vol_pct: float | None,
    liquidity_proxy: str,
    zero_volume_ratio: float,
) -> list[str]:
    """Why the model leans this way, mapped to the indicators behind it."""
    reasons: list[str] = []
    row = _safe_recent_row(
        enriched,
        ["Close_to_SMA20_Pct", "RSI", "MACD_Histogram"],
    )
    if math.isfinite(row["Close_to_SMA20_Pct"]):
        if row["Close_to_SMA20_Pct"] > 0.25:
            reasons.append(f"Price holds {row['Close_to_SMA20_Pct']:.1f}% above its 20-day average.")
        elif row["Close_to_SMA20_Pct"] < -0.25:
            reasons.append(f"Price sits {abs(row['Close_to_SMA20_Pct']):.1f}% below its 20-day average.")
        else:
            reasons.append("Price is essentially at its 20-day average.")
    if math.isfinite(row["RSI"]):
        if row["RSI"] >= 70:
            reasons.append(f"RSI {row['RSI']:.0f} is overbought; mean-reversion risk is elevated.")
        elif row["RSI"] <= 30:
            reasons.append(f"RSI {row['RSI']:.0f} is oversold.")
        elif row["RSI"] >= 55:
            reasons.append(f"RSI {row['RSI']:.0f} shows positive momentum.")
        else:
            reasons.append(f"RSI {row['RSI']:.0f} is neutral.")
    if math.isfinite(row["MACD_Histogram"]):
        reasons.append("MACD histogram is positive (momentum up)." if row["MACD_Histogram"] > 0 else "MACD histogram is negative (momentum down).")
    if regime.get("available"):
        reasons.append(f"Regime: {regime['label']}.")
    if expected_vol_pct is not None:
        reasons.append(f"Expected one-session move is about ±{expected_vol_pct:.2f}%.")
    if agreement and agreement.get("available"):
        reasons.append(f"Ensemble agreement is {agreement['level']}; members span {agreement['dispersion_pct']:.2f}% around the median.")
    if liquidity_proxy in {"limited", "low"}:
        reasons.append(f"Liquidity is {liquidity_proxy} ({zero_volume_ratio:.1%} zero-volume bars), so fills may be wider than the range.")
    else:
        reasons.append("Liquidity looks normal for this window.")
    if not reasons:
        reasons.append("Not enough verified indicator history to explain this range; the numbers are still model output.")
    return reasons


def _confidence_score_report(
    *,
    evidence_grade: str,
    empirical_coverage: float | None,
    nominal_coverage: float | None,
    beats_naive: bool,
    mae_improvement_pct: float,
    agreement_score: float | None,
    drift_detected: bool,
    data_quality_score: float,
    blocked: bool,
) -> dict[str, Any]:
    """0..100 confidence with a documented weighting.

    evidence (40) maps the evidence grade; calibration (25) loses five points
    per percentage point of |empirical - nominal| coverage gap; skill (15)
    scales with measured MAE improvement over naive persistence; agreement
    (10) tracks the ensemble dispersion; data quality (10) scales the 0..1
    input score. Drift or a blocked status caps the total so a number can
    never claim more certainty than the checks allow.
    """
    evidence_pts = {"A": ASSESSMENT_CONFIDENCE_WEIGHTS["evidence"], "B": 28.0, "C": 22.0, "none": 0.0}.get(evidence_grade, 0.0)
    calibration_pts = 0.0
    if empirical_coverage is not None and nominal_coverage is not None:
        gap = abs(float(empirical_coverage) - float(nominal_coverage))
        calibration_pts = max(0.0, ASSESSMENT_CONFIDENCE_WEIGHTS["calibration"] - 500.0 * gap)
    if beats_naive:
        skill_pts = ASSESSMENT_CONFIDENCE_WEIGHTS["skill"] * max(0.0, min(1.0, float(mae_improvement_pct) / 10.0))
    else:
        skill_pts = 4.0
    agreement_pts = ASSESSMENT_CONFIDENCE_WEIGHTS["agreement"] * (float(agreement_score) if agreement_score is not None else 0.0)
    data_pts = ASSESSMENT_CONFIDENCE_WEIGHTS["data_quality"] * max(0.0, min(1.0, float(data_quality_score)))
    score = evidence_pts + calibration_pts + skill_pts + agreement_pts + data_pts
    if drift_detected:
        score = min(score, 30.0)
    if blocked:
        score = min(score, 20.0)
    score = float(max(0.0, min(100.0, score)))
    level = "high" if score >= 70 else "moderate" if score >= 45 else "low"
    return {
        "score": round(score, 1),
        "level": level,
        "components": {
            "evidence": round(evidence_pts, 1),
            "calibration": round(calibration_pts, 1),
            "skill": round(skill_pts, 1),
            "agreement": round(agreement_pts, 1),
            "data_quality": round(data_pts, 1),
        },
        "weights": ASSESSMENT_CONFIDENCE_WEIGHTS,
        "basis": "weighted sum of evidence grade, calibration agreement vs the nominal interval, measured skill over naive persistence, ensemble agreement, and data quality; drift or blocked status caps the total.",
    }


def _build_assessment(
    *,
    forecast_status: str,
    current_price: float,
    median: float,
    recent_scale: float,
    latest_matrix: np.ndarray,
    direction_balanced_accuracy: float,
    enriched: pd.DataFrame,
    liquidity_proxy: str,
    zero_volume_ratio: float,
    missingness_ratio: float,
    duplicate_rows: int,
    feature_aligned: bool,
    corporate_action_status: str,
    evidence_grade: str,
    metrics: IntervalMetrics,
    beats_naive: bool,
    mae_improvement_pct: float,
    drift_detected: bool,
) -> dict[str, Any]:
    """Assemble the statistical assessment block for one horizon.

    Statistics derived from model output (probability, expected return,
    expected volatility, agreement) are only included when the horizon is
    publishable; context (regime, data quality, confidence, explanation) is
    always reported so the user still knows why.
    """
    publishable = forecast_status in ASSESSMENT_PUBLISHABLE
    expected_vol_pct = (
        float(recent_scale / current_price * 100.0)
        if (math.isfinite(current_price) and current_price > 0 and math.isfinite(recent_scale) and recent_scale > 0)
        else None
    )
    expected_return_pct = (
        float((median / current_price - 1.0) * 100.0)
        if (math.isfinite(current_price) and current_price > 0 and math.isfinite(median))
        else None
    )

    regime = _market_regime_report(enriched)
    raw_vote, member_count = _member_directional_vote(latest_matrix, current_price)
    skill = float(direction_balanced_accuracy) - 0.5 if math.isfinite(float(direction_balanced_accuracy)) else None
    probability_up = _calibrated_probability(raw_vote, skill)
    agreement = _model_agreement_report(latest_matrix, current_price, expected_vol_pct)
    data_quality = _data_quality_report(
        missingness_ratio=missingness_ratio,
        zero_volume_ratio=zero_volume_ratio,
        duplicate_rows=duplicate_rows,
        feature_aligned=feature_aligned,
        corporate_action_status=corporate_action_status,
    )
    explanation = _explanation_reasons(
        enriched,
        regime,
        agreement,
        expected_vol_pct,
        liquidity_proxy,
        zero_volume_ratio,
    )
    blocked = forecast_status in {"abstained", "drift_blocked", "data_quality_blocked"}
    confidence = _confidence_score_report(
        evidence_grade=evidence_grade,
        empirical_coverage=metrics.coverage,
        nominal_coverage=metrics.nominal_coverage,
        beats_naive=beats_naive,
        mae_improvement_pct=mae_improvement_pct,
        agreement_score=agreement.get("score") if agreement.get("available") else None,
        drift_detected=drift_detected,
        data_quality_score=data_quality["score"],
        blocked=blocked,
    )
    probability = None
    if publishable and member_count:
        probability = {
            "up": round(probability_up, 4),
            "down": round(1.0 - probability_up, 4),
            "basis": (
                f"{member_count} member predictions above the reference price, calibrated by measured "
                f"directional accuracy ({direction_balanced_accuracy:.3f}) on the untouched test fold; clipped to [0.05, 0.95]."
            ),
        }
    return {
        "available": True,
        "probability": probability,
        "expected_return_pct": round(expected_return_pct, 4) if (publishable and expected_return_pct is not None) else None,
        "expected_volatility_pct": round(expected_vol_pct, 4) if (publishable and expected_vol_pct is not None) else None,
        "market_regime": regime,
        "model_agreement": agreement if publishable else None,
        "confidence_score": confidence,
        "data_quality": data_quality,
        "explanation": {
            "available": True,
            "reasons": explanation,
            "summary": "Why the model leans this way, mapped to the indicators behind it.",
        },
    }


def _run_horizon(
    symbol: str,
    canonical: pd.DataFrame,
    enriched: pd.DataFrame,
    data: pd.DataFrame,
    features: list[str],
    horizon: int,
    confidence_level: float,
    training_window: str,
    timeframe: str,
) -> dict[str, Any]:
    """Run the full leakage-safe pipeline for exactly one direct horizon.

    Every horizon is an independent regressor trained on a distinct target
    (``Close.shift(-h)``); its train/meta/calibration/test folds are
    chronological and non-overlapping. Interval width comes from localised
    conformal scores: calibration absolute residuals are normalised by the
    volatility observable at each calibration origin, and weighted by recency
    and regime similarity to today, so a regime shift widens the published
    range instead of silently degrading coverage.
    """
    frame = _supervised_from_enriched(enriched, features, horizon=horizon)
    if len(frame) < MIN_SUPERVISED_ABSOLUTE:
        raise legacy_models.InsufficientDataError(
            f"At least {MIN_SUPERVISED_ABSOLUTE} complete feature rows are required for a trustable "
            f"{horizon}-session range forecast; use a longer history window for this instrument."
        )
    low_data = len(frame) < MIN_SUPERVISED_FULL

    train_slice, meta_slice, cal_slice, test_slice = _adaptive_splits(len(frame))
    X = frame[features]
    y = frame[legacy_models.TARGET_COLUMN].astype(float)

    X_train, y_train = X.iloc[train_slice], y.iloc[train_slice]
    X_meta, y_meta = X.iloc[meta_slice], y.iloc[meta_slice]
    X_cal, y_cal = X.iloc[cal_slice], y.iloc[cal_slice]
    X_test, y_test = X.iloc[test_slice], y.iloc[test_slice]

    # Stage 1 is frozen before calibration: base learners see train only, while
    # the stack and its persistence weight see meta only. Calibration therefore
    # remains a genuine conformal holdout and test remains untouched.
    base_train = _fit_base_models(X_train, y_train, low_data=low_data)
    names, meta_matrix = _matrix_predictions(base_train, X_meta)
    meta = Ridge(alpha=1.0, random_state=42)
    meta.fit(meta_matrix, y_meta)

    # The persistence blend weight is chosen on the calibration fold, which
    # neither the base learners nor the meta learner saw during fitting.
    # Judging the stack on the meta fold is in-sample for the stacker and
    # over-trusts an overfit ensemble, which shows up as worse-than-naive
    # midpoints and inflated conformal widths. The calibration fold keeps the
    # decision out-of-sample for every component; only the final test fold is
    # reported as validation evidence.
    base_fit = base_train
    cal_matrix = np.column_stack([np.asarray(base_fit[name].predict(X_cal), dtype=float) for name in names])
    stacked_cal = np.asarray(meta.predict(cal_matrix), dtype=float)
    naive_cal = X_cal["Close"].to_numpy(dtype=float)
    cal_actual = y_cal.to_numpy(dtype=float)
    naive_cal_mae = float(mean_absolute_error(cal_actual, naive_cal))
    model_cal_mae = float(mean_absolute_error(cal_actual, stacked_cal))
    if model_cal_mae >= naive_cal_mae or naive_cal_mae <= 0:
        shrink_w = 0.0
    else:
        shrink_w = min(1.0, (naive_cal_mae - model_cal_mae) / naive_cal_mae)
    shrink_w = float(shrink_w)

    blended_cal = shrink_w * stacked_cal + (1.0 - shrink_w) * naive_cal
    cal_residuals = np.abs(y_cal.to_numpy(dtype=float) - blended_cal)

    # Localised conformal: residuals are normalised by the MAD scale observable
    # at each calibration origin, then weighted by recency and similarity to the
    # current volatility regime. The quiet-RELIANCE-learns nothing case is
    # guarded by a floor: normalised scores keep their absolute anchors.
    raw_scales = np.asarray(
        [_sigma_at(canonical, origin) for origin in X_cal.index],
        dtype=float,
    )
    median_scale = float(np.nanmedian(raw_scales)) if np.isfinite(np.nanmedian(raw_scales)) else float("nan")
    fallback_scale = median_scale if math.isfinite(median_scale) and median_scale > 0 else 1.0
    cal_scales = np.where(np.isfinite(raw_scales) & (raw_scales > 0), raw_scales, fallback_scale)
    recent_scale = _recent_sigma(canonical["Close"])
    if not (math.isfinite(recent_scale) and recent_scale > 0):
        recent_scale = fallback_scale
    cal_scores = cal_residuals / cal_scales
    weights = _calibration_weights(cal_scales, recent_scale)

    test_matrix = np.column_stack([np.asarray(base_fit[name].predict(X_test), dtype=float) for name in names])
    stacked_test = np.asarray(meta.predict(test_matrix), dtype=float)
    naive_test = X_test["Close"].to_numpy(dtype=float)
    blended_test = shrink_w * stacked_test + (1.0 - shrink_w) * naive_test

    alpha = 1.0 - confidence_level
    q_level = min(1.0, math.ceil((len(cal_residuals) + 1) * confidence_level) / max(len(cal_residuals), 1))
    conformal_q_normalized = _weighted_quantile(cal_scores, q_level, weights)
    conformal_q_absolute = conformal_q_normalized * recent_scale

    # Quantile families remain research challengers. They do not determine the
    # published interval and are therefore not fitted on every user request.
    q_low = alpha / 2
    q_high = 1 - alpha / 2
    quantile_families: list[str] = []

    current_price = float(canonical["Close"].iloc[-1])
    z_conf = float(_stats.norm.ppf(0.5 + confidence_level / 2.0))
    width_factor = LOW_DATA_WIDTH_FACTOR if low_data else 1.0
    half_width = _interval_half_width(conformal_q_absolute, recent_scale, current_price, z_conf) * width_factor

    # Replay the published width rule at every untouched test origin using only
    # canonical closes observable at that origin, with that origin's own scale.
    # This avoids using today's volatility to make historical validation look
    # better, and it exercises the same regime-adaptive rule the live forecast
    # will use (including the same low-data widening, so calibration stays
    # comparable between the live width and the replayed test widths).
    def width_at(origin_ts: Any, reference: float) -> float:
        sigma = _sigma_at(canonical, origin_ts)
        if not (math.isfinite(sigma) and sigma > 0):
            sigma = fallback_scale
        return _interval_half_width(conformal_q_normalized * sigma, sigma, float(reference), z_conf) * width_factor

    test_half_widths = np.asarray(
        [width_at(timestamp, reference) for timestamp, reference in zip(X_test.index, naive_test)],
        dtype=float,
    )
    final_low_test = np.maximum(0.01, blended_test - test_half_widths)
    final_high_test = np.maximum(final_low_test, blended_test + test_half_widths)
    actual = y_test.to_numpy(dtype=float)
    model_mae = float(mean_absolute_error(actual, blended_test))
    naive_mae = float(mean_absolute_error(actual, naive_test))
    metrics = _evaluate_published_predictions(
        actual,
        blended_test,
        final_low_test,
        final_high_test,
        confidence_level=confidence_level,
        references=naive_test,
    )

    latest = _latest_feature_row(enriched, features)
    latest_matrix = np.column_stack([np.asarray(base_fit[name].predict(latest), dtype=float) for name in names])
    stacked_point = float(meta.predict(latest_matrix)[0])
    persistence_point = current_price
    point = shrink_w * stacked_point + (1.0 - shrink_w) * persistence_point

    low = max(0.01, point - half_width)
    high = max(low, point + half_width)
    median = min(max(point, low), high)

    direction = "bullish" if median > current_price else "bearish" if median < current_price else "neutral"
    drift = _drift_status(actual - blended_test)
    drift["evaluated_prediction"] = "published_blended_prediction"
    latest_timestamp = _timestamp(canonical.index[-1])
    feature_timestamp = _timestamp(latest.index[-1])
    target_timestamp = _target_timestamp(latest.index[-1], timeframe, sessions=horizon)
    range_width_pct = (high - low) / current_price if current_price > 0 else math.inf
    low_utility = bool(range_width_pct > LOW_UTILITY_RANGE_PCT)
    raw_numeric = data[legacy_models.RAW_COLUMNS].apply(pd.to_numeric, errors="coerce")
    missingness_ratio = float(raw_numeric.isna().any(axis=1).mean()) if len(raw_numeric) else 1.0
    zero_volume_ratio = float((canonical["Volume"] <= 0).mean()) if len(canonical) else 1.0
    duplicate_rows = int(data.index.duplicated(keep=False).sum())
    unique_sessions = int(pd.Index(pd.to_datetime(canonical.index).date).nunique())
    extreme_gaps = canonical["Close"].pct_change().abs().gt(0.45)
    corporate_action_status = "review_required" if bool(extreme_gaps.any()) else "no_large_gap_detected"
    liquidity_proxy = "low" if zero_volume_ratio > 0.20 else "limited" if zero_volume_ratio > 0.05 else "normal"
    sufficiency = DataSufficiencyReport.from_counts(
        raw_rows=len(data),
        cleaned_rows=len(canonical),
        supervised_rows=len(frame),
        validation_samples=len(actual),
        unique_sessions=unique_sessions,
        missingness_ratio=missingness_ratio,
        zero_volume_ratio=zero_volume_ratio,
        duplicate_rows=duplicate_rows,
        corporate_action_status=corporate_action_status,
        liquidity_proxy=liquidity_proxy,
    )
    feature_aligned = pd.Timestamp(latest.index[-1]) == pd.Timestamp(canonical.index[-1])
    model_supported = bool(model_mae < naive_mae and shrink_w > 0)
    if not feature_aligned or corporate_action_status == "review_required":
        forecast_status = "data_quality_blocked"
        abstention_reason = "The latest feature snapshot is misaligned or a possible corporate action requires review."
    elif low_utility:
        forecast_status = "abstained"
        abstention_reason = "The calibrated range is too wide to be decision-useful and was not narrowed cosmetically."
    elif bool(drift.get("drift_detected")):
        forecast_status = "drift_blocked"
        abstention_reason = "Recent errors indicate drift, so no public numerical corridor is released."
    elif sufficiency.evidence_grade == "none":
        forecast_status = "abstained"
        abstention_reason = "There is not enough unseen evidence to release a numerical corridor."
    elif sufficiency.evidence_grade == "C":
        forecast_status = "low_evidence"
        abstention_reason = None
    elif not model_supported:
        forecast_status = "baseline_only"
        abstention_reason = None
    else:
        forecast_status = "model_supported"
        abstention_reason = None
    abstained = forecast_status in {"abstained", "drift_blocked", "data_quality_blocked"}

    # Statistical assessment (probability up/down, expected volatility, regime,
    # agreement, confidence score, data quality, explanation). All inputs are
    # validated outputs or indicator rows observable at the latest bar.
    beats_naive = bool(model_mae < naive_mae)
    mae_improvement_pct = float((naive_mae - model_mae) / naive_mae * 100.0) if naive_mae > 0 else 0.0
    assessment = _build_assessment(
        forecast_status=forecast_status,
        current_price=current_price,
        median=median,
        recent_scale=recent_scale,
        latest_matrix=latest_matrix,
        direction_balanced_accuracy=metrics.direction_balanced_accuracy,
        enriched=enriched,
        liquidity_proxy=liquidity_proxy,
        zero_volume_ratio=zero_volume_ratio,
        missingness_ratio=missingness_ratio,
        duplicate_rows=duplicate_rows,
        feature_aligned=feature_aligned,
        corporate_action_status=corporate_action_status,
        evidence_grade=sufficiency.evidence_grade,
        metrics=metrics,
        beats_naive=beats_naive,
        mae_improvement_pct=mae_improvement_pct,
        drift_detected=bool(drift["drift_detected"]),
    )

    return {
        "horizon": horizon,
        "frame_rows": len(frame),
        "low_data": low_data,
        "low_data_branch": "classical_ridge_ets" if low_data else "boosted_stack",
        "base_models": base_fit,
        "names": names,
        "shrink_w": shrink_w,
        "naive_cal_mae": naive_cal_mae,
        "model_cal_mae": model_cal_mae,
        "conformal_q_normalized": conformal_q_normalized,
        "conformal_q_absolute": conformal_q_absolute,
        "cal_scales": cal_scales,
        "weights": weights,
        "fallback_scale": fallback_scale,
        "current_price": current_price,
        "recent_sigma": recent_scale,
        "z_conf": z_conf,
        "half_width": half_width,
        "alpha": alpha,
        "q_low": q_low,
        "q_high": q_high,
        "quantile_families": quantile_families,
        "latest": latest,
        "latest_matrix": latest_matrix,
        "point": point,
        "low": low,
        "high": high,
        "median": median,
        "direction": direction,
        "drift": drift,
        "metrics": metrics,
        "model_mae": model_mae,
        "naive_mae": naive_mae,
        "latest_timestamp": latest_timestamp,
        "feature_timestamp": feature_timestamp,
        "target_timestamp": target_timestamp,
        "range_width_pct": range_width_pct,
        "low_utility": low_utility,
        "sufficiency": sufficiency,
        "feature_aligned": feature_aligned,
        "model_supported": model_supported,
        "forecast_status": forecast_status,
        "abstention_reason": abstention_reason,
        "abstained": abstained,
        "liquidity_proxy": liquidity_proxy,
        "corporate_action_status": corporate_action_status,
        "zero_volume_ratio": zero_volume_ratio,
        "missingness_ratio": missingness_ratio,
        "duplicate_rows": duplicate_rows,
        "unique_sessions": unique_sessions,
        "split": {"train": len(X_train), "meta": len(X_meta), "calibration": len(X_cal), "test": len(X_test)},
        "assessment": assessment,
        "cal_residuals": cal_residuals.tolist() if isinstance(cal_residuals, np.ndarray) else cal_residuals,
        "cal_scores": cal_scores.tolist() if isinstance(cal_scores, np.ndarray) else cal_scores,
        "cal_scales": cal_scales.tolist() if isinstance(cal_scales, np.ndarray) else cal_scales,
        "X_cal_index": X_cal.index.tolist(),
        # Internal replay evidence for paired research comparisons; never serialized
        # wholesale into the public forecast contract.
        "test_index": X_test.index.tolist(),
        "test_actual": actual,
        "test_median": blended_test,
        "test_low": final_low_test,
        "test_high": final_high_test,
    }


def _v13_enhance_forecast(
    result: dict[str, Any],
    canonical: pd.DataFrame,
    enriched: pd.DataFrame,
    symbol: str,
    confidence_level: float,
    training_window: str,
    timeframe: str,
    *,
    market_data: pd.DataFrame | None = None,
    vix_level: float | None = None,
    is_fno: bool = False,
    surveillance_status: str | None = None,
    ipo_info: dict[str, Any] | None = None,
    peer_universe: list[str] | None = None,
    history_loader: Callable[[str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Apply v13 enhancements: tier router, volatility, regime, CQR/ACI/Mondrian.

    Each enhancement step runs inside its own ``_isolate`` guard so a failure
    in one subsystem degrades only that subsystem instead of discarding the
    whole enrichment block.  Every step outcome is reported under
    ``enhancement_status`` and counted in the module-level metrics exposed by
    :func:`get_enhancement_metrics`.
    """
    _increment_metric("total_calls")
    enhanced = dict(result)
    primary = result  # primary horizon result
    started = time.monotonic()
    steps: dict[str, str] = {}
    degraded: list[str] = []

    @contextmanager
    def _isolate(step: str, metric_key: str, *, critical: bool = False) -> Iterator[None]:
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - per-step isolation is the point
            _increment_metric(metric_key)
            degraded.append(step)
            steps[step] = f"failed:{type(exc).__name__}"
            log = LOGGER.error if critical else LOGGER.warning
            log(
                "v13 enhancement step %r failed; forecast continues without it (%s: %s)",
                step,
                type(exc).__name__,
                exc,
                extra={"step": step, "symbol": symbol, "timeframe": timeframe},
            )
        else:
            steps[step] = "ok"

    horizon_raw = primary.get("horizon")
    horizon_block: dict[str, Any] = horizon_raw if isinstance(horizon_raw, dict) else {}
    primary_horizon = int(horizon_block.get("sessions") or 1)

    supervised_rows = int(primary.get("frame_rows", 0) or 0)
    metrics_obj = primary.get("metrics")
    validation_samples = int(getattr(metrics_obj, "samples", 0) or 0)

    # Neutral fallbacks so steps downstream of a degraded step still run
    # against well-formed inputs rather than crashing on missing state.
    tier_assignment = TierAssignment(
        tier=DataTier.T2,
        usable_days=supervised_rows,
        evidence_grade="none",
        reason="tier router unavailable; neutral fallback applied",
        primary_output="range",
        model_stack=[],
        supported_horizons=(1, 3, 5, 10),
        liquidity_bucket="unknown",
        corporate_action_flag=False,
        surveillance_flag=False,
    )
    regime_state = RegimeState(
        regime=MarketRegime.UNCLASSIFIED,
        trend="unknown",
        volatility="unknown",
        stress="unknown",
        probability=0.0,
        adx=None,
        atr_percentile=None,
        vix_level=vix_level,
        nifty_drawdown_pct=None,
        basis="regime detector unavailable; unclassified fallback",
    )
    enhanced["tier"] = tier_to_dict(tier_assignment)

    # --- 1. Data-tier assignment ---
    with _isolate("tier_router", "tier_router_failures"):
        tier_assignment = assign_tier(
            usable_days=supervised_rows,
            validation_samples=validation_samples,
            median_turnover=None,
            zero_volume_ratio=float(primary.get("zero_volume_ratio", 0.0) or 0.0),
            corporate_action_status=primary.get("corporate_action_status", "not_assessed"),
            surveillance_status=surveillance_status,
            is_fno=is_fno,
            options_implied_available=False,
        )
        enhanced["tier"] = tier_to_dict(tier_assignment)

    # --- 2. Volatility forecast (separate, most reliable output) ---
    with _isolate("volatility", "volatility_failures"):
        intraday_bars = None  # would be loaded if available
        vol_fc = composite_volatility_forecast(
            canonical, intraday_bars_5m=intraday_bars, horizon=1, confidence=confidence_level
        )
        enhanced["volatility_forecast"] = {
            "expected_daily_vol_pct": vol_fc.expected_daily_vol_pct,
            "expected_range_pct": vol_fc.expected_range_pct,
            "model": vol_fc.model,
            "components": vol_fc.components,
            "confidence_level": confidence_level,
            "disclosure": "Volatility is the most predictable component of markets. Range forecasts derived from volatility are more reliable than directional forecasts.",
        }
        enhanced["volatility_scorecard"] = volatility_scorecard(
            canonical, intraday_bars_5m=intraday_bars, horizons=(1, 5, 10, 20), confidence=confidence_level
        )
        range_est = range_based_estimators(canonical)
        enhanced["range_estimators"] = {
            "parkinson": range_est.parkinson,
            "garman_klass": range_est.garman_klass,
            "rogers_satchell": range_est.rogers_satchell,
            "yang_zhang": range_est.yang_zhang,
            "close_to_close": range_est.close_to_close,
        }

    # --- 3. Regime detection and routing ---
    with _isolate("regime", "regime_failures"):
        regime_state = detect_regime(
            enriched,
            market_close=market_data["Close"] if market_data is not None else None,
            vix_level=vix_level,
        )
        enhanced["market_regime"] = regime_to_dict(regime_state)
        available_models = [
            "per_stock_cqr",
            "pooled_cross_sectional",
            "volatility_model",
            "regime_adaptive",
            "trend_following",
            "mean_reversion",
        ]
        enhanced["regime_model_weights"] = regime_router(regime_state.regime, available_models=available_models)

    # --- 4. CQR calibration (if quantile families available) ---
    # Critical: this step mutates the published bounds, so a failure is logged
    # at error level and surfaced loudly in enhancement_status.
    with _isolate("cqr", "cqr_failures", critical=True):
        if "q_low" in primary and "q_high" in primary:
            q_low = primary["q_low"]
            q_high = primary["q_high"]
            raw_cqr_ctx = enhanced.get("cqr")
            cqr_ctx = raw_cqr_ctx if isinstance(raw_cqr_ctx, dict) else {}
            cqr_status = str(cqr_ctx.get("status") or "challenger_only")
            receipt_valid = bool(cqr_ctx.get("receipt_valid"))
            # Routing was decided once, upstream, by ``cqr_canary_status``
            # using a stable SHA-256 bucket, so every process, worker and
            # restart agrees on which arm a symbol is in.
            in_canary = receipt_valid and bool(cqr_ctx.get("canary_assigned"))
            canary_pct = cqr_ctx.get("canary_percentage")
            canary_bucket_value = cqr_ctx.get("canary_bucket")
            has_residuals = "cal_residuals" in primary and primary["cal_residuals"] is not None

            if in_canary and has_residuals:
                cal_residuals = np.asarray(primary["cal_residuals"], dtype=float)
                # Conformalize the existing interval using the calibration
                # residuals as pseudo quantile scores.
                lower_offset, upper_offset = cqr_calibration(
                    cal_residuals,
                    -cal_residuals,  # pseudo lower quantile predictions
                    cal_residuals,  # pseudo upper quantile predictions
                    confidence_level,
                )
                fc = enhanced.get("forecast")
                if fc and "low" in fc and "high" in fc:
                    fc["low"] = max(0.01, fc["low"] + lower_offset)
                    fc["high"] = fc["high"] + upper_offset
                    fc["cqr_applied"] = True
                    fc["cqr_offsets"] = {"lower": lower_offset, "upper": upper_offset}

                # Also apply to multi-horizon runs (list of dicts, not pairs).
                for run in enhanced.get("multi_horizon", {}).get("horizons", []):
                    if not isinstance(run, dict):
                        continue
                    fc = run.get("forecast")
                    if fc and "low" in fc and "high" in fc:
                        fc["low"] = max(0.01, fc["low"] + lower_offset)
                        fc["high"] = fc["high"] + upper_offset
                        fc["cqr_applied"] = True

                enhanced["cqr"] = {
                    "available": True,
                    "low_quantile": q_low,
                    "high_quantile": q_high,
                    "status": "promoted_canary",
                    "determines_published_bounds": True,
                    "in_canary": True,
                    "receipt_valid": receipt_valid,
                    "canary_percentage": canary_pct,
                    "canary_bucket": canary_bucket_value,
                    "canary_pct": CQR_CANARY_PCT,
                    "offsets_applied": {"lower": lower_offset, "upper": upper_offset},
                }
            elif receipt_valid and not in_canary:
                # Control arm: tracked, but offsets never touch published bounds.
                enhanced["cqr"] = {
                    "available": True,
                    "low_quantile": q_low,
                    "high_quantile": q_high,
                    "status": "control_arm",
                    "determines_published_bounds": False,
                    "in_canary": False,
                    "receipt_valid": True,
                    "canary_percentage": canary_pct,
                    "canary_bucket": canary_bucket_value,
                    "canary_pct": CQR_CANARY_PCT,
                    "note": "Control arm - CQR offsets withheld so challenger-vs-control coverage stays comparable",
                }
            else:
                enhanced["cqr"] = {
                    "available": True,
                    "low_quantile": q_low,
                    "high_quantile": q_high,
                    "status": cqr_status,
                    "determines_published_bounds": False,
                    "in_canary": False,
                    "receipt_valid": receipt_valid,
                    "canary_percentage": canary_pct,
                    "reason": "quantile predictions not available" if not has_residuals else "no passed promotion receipt",
                }

    # --- 5. ACI state (read-only; mutations require settled outcomes) ---
    with _isolate("aci", "aci_failures"):
        enhanced["aci_state"] = read_aci_state(symbol, timeframe, primary_horizon, confidence_level)

    # --- 6. Mondrian conformal groups (with persisted calibration) ---
    with _isolate("mondrian", "mondrian_failures"):
        mondrian_groups = {
            "tier": tier_assignment.tier.value,
            "regime": regime_state.regime.value,
            "liquidity": tier_assignment.liquidity_bucket,
            "sector": "unknown",  # would come from instrument master
        }
        enhanced["mondrian_groups"] = mondrian_groups

        if "cal_residuals" in primary and primary["cal_residuals"] is not None:
            cal_residuals = np.asarray(primary["cal_residuals"], dtype=float)
            groups = np.array([tuple(mondrian_groups.values())] * len(cal_residuals))
            save_mondrian_calibration(
                symbol=symbol,
                timeframe=timeframe,
                horizon=primary_horizon,
                confidence=confidence_level,
                tier=mondrian_groups["tier"],
                regime=mondrian_groups["regime"],
                liquidity=mondrian_groups["liquidity"],
                sector=mondrian_groups["sector"],
                residuals=cal_residuals,
                groups=groups,
            )
            mondrian_hw = mondrian_conformal_half_width_persisted(
                residuals=cal_residuals,
                groups=groups,
                confidence=confidence_level,
                group=tuple(mondrian_groups.values()),
                symbol=symbol,
                timeframe=timeframe,
                horizon=primary_horizon,
            )
            # Reported, never auto-applied: Mondrian is still challenger-only.
            current_hw = (enhanced["forecast"]["high"] - enhanced["forecast"]["low"]) / 2
            if abs(mondrian_hw - current_hw) > 1e-6:
                enhanced["mondrian_adjustment"] = {
                    "current_half_width": current_hw,
                    "mondrian_half_width": mondrian_hw,
                    "applied": False,
                }

    # --- 7. Hierarchical shrinkage and peer transfer for IPOs (T0/T1) ---
    with _isolate("ipo_peer", "ipo_peer_failures"):
        if tier_assignment.tier in (DataTier.T0, DataTier.T1) and ipo_info is not None and history_loader is not None:
            build_ipo_features(symbol, canonical, **ipo_info)
            peers = find_ipo_peers(
                symbol,
                sector=ipo_info.get("sector"),
                market_cap_bucket=ipo_info.get("cap_bucket"),
                listing_year=ipo_info.get("listing_year"),
                candidate_universe=peer_universe,
            )
            if peers:
                peer_prior = peer_transfer_prior(peers, history_loader, horizon=primary_horizon)
                expected_return = (
                    primary.get("assessment", {}).get("expected_return_pct", 0) / 100
                    if isinstance(primary.get("assessment"), dict)
                    else 0.0
                )
                blended = blended_forecast_with_peer_prior(
                    {"expected_return": expected_return, "residual_scale": 0.02},
                    peer_prior,
                    sessions_available=supervised_rows,
                )
                enhanced["ipo_peer_blend"] = {
                    "peers_used": peers,
                    "peer_prior": peer_prior,
                    "blended_expected_return": blended["expected_return"],
                    "blended_residual_scale": blended["residual_scale"],
                    "peer_weight": blended["peer_prior_weight"],
                }

        if tier_assignment.tier in (DataTier.T2, DataTier.T3, DataTier.T4):
            # Would use actual sector/cap-bucket skills from validation.
            shrink_weight, components = hierarchical_shrinkage_weight(
                sessions_available=supervised_rows,
                sector_skill=0.0,  # placeholder
                cap_bucket_skill=0.0,
                pooled_skill=0.0,
            )
            enhanced["hierarchical_shrinkage"] = {
                "total_weight": shrink_weight,
                "components": components,
            }

    # --- 8. Circuit-limit clipping on final intervals ---
    # Critical: unclipped bounds could breach exchange circuit limits.
    with _isolate("circuit_clip", "circuit_clip_failures", critical=True):
        for run in enhanced.get("multi_horizon", {}).get("horizons", []):
            if not isinstance(run, dict):
                continue
            fc = run.get("forecast")
            if fc and "low" in fc and "high" in fc:
                price = enhanced.get("current_price", fc.get("median", 0))
                low, high, clip_info = apply_circuit_limits(
                    fc["low"], fc["high"], price, is_fno=is_fno, surveillance=bool(surveillance_status)
                )
                fc["low"] = low
                fc["high"] = high
                fc["circuit_clip"] = clip_info

        fc = enhanced.get("forecast")
        if fc and "low" in fc and "high" in fc:
            price = enhanced.get("current_price", fc.get("median", 0))
            low, high, clip_info = apply_circuit_limits(
                fc["low"], fc["high"], price, is_fno=is_fno, surveillance=bool(surveillance_status)
            )
            fc["low"] = low
            fc["high"] = high
            fc["circuit_clip"] = clip_info

    # --- 9. Distributional output (fan chart quantiles) ---
    with _isolate("fan_chart", "fan_chart_failures"):
        quantile_levels = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
        median = enhanced["forecast"]["median"]
        half_width = (enhanced["forecast"]["high"] - enhanced["forecast"]["low"]) / 2
        # Approximate fan chart from a normal assumption until native
        # quantile models replace the z-score mapping.
        fan_chart = {}
        for q in quantile_levels:
            if q == 0.50:
                fan_chart[str(q)] = median
            else:
                z = _stats.norm.ppf(q)
                fan_chart[str(q)] = round(median + z * half_width / _stats.norm.ppf(0.975), 2)
        enhanced["fan_chart"] = fan_chart

    # --- 10. Minimum width floor ---
    # Critical: a sub-tick or sub-minimum width would be misleadingly precise.
    with _isolate("width_floor", "width_floor_failures", critical=True):
        min_half = minimum_width_floor(enhanced["current_price"])
        current_half = (enhanced["forecast"]["high"] - enhanced["forecast"]["low"]) / 2
        if current_half < min_half:
            centre = enhanced["forecast"]["median"]
            enhanced["forecast"]["low"] = round(centre - min_half, 2)
            enhanced["forecast"]["high"] = round(centre + min_half, 2)
            enhanced["min_width_floor_applied"] = True
            enhanced["min_half_width"] = min_half

    # --- Per-step degradation report ---
    failed_count = len(degraded)
    enhanced["enhancement_status"] = {
        "status": "degraded" if failed_count else "ok",
        "steps": steps,
        "degraded_steps": list(degraded),
        "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
    }
    if failed_count:
        _increment_metric("partial_degradations")
        if failed_count == len(steps):
            _increment_metric("full_degradations")

    return enhanced


def forecast_range(
    symbol: str,
    data: pd.DataFrame,
    *,
    confidence_level: float = 0.80,
    training_window: str = "1y",
    timeframe: str | None = None,
    horizons: Sequence[int] | None = None,
    explain: bool = False,
    market_data: pd.DataFrame | None = None,
    vix_data: pd.DataFrame | None = None,
    context_history_loader: Callable[[str, str, str], pd.DataFrame] | None = None,
    context_timeout_seconds: float = 3.0,
    cqr_promotion_receipt: dict[str, Any] | None = None,
    ipo_info: dict[str, Any] | None = None,
    peer_universe: list[str] | None = None,
) -> dict[str, Any]:
    """Publish calibrated range forecasts for one or more direct horizons.

    ``horizons`` defaults to ``(1,)`` (next-bar only) so callers that predate
    multi-horizon support keep the exact previous contract.  Product surfaces
    pass ``DEFAULT_HORIZONS`` to get the 1/3/5/10-session ladder; horizons that
    lack enough history are reported under ``multi_horizon.unavailable`` rather
    than guessed.  ``explain`` attaches post-hoc SHAP attribution for the tree
    members of the primary stack.
    """
    if training_window not in TRAINING_WINDOWS:
        raise ValueError(f"training_window must be one of: {', '.join(sorted(TRAINING_WINDOWS))}")
    if not 0.60 <= float(confidence_level) <= 0.95:
        raise ValueError("confidence_level must be between 0.60 and 0.95.")

    canonical = _coerce_market_data(data)
    enriched = add_indicators(canonical)
    features = _retained_features(enriched, MIN_SUPERVISED_FULL + 1)
    resolved_timeframe = timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]

    ladder = _resolve_horizons(horizons)
    runs: dict[int, dict[str, Any]] = {}
    unavailable: list[dict[str, Any]] = []
    for horizon in ladder:
        try:
            runs[horizon] = _run_horizon(
                symbol,
                canonical,
                enriched,
                data,
                features,
                horizon,
                confidence_level,
                training_window,
                resolved_timeframe,
            )
        except legacy_models.InsufficientDataError as exc:
            unavailable.append({"sessions": horizon, "reason": str(exc)})
        except Exception as exc:  # a failing long horizon must not sink shorter ones
            unavailable.append({"sessions": horizon, "reason": f"{type(exc).__name__}: {exc}"})
    if not runs:
        raise legacy_models.InsufficientDataError(
            "Not enough complete feature rows to produce a trustable range forecast for any horizon; "
            "use a longer history window for this instrument."
        )

    primary_h = min(runs)
    primary = runs[primary_h]
    current_price = primary["current_price"]
    latest_timestamp = primary["latest_timestamp"]
    feature_timestamp = primary["feature_timestamp"]
    target_timestamp = primary["target_timestamp"]
    sufficiency = primary["sufficiency"]
    arima = _arima_baseline(canonical["Close"])
    horizon_consistency = _horizon_consistency(runs, current_price)

    explainability: dict[str, Any] | None = None
    if explain:
        try:
            from forecasting.explainability import explain_stacked_tree_ensemble

            explainability = explain_stacked_tree_ensemble(
                primary["base_models"],
                primary["latest"],
                features,
                reference_point=current_price,
            )
        except Exception:
            explainability = {"available": False, "reason": "Feature attribution could not be computed on this request."}

    horizon_label = (
        f"next {resolved_timeframe} bar" if primary_h == 1 else f"{primary_h} sessions ahead"
    )
    payload = {
        "symbol": legacy_models.normalize_symbol(symbol).replace(".NS", "").replace(".BO", ""),
        "forecast": {
            "low": round(primary["low"], 2),
            "median": round(primary["median"], 2),
            "high": round(primary["high"], 2),
            "confidence_level": round(confidence_level, 2),
            "label": f"{int(confidence_level * 100)}% interval",
            "currency": "INR",
            "direction": primary["direction"],
        },
        "current_price": round(current_price, 2),
        "data_timestamp": latest_timestamp,
        "feature_timestamp": feature_timestamp,
        "target_timestamp": target_timestamp,
        "horizon": {
            "bars": primary_h,
            "sessions": primary_h,
            "timeframe": resolved_timeframe,
            "label": horizon_label,
        },
        "low_utility": primary["low_utility"],
        "low_data": primary["low_data"],
        "low_data_branch": primary["low_data_branch"],
        "forecast_status": primary["forecast_status"],
        "support_state": primary["forecast_status"],
        "model_supported": primary["forecast_status"] == "model_supported",
        "baseline_only": primary["forecast_status"] == "baseline_only",
        "low_evidence": primary["forecast_status"] == "low_evidence",
        "abstained": primary["abstained"],
        "abstention_reason": primary["abstention_reason"],
        "evidence": {"grade": sufficiency.evidence_grade, "summary": sufficiency.summary},
        "assessment": primary["assessment"],
        "trust": {
            "data": {"status": "blocked" if primary["forecast_status"] == "data_quality_blocked" else "strong", "summary": "Canonical data and feature timestamps are aligned." if primary["feature_aligned"] else "Feature freshness is misaligned."},
            "model": {"status": "strong" if primary["model_supported"] else "limited", "summary": "Improved on persistence in the untouched test." if primary["model_supported"] else "Did not establish skill beyond persistence."},
            "market_regime": {"status": "blocked" if bool(primary["drift"].get("drift_detected")) else "strong", "summary": "Drift detected." if bool(primary["drift"].get("drift_detected")) else "No drift block detected."},
            "liquidity": {"status": "limited" if primary["liquidity_proxy"] != "normal" else "strong", "summary": f"Zero-volume ratio {primary['zero_volume_ratio']:.1%}; liquidity proxy {primary['liquidity_proxy']}."},
        },
        "data_sufficiency": sufficiency.to_dict(),
        "methods": {
            "stacking": {"base_models": primary["names"], "meta_learner": "Ridge (alpha=1.0) with persistence anchor"},
            "low_data_policy": {
                "active": primary["low_data"],
                "branch": primary["low_data_branch"],
                "full_pipeline_min_supervised_rows": MIN_SUPERVISED_FULL,
                "absolute_min_supervised_rows": MIN_SUPERVISED_ABSOLUTE,
                "extra_challenger_models": ["Ridge (alpha=5.0)", "ETS (damped additive trend)"] if primary["low_data"] else [],
                "published_width_factor": LOW_DATA_WIDTH_FACTOR if primary["low_data"] else 1.0,
                "note": "Low-data runs widen the published range by the factor above and replay the same factor on the untouched test fold, so thin-history instruments never appear spuriously precise.",
            },
            "conformal": {
                "type": "MAD-normalised localised split-conformal (recency and regime-weighted)",
                "normalized_residual_quantile": round(primary["conformal_q_normalized"], 6),
                "absolute_residual_quantile_at_current_scale": round(primary["conformal_q_absolute"], 4),
                "weighting": "recency decay (0.98) x regime similarity (volatility-ratio proximity; floor 0.1)",
                "nearby_scale_count": int(np.count_nonzero(primary["weights"] >= 0.1 / max(len(primary["weights"]), 1))),
            },
            "quantile_regression": {
                "families": primary["quantile_families"],
                "low_quantile": round(primary["q_low"], 4),
                "median_quantile": 0.5,
                "high_quantile": round(primary["q_high"], 4),
                "status": "challenger_only",
                "determines_published_bounds": False,
                "request_fit": False,
            },
            "range_anchor": {
                "base": "recent realized volatility (MAD-normalised 1-bar moves) scaling the localised conformal residual quantile",
                "tail_bars": VOL_TAIL_BARS,
                "z_score": round(primary["z_conf"], 4),
                "realized_sigma_points": round(primary["recent_sigma"], 4),
                "half_width_points": round(primary["half_width"], 2),
                "minimum_half_width_pct_of_price": round(MIN_RANGE_HALF_PCT * 100.0, 3),
                "maximum_width_cap": None,
            },
            "point_shrinkage": {
                "method": "calibration-fold-selected blend with persistence (last close); the fold is unseen by base models and the stacker",
                "stack_weight": round(primary["shrink_w"], 4),
                "calibration_naive_mae": round(primary["naive_cal_mae"], 4),
                "calibration_stack_mae": round(primary["model_cal_mae"], 4),
            },
            "interval_ensemble": "maximum of regime-scaled conformal residual width, z-scaled realized volatility, and a numerical floor; no maximum width cap",
            "multi_horizon_method": {
                "method": "independent direct regressors per horizon (target Close.shift(-h)); own chronological folds and untouched test per horizon",
                "ladder": list(ladder),
                "primary_horizon": primary_h,
            },
            "arima_baseline": arima,
        },
        "validation": {
            **primary["metrics"].to_dict(),
            "evaluated_prediction": "published_blended_prediction",
            "evaluated_bounds": "published_interval_rule_replayed_at_each_test_origin",
            "naive_baseline_mae": round(primary["naive_mae"], 4),
            "beats_naive_baseline": bool(primary["model_mae"] < primary["naive_mae"]),
            "mae_improvement_vs_naive_pct": round(((primary["naive_mae"] - primary["model_mae"]) / primary["naive_mae"]) * 100.0, 3) if primary["naive_mae"] > 0 else 0.0,
        },
        "drift": primary["drift"],
        "training": {
            "training_window": training_window,
            "timeframe": resolved_timeframe,
            "mode": "low-data" if primary["low_data"] else "full",
            "data_mode": primary["low_data_branch"],
            "rows": len(data),
            "cleaned_rows": len(canonical),
            "supervised_rows": primary["frame_rows"],
            "feature_count": len(features),
            "split": primary["split"],
            "chronological": True,
            "horizons": {"requested": list(ladder), "computed": sorted(runs), "primary": primary_h},
            "methodology": "Per horizon: base models fit train; stack and blend fit meta; localised conformal width fits calibration; all reported metrics and drift use untouched test.",
            "gap_note": "Features use bar t and target t+h; train < meta < calibration < test with no overlapping rows.",
        },
        "multi_horizon": {
            "primary": primary_h,
            "requested": list(ladder),
            "computed": sorted(runs),
            "consistency": horizon_consistency,
            "horizons": [
                {
                    "sessions": horizon,
                    "bars": horizon,
                    "label": "next session" if horizon == 1 else f"{horizon} sessions ahead",
                    "timeframe": resolved_timeframe,
                    "target_timestamp": run["target_timestamp"],
                    "forecast": {
                        "low": round(run["low"], 2),
                        "median": round(run["median"], 2),
                        "high": round(run["high"], 2),
                        "confidence_level": round(confidence_level, 2),
                        "label": f"{int(confidence_level * 100)}% interval",
                        "currency": "INR",
                        "direction": run["direction"],
                    },
                    "forecast_status": run["forecast_status"],
                    "support_state": run["forecast_status"],
                    "model_supported": run["forecast_status"] == "model_supported",
                    "low_data": run["low_data"],
                    "low_data_branch": run["low_data_branch"],
                    "low_utility": run["low_utility"],
                    "abstained": run["abstained"],
                    "abstention_reason": run["abstention_reason"],
                    "assessment": run["assessment"],
                    "evidence": {"grade": run["sufficiency"].evidence_grade},
                    "validation": {
                        **run["metrics"].to_dict(),
                        "beats_naive_baseline": bool(run["model_mae"] < run["naive_mae"]),
                        "mae_improvement_vs_naive_pct": round(((run["naive_mae"] - run["model_mae"]) / run["naive_mae"]) * 100.0, 3) if run["naive_mae"] > 0 else 0.0,
                    },
                    "width_pct": round(run["range_width_pct"] * 100.0, 4) if math.isfinite(run["range_width_pct"]) else None,
                }
                for horizon, run in sorted(runs.items())
            ],
            "unavailable": unavailable,
        },
        "explainability": explainability,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Research and paper-trading simulation only; not investment advice.",
    }

    payload = {
        "symbol": legacy_models.normalize_symbol(symbol).replace(".NS", "").replace(".BO", ""),
        "forecast": {
            "low": round(primary["low"], 2),
            "median": round(primary["median"], 2),
            "high": round(primary["high"], 2),
            "confidence_level": round(confidence_level, 2),
            "label": f"{int(confidence_level * 100)}% interval",
            "currency": "INR",
            "direction": primary["direction"],
        },
        "current_price": round(current_price, 2),
        "data_timestamp": latest_timestamp,
        "feature_timestamp": feature_timestamp,
        "target_timestamp": target_timestamp,
        "horizon": {
            "bars": primary_h,
            "sessions": primary_h,
            "timeframe": resolved_timeframe,
            "label": horizon_label,
        },
        "low_utility": primary["low_utility"],
        "low_data": primary["low_data"],
        "low_data_branch": primary["low_data_branch"],
        "forecast_status": primary["forecast_status"],
        "support_state": primary["forecast_status"],
        "model_supported": primary["forecast_status"] == "model_supported",
        "baseline_only": primary["forecast_status"] == "baseline_only",
        "low_evidence": primary["forecast_status"] == "low_evidence",
        "abstained": primary["abstained"],
        "abstention_reason": primary["abstention_reason"],
        "evidence": {"grade": sufficiency.evidence_grade, "summary": sufficiency.summary},
        "assessment": primary["assessment"],
        "trust": {
            "data": {"status": "blocked" if primary["forecast_status"] == "data_quality_blocked" else "strong", "summary": "Canonical data and feature timestamps are aligned." if primary["feature_aligned"] else "Feature freshness is misaligned."},
            "model": {"status": "strong" if primary["model_supported"] else "limited", "summary": "Improved on persistence in the untouched test." if primary["model_supported"] else "Did not establish skill beyond persistence."},
            "market_regime": {"status": "blocked" if bool(primary["drift"].get("drift_detected")) else "strong", "summary": "Drift detected." if bool(primary["drift"].get("drift_detected")) else "No drift block detected."},
            "liquidity": {"status": "limited" if primary["liquidity_proxy"] != "normal" else "strong", "summary": f"Zero-volume ratio {primary['zero_volume_ratio']:.1%}; liquidity proxy {primary['liquidity_proxy']}."},
        },
        "data_sufficiency": sufficiency.to_dict(),
        "methods": {
            "stacking": {"base_models": primary["names"], "meta_learner": "Ridge (alpha=1.0) with persistence anchor"},
            "low_data_policy": {
                "active": primary["low_data"],
                "branch": primary["low_data_branch"],
                "full_pipeline_min_supervised_rows": MIN_SUPERVISED_FULL,
                "absolute_min_supervised_rows": MIN_SUPERVISED_ABSOLUTE,
                "extra_challenger_models": ["Ridge (alpha=5.0)", "ETS (damped additive trend)"] if primary["low_data"] else [],
                "published_width_factor": LOW_DATA_WIDTH_FACTOR if primary["low_data"] else 1.0,
                "note": "Low-data runs widen the published range by the factor above and replay the same factor on the untouched test fold, so thin-history instruments never appear spuriously precise.",
            },
            "conformal": {
                "type": "MAD-normalised localised split-conformal (recency and regime-weighted)",
                "normalized_residual_quantile": round(primary["conformal_q_normalized"], 6),
                "absolute_residual_quantile_at_current_scale": round(primary["conformal_q_absolute"], 4),
                "weighting": "recency decay (0.98) x regime similarity (volatility-ratio proximity; floor 0.1)",
                "nearby_scale_count": int(np.count_nonzero(primary["weights"] >= 0.1 / max(len(primary["weights"]), 1))),
            },
            "quantile_regression": {
                "families": primary["quantile_families"],
                "low_quantile": round(primary["q_low"], 4),
                "median_quantile": 0.5,
                "high_quantile": round(primary["q_high"], 4),
                "status": "challenger_only",
                "determines_published_bounds": False,
                "request_fit": False,
            },
            "range_anchor": {
                "base": "recent realized volatility (MAD-normalised 1-bar moves) scaling the localised conformal residual quantile",
                "tail_bars": VOL_TAIL_BARS,
                "z_score": round(primary["z_conf"], 4),
                "realized_sigma_points": round(primary["recent_sigma"], 4),
                "half_width_points": round(primary["half_width"], 2),
                "minimum_half_width_pct_of_price": round(MIN_RANGE_HALF_PCT * 100.0, 3),
                "maximum_width_cap": None,
            },
            "point_shrinkage": {
                "method": "calibration-fold-selected blend with persistence (last close); the fold is unseen by base models and the stacker",
                "stack_weight": round(primary["shrink_w"], 4),
                "calibration_naive_mae": round(primary["naive_cal_mae"], 4),
                "calibration_stack_mae": round(primary["model_cal_mae"], 4),
            },
            "interval_ensemble": "maximum of regime-scaled conformal residual width, z-scaled realized volatility, and a numerical floor; no maximum width cap",
            "multi_horizon_method": {
                "method": "independent direct regressors per horizon (target Close.shift(-h)); own chronological folds and untouched test per horizon",
                "ladder": list(ladder),
                "primary_horizon": primary_h,
            },
            "arima_baseline": arima,
        },
        "validation": {
            **primary["metrics"].to_dict(),
            "evaluated_prediction": "published_blended_prediction",
            "evaluated_bounds": "published_interval_rule_replayed_at_each_test_origin",
            "naive_baseline_mae": round(primary["naive_mae"], 4),
            "beats_naive_baseline": bool(primary["model_mae"] < primary["naive_mae"]),
            "mae_improvement_vs_naive_pct": round(((primary["naive_mae"] - primary["model_mae"]) / primary["naive_mae"]) * 100.0, 3) if primary["naive_mae"] > 0 else 0.0,
        },
        "drift": primary["drift"],
        "training": {
            "training_window": training_window,
            "timeframe": resolved_timeframe,
            "mode": "low-data" if primary["low_data"] else "full",
            "data_mode": primary["low_data_branch"],
            "rows": len(data),
            "cleaned_rows": len(canonical),
            "supervised_rows": primary["frame_rows"],
            "feature_count": len(features),
            "split": primary["split"],
            "chronological": True,
            "horizons": {"requested": list(ladder), "computed": sorted(runs), "primary": primary_h},
            "methodology": "Per horizon: base models fit train; stack and blend fit meta; localised conformal width fits calibration; all reported metrics and drift use untouched test.",
            "gap_note": "Features use bar t and target t+h; train < meta < calibration < test with no overlapping rows.",
        },
        "multi_horizon": {
            "primary": primary_h,
            "requested": list(ladder),
            "computed": sorted(runs),
            "consistency": horizon_consistency,
            "horizons": [
                {
                    "sessions": horizon,
                    "bars": horizon,
                    "label": "next session" if horizon == 1 else f"{horizon} sessions ahead",
                    "timeframe": resolved_timeframe,
                    "target_timestamp": run["target_timestamp"],
                    "forecast": {
                        "low": round(run["low"], 2),
                        "median": round(run["median"], 2),
                        "high": round(run["high"], 2),
                        "confidence_level": round(confidence_level, 2),
                        "label": f"{int(confidence_level * 100)}% interval",
                        "currency": "INR",
                        "direction": run["direction"],
                    },
                    "forecast_status": run["forecast_status"],
                    "support_state": run["forecast_status"],
                    "model_supported": run["forecast_status"] == "model_supported",
                    "low_data": run["low_data"],
                    "low_data_branch": run["low_data_branch"],
                    "low_utility": run["low_utility"],
                    "abstained": run["abstained"],
                    "abstention_reason": run["abstention_reason"],
                    "assessment": run["assessment"],
                    "evidence": {"grade": run["sufficiency"].evidence_grade},
                    "validation": {
                        **run["metrics"].to_dict(),
                        "beats_naive_baseline": bool(run["model_mae"] < run["naive_mae"]),
                        "mae_improvement_vs_naive_pct": round(((run["naive_mae"] - run["model_mae"]) / run["naive_mae"]) * 100.0, 3) if run["naive_mae"] > 0 else 0.0,
                    },
                    "width_pct": round(run["range_width_pct"] * 100.0, 4) if math.isfinite(run["range_width_pct"]) else None,
                }
                for horizon, run in sorted(runs.items())
            ],
            "unavailable": unavailable,
        },
        "explainability": explainability,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Research and paper-trading simulation only; not investment advice.",
    }

    payload["next_day_evidence"] = {
        "available": 1 in runs and resolved_timeframe == "1D",
        "published_model": "existing_direct_horizon_pipeline",
        "specialist_status": "research_only_pending_next_day_promotion",
        "evidence_grade": runs[1]["sufficiency"].evidence_grade if 1 in runs and resolved_timeframe == "1D" else "none",
        "calibration": runs[1]["metrics"].to_dict() if 1 in runs and resolved_timeframe == "1D" else None,
        "basis": "Untouched horizon=1 test fold; intraday next-bar evidence is not next-session evidence.",
    }

    # v14 context is optional and additive. Provider calls only occur when a
    # caller explicitly supplies a loader; ordinary forecasts remain offline.
    context = collect_context(
        symbol=symbol,
        origin=canonical.index[-1],
        timeframe=resolved_timeframe,
        window=training_window,
        market_data=market_data,
        vix_data=vix_data,
        history_loader=context_history_loader,
        ipo_info=ipo_info,
        peer_universe=peer_universe,
        timeout_seconds=context_timeout_seconds,
    )
    market_input = context["inputs"]["market_index"]
    vix_input = context["inputs"]["india_vix"]
    fno_input = context["inputs"]["fno"]
    market_frame = market_input.value if market_input.status == "available" else None
    vix_level = None
    if vix_input.status == "available" and isinstance(vix_input.value, pd.DataFrame) and "Close" in vix_input.value:
        try:
            vix_level = float(vix_input.value["Close"].iloc[-1])
        except (TypeError, ValueError, IndexError):
            vix_level = None
    if vix_level is None:
        vix_level = float(primary["recent_sigma"] / primary["current_price"] * 100.0) if primary["current_price"] > 0 else None
        vix_input.status = "fallback"
        vix_input.value = vix_level
        vix_input.source = "realized_volatility_proxy"
        vix_input.as_of = latest_timestamp
        vix_input.reason = "India VIX unavailable; using trailing realized volatility proxy."
        context["lineage"]["india_vix"] = {k: v for k, v in vix_input.to_dict().items() if k != "value"}
        context["metrics"]["degraded"] = int(context["metrics"].get("degraded", 0)) + 1

    # ACI is read-only here. Its sole mutation API requires an official,
    # automatically settled, non-stale, non-demo realized outcome. A failed
    # read degrades only these two fields, never the forecast itself.
    try:
        payload["aci_state"] = read_aci_state(symbol, resolved_timeframe, primary_h, confidence_level)
        has_residuals = "cal_residuals" in primary and primary["cal_residuals"] is not None
        payload["cqr"] = cqr_canary_status(cqr_promotion_receipt, symbol=symbol, has_residuals=has_residuals)
    except Exception as exc:  # noqa: BLE001 - optional context must not break the core forecast
        LOGGER.error(
            "ACI/CQR status read failed; publishing degraded placeholders (%s: %s)",
            type(exc).__name__,
            exc,
            extra={"symbol": symbol, "timeframe": resolved_timeframe},
        )
        payload["aci_state"] = {"status": "unavailable", "reason": type(exc).__name__}
        payload["cqr"] = {
            "status": "unavailable",
            "reason": type(exc).__name__,
            "receipt_valid": False,
            "canary_assigned": False,
            "determines_published_bounds": False,
        }
    payload["context_inputs"] = context["lineage"]
    payload["context_metrics"] = context["metrics"]
    payload["lineage"] = {
        "schema_version": context["schema_version"],
        "forecast_origin": latest_timestamp,
        "feature_timestamp": feature_timestamp,
        "provider": str(data.attrs.get("provider") or data.attrs.get("source") or "caller_supplied"),
        "provider_context": dict(data.attrs.get("context") or {}),
        "lookahead_policy": "all contextual time series truncated at or before forecast_origin",
        "context": context["lineage"],
    }

    # v13 research enrichments remain best-effort, but receive genuine aligned
    # index/VIX/F&O facts where available. CQR/ACI fields are overwritten below
    # so placeholders can never imply production activation.
    try:
        enhanced = _v13_enhance_forecast(
            payload,
            canonical,
            enriched,
            symbol,
            confidence_level,
            training_window,
            resolved_timeframe,
            market_data=market_frame,
            vix_level=vix_level,
            is_fno=bool(fno_input.value) if fno_input.status == "available" else False,
            surveillance_status=None,
            ipo_info=ipo_info,
            peer_universe=peer_universe,
            history_loader=(lambda peer: context_history_loader(peer, resolved_timeframe, training_window)) if context_history_loader else None,
        )
        enhanced["aci_state"] = payload["aci_state"]
        # Preserve enhanced CQR info if it was actually applied (offsets_applied present)
        # otherwise fall back to the v14 integration status
        if "offsets_applied" not in enhanced.get("cqr", {}):
            enhanced["cqr"] = payload["cqr"]
        enhanced["context_inputs"] = payload["context_inputs"]
        enhanced["context_metrics"] = payload["context_metrics"]
        enhanced["lineage"] = payload["lineage"]
        from forecasting.live_decay import apply_tier_controls
        return apply_tier_controls(enhanced)
    except Exception as exc:
        # v13 enhancements are best-effort; never break the core forecast
        payload["enhancement_status"] = {"status": "degraded", "reason": type(exc).__name__}
        from forecasting.live_decay import apply_tier_controls
        return apply_tier_controls(payload)
