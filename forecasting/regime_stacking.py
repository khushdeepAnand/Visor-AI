"""Regime-conditional stacking of forecast components.

``regime_router`` in :mod:`forecasting.regime_detection` produces per-regime
model weights, but nothing consumed them: the weights were published as
``regime_model_weights`` and then ignored, so every symbol was forecast as if
the regime label did not exist. This module turns those weights into an actual
blend.

Design constraints that shaped this implementation:

* **Dimensionally honest.** Only like-for-like quantities are stacked. Volatility
  forecasts expressed in percent are never averaged into a price forecast; the
  stack blends candidate *interval half-widths*, which are all in price units.
* **Leakage-safe.** Every input is computed from data already available at the
  bar being forecast. No future bar, realised outcome, or settled state is read.
* **Probability-shrunk.** A rule-based regime label is only ~0.6-0.9 confident
  (see ``detect_regime``), so raw regime weights are shrunk toward an equal
  weight mix by that probability. A low-confidence label therefore nudges the
  blend instead of dominating it.
* **Renormalized over what actually exists.** A weighted model that produced no
  usable component is dropped and the surviving weights renormalized, rather
  than silently contributing zero and biasing the blend downward.
* **Auditable.** Every call returns the weights used, each component's
  contribution, and the reason any component was dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from forecasting.regime_detection import MarketRegime

__all__: Sequence[str] = (
    "RegimeStack",
    "WalkForwardWeights",
    "regime_width_multipliers",
    "soft_regime_weights",
    "stack_regime_components",
    "walk_forward_stacking_weights",
    "walk_forward_stacked_forecast",
)


# Raw multiplier applied to the interval half-width per regime, before the
# probability shrink. Widening in stress/high-vol regimes and tightening in
# low-vol regimes is the whole point of a regime-aware interval.
_REGIME_WIDTH_MULTIPLIER: dict[MarketRegime, float] = {
    MarketRegime.CRISIS: 1.40,
    MarketRegime.BULL_TREND_HIGH_VOL: 1.18,
    MarketRegime.BEAR_TREND_HIGH_VOL: 1.20,
    MarketRegime.SIDEWAYS_HIGH_VOL: 1.15,
    MarketRegime.BULL_TREND_LOW_VOL: 0.94,
    MarketRegime.BEAR_TREND_LOW_VOL: 0.96,
    MarketRegime.SIDEWAYS_LOW_VOL: 0.93,
    MarketRegime.UNCLASSIFIED: 1.00,
}

# Hard clamps so no regime can collapse or explode the published interval.
_MIN_WIDTH_MULTIPLIER = 0.90
_MAX_WIDTH_MULTIPLIER = 1.50


def regime_width_multipliers(
    regime: MarketRegime,
    *,
    probability: float = 1.0,
) -> dict[str, float | str]:
    """Return the half-width multiplier for ``regime`` and the basis for it.

    The raw per-regime multiplier is shrunk toward 1.0 by ``probability`` so a
    0.6-confidence label applies roughly 60% of its tilt.
    """
    raw = _REGIME_WIDTH_MULTIPLIER.get(regime, 1.0)
    prob = float(np.clip(probability, 0.0, 1.0))
    shrunk = 1.0 + (raw - 1.0) * prob
    clamped = float(np.clip(shrunk, _MIN_WIDTH_MULTIPLIER, _MAX_WIDTH_MULTIPLIER))
    return {
        "raw_multiplier": round(float(raw), 4),
        "multiplier": round(clamped, 4),
        "probability_used": round(prob, 4),
        "basis": (
            f"regime={regime.value}; raw={raw:.2f} shrunk by probability {prob:.2f} "
            f"and clamped to [{_MIN_WIDTH_MULTIPLIER}, {_MAX_WIDTH_MULTIPLIER}]"
        ),
    }


def soft_regime_weights(
    regime_weights: Mapping[str, float],
    *,
    available: Sequence[str],
    probability: float = 1.0,
) -> dict[str, float]:
    """Blend regime weights toward an equal mix, keeping only ``available``.

    ``effective = p * regime_weight + (1 - p) * uniform`` over the available
    models, renormalized to sum to 1. Models absent from ``available`` are
    dropped before renormalizing so their weight is redistributed rather than
    lost.
    """
    names = [m for m in available]
    if not names:
        return {}

    prob = float(np.clip(probability, 0.0, 1.0))
    uniform = 1.0 / len(names)
    out: dict[str, float] = {}
    for name in names:
        raw = float(regime_weights.get(name, uniform))
        if not np.isfinite(raw) or raw < 0.0:
            raw = uniform
        out[name] = prob * raw + (1.0 - prob) * uniform

    total = sum(out.values())
    if total <= 0:
        return {name: uniform for name in names}
    return {name: round(value / total, 6) for name, value in out.items()}


@dataclass(frozen=True, slots=True)
class RegimeStack:
    """Outcome of one regime-conditional stacking attempt."""

    value: float | None
    weights: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    applied: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "value": None if self.value is None else round(float(self.value), 4),
            "weights": self.weights,
            "contributions": {
                k: round(float(v), 4) for k, v in self.contributions.items()
            },
            "dropped": self.dropped,
            "applied": self.applied,
            "reason": self.reason,
        }


def stack_regime_components(
    components: Mapping[str, float],
    regime_weights: Mapping[str, float],
    *,
    probability: float = 1.0,
    fallback: float | None = None,
    min_components: int = 1,
) -> RegimeStack:
    """Blend ``components`` with regime weights, dropping unusable members.

    Components are weighted by :func:`soft_regime_weights`. Non-finite or
    negative values are dropped with a reason. If fewer than ``min_components``
    survive, nothing is applied and ``fallback`` is returned instead, so a
    caller can keep its previous estimate rather than publish a blend of
    nothing.
    """
    dropped: dict[str, str] = {}
    usable: dict[str, float] = {}
    for name, raw in components.items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            dropped[name] = "not_numeric"
            continue
        if not np.isfinite(value):
            dropped[name] = "not_finite"
            continue
        if value < 0.0:
            dropped[name] = "negative"
            continue
        usable[name] = value

    if len(usable) < max(1, int(min_components)):
        reason = (
            f"only {len(usable)} usable component(s); need >= {max(1, int(min_components))}"
        )
        return RegimeStack(
            value=None if fallback is None else float(fallback),
            weights={},
            contributions={},
            dropped=dropped,
            applied=False,
            reason=reason,
        )

    weights = soft_regime_weights(
        regime_weights, available=list(usable), probability=probability
    )
    contributions = {name: usable[name] * weights[name] for name in usable}
    total = sum(contributions.values())

    return RegimeStack(
        value=float(total),
        weights=weights,
        contributions=contributions,
        dropped=dropped,
        applied=True,
        reason="",
    )


# --------------------------------------------------------------------------
# Time-ordered stacking meta-learner
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WalkForwardWeights:
    """Non-negative stacking weights learned on past observations only.

    ``weights`` are the simplex-constrained weights in standardised units and
    always sum to 1. ``coefficients`` and ``intercept`` are the same fit carried
    back to raw units and are what :meth:`predict` applies.
    """

    weights: dict[str, float]
    fold: int
    n_train: int
    method: str
    coefficients: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    regime_counts: dict[str, int] = field(default_factory=dict)
    regime_weights: dict[str, dict[str, float]] = field(default_factory=dict)

    def predict(self, components: Mapping[str, float]) -> float | None:
        usable = {
            name: float(components[name])
            for name in self.coefficients or self.weights
            if name in components
            and np.isfinite(components[name])
        }
        if not usable:
            return None
        scale = self.coefficients or self.weights
        return float(self.intercept) + float(
            sum(scale[name] * usable[name] for name in usable)
        )


def _project_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection of ``values`` onto {w >= 0, sum(w) == 1}."""
    if values.size == 0:
        return values
    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered) - 1.0
    index = np.arange(1, values.size + 1)
    condition = ordered - cumulative / index > 0
    if not np.any(condition):
        return np.full(values.shape, 1.0 / values.size)
    rho = int(np.nonzero(condition)[0][-1])
    theta = cumulative[rho] / float(rho + 1)
    return np.asarray(np.maximum(values - theta, 0.0), dtype=float)


@dataclass(frozen=True, slots=True)
class _Fit:
    """A stacking fit carried in both standardised and raw units.

    ``weights`` are the simplex-constrained weights (they sum to 1 and are what
    "how much of each model" means). ``coefficients`` and ``intercept`` are the
    same fit expressed in raw units, which is what prediction applies.

    The standardisation statistics are retained so that re-weighting the simplex
    vector later — for shrinkage or a rule-based prior — can be resolved back into
    raw coefficients without losing the level the fit actually reproduced.
    """

    weights: dict[str, float]
    coefficients: dict[str, float]
    intercept: float
    columns: tuple[str, ...]
    mean: np.ndarray
    spread: np.ndarray
    target_mean: float

    @classmethod
    def resolve(
        cls,
        names: Sequence[str],
        weights: Mapping[str, float],
        mean: np.ndarray,
        spread: np.ndarray,
        target_mean: float,
    ) -> "_Fit":
        columns = tuple(names)
        if not columns:
            return cls({}, {}, 0.0, (), mean, spread, target_mean)
        total = sum(float(weights[name]) for name in columns)
        if total <= 0 or not np.isfinite(total):
            uniform = 1.0 / len(columns)
            mix = {name: uniform for name in columns}
        else:
            mix = {name: float(weights[name]) / total for name in columns}
        coefficients = {
            name: mix[name] / float(spread[index])
            for index, name in enumerate(columns)
        }
        intercept = target_mean - float(
            sum(
                mix[name] * float(mean[index]) / float(spread[index])
                for index, name in enumerate(columns)
            )
        )
        return cls(mix, coefficients, float(intercept), columns, mean, spread, target_mean)

    def reweight(self, weights: Mapping[str, float]) -> "_Fit":
        return _Fit.resolve(
            self.columns, weights, self.mean, self.spread, self.target_mean
        )

    def predict_row(self, row: np.ndarray) -> float:
        return self.intercept + float(
            sum(
                self.coefficients[name] * float(row[index])
                for index, name in enumerate(self.columns)
            )
        )


def _shrink_to_uniform(
    weights: Mapping[str, float], shrinkage: float
) -> dict[str, float]:
    """Blend weights toward an equal mix, then renormalise onto the simplex."""
    names = list(weights)
    if not names:
        return {}
    bounded = float(np.clip(shrinkage, 0.0, 1.0))
    uniform = 1.0 / len(names)
    blended = {
        name: (1.0 - bounded) * float(weights[name]) + bounded * uniform
        for name in names
    }
    total = sum(blended.values())
    if total <= 0:
        return {name: uniform for name in names}
    return {name: value / total for name, value in blended.items()}


def _blend_with_prior(
    weights: Mapping[str, float],
    prior: Mapping[str, float] | None,
    share: float,
) -> dict[str, float]:
    """Mix learned weights with a rule-based prior, then renormalise."""
    names = list(weights)
    if not prior or not names or share <= 0.0:
        return {name: float(weights[name]) for name in names}
    bounded = float(np.clip(share, 0.0, 1.0))
    uniform = 1.0 / len(names)
    mixed = {
        name: (1.0 - bounded) * float(weights[name]) + bounded * float(prior.get(name, uniform))
        for name in names
    }
    total = sum(mixed.values())
    if total <= 0:
        return {name: uniform for name in names}
    return {name: value / total for name, value in mixed.items()}


def _fit_fold_weights(
    predictions: np.ndarray,
    outcomes: np.ndarray,
    columns: Sequence[str],
    ridge: float,
) -> _Fit:
    """Fit non-negative stacking weights for one fold.

    The solve is done on column-standardised data with the mean removed. Base
    forecasts for the same instrument are nearly always collinear — they move
    together — so an un-intercepted raw-scale least-squares problem is
    numerically singular and only a huge ridge stabilises it. Standardising
    first makes ``ridge`` meaningful in units of the outcome spread and makes
    the simplex constraint apply to "standard deviations of each model", which
    is the scale-free reading of a stacking weight.
    """
    names = list(columns)
    if not names or predictions.shape[0] == 0:
        return _Fit.resolve(names, {}, np.zeros(len(names)), np.ones(len(names)), 0.0)

    mean = predictions.mean(axis=0)
    spread = predictions.std(axis=0)
    spread = np.where(spread > 1e-12, spread, 1.0)
    target = np.asarray(outcomes, dtype=float)
    target_mean = float(target.mean())

    standardised = (predictions - mean) / spread
    centred = target - target_mean
    gram = standardised.T @ standardised + max(float(ridge), 0.0) * np.eye(len(names))
    try:
        raw = np.linalg.solve(gram, standardised.T @ centred)
    except np.linalg.LinAlgError:  # pragma: no cover - ridge keeps this solvable
        raw = np.full(len(names), 1.0 / len(names))
    if not np.isfinite(raw).all():  # pragma: no cover - defensive
        raw = np.full(len(names), 1.0 / len(names))

    projected = _project_simplex(np.asarray(raw, dtype=float))
    return _Fit.resolve(
        names,
        {name: float(value) for name, value in zip(names, projected)},
        mean,
        spread,
        target_mean,
    )


def _fit_regime_weights(
    matrix: np.ndarray,
    target: np.ndarray,
    label: str,
    labels: Sequence[str],
    columns: Sequence[str],
    *,
    min_rows: int,
    ridge: float,
    shrinkage: float,
    prior: Mapping[str, float] | None,
    regime_prior_weight: float,
    upto: int | None = None,
) -> _Fit | None:
    """Weights for one regime, fitted only on that regime's earlier rows.

    ``upto`` restricts the fit to rows strictly before an index, which is what
    keeps per-regime overrides leak-free during walk-forward scoring.
    """
    limit = len(labels) if upto is None else max(0, min(upto, len(labels)))
    if limit <= 0:
        return None
    mask = np.asarray([item == label for item in labels[:limit]], dtype=bool)
    if int(mask.sum()) < min_rows:
        return None
    block = np.nan_to_num(matrix[:limit][mask], nan=0.0)
    fit = _fit_fold_weights(
        block, np.asarray(target[:limit][mask], dtype=float), columns, ridge
    )
    mixed = _blend_with_prior(
        _shrink_to_uniform(fit.weights, shrinkage), prior, regime_prior_weight
    )
    return fit.reweight(mixed)


def walk_forward_stacking_weights(
    base_predictions: Mapping[str, Sequence[float]],
    outcomes: Sequence[float],
    *,
    regimes: Sequence[str] | None = None,
    regime_prior: Mapping[str, Mapping[str, float]] | None = None,
    min_train: int = 20,
    folds: int = 4,
    shrinkage: float = 0.5,
    ridge: float = 1.0,
    regime_prior_weight: float = 0.25,
) -> WalkForwardWeights | None:
    """Learn stacking weights from an expanding set of past folds.

    Each fold is fitted on a strict prefix of the series, so no weight vector can
    have seen the rows it is later applied to. A single fit over all rows and
    then scored on those same rows would leak the future into the weights, which
    is exactly what the chronological ordering here prevents.

    The deployed weights are the mean of the per-fold simplex vectors, re-resolved
    against the widest fold's statistics so the reported raw-unit coefficients
    stay consistent with the reported weights.

    ``regime_prior`` optionally supplies the rule-based weights from
    :func:`forecasting.regime_detection.regime_router`. A regime with enough rows
    is learned and then blended with the rule-based view by
    ``regime_prior_weight``; a regime with too few rows falls back to the rule
    weights alone rather than an unlearned guess.

    ``shrinkage`` (0..1) pulls the learned weights toward an equal-weight mix,
    which stabilises the fit when base models are collinear or folds are few.
    """
    columns = list(base_predictions)
    if not columns or outcomes is None or len(outcomes) == 0:
        return None

    size = len(outcomes)
    if size != len(next(iter(base_predictions.values()))):
        return None
    if size < min_train:
        return None

    try:
        columns_data = {
            name: np.asarray(values, dtype=float) for name, values in base_predictions.items()
        }
        target = np.asarray(outcomes, dtype=float)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(target).all():
        return None
    for values in columns_data.values():
        if values.shape[0] != size:
            return None

    usable = [
        name
        for name, values in columns_data.items()
        if np.isfinite(values).any()
    ]
    if not usable:
        return None
    for name in list(columns_data):
        if name not in usable:
            columns_data[name] = np.nan_to_num(columns_data[name], nan=0.0)

    matrix = np.column_stack([columns_data[name] for name in usable])

    fits: list[_Fit] = []
    fold_sizes: list[int] = []
    for fold in range(folds):
        train = size - folds + fold
        if train < min_train:
            break
        block = np.nan_to_num(matrix[:train], nan=0.0)
        fits.append(_fit_fold_weights(block, target[:train], usable, ridge))
        fold_sizes.append(train)

    if not fits:
        return None

    averaged = {
        name: float(np.mean([fit.weights[name] for fit in fits]))
        for name in usable
    }
    projected = _shrink_to_uniform(
        {
            name: float(value)
            for name, value in zip(
                usable,
                _project_simplex(np.asarray([averaged[name] for name in usable])),
            )
        },
        shrinkage,
    )
    # Re-resolve against the widest fold so coefficients and intercept match the
    # averaged weights rather than any single fold.
    deployed = fits[-1].reweight(projected)

    per_regime: dict[str, dict[str, float]] = {}
    if regimes is not None and regime_prior:
        labels = list(regimes)
        if len(labels) == size:
            for label in sorted(set(labels)):
                learned = _fit_regime_weights(
                    matrix,
                    target,
                    label,
                    labels,
                    usable,
                    min_rows=min_train,
                    ridge=ridge,
                    shrinkage=shrinkage,
                    prior=regime_prior.get(label),
                    regime_prior_weight=regime_prior_weight,
                )
                if learned is not None:
                    per_regime[label] = learned.weights

    return WalkForwardWeights(
        weights=deployed.weights,
        fold=len(fits),
        n_train=fold_sizes[-1],
        method=(
            f"time-ordered walk-forward standardised ridge stack, "
            f"{len(fits)} fold(s), last train size {fold_sizes[-1]}"
        ),
        coefficients=deployed.coefficients,
        intercept=deployed.intercept,
        regime_counts={
            label: int(sum(1 for item in (regimes or []) if item == label))
            for label in per_regime
        },
        regime_weights=per_regime,
    )


def _expanding_folds(size: int, min_train: int, folds: int) -> list[tuple[int, int]]:
    """Split the tail after ``min_train`` into contiguous score blocks.

    Fold ``k`` is fitted on ``[0, start_k)`` and may only score ``[start_k,
    stop_k)``, so no weight vector ever sees the bar it is applied to.
    """
    if folds < 1 or size <= min_train:
        return []
    block = (size - min_train) // folds
    if block < 1:
        return []
    spans: list[tuple[int, int]] = []
    for index in range(folds):
        start = min_train + index * block
        stop = start + block if index < folds - 1 else size
        if stop <= start:
            break
        spans.append((start, stop))
    return spans


def walk_forward_stacked_forecast(
    base_predictions: Mapping[str, Sequence[float]],
    outcomes: Sequence[float],
    horizons: Sequence[int],
    *,
    regimes: Sequence[str] | None = None,
    regime_prior: Mapping[str, Mapping[str, float]] | None = None,
    min_train: int = 20,
    folds: int = 4,
    shrinkage: float = 0.5,
    ridge: float = 1.0,
    regime_prior_weight: float = 0.25,
) -> dict[str, Any]:
    """Score a walk-forward stack strictly out of sample and report the comparison.

    Every scored bar is predicted by weights fitted only on bars that precede it,
    so the reported MAE is achievable rather than in-sample. The equal-weight
    baseline is scored on exactly the same bars; when the learned stack does not
    beat it, ``use_learned`` is False and the caller should not promote it.
    """
    columns = list(base_predictions)
    unavailable: dict[str, object] = {
        "available": False,
        "reason": "",
        "folds": 0,
        "scored": 0,
        "method": "",
    }
    if not columns or outcomes is None or len(outcomes) == 0:
        unavailable["reason"] = "no base predictions or outcomes to stack"
        return unavailable
    if not np.isfinite(float(regime_prior_weight)):
        unavailable["reason"] = "regime_prior_weight must be a real number"
        return unavailable

    try:
        target = np.asarray(outcomes, dtype=float)
        matrix = np.column_stack(
            [np.asarray(base_predictions[name], dtype=float) for name in columns]
        )
    except (TypeError, ValueError):
        unavailable["reason"] = "base predictions could not be read as numbers"
        return unavailable

    size = len(target)
    spans = _expanding_folds(size, min_train, folds)
    if not spans:
        unavailable["reason"] = (
            f"not enough history to fit a walk-forward stack "
            f"(need more than {min_train} bars)"
        )
        return unavailable

    usable = [name for name in columns if np.isfinite(matrix[:, columns.index(name)]).any()]
    if not usable:
        unavailable["reason"] = "no base model produced a finite prediction"
        return unavailable
    for name in columns:
        if name not in usable:
            matrix[:, columns.index(name)] = np.nan_to_num(
                matrix[:, columns.index(name)], nan=0.0
            )

    finite_rows = np.isfinite(matrix).all(axis=1) & np.isfinite(target)
    usable_indices = [columns.index(name) for name in usable]

    predicted: list[float] = []
    baseline: list[float] = []
    truth: list[float] = []
    used_horizons: list[int] = []
    scored_indices: list[int] = []
    effective = {name: 0.0 for name in columns}
    scored_rows = 0

    for start, stop in spans:
        train = np.nan_to_num(matrix[:start], nan=0.0)
        fold_fit = _fit_fold_weights(train, target[:start], usable, ridge)
        fold_fit = fold_fit.reweight(_shrink_to_uniform(fold_fit.weights, shrinkage))

        for index in range(start, min(stop, size)):
            if not finite_rows[index]:
                continue
            row_fit = fold_fit
            if regimes is not None and index < len(regimes):
                override = _fit_regime_weights(
                    matrix,
                    target,
                    regimes[index],
                    regimes,
                    usable,
                    min_rows=min_train,
                    ridge=ridge,
                    shrinkage=shrinkage,
                    prior=(regime_prior or {}).get(regimes[index]),
                    regime_prior_weight=regime_prior_weight,
                    upto=index,
                )
                if override is not None:
                    row_fit = override
            predicted.append(row_fit.predict_row(matrix[index]))
            baseline.append(float(np.mean(matrix[index][usable_indices])))
            truth.append(float(target[index]))
            scored_rows += 1
            used_horizons.append(int(horizons[index]) if index < len(horizons) else 1)
            scored_indices.append(index)
            for name in columns:
                effective[name] += row_fit.weights.get(name, 0.0)

    if not predicted:
        unavailable["reason"] = "no fully finite rows to score out of sample"
        unavailable["method"] = (
            "time-ordered walk-forward standardised ridge scorer"
            if spans
            else ""
        )
        return unavailable

    production = walk_forward_stacking_weights(
        base_predictions,
        outcomes,
        regimes=regimes,
        regime_prior=regime_prior,
        min_train=min_train,
        folds=folds,
        shrinkage=shrinkage,
        ridge=ridge,
        regime_prior_weight=regime_prior_weight,
    )

    truth_array = np.asarray(truth)
    mae = float(np.mean(np.abs(np.asarray(predicted) - truth_array)))
    baseline_mae = float(np.mean(np.abs(np.asarray(baseline) - truth_array)))
    for name in effective:
        effective[name] = round(effective[name] / scored_rows, 6)

    return {
        "available": True,
        "reason": "",
        "folds": len(spans),
        "n_train": spans[-1][0],
        "scored": scored_rows,
        "method": production.method if production is not None else "",
        "weights": production.weights if production is not None else {},
        "effective_weights": effective,
        "regime_weights": production.regime_weights if production is not None else {},
        "regime_counts": production.regime_counts if production is not None else {},
        "oos_mae": round(mae, 6),
        "equal_weight_mae": round(baseline_mae, 6),
        "use_learned": bool(mae < baseline_mae),
        "improvement": round(baseline_mae - mae, 6),
        "mean_horizon": round(float(np.mean(used_horizons)), 3),
        # Retained so the out-of-sample claim can be audited rather than taken on
        # trust: each entry was produced before its own outcome was seen.
        "scored_indices": scored_indices,
        "predictions": [round(value, 6) for value in predicted],
        "baseline_predictions": [round(value, 6) for value in baseline],
        "disclosure": (
            "Every scored bar is predicted by weights fitted only on bars that "
            "precede it, so these errors are out of sample. A stack that does "
            "not beat the equal-weight baseline on the same bars should not be "
            "promoted."
        ),
    }
