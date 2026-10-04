"""Offline, leakage-safe hyperparameter tuning for selected regression models.

This command is intentionally separate from an interactive API request. It
uses chronological TimeSeriesSplit and writes symbol-specific parameters that
the normal prediction pipeline automatically applies during later retraining.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.tree import DecisionTreeRegressor

from model_runtime import get_model_worker_count
from prediction import (
    FEATURE_COLUMNS,
    RANDOM_STATE,
    TARGET_COLUMN,
    build_supervised_frame,
    get_tuning_path,
    normalize_symbol,
)
from stock_api import get_stock_data


def _search_specs():
    return {
        "Decision Tree": (
            DecisionTreeRegressor(random_state=RANDOM_STATE),
            {
                "max_depth": [3, 4, 5, 6, 8, 10, None],
                "min_samples_split": [5, 8, 12, 20],
                "min_samples_leaf": [2, 4, 6, 10],
            },
        ),
        "Random Forest": (
            RandomForestRegressor(
                random_state=RANDOM_STATE,
                n_jobs=get_model_worker_count(),
            ),
            {
                "n_estimators": [100, 150, 220, 300],
                "max_depth": [5, 7, 9, 12, None],
                "min_samples_split": [4, 8, 12],
                "min_samples_leaf": [2, 3, 5],
                "max_features": ["sqrt", 0.7, 1.0],
            },
        ),
        "Gradient Boosting": (
            HistGradientBoostingRegressor(random_state=RANDOM_STATE, early_stopping=True),
            {
                "learning_rate": [0.025, 0.04, 0.06, 0.08, 0.12],
                "max_iter": [100, 150, 220, 300],
                "max_depth": [3, 5, 7, None],
                "min_samples_leaf": [10, 15, 20, 30],
                "l2_regularization": [0.0, 0.05, 0.15, 0.5],
            },
        ),
        "SVM": (
            Pipeline([("scaler", StandardScaler()), ("model", SVR())]),
            {
                "model__C": [0.5, 1.0, 3.0, 8.0, 15.0, 30.0],
                "model__epsilon": [0.01, 0.05, 0.10, 0.20, 0.50],
                "model__gamma": ["scale", "auto", 0.005, 0.01, 0.05],
                "model__kernel": ["rbf", "linear"],
            },
        ),
    }


def tune_symbol(
    symbol: str, period: str = "5y", iterations: int = 12
) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    frame = build_supervised_frame(get_stock_data(symbol, period))
    X, y = frame[FEATURE_COLUMNS], frame[TARGET_COLUMN]
    splitter = TimeSeriesSplit(n_splits=4, gap=1)
    parameters, scores = {}, {}

    for name, (estimator, search_space) in _search_specs().items():
        search = RandomizedSearchCV(
            estimator,
            search_space,
            n_iter=max(2, int(iterations)),
            scoring="neg_root_mean_squared_error",
            cv=splitter,
            random_state=RANDOM_STATE,
            n_jobs=get_model_worker_count(),
            refit=True,
            error_score="raise",
        )
        search.fit(X, y)
        parameters[name] = search.best_params_
        scores[name] = abs(float(search.best_score_))
        print(f"{name}: CV RMSE {scores[name]:.4f} · {search.best_params_}")

    payload = {
        "symbol": symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "RandomizedSearchCV with chronological TimeSeriesSplit(gap=1)",
        "training_rows": len(frame),
        "parameters": parameters,
        "cross_validation_rmse": scores,
    }
    output = Path(get_tuning_path(symbol))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved: {output}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--iterations", type=int, default=12)
    args = parser.parse_args()
    tune_symbol(args.symbol, args.period, args.iterations)


if __name__ == "__main__":
    main()
