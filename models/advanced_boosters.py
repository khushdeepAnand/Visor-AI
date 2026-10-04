"""Gradient-boosting adapters retained for compatibility and evaluation.

LightGBM and CatBoost are the core gradient-boosting requirements for
StockPilot AI (XGBoost is out of scope). Models still need to beat
chronological baselines before their quality claims are promoted.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

from model_runtime import get_model_worker_count
from prediction import FEATURE_COLUMNS, TARGET_COLUMN, build_supervised_frame

OPTIONAL_BOOSTERS = {
    "LightGBM": "lightgbm",
    "CatBoost": "catboost",
}


def optional_booster_status() -> list[dict[str, Any]]:
    return [
        {
            "model": name,
            "package": package,
            "available": importlib.util.find_spec(package) is not None,
            "promotion_rule": "Must beat the naive baseline on chronological unseen data.",
        }
        for name, package in OPTIONAL_BOOSTERS.items()
    ]


def build_optional_regressor(name: str, parameters: dict[str, Any] | None = None):
    parameters = dict(parameters or {})

    if name == "LightGBM":
        try:
            from lightgbm import LGBMRegressor
        except ImportError as error:
            raise RuntimeError("Install requirements.txt to use LightGBM.") from error
        defaults: dict[str, Any] = {
            "n_estimators": 300,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "random_state": 42,
            "verbosity": -1,
            "n_jobs": get_model_worker_count(),
        }
        defaults.update(parameters)
        defaults["n_jobs"] = get_model_worker_count(defaults.get("n_jobs"))
        return LGBMRegressor(**defaults)
    if name == "CatBoost":
        try:
            from catboost import CatBoostRegressor
        except ImportError as error:
            raise RuntimeError("Install requirements.txt to use CatBoost.") from error
        defaults = {
            "iterations": 300,
            "depth": 6,
            "learning_rate": 0.03,
            "loss_function": "RMSE",
            "random_seed": 42,
            "verbose": False,
            "thread_count": get_model_worker_count(),
        }
        defaults.update(parameters)
        defaults["thread_count"] = get_model_worker_count(defaults.get("thread_count"))
        return CatBoostRegressor(**defaults)
    raise ValueError("Unknown optional booster. Choose LightGBM or CatBoost.")


def evaluate_optional_booster(data, model_name: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Train and evaluate one optional booster using a chronological holdout."""

    frame = build_supervised_frame(data)
    split = int(len(frame) * 0.80)
    X_train = frame[FEATURE_COLUMNS].iloc[:split]
    X_test = frame[FEATURE_COLUMNS].iloc[split:]
    y_train = frame[TARGET_COLUMN].iloc[:split]
    y_test = frame[TARGET_COLUMN].iloc[split:]
    baseline = frame["Close"].iloc[split:].to_numpy(dtype=float)
    model = build_optional_regressor(model_name, parameters)
    model.fit(X_train, y_train)
    predictions = np.asarray(model.predict(X_test), dtype=float)
    rmse = float(np.sqrt(mean_squared_error(y_test, predictions)))
    baseline_rmse = float(np.sqrt(mean_squared_error(y_test, baseline)))
    return {
        "model": model_name,
        "rmse": rmse,
        "mae": float(mean_absolute_error(y_test, predictions)),
        "baseline_rmse": baseline_rmse,
        "beats_baseline": rmse < baseline_rmse,
        "eligible_for_ensemble": rmse < baseline_rmse,
        "test_samples": len(X_test),
    }
