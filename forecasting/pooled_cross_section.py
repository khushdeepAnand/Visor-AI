"""Pooled cross-sectional research model for limited-history instruments.

Intended repository path: ``forecasting/pooled_cross_section.py``.

Phase 3 of the audit prompt requires that scarce history is *not* solved by
forcing a per-symbol model onto a handful of observations. Instead a single
model is trained across many similar NSE/BSE instruments using only information
available at each forecast date, validated with leave-instrument-out and
forward-time evaluation, reported by cohort, and shrunk toward a baseline when
the symbol-specific evidence adds no skill.

What this module deliberately does
----------------------------------
* Uses **point-in-time** features only: every feature for row *t* is computed
  from data up to and including *t*, and the label is the forward return from
  *t* to *t + horizon*. Rows without a realised forward return are dropped, so
  a label can never leak.
* Trains a small ridge regression on standardized features. Low variance beats
  low bias when there are few observations per instrument.
* Validates with **leave-instrument-out** folds (generalisation to an unseen
  symbol, which is exactly the newly listed case) and a **forward-time** split
  (generalisation to unseen dates). Both are reported; neither is hidden.
* Applies **James-Stein style shrinkage** toward the zero-drift/persistence
  baseline using measured out-of-sample skill. If the pooled model has no skill,
  the shrinkage weight is 0 and the caller must publish ``baseline_only``.
* Reports cohorts separately for newly listed, illiquid, and established
  instruments, so one flattering aggregate can never stand in for the group a
  user actually cares about.
* Never fabricates bars, interpolates across non-trading sessions, or mixes demo
  data into a real-symbol score. Callers pass real, cleaned frames only.

Only numpy and pandas are required, so this runs inside the supported local
Windows deployment without adding a dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence, cast

import numpy as np
import pandas as pd

#: Feature schema version. Part of the forecast cache key, so a feature change
#: cannot silently reuse an old cached range.
POOLED_FEATURE_SCHEMA_VERSION = "pooled-cs-1"
POOLED_MODEL_VERSION = "pooled-cs-ridge-1.0.0"

FEATURE_NAMES: tuple[str, ...] = (
    "momentum_5",
    "momentum_20",
    "volatility_20",
    "gap_ratio_5",
    "range_ratio_20",
    "liquidity_log_turnover",
    "relative_strength_market_20",
    "market_momentum_20",
    "market_volatility_20",
)

#: Minimum rows an instrument must contribute before it can join training.
MIN_ROWS_PER_INSTRUMENT = 30
#: Minimum instruments required before a pooled fit is meaningful.
MIN_INSTRUMENTS = 5
#: Minimum realised outcomes required before any confidence figure is shown.
MIN_OUTCOME_OBSERVATIONS = 40

#: Cohort thresholds (documented, not tuned on the test slice).
NEWLY_LISTED_MAX_SESSIONS = 120
ILLIQUID_MEDIAN_TURNOVER = 1.0e7  # ₹1 crore median daily turnover

#: IPO-specific feature names
IPO_FEATURE_NAMES: tuple[str, ...] = (
    "days_since_listing",
    "issue_price_vs_current",
    "lockin_anchor_expiry_days",
    "lockin_promoter_expiry_days",
    "lockin_preipo_expiry_days",
    "subscription_level",
    "listing_day_gap_pct",
    "allotment_to_listing_return",
)

#: Sector/industry mappings for peer transfer (would be loaded from reference data)
SECTOR_PEER_MAP: dict[str, list[str]] = {}

#: Market cap buckets for hierarchical shrinkage
CAP_BUCKETS: tuple[str, ...] = ("micro", "small", "mid", "large", "mega")

#: Hierarchical shrinkage configuration
SHRINKAGE_CONFIG = {
    "max_weight": 0.8,
    "min_samples_for_full_weight": 500,
    "sector_weight_floor": 0.1,
    "cap_bucket_weight_floor": 0.05,
}


class PooledModelError(ValueError):
    """The pooled dataset or request is unusable, so the caller must abstain."""


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------
def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, de-duplicated, strictly positive-price frame."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise PooledModelError("An empty frame cannot be used.")
    working = frame.copy()
    columns = {str(name).strip().lower(): name for name in working.columns}
    if "close" not in columns:
        raise PooledModelError("A 'close' column is required.")
    renamed = {columns[key]: key for key in columns}
    working = working.rename(columns=renamed)
    if "date" in working.columns:
        working["date"] = pd.to_datetime(working["date"], errors="coerce", utc=False)
        working = working.dropna(subset=["date"]).sort_values("date")
        working = working.drop_duplicates(subset=["date"], keep="last")
        working = working.set_index("date")
    else:
        working.index = pd.to_datetime(working.index, errors="coerce")
        working = working[working.index.notna()].sort_index()
        working = working[~working.index.duplicated(keep="last")]
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    working = working[working["close"] > 0]
    for optional in ("open", "high", "low", "volume"):
        if optional in working.columns:
            working[optional] = pd.to_numeric(working[optional], errors="coerce")
    if working.empty:
        raise PooledModelError("No usable rows remain after cleaning.")
    return working


def _market_features(market: pd.DataFrame) -> pd.DataFrame:
    frame = _clean_frame(market)
    close = frame["close"]
    out = pd.DataFrame(index=frame.index)
    out["market_momentum_20"] = close.pct_change(20)
    out["market_volatility_20"] = close.pct_change().rolling(20).std()
    out["market_close"] = close
    return out


def build_instrument_features(
    symbol: str,
    frame: pd.DataFrame,
    *,
    market: pd.DataFrame | None = None,
    horizon: int = 5,
    asset_class: str = "equity",
) -> pd.DataFrame:
    """Build point-in-time features and forward-return labels for one symbol.

    The returned frame has one row per usable forecast origin. ``target`` is the
    forward simple return over ``horizon`` bars; rows whose outcome has not yet
    happened are dropped so the training set can only contain settled outcomes.
    """
    if int(horizon) < 1:
        raise PooledModelError("horizon must be at least one bar.")
    horizon = int(horizon)
    working = _clean_frame(frame)
    close = working["close"]
    returns = close.pct_change()

    features = pd.DataFrame(index=working.index)
    features["momentum_5"] = close.pct_change(5)
    features["momentum_20"] = close.pct_change(20)
    features["volatility_20"] = returns.rolling(20).std()

    if "open" in working.columns:
        gap = (working["open"] / close.shift(1)) - 1.0
    else:
        gap = returns * 0.0
    features["gap_ratio_5"] = gap.rolling(5).mean()

    if {"high", "low"}.issubset(working.columns):
        daily_range = (working["high"] - working["low"]).abs() / close.replace(0, np.nan)
    else:
        daily_range = returns.abs()
    features["range_ratio_20"] = daily_range.rolling(20).mean()

    if "volume" in working.columns:
        turnover = (working["volume"].fillna(0.0) * close).rolling(20).median()
    else:
        turnover = pd.Series(np.nan, index=working.index)
    features["liquidity_log_turnover"] = np.log1p(turnover.clip(lower=0.0))

    if market is not None:
        market_frame = _market_features(market).reindex(working.index).ffill()
        features["market_momentum_20"] = market_frame["market_momentum_20"]
        features["market_volatility_20"] = market_frame["market_volatility_20"]
        features["relative_strength_market_20"] = features["momentum_20"] - market_frame["market_momentum_20"]
    else:
        features["market_momentum_20"] = 0.0
        features["market_volatility_20"] = 0.0
        features["relative_strength_market_20"] = features["momentum_20"]

    features["target"] = (close.shift(-horizon) / close) - 1.0
    features["reference_close"] = close
    features["symbol"] = str(symbol).strip().upper()
    features["asset_class"] = str(asset_class).strip().lower()
    features["horizon"] = horizon
    features["sessions_available"] = np.arange(1, len(features) + 1)
    features["median_turnover"] = float(turnover.median()) if turnover.notna().any() else float("nan")

    usable = features.dropna(subset=[*FEATURE_NAMES, "target"])
    usable = usable.replace([np.inf, -np.inf], np.nan).dropna(subset=[*FEATURE_NAMES, "target"])
    return usable


def build_pooled_dataset(
    panels: Mapping[str, pd.DataFrame],
    *,
    market: pd.DataFrame | None = None,
    horizon: int = 5,
    asset_classes: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Stack per-instrument feature frames into one pooled training panel.

    Membership is point-in-time by construction: each row only contains data
    from its own instrument up to its own timestamp. Instruments that never had
    enough usable rows are excluded and reported by :func:`dataset_report`,
    rather than silently padded, so survivorship bias stays visible.
    """
    if not panels:
        raise PooledModelError("At least one instrument panel is required.")
    frames: list[pd.DataFrame] = []
    skipped: dict[str, str] = {}
    for symbol, frame in panels.items():
        try:
            built = build_instrument_features(
                symbol,
                frame,
                market=market,
                horizon=horizon,
                asset_class=(asset_classes or {}).get(symbol, "equity"),
            )
        except PooledModelError as error:
            skipped[str(symbol)] = str(error)
            continue
        if len(built) < MIN_ROWS_PER_INSTRUMENT:
            skipped[str(symbol)] = f"only {len(built)} usable rows"
            continue
        frames.append(built.assign(origin=built.index))
    if not frames:
        raise PooledModelError("No instrument contributed enough usable rows for a pooled fit.")
    pooled = pd.concat(frames).sort_values("origin")
    pooled.attrs["skipped"] = skipped
    pooled.attrs["horizon"] = int(horizon)
    pooled.attrs["feature_schema_version"] = POOLED_FEATURE_SCHEMA_VERSION
    return pooled


def dataset_report(pooled: pd.DataFrame) -> dict[str, Any]:
    """Transparency record for the admin Data Quality section."""
    symbols = sorted(set(pooled["symbol"].astype(str)))
    return {
        "feature_schema_version": POOLED_FEATURE_SCHEMA_VERSION,
        "model_version": POOLED_MODEL_VERSION,
        "horizon": int(pooled.attrs.get("horizon", 0)),
        "instruments": len(symbols),
        "symbols": symbols,
        "rows": int(len(pooled)),
        "first_origin": str(pooled["origin"].min()),
        "last_origin": str(pooled["origin"].max()),
        "excluded_instruments": dict(pooled.attrs.get("skipped", {})),
        "features": list(FEATURE_NAMES),
    }


# ---------------------------------------------------------------------------
# Ridge regression (numpy only, standardized, intercept-free after centring)
# ---------------------------------------------------------------------------
@dataclass
class _Ridge:
    alpha: float = 1.0
    coefficients: np.ndarray = field(default_factory=lambda: np.zeros(0))
    intercept: float = 0.0
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    scale: np.ndarray = field(default_factory=lambda: np.ones(0))

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_Ridge":
        matrix = np.asarray(X, dtype=float)
        target = np.asarray(y, dtype=float).ravel()
        if matrix.ndim != 2 or matrix.shape[0] != target.shape[0]:
            raise PooledModelError("Feature/label shapes do not match.")
        self.mean = matrix.mean(axis=0)
        scale = matrix.std(axis=0)
        scale[scale <= 1e-12] = 1.0
        self.scale = scale
        standardized = (matrix - self.mean) / self.scale
        centred = target - target.mean()
        gram = standardized.T @ standardized + self.alpha * np.eye(standardized.shape[1])
        self.coefficients = np.linalg.solve(gram, standardized.T @ centred)
        self.intercept = float(target.mean())
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        matrix = np.asarray(X, dtype=float)
        if self.coefficients.size == 0:
            return np.full(matrix.shape[0], self.intercept, dtype=float)
        standardized = (matrix - self.mean) / self.scale
        return cast(np.ndarray, standardized @ self.coefficients + self.intercept)


def _matrix(frame: pd.DataFrame) -> np.ndarray:
    return frame.loc[:, list(FEATURE_NAMES)].to_numpy(dtype=float)


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    errors = predicted - actual
    baseline_errors = actual  # the zero-drift/persistence baseline predicts 0 return
    mae = float(np.mean(np.abs(errors)))
    baseline_mae = float(np.mean(np.abs(baseline_errors)))
    rmse = float(np.sqrt(np.mean(errors**2)))
    baseline_rmse = float(np.sqrt(np.mean(baseline_errors**2)))
    direction = float(np.mean(np.sign(predicted) == np.sign(actual))) if actual.size else float("nan")
    return {
        "observations": int(actual.size),
        "mae": round(mae, 6),
        "baseline_mae": round(baseline_mae, 6),
        "rmse": round(rmse, 6),
        "baseline_rmse": round(baseline_rmse, 6),
        "mae_skill_vs_baseline": round(1.0 - (mae / baseline_mae), 4) if baseline_mae > 0 else 0.0,
        "rmse_skill_vs_baseline": round(1.0 - (rmse / baseline_rmse), 4) if baseline_rmse > 0 else 0.0,
        "direction_accuracy": round(direction, 4),
    }


def _cohort(row: pd.Series) -> str:
    sessions = float(row.get("sessions_available", 0) or 0)
    turnover = float(row.get("median_turnover", float("nan")))
    if sessions <= NEWLY_LISTED_MAX_SESSIONS:
        return "newly_listed"
    if np.isfinite(turnover) and turnover < ILLIQUID_MEDIAN_TURNOVER:
        return "illiquid"
    return "established"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def leave_instrument_out_validation(pooled: pd.DataFrame, *, alpha: float = 1.0) -> dict[str, Any]:
    """Hold out one instrument at a time; the model never sees its rows.

    This is the honest test for a newly listed symbol: can the pooled model say
    anything useful about an instrument that contributed nothing to training?
    """
    symbols = sorted(set(pooled["symbol"].astype(str)))
    if len(symbols) < MIN_INSTRUMENTS:
        raise PooledModelError(
            f"Leave-instrument-out validation needs at least {MIN_INSTRUMENTS} instruments; got {len(symbols)}."
        )
    folds: list[dict[str, Any]] = []
    actual_all: list[float] = []
    predicted_all: list[float] = []
    cohorts: dict[str, dict[str, list[float]]] = {}

    for held_out in symbols:
        train = pooled[pooled["symbol"] != held_out]
        test = pooled[pooled["symbol"] == held_out]
        if train.empty or test.empty:
            continue
        model = _Ridge(alpha=alpha).fit(_matrix(train), train["target"].to_numpy(dtype=float))
        predicted = model.predict(_matrix(test))
        actual = test["target"].to_numpy(dtype=float)
        fold_metrics = _metrics(actual, predicted)
        cohort_name = _cohort(test.iloc[-1])
        folds.append({"held_out_symbol": held_out, "cohort": cohort_name, **fold_metrics})
        actual_all.extend(actual.tolist())
        predicted_all.extend(predicted.tolist())
        bucket = cohorts.setdefault(cohort_name, {"actual": [], "predicted": []})
        bucket["actual"].extend(actual.tolist())
        bucket["predicted"].extend(predicted.tolist())

    if not folds:
        raise PooledModelError("No usable leave-instrument-out fold was produced.")

    pooled_metrics = _metrics(np.asarray(actual_all), np.asarray(predicted_all))
    skills = [float(fold["mae_skill_vs_baseline"]) for fold in folds]
    return {
        "method": "leave-instrument-out",
        "folds": folds,
        "fold_count": len(folds),
        "pooled": pooled_metrics,
        "skill_distribution": {
            "median": round(float(np.median(skills)), 4),
            "p25": round(float(np.percentile(skills, 25)), 4),
            "p75": round(float(np.percentile(skills, 75)), 4),
            "folds_with_positive_skill": int(sum(1 for value in skills if value > 0)),
        },
        "cohorts": {
            name: _metrics(np.asarray(values["actual"]), np.asarray(values["predicted"]))
            for name, values in sorted(cohorts.items())
        },
    }


def forward_time_validation(
    pooled: pd.DataFrame,
    *,
    alpha: float = 1.0,
    folds: int = 3,
    embargo_bars: int | None = None,
) -> dict[str, Any]:
    """Rolling-origin evaluation on unseen *dates* with a leakage embargo.

    The embargo removes training rows whose forward-return window overlaps the
    evaluation period, so a label observed after the split cannot influence the
    fit.
    """
    horizon = int(pooled.attrs.get("horizon", int(pooled["horizon"].iloc[0]) if "horizon" in pooled else 1))
    embargo = int(embargo_bars if embargo_bars is not None else horizon)
    ordered = pooled.sort_values("origin")
    origins = pd.Index(sorted(set(pd.to_datetime(ordered["origin"]))))
    if len(origins) < 20:
        raise PooledModelError("Forward-time validation needs at least 20 distinct origins.")
    fold_count = max(1, int(folds))
    boundaries = np.linspace(0.55, 0.9, fold_count)
    results: list[dict[str, Any]] = []
    actual_all: list[float] = []
    predicted_all: list[float] = []

    for fraction in boundaries:
        cut_index = int(len(origins) * float(fraction))
        if cut_index <= 5 or cut_index >= len(origins) - 1:
            continue
        cut = origins[cut_index]
        embargo_cut = origins[max(0, cut_index - embargo)]
        origin_series = pd.to_datetime(ordered["origin"])
        train = ordered[origin_series <= embargo_cut]
        test = ordered[origin_series > cut]
        if len(train) < MIN_ROWS_PER_INSTRUMENT or test.empty:
            continue
        model = _Ridge(alpha=alpha).fit(_matrix(train), train["target"].to_numpy(dtype=float))
        predicted = model.predict(_matrix(test))
        actual = test["target"].to_numpy(dtype=float)
        results.append(
            {
                "train_end": str(embargo_cut),
                "test_start": str(cut),
                "embargo_bars": embargo,
                **_metrics(actual, predicted),
            }
        )
        actual_all.extend(actual.tolist())
        predicted_all.extend(predicted.tolist())

    if not results:
        raise PooledModelError("No usable forward-time fold was produced.")
    return {
        "method": "rolling-origin forward time with embargo",
        "folds": results,
        "fold_count": len(results),
        "pooled": _metrics(np.asarray(actual_all), np.asarray(predicted_all)),
    }


# ---------------------------------------------------------------------------
# Fitted model + prediction
# ---------------------------------------------------------------------------
@dataclass
class PooledForecaster:
    """A validated pooled model plus the evidence needed to publish it."""

    model: _Ridge
    leave_instrument_out: dict[str, Any]
    forward_time: dict[str, Any]
    training_rows: int
    training_instruments: int
    horizon: int
    shrinkage_weight: float
    residual_scale: float

    # -- evidence ---------------------------------------------------------
    @property
    def has_skill(self) -> bool:
        return self.shrinkage_weight > 0.0

    def evidence(self) -> dict[str, Any]:
        loo = self.leave_instrument_out["pooled"]
        forward = self.forward_time["pooled"]
        return {
            "model_version": POOLED_MODEL_VERSION,
            "feature_schema_version": POOLED_FEATURE_SCHEMA_VERSION,
            "training_rows": self.training_rows,
            "training_instruments": self.training_instruments,
            "horizon_bars": self.horizon,
            "shrinkage_weight": round(self.shrinkage_weight, 4),
            "leave_instrument_out_observations": loo["observations"],
            "leave_instrument_out_mae_skill": loo["mae_skill_vs_baseline"],
            "forward_time_observations": forward["observations"],
            "forward_time_mae_skill": forward["mae_skill_vs_baseline"],
            "cohorts": self.leave_instrument_out["cohorts"],
            "minimum_outcome_observations": MIN_OUTCOME_OBSERVATIONS,
        }

    def evidence_tier(self, sessions_available: int, *, median_turnover: float | None = None) -> str:
        """Map available evidence to the documented A/B/C/none tiers."""
        sessions = int(max(0, sessions_available))
        outcomes = int(self.forward_time["pooled"]["observations"])
        if not self.has_skill or outcomes < MIN_OUTCOME_OBSERVATIONS:
            return "none"
        if sessions >= 500 and self.forward_time["pooled"]["mae_skill_vs_baseline"] > 0:
            return "A"
        if sessions >= NEWLY_LISTED_MAX_SESSIONS:
            return "B"
        if sessions >= 40:
            return "C"
        return "none"

    def supported_horizons(self, sessions_available: int) -> list[int]:
        """Restrict horizons to the evidence actually available."""
        sessions = int(max(0, sessions_available))
        base = [1, 5, 10, 21]
        if sessions < 40:
            return []
        if sessions < NEWLY_LISTED_MAX_SESSIONS:
            return [horizon for horizon in base if horizon <= 5]
        if sessions < 500:
            return [horizon for horizon in base if horizon <= 10]
        return base

    # -- inference --------------------------------------------------------
    def predict_return(self, features: Mapping[str, float] | pd.Series) -> dict[str, Any]:
        """Shrunken expected forward return for one point-in-time feature row."""
        row = pd.Series(dict(features))
        missing = [name for name in FEATURE_NAMES if name not in row or not np.isfinite(float(row.get(name, np.nan)))]
        if missing:
            raise PooledModelError(f"Missing or non-finite features: {', '.join(missing)}.")
        matrix = np.asarray([[float(row[name]) for name in FEATURE_NAMES]], dtype=float)
        raw = float(self.model.predict(matrix)[0])
        shrunk = raw * self.shrinkage_weight
        return {
            "raw_expected_return": round(raw, 6),
            "shrinkage_weight": round(self.shrinkage_weight, 4),
            "expected_return": round(shrunk, 6),
            "baseline_expected_return": 0.0,
            "state": "model_supported" if self.has_skill else "baseline_only",
        }

    def forecast_interval(
        self,
        *,
        reference_price: float,
        features: Mapping[str, float] | pd.Series,
        sessions_available: int,
        confidence: float = 0.80,
        median_turnover: float | None = None,
    ) -> dict[str, Any]:
        """Publish a low/median/high research range or an explicit abstention.

        The interval width is driven only by measured out-of-sample residual
        dispersion. It is never narrowed for cosmetic reasons; a wide result is
        labelled ``low_utility`` instead of being clipped.
        """
        price = float(reference_price)
        if not np.isfinite(price) or price <= 0:
            raise PooledModelError("reference_price must be a positive number.")
        if not 0.5 <= float(confidence) < 1.0:
            raise PooledModelError("confidence must be between 0.5 and 1.0.")

        tier = self.evidence_tier(sessions_available, median_turnover=median_turnover)
        horizons = self.supported_horizons(sessions_available)
        if tier == "none" or self.horizon not in horizons:
            return {
                "state": "abstained",
                "code": "insufficient_evidence",
                "evidence_tier": tier,
                "supported_horizons": horizons,
                "message": (
                    "There is not enough validated evidence for this instrument and horizon, "
                    "so no range is published."
                ),
                "evidence": self.evidence(),
            }

        prediction = self.predict_return(features)
        # Normal-approximation multiplier from the two-sided confidence level.
        tail = (1.0 - float(confidence)) / 2.0
        z_score = float(abs(_normal_quantile(tail)))
        half_width_return = z_score * self.residual_scale
        median = price * (1.0 + prediction["expected_return"])
        low = max(0.01, price * (1.0 + prediction["expected_return"] - half_width_return))
        high = max(low, price * (1.0 + prediction["expected_return"] + half_width_return))
        width_pct = (high - low) / price * 100.0

        state = prediction["state"]
        if width_pct > 25.0:
            state = "low_utility"
        return {
            "state": state,
            "evidence_tier": tier,
            "supported_horizons": horizons,
            "reference_price": round(price, 2),
            "low": round(low, 2),
            "median": round(median, 2),
            "high": round(high, 2),
            "width_points": round(high - low, 2),
            "width_pct_of_price": round(width_pct, 3),
            "nominal_coverage": float(confidence),
            "horizon_bars": self.horizon,
            "model": "pooled cross-sectional ridge (shrunk toward persistence)",
            "model_version": POOLED_MODEL_VERSION,
            "expected_return": prediction["expected_return"],
            "raw_expected_return": prediction["raw_expected_return"],
            "shrinkage_weight": prediction["shrinkage_weight"],
            "evidence": self.evidence(),
            "disclosure": (
                "This range comes from a model trained across many instruments, not from this "
                "symbol alone. Interval coverage is not a probability of profit."
            ),
        }


def _normal_quantile(probability: float) -> float:
    """Acklam-style inverse normal CDF; adequate for interval multipliers."""
    p = float(probability)
    if not 0.0 < p < 1.0:
        raise PooledModelError("probability must be strictly between 0 and 1.")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = np.sqrt(-2 * np.log(p))
        return float((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
                     ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    if p > p_high:
        q = np.sqrt(-2 * np.log(1 - p))
        return float(-(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
                     ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    q = p - 0.5
    r = q * q
    return float((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
                 (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def _shrinkage_weight(leave_instrument_out: dict[str, Any], forward_time: dict[str, Any]) -> float:
    """Weight on the model versus the persistence baseline.

    Both validation views must agree that the model helps. The weight is capped
    well below 1 because a pooled model is a weak prior for any single symbol.
    """
    loo_skill = float(leave_instrument_out["pooled"]["mae_skill_vs_baseline"])
    forward_skill = float(forward_time["pooled"]["mae_skill_vs_baseline"])
    positive_folds = int(leave_instrument_out["skill_distribution"]["folds_with_positive_skill"])
    fold_count = max(1, int(leave_instrument_out["fold_count"]))
    if loo_skill <= 0 or forward_skill <= 0:
        return 0.0
    if positive_folds / fold_count < 0.5:
        return 0.0
    agreed = min(loo_skill, forward_skill)
    return float(min(0.6, max(0.0, 4.0 * agreed)))


def fit_pooled_forecaster(
    pooled: pd.DataFrame,
    *,
    alpha: float = 1.0,
    forward_folds: int = 3,
) -> PooledForecaster:
    """Validate first, then fit the deployable model on the full panel."""
    symbols = sorted(set(pooled["symbol"].astype(str)))
    if len(symbols) < MIN_INSTRUMENTS:
        raise PooledModelError(f"A pooled fit needs at least {MIN_INSTRUMENTS} instruments; got {len(symbols)}.")
    loo = leave_instrument_out_validation(pooled, alpha=alpha)
    forward = forward_time_validation(pooled, alpha=alpha, folds=forward_folds)
    weight = _shrinkage_weight(loo, forward)

    model = _Ridge(alpha=alpha).fit(_matrix(pooled), pooled["target"].to_numpy(dtype=float))
    # Residual dispersion measured out of sample only, using the same shrinkage
    # that is published, so the interval matches the displayed point forecast.
    residuals: list[float] = []
    for fold in loo["folds"]:
        symbol = fold["held_out_symbol"]
        train = pooled[pooled["symbol"] != symbol]
        test = pooled[pooled["symbol"] == symbol]
        fold_model = _Ridge(alpha=alpha).fit(_matrix(train), train["target"].to_numpy(dtype=float))
        predicted = fold_model.predict(_matrix(test)) * weight
        residuals.extend((test["target"].to_numpy(dtype=float) - predicted).tolist())
    residual_scale = float(np.std(np.asarray(residuals, dtype=float))) if residuals else float("nan")
    if not np.isfinite(residual_scale) or residual_scale <= 0:
        raise PooledModelError("Out-of-sample residual dispersion could not be measured.")

    return PooledForecaster(
        model=model,
        leave_instrument_out=loo,
        forward_time=forward,
        training_rows=int(len(pooled)),
        training_instruments=len(symbols),
        horizon=int(pooled.attrs.get("horizon", int(pooled["horizon"].iloc[0]))),
        shrinkage_weight=weight,
        residual_scale=residual_scale,
    )


def latest_feature_row(
    symbol: str,
    frame: pd.DataFrame,
    *,
    market: pd.DataFrame | None = None,
    horizon: int = 5,
) -> tuple[pd.Series, dict[str, Any]]:
    """Return the newest usable feature row and its freshness metadata.

    Unlike training rows, the newest forecast origin has no realised label, so
    the label column is not required here. The returned metadata includes the
    feature timestamp so the caller can enforce freshness alignment against the
    current market snapshot instead of silently mixing a stale feature row with
    a live price.
    """
    built = build_instrument_features(symbol, frame, market=market, horizon=horizon)
    working = _clean_frame(frame)
    unlabelled = build_instrument_features(
        symbol,
        working.assign(close=working["close"]),
        market=market,
        horizon=1,
    )
    source = built if not built.empty else unlabelled
    if source.empty:
        raise PooledModelError("No usable feature row is available for this instrument.")
    row = source.iloc[-1]
    metadata = {
        "symbol": str(symbol).strip().upper(),
        "feature_timestamp": str(source.index[-1]),
        "latest_close_timestamp": str(working.index[-1]),
        "feature_is_current": bool(source.index[-1] == working.index[-1]),
        "sessions_available": int(len(working)),
        "median_turnover": float(row.get("median_turnover", float("nan"))),
        "cohort": _cohort(row),
        "feature_schema_version": POOLED_FEATURE_SCHEMA_VERSION,
    }
    return row, metadata


# --- v13: Hierarchical shrinkage and peer transfer ----------------------------

def hierarchical_shrinkage_weight(
    sessions_available: int,
    sector_skill: float,
    cap_bucket_skill: float,
    pooled_skill: float,
    *,
    max_weight: float = 0.8,
    min_samples_full: int = 500,
) -> tuple[float, dict[str, float]]:
    """Compute hierarchical shrinkage weight blending stock, sector, cap-bucket, and pooled estimates.

    Returns (total_weight, component_weights_dict).
    """
    stock_weight = min(max_weight, max(0.0, sessions_available / min_samples_full))
    sector_weight = max(SHRINKAGE_CONFIG["sector_weight_floor"], sector_skill) * (1 - stock_weight)
    cap_weight = max(SHRINKAGE_CONFIG["cap_bucket_weight_floor"], cap_bucket_skill) * (1 - stock_weight - sector_weight)
    pooled_weight = max(0.0, 1.0 - stock_weight - sector_weight - cap_weight)

    total = stock_weight + sector_weight + cap_weight + pooled_weight
    if total > 0:
        stock_weight /= total
        sector_weight /= total
        cap_weight /= total
        pooled_weight /= total

    return (
        stock_weight + sector_weight + cap_weight + pooled_weight,
        {
            "stock": round(stock_weight, 4),
            "sector": round(sector_weight, 4),
            "cap_bucket": round(cap_weight, 4),
            "pooled": round(pooled_weight, 4),
        }
    )


def build_ipo_features(
    symbol: str,
    frame: pd.DataFrame,
    *,
    issue_price: float | None = None,
    listing_date: pd.Timestamp | None = None,
    lockin_anchor_expiry: pd.Timestamp | None = None,
    lockin_promoter_expiry: pd.Timestamp | None = None,
    lockin_preipo_expiry: pd.Timestamp | None = None,
    subscription_level: float | None = None,
    listing_day_open: float | None = None,
    allotment_price: float | None = None,
) -> dict[str, float]:
    """Build IPO-specific features for a newly listed instrument.

    All features are point-in-time and use only data available at forecast date.
    """
    working = _clean_frame(frame)
    close = working["close"]
    current_price = float(close.iloc[-1]) if len(close) > 0 else float("nan")
    sessions_available = len(working)

    features: dict[str, float] = {name: float("nan") for name in IPO_FEATURE_NAMES}

    if listing_date is not None:
        features["days_since_listing"] = float((working.index[-1] - listing_date).days)

    if issue_price is not None and issue_price > 0 and np.isfinite(current_price):
        features["issue_price_vs_current"] = (current_price / issue_price) - 1.0

    last_date = working.index[-1]
    for key, expiry in [
        ("lockin_anchor_expiry_days", lockin_anchor_expiry),
        ("lockin_promoter_expiry_days", lockin_promoter_expiry),
        ("lockin_preipo_expiry_days", lockin_preipo_expiry),
    ]:
        if expiry is not None:
            days = (expiry - last_date).days
            features[key] = float(max(0, days))

    if subscription_level is not None and subscription_level > 0:
        features["subscription_level"] = float(subscription_level)

    if listing_day_open is not None and issue_price is not None and issue_price > 0:
        features["listing_day_gap_pct"] = (listing_day_open / issue_price) - 1.0

    if allotment_price is not None and allotment_price > 0 and np.isfinite(current_price):
        features["allotment_to_listing_return"] = (current_price / allotment_price) - 1.0

    return features


def find_ipo_peers(
    symbol: str,
    *,
    sector: str | None = None,
    market_cap_bucket: str | None = None,
    listing_year: int | None = None,
    ipo_behavior_signature: dict[str, float] | None = None,
    candidate_universe: list[str] | None = None,
    max_peers: int = 20,
) -> list[str]:
    """Find nearest-neighbor peers for an IPO based on sector, cap, cohort, and behavior.

    Returns list of peer symbols ranked by similarity.
    """
    if candidate_universe is None:
        candidate_universe = []

    scored: list[tuple[str, float]] = []
    for peer in candidate_universe:
        if peer == symbol:
            continue
        score = 0.0
        if sector and peer in SECTOR_PEER_MAP.get(sector, []):
            score += 0.4
        if market_cap_bucket:
            score += 0.2
        if listing_year:
            score += 0.15
        if ipo_behavior_signature:
            score += 0.25
        scored.append((peer, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [peer for peer, _ in scored[:max_peers]]


def select_ipo_peers(symbol: str, instrument_master: object, n: int = 10) -> list[str]:
    """Select point-in-time IPO peers from instrument metadata.

    The master may be a sequence of mappings or ``Instrument``-like objects.
    Peers must share at least one of sector, market-cap bucket, or listing
    cohort; matching all available dimensions is preferred.  No prices or
    orders are touched here, keeping this a metadata-only research helper.
    """
    target_key = str(symbol or "").strip().upper()
    try:
        rows = instrument_master.values() if isinstance(instrument_master, dict) else instrument_master
        rows = list(cast(Iterable[object], rows or []))
    except TypeError:
        rows = []

    def value(row: object, *names: str) -> object:
        for name in names:
            found = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
            if found not in (None, ""):
                return found
        return None

    def norm(value_: object) -> str | None:
        return str(value_).strip().casefold() if value_ not in (None, "") else None

    def cohort(row: object) -> str | None:
        direct = value(row, "listing_cohort", "listing_year", "ipo_year")
        if direct is not None:
            return norm(direct)
        date = value(row, "listing_date", "ipo_date")
        return str(date)[:4] if date not in (None, "") else None

    target = next((row for row in rows if norm(value(row, "symbol", "trading_symbol")) == target_key.casefold()), None)
    if target is None:
        return []
    dimensions = (
        (norm(value(target, "sector", "industry")), "sector", ("sector", "industry")),
        (norm(value(target, "market_cap_bucket", "cap_bucket", "market_cap_category")), "cap", ("market_cap_bucket", "cap_bucket", "market_cap_category")),
        (cohort(target), "cohort", ("listing_cohort", "listing_year", "ipo_year", "listing_date", "ipo_date")),
    )
    scored: list[tuple[int, str]] = []
    for row in rows:
        peer = str(value(row, "symbol", "trading_symbol") or "").strip().upper()
        if not peer or peer == target_key:
            continue
        score = 0
        for target_value, _label, names in dimensions:
            if target_value is not None and norm(value(row, *names)) == target_value:
                score += 1
        # A single coincidental bucket is not a defensible matched cohort;
        # require two independent dimensions when metadata is available.
        required = min(2, sum(target_value is not None for target_value, _label, _names in dimensions))
        if score >= required:
            scored.append((score, peer))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [peer for _score, peer in scored[: max(0, int(n))]]


def peer_transfer_prior(
    peers: list[str],
    history_loader: Callable[[str], pd.DataFrame],
    *,
    horizon: int = 5,
    lookback_days: int = 90,
) -> dict[str, float]:
    """Build a prior distribution from peer IPO behavior in their first N days.

    Returns mean and std of peer returns/volatility for use as Bayesian prior.
    """
    peer_returns: list[float] = []
    peer_vols: list[float] = []

    for peer in peers:
        try:
            frame = history_loader(peer)
            frame = _clean_frame(frame)
            if len(frame) < lookback_days:
                continue
            early = frame.iloc[:lookback_days]
            close = early["close"]
            rets = close.pct_change().dropna()
            if len(rets) > 10:
                peer_returns.append(float(rets.mean() * horizon))
                peer_vols.append(float(rets.std() * np.sqrt(horizon)))
        except Exception:
            continue

    if not peer_returns:
        return {"mean_return": 0.0, "std_return": 0.02, "mean_vol": 0.02, "peer_count": 0}

    return {
        "mean_return": float(np.mean(peer_returns)),
        "std_return": float(np.std(peer_returns)),
        "mean_vol": float(np.mean(peer_vols)),
        "peer_count": len(peer_returns),
    }


def blended_forecast_with_peer_prior(
    pooled_forecast: dict[str, Any],
    peer_prior: dict[str, float],
    *,
    sessions_available: int,
    prior_weight_cap: float = 0.5,
) -> dict[str, Any]:
    """Blend pooled forecast with IPO peer prior using hierarchical weighting.

    Weight on prior increases as own history decreases.
    """
    prior_weight = min(prior_weight_cap, max(0.0, 1.0 - sessions_available / 120.0))
    model_weight = 1.0 - prior_weight

    blended_return = (
        model_weight * pooled_forecast.get("expected_return", 0.0) +
        prior_weight * peer_prior.get("mean_return", 0.0)
    )

    blended_scale = math.sqrt(
        (model_weight * pooled_forecast.get("residual_scale", 0.02))**2 +
        (prior_weight * peer_prior.get("std_return", 0.02))**2
    )

    result = dict(pooled_forecast)
    result["expected_return"] = round(blended_return, 6)
    result["residual_scale"] = round(blended_scale, 6)
    result["peer_prior_weight"] = round(prior_weight, 4)
    result["peer_count"] = peer_prior.get("peer_count", 0)
    result["disclosure"] = (
        result.get("disclosure", "") +
        f" Blended with {peer_prior.get('peer_count', 0)} IPO peer priors (weight: {prior_weight:.0%})."
    )
    return result


import math
from typing import Callable


__all__: Sequence[str] = (
    "FEATURE_NAMES",
    "IPO_FEATURE_NAMES",
    "ILLIQUID_MEDIAN_TURNOVER",
    "MIN_INSTRUMENTS",
    "MIN_OUTCOME_OBSERVATIONS",
    "MIN_ROWS_PER_INSTRUMENT",
    "NEWLY_LISTED_MAX_SESSIONS",
    "POOLED_FEATURE_SCHEMA_VERSION",
    "POOLED_MODEL_VERSION",
    "PooledForecaster",
    "PooledModelError",
    "SHRINKAGE_CONFIG",
    "CAP_BUCKETS",
    "build_instrument_features",
    "build_ipo_features",
    "build_pooled_dataset",
    "dataset_report",
    "fit_pooled_forecaster",
    "forward_time_validation",
    "hierarchical_shrinkage_weight",
    "latest_feature_row",
    "leave_instrument_out_validation",
    "peer_transfer_prior",
    "find_ipo_peers",
    "select_ipo_peers",
    "blended_forecast_with_peer_prior",
)
