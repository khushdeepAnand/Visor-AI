"""Regime-conditional model weighting and time-ordered stacking meta-learner for StockPilot AI v16.

This module extends the existing regime_detection.py and evaluation_harness.py with:
1. Regime-conditional model weighting: weights models based on detected market regime
2. Time-ordered stacking meta-learner: learns optimal model combination using walk-forward validation
"""
from __future__ import annotations

import json
import math
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from forecasting.regime_detection import MarketRegime, RegimeState, detect_regime, regime_router, regime_to_dict
from forecasting.evaluation_harness import (
    TierEvaluationResult,
    evaluate_tier,
    evaluation_summary,
    pinball_loss,
    crps_gaussian,
    mase,
    directional_accuracy,
)


MODEL_REGISTRY = [
    "per_stock_cqr",
    "pooled_cross_sectional",
    "volatility_model",
    "regime_adaptive",
    "trend_following",
    "mean_reversion",
]


@dataclass(frozen=True, slots=True)
class RegimeWeights:
    regime: MarketRegime
    weights: dict[str, float]
    confidence: float
    sample_count: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class StackingResult:
    meta_weights: dict[str, float]
    meta_learner_type: str
    train_periods: int
    validation_metrics: dict[str, float]
    feature_importance: dict[str, float] | None


@dataclass(frozen=True, slots=True)
class EnsembleForecast:
    symbol: str
    timestamp: str
    regime: MarketRegime
    regime_probability: float
    model_predictions: dict[str, dict[str, float]]
    ensemble_prediction: dict[str, float]
    regime_weights: dict[str, float]
    meta_weights: dict[str, float]
    disclosures: list[str]


def compute_regime_conditional_weights(
    evaluation_history: dict[str, list[TierEvaluationResult]],
    regime_history: list[RegimeState],
    *,
    min_samples_per_regime: int = 20,
    recency_half_life: int = 60,
) -> dict[MarketRegime, RegimeWeights]:
    """Compute model weights conditioned on market regime.

    Args:
        evaluation_history: Dict mapping model names to lists of TierEvaluationResult
        regime_history: List of RegimeState corresponding to evaluation periods
        min_samples_per_regime: Minimum samples needed to trust regime-specific weights
        recency_half_life: Half-life for exponential recency weighting (in periods)

    Returns:
        Dict mapping MarketRegime to RegimeWeights with learned weights
    """
    if not evaluation_history or not regime_history:
        return {regime: RegimeWeights(regime=regime, weights={m: 1.0/len(MODEL_REGISTRY) for m in MODEL_REGISTRY}, confidence=0.0, sample_count=0, updated_at=datetime.now(timezone.utc).isoformat()) for regime in MarketRegime}

    n_periods = len(regime_history)
    recency_weights = np.exp(-np.arange(n_periods)[::-1] * np.log(2) / recency_half_life)
    recency_weights = recency_weights / recency_weights.sum()

    regime_weights: dict[MarketRegime, RegimeWeights] = {}

    for regime in MarketRegime:
        regime_mask = np.array([r.regime == regime for r in regime_history])
        regime_count = regime_mask.sum()

        if regime_count < min_samples_per_regime:
            regime_weights[regime] = RegimeWeights(
                regime=regime,
                weights={m: 1.0/len(MODEL_REGISTRY) for m in MODEL_REGISTRY},
                confidence=0.0,
                sample_count=int(regime_count),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            continue

        model_scores: dict[str, float] = {}
        for model_name in MODEL_REGISTRY:
            if model_name not in evaluation_history:
                model_scores[model_name] = 0.0
                continue

            scores = []
            for i, result in enumerate(evaluation_history[model_name]):
                if regime_mask[i]:
                    weight = recency_weights[i] / recency_weights[regime_mask].sum()
                    score = (1.0 - abs(result.coverage - result.target_coverage)) * (1.0 / (1.0 + result.mase))
                    scores.append(score * weight)

            model_scores[model_name] = float(np.mean(scores)) if scores else 0.0

        total_score = sum(model_scores.values())
        if total_score > 0:
            weights = {k: v / total_score for k, v in model_scores.items()}
        else:
            weights = {m: 1.0/len(MODEL_REGISTRY) for m in MODEL_REGISTRY}

        confidence = min(1.0, regime_count / min_samples_per_regime)
        regime_weights[regime] = RegimeWeights(
            regime=regime,
            weights=weights,
            confidence=confidence,
            sample_count=int(regime_count),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    return regime_weights


def prepare_stacking_features(
    model_predictions: dict[str, np.ndarray],
    actuals: np.ndarray,
    regime_labels: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Prepare features for stacking meta-learner.

    Args:
        model_predictions: Dict mapping model name to predictions (n_samples,)
        actuals: Actual values (n_samples,)
        regime_labels: Optional regime labels for regime-conditional stacking

    Returns:
        Tuple of (features, targets) where features include model predictions and optionally regime one-hot
    """
    model_names = sorted(model_predictions.keys())
    n_samples = len(actuals)

    features_list = []
    for model_name in model_names:
        preds = model_predictions[model_name]
        if len(preds) == n_samples:
            features_list.append(preds.reshape(-1, 1))
        else:
            features_list.append(np.zeros((n_samples, 1)))

    features = np.hstack(features_list)

    if regime_labels is not None:
        regime_dummies = pd.get_dummies(regime_labels, prefix="regime")
        features = np.hstack([features, regime_dummies.values])

    return features, actuals


def train_time_ordered_stacking(
    model_predictions: dict[str, np.ndarray],
    actuals: np.ndarray,
    regime_labels: np.ndarray | None = None,
    *,
    n_folds: int = 5,
    embargo: int = 5,
    meta_learner: str = "ridge",
) -> StackingResult:
    """Train a time-ordered stacking meta-learner using purged walk-forward validation.

    Args:
        model_predictions: Dict mapping model name to out-of-fold predictions
        actuals: Actual values
        regime_labels: Optional regime labels for regime-conditional stacking
        n_folds: Number of walk-forward folds
        embargo: Embargo period between train and test
        meta_learner: Type of meta-learner ("ridge", "nnls", "constrained")

    Returns:
        StackingResult with learned meta-weights and validation metrics
    """
    features, targets = prepare_stacking_features(model_predictions, actuals, regime_labels)
    n_samples, n_features = features.shape
    model_names = sorted(model_predictions.keys())

    if meta_learner == "ridge":
        from sklearn.linear_model import Ridge
        base_learner = Ridge(alpha=1.0, fit_intercept=True)
    elif meta_learner == "nnls":
        from scipy.optimize import nnls
        base_learner = None
    elif meta_learner == "constrained":
        base_learner = None
    else:
        raise ValueError(f"Unknown meta_learner: {meta_learner}")

    fold_size = n_samples // (n_folds + 1)
    oof_predictions = np.zeros(n_samples)
    fold_metrics: list[dict[str, float]] = []

    for fold in range(n_folds):
        train_end = (fold + 1) * fold_size
        test_start = train_end + embargo
        test_end = min(test_start + fold_size, n_samples)

        if test_start >= n_samples:
            break

        train_idx = slice(0, train_end)
        test_idx = slice(test_start, test_end)

        X_train, y_train = features[train_idx], targets[train_idx]
        X_test, y_test = features[test_idx], targets[test_idx]

        if meta_learner == "ridge":
            base_learner.fit(X_train, y_train)
            fold_pred = base_learner.predict(X_test)
            weights = base_learner.coef_[:len(model_names)]
        elif meta_learner == "nnls":
            weights, _ = nnls(X_train, y_train)
            fold_pred = X_test @ weights
        elif meta_learner == "constrained":
            def objective(w: np.ndarray) -> float:
                return float(np.mean((X_train @ w - y_train) ** 2))
            constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w[:len(model_names)]) - 1.0},
                           {'type': 'ineq', 'fun': lambda w: w[:len(model_names)]})
            x0 = np.ones(n_features) / n_features
            result = minimize(objective, x0, constraints=constraints, method='SLSQP')
            weights = result.x
            fold_pred = X_test @ weights

        oof_predictions[test_idx] = fold_pred

        fold_metrics.append({
            "mae": float(np.mean(np.abs(fold_pred - y_test))),
            "rmse": float(np.sqrt(np.mean((fold_pred - y_test) ** 2))),
            "pinball_0.5": pinball_loss(y_test, fold_pred, 0.5),
            "crps": crps_gaussian(y_test, fold_pred, np.std(fold_pred - y_test) * np.ones_like(fold_pred)),
        })

    if meta_learner in ("ridge", "nnls", "constrained"):
        if meta_learner == "ridge":
            base_learner.fit(features, targets)
            final_weights = base_learner.coef_[:len(model_names)]
        elif meta_learner == "nnls":
            final_weights, _ = nnls(features, targets)
        elif meta_learner == "constrained":
            def objective(w: np.ndarray) -> float:
                return float(np.mean((features @ w - targets) ** 2))
            constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w[:len(model_names)]) - 1.0},
                           {'type': 'ineq', 'fun': lambda w: w[:len(model_names)]})
            x0 = np.ones(n_features) / n_features
            result = minimize(objective, x0, constraints=constraints, method='SLSQP')
            final_weights = result.x

    else:
        final_weights = np.ones(len(model_names)) / len(model_names)

    meta_weights = {name: float(max(0.0, w)) for name, w in zip(model_names, final_weights)}
    total = sum(meta_weights.values())
    if total > 0:
        meta_weights = {k: v / total for k, v in meta_weights.items()}

    feature_importance = None
    if meta_learner == "ridge" and hasattr(base_learner, 'coef_'):
        feature_importance = {name: float(abs(w)) for name, w in zip(model_names, base_learner.coef_[:len(model_names)])}
        total_imp = sum(feature_importance.values())
        if total_imp > 0:
            feature_importance = {k: v / total_imp for k, v in feature_importance.items()}

    avg_metrics = {}
    for key in fold_metrics[0].keys():
        avg_metrics[key] = float(np.mean([m[key] for m in fold_metrics]))

    return StackingResult(
        meta_weights=meta_weights,
        meta_learner_type=meta_learner,
        train_periods=n_samples,
        validation_metrics=avg_metrics,
        feature_importance=feature_importance,
    )


def generate_ensemble_forecast(
    symbol: str,
    model_predictions: dict[str, dict[str, float]],
    regime_state: RegimeState,
    regime_weights: dict[MarketRegime, RegimeWeights],
    meta_weights: dict[str, float] | None = None,
    *,
    confidence: float = 0.80,
) -> EnsembleForecast:
    """Generate an ensemble forecast combining regime weights and stacking meta-weights.

    Args:
        symbol: Trading symbol
        model_predictions: Dict of model_name -> {"low": float, "median": float, "high": float}
        regime_state: Current RegimeState from detect_regime
        regime_weights: Regime-conditional weights from compute_regime_conditional_weights
        meta_weights: Optional stacking meta-weights from train_time_ordered_stacking
        confidence: Confidence level for intervals

    Returns:
        EnsembleForecast with combined prediction
    """
    regime_weight_obj = regime_weights.get(regime_state.regime)
    if regime_weight_obj is None:
        regime_w = {m: 1.0/len(MODEL_REGISTRY) for m in MODEL_REGISTRY}
    else:
        regime_w = regime_weight_obj.weights

    if meta_weights is None:
        meta_w = {m: 1.0/len(MODEL_REGISTRY) for m in MODEL_REGISTRY}
    else:
        meta_w = meta_weights

    combined_w = {}
    for model in MODEL_REGISTRY:
        combined_w[model] = 0.5 * regime_w.get(model, 0.0) + 0.5 * meta_w.get(model, 0.0)
    total = sum(combined_w.values())
    if total > 0:
        combined_w = {k: v / total for k, v in combined_w.items()}

    ensemble_low = sum(model_predictions.get(m, {}).get("low", 0.0) * combined_w.get(m, 0.0) for m in MODEL_REGISTRY)
    ensemble_median = sum(model_predictions.get(m, {}).get("median", 0.0) * combined_w.get(m, 0.0) for m in MODEL_REGISTRY)
    ensemble_high = sum(model_predictions.get(m, {}).get("high", 0.0) * combined_w.get(m, 0.0) for m in MODEL_REGISTRY)

    disclosures = [
        "Ensemble combines regime-conditional weights and time-ordered stacking meta-learner.",
        "Regime weights based on historical performance per market regime (minimum 20 samples).",
        "Meta-learner trained with purged walk-forward validation to prevent leakage.",
        "Individual model predictions are model-priced, not exchange-quoted.",
        "Not a trade recommendation. Past regime performance does not guarantee future results.",
    ]

    return EnsembleForecast(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc).isoformat(),
        regime=regime_state.regime,
        regime_probability=regime_state.probability,
        model_predictions=model_predictions,
        ensemble_prediction={"low": ensemble_low, "median": ensemble_median, "high": ensemble_high},
        regime_weights=regime_w,
        meta_weights=meta_w,
        disclosures=disclosures,
    )


def save_regime_weights(regime_weights: dict[MarketRegime, RegimeWeights], path: Path) -> None:
    """Save regime weights to disk."""
    data = {regime.value: {
        "regime": regime.value,
        "weights": weights.weights,
        "confidence": weights.confidence,
        "sample_count": weights.sample_count,
        "updated_at": weights.updated_at,
    } for regime, weights in regime_weights.items()}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_regime_weights(path: Path) -> dict[MarketRegime, RegimeWeights]:
    """Load regime weights from disk."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {MarketRegime(k): RegimeWeights(
        regime=MarketRegime(k),
        weights=v["weights"],
        confidence=v["confidence"],
        sample_count=v["sample_count"],
        updated_at=v["updated_at"],
    ) for k, v in data.items()}


def save_stacking_result(result: StackingResult, path: Path) -> None:
    """Save stacking result to disk."""
    data = {
        "meta_weights": result.meta_weights,
        "meta_learner_type": result.meta_learner_type,
        "train_periods": result.train_periods,
        "validation_metrics": result.validation_metrics,
        "feature_importance": result.feature_importance,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


__all__: Sequence[str] = (
    "MODEL_REGISTRY",
    "RegimeWeights",
    "StackingResult",
    "EnsembleForecast",
    "compute_regime_conditional_weights",
    "prepare_stacking_features",
    "train_time_ordered_stacking",
    "generate_ensemble_forecast",
    "save_regime_weights",
    "load_regime_weights",
    "save_stacking_result",
)
