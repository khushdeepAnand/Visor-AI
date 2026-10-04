"""SHAP-based attribution for the tree members of the production ensemble.

The published interval never uses these numbers.  This block explains, after
the fact, what moved the *point* forecast on the latest feature row: which
indicators pushed it higher or lower, and how strongly relative to the other
drivers.  Correlated indicators can share credit, so the output is directional
and explicitly non-causal.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_TREE_FAMILY_NAMES = frozenset({
    "GradientBoostingRegressor",
    "RandomForestRegressor",
    "HistGradientBoostingRegressor",
    "ExtraTreesRegressor",
    "LGBMRegressor",
    "CatBoostRegressor",
})


def _is_tree_model(model: Any) -> bool:
    return type(model).__name__ in _TREE_FAMILY_NAMES


def explain_stacked_tree_ensemble(
    models: dict[str, Any],
    X_latest: pd.DataFrame,
    features: list[str],
    *,
    n_top: int = 5,
    reference_point: float | None = None,
) -> dict[str, Any]:
    """Attribution over the tree members of a fitted stack.

    ``models`` maps member name -> fitted regressor (only tree-family members
    are attributed; linear, elastic-net, ridge and persistence members are
    deliberately excluded).  Each tree model explains the single latest row;
    per-feature SHAP values are averaged across models, then the top drivers
    by mean absolute contribution are returned with their direction.
    """
    tree_models = {name: model for name, model in (models or {}).items() if _is_tree_model(model)}
    if not tree_models:
        return {
            "available": False,
            "reason": "No tree-based base learners were in the stack for attribution.",
        }

    try:
        import shap  # type: ignore[import-not-found]
    except ImportError:
        return {
            "available": False,
            "reason": "shap is not installed; feature attribution is unavailable.",
        }

    contributions: dict[str, np.ndarray] = {}
    fitted: list[str] = []
    for name, model in sorted(tree_models.items()):
        try:
            explainer = shap.TreeExplainer(model)
            values = explainer.shap_values(X_latest)
        except Exception:
            continue
        array = np.asarray(values, dtype=float)
        if array.ndim == 3:
            # Multi-class layout: keep the final class slice for regression parity.
            array = array[..., -1]
        if array.ndim != 2 or array.shape[0] != len(X_latest) or array.shape[1] != len(features):
            continue
        contributions[name] = array[-1]
        fitted.append(name)

    if not contributions:
        return {
            "available": False,
            "reason": "No tree model produced SHAP values on the latest feature row.",
        }

    stacked = np.mean(np.stack([contributions[name] for name in fitted]), axis=0)
    magnitudes = np.abs(stacked)
    total = float(np.sum(magnitudes))
    order = np.argsort(magnitudes)[::-1]

    drivers: list[dict[str, Any]] = []
    for index in order[: max(1, int(n_top))]:
        feature = str(features[index])
        value = float(stacked[index])
        drivers.append({
            "feature": feature,
            "direction": "pushes_higher" if value > 0 else "pushes_lower" if value < 0 else "neutral",
            "shap_value": round(value, 6),
            "magnitude_pct": round(float(magnitudes[index]) / total * 100.0, 3) if total > 0 else 0.0,
        })

    return {
        "available": True,
        "method": "SHAP TreeExplainer averaged over the tree members of the stacked ensemble",
        "tree_models": fitted,
        "excluded_members": sorted(name for name in (models or {}) if name not in tree_models),
        "n_features": len(features),
        "row_timestamp": str(X_latest.index[-1]),
        "reference_price": round(float(reference_point), 2) if reference_point else None,
        "drivers": drivers,
        "caveat": "Attribution describes what moved the point forecast, not causation. Correlated indicators may share credit, and non-tree stack members are not attributed by this block.",
    }
