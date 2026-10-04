"""StockPilot AI forecasting, validation, classification, and explainability.

.. note:: Legacy module (retired pipeline).

   The ``.pkl`` pipeline in this module (``MODEL_VERSION`` "5.2",
   ``train_models``/``save_model``/``load_model``) is **retired**: no production
   path trains or loads those artifacts.  The production forecast surface is
   ``forecasting.interval_forecast`` (calibrated interval ensemble).  This
   module survives because the modern stack still shares its constants
   (``FEATURE_COLUMNS``, ``RAW_COLUMNS``, ``TARGET_COLUMN``), its
   ``InsufficientDataError``/``normalize_symbol``/``safe_float`` utilities and
   its ``_train_model_by_name`` base-learner factory, and because legacy
   regression tests exercise it.

The module keeps the original public API while expanding the model roster and
adding leakage-safe walk-forward classification.  All production model ranking
is based on chronological out-of-sample results and a naive last-close baseline.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import warnings
from datetime import datetime, timezone
from typing import Any, Iterable, cast

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.inspection import permutation_importance
from sklearn.linear_model import ElasticNet, LinearRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

from indicators import add_indicators
from model_runtime import get_model_worker_count, load_trusted_joblib_artifact


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_FOLDER = os.path.join(BASE_DIR, "models", "trained")


class InsufficientDataError(ValueError):
    """The instrument has too little complete history to fit a model.

    It subclasses ``ValueError`` so existing callers keep working, while the API
    layer can distinguish "not enough data yet" from a malformed request and
    return a specific, actionable message instead of a generic rejection.
    """

RAW_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
FEATURE_COLUMNS = [
    "Open", "High", "Low", "Close", "Volume",
    "SMA_10", "SMA_20", "SMA_50", "SMA_100",
    "EMA_10", "EMA_20", "EMA_50", "EMA_100",
    "RSI_7", "RSI", "RSI_21",
    "MACD", "MACD_Signal", "MACD_Histogram",
    "BB_Upper", "BB_Middle", "BB_Lower", "BB_Width",
    "ATR", "ATR_Pct", "ROC", "OBV", "Stochastic_K", "Stochastic_D",
    "VWAP", "CMF", "MFI", "ADX", "Plus_DI", "Minus_DI", "CCI", "Williams_R",
    "Donchian_Upper", "Donchian_Middle", "Donchian_Lower", "Donchian_Position",
    "Daily_Return", "Return_2D", "Return_5D", "Return_10D", "Return_20D", "Return_60D",
    "Volatility", "Volatility_5D", "Volatility_10D", "Volatility_30D", "Volatility_60D",
    "Momentum_5", "Momentum_10", "Momentum_20",
    "Volume_Change", "Volume_Ratio20", "Volume_ZScore20",
    "Price_Range_Pct", "Close_Location", "Gap_Pct",
    "Close_to_SMA20_Pct", "Close_to_SMA50_Pct", "Close_to_VWAP_Pct",
    "Return_Skew20", "Return_Kurt20", "Drawdown_20", "Drawdown_60",
]

TARGET_COLUMN = "Target"
TARGET_DATE_COLUMN = "Target_Date"
DIRECTION_TARGET_COLUMN = "Direction_Target"
NEXT_VOLATILITY_COLUMN = "Next_Absolute_Return"

MINIMUM_DATA_ROWS = 80
MINIMUM_SUPERVISED_ROWS = 80
TRAIN_RATIO = 0.80
WALK_FORWARD_SPLITS = 5
PREDICTION_INTERVAL_QUANTILE = 0.80
MODEL_VERSION = "5.2"
RANDOM_STATE = 42

MODEL_FILENAMES = {
    "Linear Regression": "linear.pkl",
    "ElasticNet": "elasticnet.pkl",
    "Decision Tree": "decision_tree.pkl",
    "Random Forest": "random_forest.pkl",
    "Gradient Boosting": "gradient_boosting.pkl",
    "SVM": "svm.pkl",
    "KNN": "knn.pkl",
}

CLASSIFIER_FILENAMES = {
    "Decision Tree Classifier": "decision_tree_classifier.pkl",
    "Random Forest Classifier": "random_forest_classifier.pkl",
    "Gradient Boosting Classifier": "gradient_boosting_classifier.pkl",
    "SVM Classifier": "svm_classifier.pkl",
}

BASELINE_KEY = "Naive Baseline"
CONSENSUS_KEY = "Weighted Consensus"
DIRECTION_LABELS = {0: "Down", 1: "Up"}
VOLATILITY_LABELS = {0: "Low", 1: "Medium", 2: "High"}

os.makedirs(MODEL_FOLDER, exist_ok=True)


def normalize_symbol(symbol: Any) -> str:
    if symbol is None:
        return "UNKNOWN"
    normalized = str(symbol).strip().upper()
    return normalized or "UNKNOWN"


def safe_symbol_name(symbol: Any) -> str:
    safe_name = re.sub(r"[^A-Z0-9_-]+", "_", normalize_symbol(symbol))
    return safe_name.strip("_") or "UNKNOWN"


def get_symbol_model_folder(symbol: Any) -> str:
    folder = os.path.join(MODEL_FOLDER, safe_symbol_name(symbol))
    os.makedirs(folder, exist_ok=True)
    return folder


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return numeric_value if np.isfinite(numeric_value) else float(default)


def safe_round(value: Any, digits: int = 2) -> float:
    return round(safe_float(value), digits)


def validate_market_data(data: pd.DataFrame) -> pd.DataFrame:
    if data is None:
        raise ValueError("Stock data is required.")
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Stock data must be a pandas DataFrame.")
    if data.empty:
        raise ValueError("Stock data is empty.")

    missing_columns = [column for column in RAW_COLUMNS if column not in data.columns]
    if missing_columns:
        raise ValueError("Missing required market columns: " + ", ".join(missing_columns))

    cleaned = data[RAW_COLUMNS].copy()
    for column in RAW_COLUMNS:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned.replace([np.inf, -np.inf], np.nan, inplace=True)
    cleaned = cleaned.loc[~cleaned.index.duplicated(keep="last")]
    cleaned.sort_index(inplace=True)
    cleaned.dropna(subset=RAW_COLUMNS, inplace=True)
    cleaned = cleaned[
        (cleaned["Open"] > 0)
        & (cleaned["High"] > 0)
        & (cleaned["Low"] > 0)
        & (cleaned["Close"] > 0)
        & (cleaned["Volume"] >= 0)
    ]
    if len(cleaned) < MINIMUM_DATA_ROWS:
        raise InsufficientDataError(
            f"At least {MINIMUM_DATA_ROWS} valid historical records are required "
            "for AI prediction. Use a longer market-history period."
        )
    return cleaned


def create_data_fingerprint(data: pd.DataFrame) -> str:
    cleaned = validate_market_data(data)
    hashed_values = pd.util.hash_pandas_object(cleaned, index=True).values
    hasher = hashlib.sha256()
    hasher.update(np.asarray(hashed_values).tobytes())
    hasher.update(MODEL_VERSION.encode("utf-8"))
    hasher.update("|".join(FEATURE_COLUMNS).encode("utf-8"))
    return hasher.hexdigest()


def build_enriched_data(data: pd.DataFrame) -> pd.DataFrame:
    return cast(pd.DataFrame, add_indicators(validate_market_data(data)))


def build_supervised_frame(data: pd.DataFrame) -> pd.DataFrame:
    """Create feature rows at day t and targets at the next observed day t+1."""

    enriched = build_enriched_data(data)
    missing_features = [name for name in FEATURE_COLUMNS if name not in enriched.columns]
    if missing_features:
        raise ValueError("Feature engineering did not produce: " + ", ".join(missing_features))

    frame = enriched[FEATURE_COLUMNS].copy()
    frame[TARGET_COLUMN] = enriched["Close"].shift(-1)
    frame[DIRECTION_TARGET_COLUMN] = (
        enriched["Close"].shift(-1) > enriched["Close"]
    ).astype("float")
    frame[NEXT_VOLATILITY_COLUMN] = enriched["Daily_Return"].shift(-1).abs()

    observed_dates = pd.Series(enriched.index, index=enriched.index, dtype="object")
    frame[TARGET_DATE_COLUMN] = observed_dates.shift(-1)

    numeric_columns = [
        *FEATURE_COLUMNS,
        TARGET_COLUMN,
        DIRECTION_TARGET_COLUMN,
        NEXT_VOLATILITY_COLUMN,
    ]
    frame[numeric_columns] = frame[numeric_columns].replace([np.inf, -np.inf], np.nan)
    frame.dropna(
        subset=[
            *FEATURE_COLUMNS,
            TARGET_COLUMN,
            DIRECTION_TARGET_COLUMN,
            NEXT_VOLATILITY_COLUMN,
            TARGET_DATE_COLUMN,
        ],
        inplace=True,
    )
    frame[DIRECTION_TARGET_COLUMN] = frame[DIRECTION_TARGET_COLUMN].astype(int)

    if len(frame) < MINIMUM_SUPERVISED_ROWS:
        raise InsufficientDataError(
            f"At least {MINIMUM_SUPERVISED_ROWS} complete training records are "
            "required after indicator warm-up. Use two to five years of history."
        )
    return frame


def _chronological_split_index(length: int) -> int:
    split_index = int(length * TRAIN_RATIO)
    minimum_test_rows = max(5, int(length * 0.10))
    if split_index <= 0 or split_index >= length or length - split_index < minimum_test_rows:
        raise InsufficientDataError("Unable to create valid chronological training and testing datasets.")
    return split_index


def prepare_data(data: pd.DataFrame):
    frame = build_supervised_frame(data)
    split_index = _chronological_split_index(len(frame))
    X_train = frame[FEATURE_COLUMNS].iloc[:split_index].copy()
    X_test = frame[FEATURE_COLUMNS].iloc[split_index:].copy()
    y_train = frame[TARGET_COLUMN].iloc[:split_index].copy()
    y_test = frame[TARGET_COLUMN].iloc[split_index:].copy()
    y_train.attrs["target_dates"] = frame[TARGET_DATE_COLUMN].iloc[:split_index].tolist()
    y_test.attrs["target_dates"] = frame[TARGET_DATE_COLUMN].iloc[split_index:].tolist()
    return X_train, X_test, y_train, y_test


# ---------------------------------------------------------------------------
# Regression models
# ---------------------------------------------------------------------------

def train_linear_regression(X_train, y_train):
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("model", LinearRegression()),
    ])
    model.fit(X_train, y_train)
    return model


def train_elastic_net(X_train, y_train):
    model = Pipeline([
        ("scaler", StandardScaler()),
        (
            "model",
            ElasticNet(
                alpha=0.002,
                l1_ratio=0.25,
                max_iter=20_000,
                tol=1e-2,
                random_state=RANDOM_STATE,
            ),
        ),
    ])
    model.fit(X_train, y_train)
    return model


def train_decision_tree(X_train, y_train):
    model = DecisionTreeRegressor(
        max_depth=7,
        min_samples_split=8,
        min_samples_leaf=4,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)
    return model


def train_random_forest(X_train, y_train):
    model = RandomForestRegressor(
        n_estimators=140,
        max_depth=10,
        min_samples_split=8,
        min_samples_leaf=3,
        max_features="sqrt",
        random_state=RANDOM_STATE,
        n_jobs=get_model_worker_count(),
    )
    # scikit-learn 1.9 treats an empty warning-filter list as if its own
    # Parallel wrapper was bypassed. Keep a real default filter in scope so
    # configuration propagation works without suppressing genuine warnings.
    with warnings.catch_warnings():
        warnings.simplefilter("default")
        model.fit(X_train, y_train)
    return model


def train_gradient_boosting(X_train, y_train):
    model = HistGradientBoostingRegressor(
        learning_rate=0.055,
        max_iter=160,
        max_depth=6,
        min_samples_leaf=16,
        l2_regularization=0.15,
        early_stopping=True,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)
    return model


def train_svm(X_train, y_train):
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("model", SVR(C=12.0, epsilon=0.05, gamma="scale", kernel="rbf")),
    ])
    model.fit(X_train, y_train)
    return model


def train_knn(X_train, y_train):
    neighbor_count = max(3, min(9, int(math.sqrt(max(9, len(X_train)))) // 2 * 2 + 1))
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("model", KNeighborsRegressor(n_neighbors=neighbor_count, weights="distance", p=2)),
    ])
    model.fit(X_train, y_train)
    return model


def _train_model_by_name(model_name: str, X_train, y_train, parameters: dict[str, Any] | None = None):
    trainers = {
        "Linear Regression": train_linear_regression,
        "ElasticNet": train_elastic_net,
        "Decision Tree": train_decision_tree,
        "Random Forest": train_random_forest,
        "Gradient Boosting": train_gradient_boosting,
        "SVM": train_svm,
        "KNN": train_knn,
    }
    if model_name not in trainers:
        raise KeyError(f"Unknown regression model: {model_name}")
    model = trainers[model_name](X_train, y_train)
    if parameters:
        model.set_params(**parameters)
        with warnings.catch_warnings():
            warnings.simplefilter("default")
            model.fit(X_train, y_train)
    return model


# ---------------------------------------------------------------------------
# Classification models
# ---------------------------------------------------------------------------

def _classifier_factory(model_name: str):
    models = {
        "Decision Tree Classifier": DecisionTreeClassifier(
            max_depth=6,
            min_samples_split=10,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=RANDOM_STATE,
        ),
        "Random Forest Classifier": RandomForestClassifier(
            n_estimators=140,
            max_depth=9,
            min_samples_split=8,
            min_samples_leaf=3,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=RANDOM_STATE,
            n_jobs=get_model_worker_count(),
        ),
        "Gradient Boosting Classifier": HistGradientBoostingClassifier(
            learning_rate=0.06,
            max_iter=140,
            max_depth=6,
            min_samples_leaf=16,
            l2_regularization=0.15,
            class_weight="balanced",
            early_stopping=True,
            random_state=RANDOM_STATE,
        ),
        "SVM Classifier": CalibratedClassifierCV(
            estimator=Pipeline([
                ("scaler", StandardScaler()),
                (
                    "model",
                    SVC(
                        C=8.0,
                        gamma="scale",
                        kernel="rbf",
                        class_weight="balanced",
                    ),
                ),
            ]),
            method="sigmoid",
            cv=3,
            n_jobs=get_model_worker_count(),
            ensemble=False,
        ),
    }
    if model_name not in models:
        raise KeyError(f"Unknown classifier: {model_name}")
    return models[model_name]


def _fit_classifiers(X_train, y_train) -> dict[str, Any]:
    fitted = {}
    classes = np.unique(np.asarray(y_train, dtype=int))
    for name in CLASSIFIER_FILENAMES:
        if len(classes) < 2:
            model = DummyClassifier(strategy="most_frequent")
        else:
            model = _classifier_factory(name)
        with warnings.catch_warnings():
            warnings.simplefilter("default")
            model.fit(X_train, y_train)
        fitted[name] = model
    return fitted


def _classification_metrics(actual, predicted) -> dict[str, Any]:
    actual = np.asarray(actual, dtype=int)
    predicted = np.asarray(predicted, dtype=int)
    labels = sorted(set(actual.tolist()) | set(predicted.tolist()))
    return {
        "accuracy": safe_round(accuracy_score(actual, predicted) * 100, 2),
        "balanced_accuracy": safe_round(balanced_accuracy_score(actual, predicted) * 100, 2),
        "f1_weighted": safe_round(f1_score(actual, predicted, average="weighted", zero_division=0) * 100, 2),
        "samples": int(len(actual)),
        "confusion_matrix": confusion_matrix(actual, predicted, labels=labels).tolist(),
        "labels": labels,
    }


def _volatility_thresholds(values: Iterable[float]) -> tuple[float, float]:
    values = np.asarray(list(values), dtype=float)
    low, high = np.quantile(values, [1 / 3, 2 / 3])
    if high <= low:
        high = low + 1e-9
    return float(low), float(high)


def _volatility_classes(values, thresholds: tuple[float, float]) -> np.ndarray:
    low, high = thresholds
    classes = np.digitize(
        np.asarray(values, dtype=float), bins=[low, high], right=False
    )
    return np.asarray(classes, dtype=int)


def _safe_predict_proba(model, X) -> tuple[np.ndarray, np.ndarray]:
    predicted = np.asarray(model.predict(X), dtype=int)
    if hasattr(model, "predict_proba"):
        probabilities = np.asarray(model.predict_proba(X), dtype=float)
    else:
        probabilities = np.ones((len(predicted), 1), dtype=float)
    return predicted, probabilities


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_predictions(predictions, actual_values, reference_values=None):
    predictions = np.asarray(predictions, dtype=float).reshape(-1)
    actual_values = np.asarray(actual_values, dtype=float).reshape(-1)
    if len(predictions) != len(actual_values) or len(actual_values) == 0:
        raise ValueError("Aligned non-empty prediction and actual arrays are required.")

    r2 = r2_score(actual_values, predictions) if len(actual_values) > 1 else 0.0
    mae = mean_absolute_error(actual_values, predictions)
    rmse = np.sqrt(mean_squared_error(actual_values, predictions))
    absolute_errors = np.abs(actual_values - predictions)
    non_zero_mask = actual_values != 0
    mape = (
        np.mean(absolute_errors[non_zero_mask] / np.abs(actual_values[non_zero_mask])) * 100
        if np.any(non_zero_mask)
        else 0.0
    )

    if reference_values is not None:
        reference_values = np.asarray(reference_values, dtype=float).reshape(-1)
        if len(reference_values) != len(actual_values):
            raise ValueError("Reference values must align with predictions.")
        actual_direction = np.sign(actual_values - reference_values)
        predicted_direction = np.sign(predictions - reference_values)
    else:
        actual_direction = np.sign(np.diff(actual_values))
        predicted_direction = np.sign(np.diff(predictions))

    directional_accuracy = (
        np.mean(actual_direction == predicted_direction) * 100
        if len(actual_direction) > 0
        else 0.0
    )
    bias = np.mean(predictions - actual_values)
    return {
        "accuracy": safe_round(r2 * 100, 2),
        "r2_score": safe_round(r2, 4),
        "mae": safe_round(mae, 4),
        "rmse": safe_round(rmse, 4),
        "mape": safe_round(mape, 2),
        "directional_accuracy": safe_round(directional_accuracy, 2),
        "median_absolute_error": safe_round(np.median(absolute_errors), 4),
        "bias": safe_round(bias, 4),
        "test_samples": int(len(actual_values)),
        "metric_label": "R² Score",
    }


def evaluate_model(model, X_test, y_test):
    return evaluate_predictions(
        model.predict(X_test),
        y_test,
        reference_values=X_test["Close"].to_numpy(dtype=float),
    )


def compute_baseline_metrics(X_test, y_test):
    baseline_predictions = X_test["Close"].to_numpy(dtype=float)
    return evaluate_predictions(
        baseline_predictions,
        y_test,
        reference_values=baseline_predictions,
    )


def _error_bands(predictions, actual_values) -> dict[str, float]:
    errors = np.asarray(actual_values, dtype=float) - np.asarray(predictions, dtype=float)
    absolute_errors = np.abs(errors)
    return {
        "p80": safe_round(np.quantile(absolute_errors, 0.80), 4),
        "p95": safe_round(np.quantile(absolute_errors, 0.95), 4),
        "lower_residual_p10": safe_round(np.quantile(errors, 0.10), 4),
        "upper_residual_p90": safe_round(np.quantile(errors, 0.90), 4),
    }


def _serialize_date(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def build_backtest_results(models, X_test, y_test, target_dates=None, fold_number=None):
    if X_test is None or y_test is None or len(X_test) != len(y_test):
        raise ValueError("Valid aligned test data is required.")
    if target_dates is None:
        target_dates = y_test.attrs.get("target_dates") if hasattr(y_test, "attrs") else None
    if target_dates is None or len(target_dates) != len(y_test):
        target_dates = list(y_test.index)

    frame = pd.DataFrame(index=np.arange(len(X_test)))
    frame["Feature Date"] = list(X_test.index)
    frame["Date"] = list(target_dates)
    frame["Actual"] = pd.to_numeric(y_test, errors="coerce").to_numpy()
    frame[BASELINE_KEY] = pd.to_numeric(X_test["Close"], errors="coerce").to_numpy()
    for model_name, model in models.items():
        frame[model_name] = model.predict(X_test)
    if fold_number is not None:
        frame["Fold"] = int(fold_number)

    numeric_columns = ["Actual", BASELINE_KEY, *models.keys()]
    frame[numeric_columns] = frame[numeric_columns].replace([np.inf, -np.inf], np.nan)
    frame.dropna(subset=["Date", *numeric_columns], inplace=True)

    rows = []
    for _, row in frame.iterrows():
        item: dict[str, Any] = {
            "Feature Date": _serialize_date(row["Feature Date"]),
            "Date": _serialize_date(row["Date"]),
        }
        if fold_number is not None:
            item["Fold"] = int(row["Fold"])
        for column in numeric_columns:
            item[column] = safe_round(row[column], 4)
        rows.append(item)
    return rows


def _resolve_walk_forward_splits(sample_count: int) -> int:
    adaptive = max(2, sample_count // 70)
    return min(WALK_FORWARD_SPLITS, adaptive, sample_count - 1)


def run_walk_forward_backtest(data: pd.DataFrame, model_parameters: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    frame = build_supervised_frame(data)
    X = frame[FEATURE_COLUMNS]
    y = frame[TARGET_COLUMN]
    target_dates = frame[TARGET_DATE_COLUMN]
    y_direction = frame[DIRECTION_TARGET_COLUMN]
    y_volatility = frame[NEXT_VOLATILITY_COLUMN]

    split_count = _resolve_walk_forward_splits(len(frame))
    splitter = TimeSeriesSplit(n_splits=split_count, gap=1)

    rows: list[dict[str, Any]] = []
    fold_details: list[dict[str, Any]] = []
    direction_records: dict[str, dict[str, list[int]]] = {
        name: {"actual": [], "predicted": []} for name in CLASSIFIER_FILENAMES
    }
    volatility_records: dict[str, dict[str, list[int]]] = {
        name: {"actual": [], "predicted": []} for name in CLASSIFIER_FILENAMES
    }

    for fold_number, (train_indices, test_indices) in enumerate(splitter.split(X), start=1):
        X_train, X_test = X.iloc[train_indices], X.iloc[test_indices]
        y_train, y_test = y.iloc[train_indices], y.iloc[test_indices]

        fold_models = {
            model_name: _train_model_by_name(
                model_name, X_train, y_train, (model_parameters or {}).get(model_name)
            )
            for model_name in MODEL_FILENAMES
        }
        fold_rows = build_backtest_results(
            fold_models,
            X_test,
            y_test,
            target_dates=target_dates.iloc[test_indices].tolist(),
            fold_number=fold_number,
        )
        rows.extend(fold_rows)

        fold_frame = pd.DataFrame(fold_rows)
        fold_actual = fold_frame["Actual"].to_numpy(dtype=float)
        fold_reference = fold_frame[BASELINE_KEY].to_numpy(dtype=float)
        fold_metrics = {
            model_name: evaluate_predictions(
                fold_frame[model_name].to_numpy(dtype=float),
                fold_actual,
                reference_values=fold_reference,
            )
            for model_name in MODEL_FILENAMES
        }
        fold_metrics[BASELINE_KEY] = evaluate_predictions(
            fold_reference,
            fold_actual,
            reference_values=fold_reference,
        )

        # Direction classifiers use binary next-day up/down labels.
        direction_models = _fit_classifiers(X_train, y_direction.iloc[train_indices])
        direction_fold_metrics = {}
        for name, model in direction_models.items():
            predicted = model.predict(X_test)
            actual = y_direction.iloc[test_indices].to_numpy(dtype=int)
            direction_records[name]["actual"].extend(actual.tolist())
            direction_records[name]["predicted"].extend(np.asarray(predicted, dtype=int).tolist())
            direction_fold_metrics[name] = _classification_metrics(actual, predicted)

        # Volatility classes are derived only from the training fold's thresholds.
        thresholds = _volatility_thresholds(y_volatility.iloc[train_indices])
        volatility_train = _volatility_classes(y_volatility.iloc[train_indices], thresholds)
        volatility_test = _volatility_classes(y_volatility.iloc[test_indices], thresholds)
        volatility_models = _fit_classifiers(X_train, volatility_train)
        volatility_fold_metrics = {}
        for name, model in volatility_models.items():
            predicted = model.predict(X_test)
            volatility_records[name]["actual"].extend(volatility_test.tolist())
            volatility_records[name]["predicted"].extend(np.asarray(predicted, dtype=int).tolist())
            volatility_fold_metrics[name] = _classification_metrics(volatility_test, predicted)

        fold_details.append({
            "fold": fold_number,
            "train_start": _serialize_date(X_train.index[0]),
            "train_end": _serialize_date(X_train.index[-1]),
            "validation_start": _serialize_date(target_dates.iloc[test_indices].iloc[0]),
            "validation_end": _serialize_date(target_dates.iloc[test_indices].iloc[-1]),
            "train_samples": int(len(train_indices)),
            "test_samples": int(len(test_indices)),
            "metrics": fold_metrics,
            "direction_metrics": direction_fold_metrics,
            "volatility_metrics": volatility_fold_metrics,
        })

    if not rows:
        raise ValueError("Walk-forward validation did not produce any test predictions.")

    backtest_frame = pd.DataFrame(rows)
    actual = backtest_frame["Actual"].to_numpy(dtype=float)
    reference = backtest_frame[BASELINE_KEY].to_numpy(dtype=float)
    metrics, error_bands = {}, {}
    for model_name in MODEL_FILENAMES:
        predictions = backtest_frame[model_name].to_numpy(dtype=float)
        metrics[model_name] = evaluate_predictions(predictions, actual, reference_values=reference)
        error_bands[model_name] = _error_bands(predictions, actual)
    metrics[BASELINE_KEY] = evaluate_predictions(reference, actual, reference_values=reference)
    error_bands[BASELINE_KEY] = _error_bands(reference, actual)

    direction_metrics = {
        name: _classification_metrics(values["actual"], values["predicted"])
        for name, values in direction_records.items()
    }
    volatility_metrics = {
        name: _classification_metrics(values["actual"], values["predicted"])
        for name, values in volatility_records.items()
    }

    return {
        "backtest": rows,
        "metrics": metrics,
        "error_bands": error_bands,
        "folds": split_count,
        "test_samples": len(rows),
        "training_rows": len(frame),
        "method": "Expanding-window walk-forward validation with one-row gap",
        "fold_details": fold_details,
        "direction_metrics": direction_metrics,
        "volatility_metrics": volatility_metrics,
    }


# ---------------------------------------------------------------------------
# Storage and metadata
# ---------------------------------------------------------------------------

def get_model_path(filename: str, symbol=None) -> str:
    folder = MODEL_FOLDER if symbol is None else get_symbol_model_folder(symbol)
    return os.path.join(folder, filename)


def save_model(model, filename: str, symbol=None) -> str:
    filepath = get_model_path(filename, symbol)
    joblib.dump(model, filepath, compress=3)
    return filepath


def load_model(filename: str, symbol=None, expected_sha256: str | None = None):
    filepath = get_model_path(filename, symbol)
    if not os.path.exists(filepath):
        return None
    try:
        return load_trusted_joblib_artifact(
            filepath,
            allowed_roots=(MODEL_FOLDER,),
            expected_sha256=expected_sha256,
        )
    except (OSError, EOFError, ValueError, TypeError):
        return None


def get_metadata_path(symbol) -> str:
    return os.path.join(get_symbol_model_folder(symbol), "metadata.json")


def save_metadata(symbol, fingerprint: str, results: dict[str, Any]) -> None:
    metadata = {
        "symbol": normalize_symbol(symbol),
        "model_version": MODEL_VERSION,
        "data_fingerprint": fingerprint,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": FEATURE_COLUMNS,
        "metrics": {
            name: results[name]["metrics"]
            for name in [*MODEL_FILENAMES, BASELINE_KEY]
        },
        "error_bands": {
            name: results[name].get("error_bands", {})
            for name in [*MODEL_FILENAMES, BASELINE_KEY]
        },
        "backtest": results.get("Backtest", []),
        "evaluation": results.get("Evaluation", {}),
        "classification": results.get("Classification", {}),
        "feature_importance": results.get("Feature Importance", {}),
    }
    with open(get_metadata_path(symbol), "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2)


def load_metadata(symbol):
    filepath = get_metadata_path(symbol)
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
        return metadata if isinstance(metadata, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def predict_next_day(model, data: pd.DataFrame) -> float:
    enriched = build_enriched_data(data)
    latest = enriched.iloc[-1]
    missing = [
        column
        for column in FEATURE_COLUMNS
        if not np.isfinite(safe_float(latest.get(column), default=np.nan))
    ]
    if missing:
        raise InsufficientDataError("Not enough history to compute all features. Missing: " + ", ".join(missing))
    features = pd.DataFrame(
        [{column: safe_float(latest[column]) for column in FEATURE_COLUMNS}],
        columns=FEATURE_COLUMNS,
    )
    prediction = safe_float(model.predict(features)[0], default=np.nan)
    if not np.isfinite(prediction) or prediction <= 0:
        raise ValueError("The model generated an invalid price prediction.")
    return round(prediction, 2)


def _latest_feature_row(data: pd.DataFrame) -> pd.DataFrame:
    enriched = build_enriched_data(data)
    latest = enriched.iloc[-1]
    values = {column: safe_float(latest.get(column), default=np.nan) for column in FEATURE_COLUMNS}
    if not all(np.isfinite(value) for value in values.values()):
        raise InsufficientDataError("The latest feature row is incomplete.")
    return pd.DataFrame([values], columns=FEATURE_COLUMNS)


def _extract_feature_importance(model, X_reference, y_reference) -> list[dict[str, Any]]:
    estimator = model.named_steps.get("model") if isinstance(model, Pipeline) else model
    values = None
    method = "Not available"

    if hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float)
        method = "Native tree importance"
    elif hasattr(estimator, "coef_"):
        values = np.abs(np.asarray(estimator.coef_, dtype=float).reshape(-1))
        method = "Absolute standardized coefficient"
    else:
        try:
            sample_size = min(120, len(X_reference))
            sample_X = X_reference.iloc[-sample_size:]
            sample_y = y_reference.iloc[-sample_size:]
            result = permutation_importance(
                model,
                sample_X,
                sample_y,
                n_repeats=4,
                random_state=RANDOM_STATE,
                scoring="neg_root_mean_squared_error",
            )
            values = np.maximum(np.asarray(result.importances_mean, dtype=float), 0.0)
            method = "Holdout permutation importance"
        except Exception:
            values = None

    if values is None or len(values) != len(FEATURE_COLUMNS):
        return []
    total = float(np.sum(values))
    normalized = values / total * 100 if total > 0 else values
    rows = [
        {"feature": feature, "importance": safe_round(value, 4), "method": method}
        for feature, value in zip(FEATURE_COLUMNS, normalized)
    ]
    return sorted(rows, key=lambda item: item["importance"], reverse=True)


def _train_final_models(data: pd.DataFrame, model_parameters: dict[str, dict[str, Any]] | None = None):
    frame = build_supervised_frame(data)
    X, y = frame[FEATURE_COLUMNS], frame[TARGET_COLUMN]
    models = {
        name: _train_model_by_name(name, X, y, (model_parameters or {}).get(name))
        for name in MODEL_FILENAMES
    }
    return models, frame


def _classifier_prediction(models, latest_features, metrics, label_map) -> dict[str, Any]:
    per_model = {}
    for name, model in models.items():
        predicted, probabilities = _safe_predict_proba(model, latest_features)
        predicted_class = int(predicted[0])
        model_classes = getattr(model, "classes_", None)
        if model_classes is None and isinstance(model, Pipeline):
            model_classes = getattr(model.named_steps.get("model"), "classes_", None)
        model_classes = list(model_classes) if model_classes is not None else [predicted_class]
        probability_map = {
            label_map.get(int(class_value), str(class_value)): safe_round(probabilities[0][index] * 100, 2)
            for index, class_value in enumerate(model_classes)
            if index < probabilities.shape[1]
        }
        per_model[name] = {
            "prediction": label_map.get(predicted_class, str(predicted_class)),
            "probabilities": probability_map,
            "metrics": metrics.get(name, {}),
        }

    ranked = sorted(
        per_model,
        key=lambda name: (
            -safe_float(per_model[name]["metrics"].get("balanced_accuracy"), 0.0),
            -safe_float(per_model[name]["metrics"].get("f1_weighted"), 0.0),
        ),
    )
    best = ranked[0]
    return {
        "best_model": best,
        "prediction": per_model[best]["prediction"],
        "probabilities": per_model[best]["probabilities"],
        "models": per_model,
    }


def get_tuning_path(symbol: str) -> str:
    return os.path.join(get_symbol_model_folder(symbol), "tuned_parameters.json")


def load_tuned_parameters(symbol: str) -> dict[str, dict[str, Any]]:
    path = get_tuning_path(symbol)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        parameters = payload.get("parameters", payload)
        return parameters if isinstance(parameters, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def train_models(data: pd.DataFrame, symbol="UNKNOWN"):
    normalized_symbol = normalize_symbol(symbol)
    tuned_parameters = load_tuned_parameters(normalized_symbol)
    evaluation = run_walk_forward_backtest(data, tuned_parameters)
    trained_models, frame = _train_final_models(data, tuned_parameters)
    results: dict[str, Any] = {}

    for model_name, model in trained_models.items():
        save_model(model, MODEL_FILENAMES[model_name], normalized_symbol)
        results[model_name] = {
            "prediction": predict_next_day(model, data),
            "metrics": evaluation["metrics"][model_name],
            "error_bands": evaluation["error_bands"][model_name],
        }

    latest_close = safe_float(build_enriched_data(data)["Close"].iloc[-1])
    results[BASELINE_KEY] = {
        "prediction": round(latest_close, 2),
        "metrics": evaluation["metrics"][BASELINE_KEY],
        "error_bands": evaluation["error_bands"][BASELINE_KEY],
    }
    results["Backtest"] = evaluation["backtest"]
    results["Evaluation"] = {
        "method": evaluation["method"],
        "folds": evaluation["folds"],
        "test_samples": evaluation["test_samples"],
        "training_rows": len(frame),
        "fold_details": evaluation.get("fold_details", []),
        "feature_count": len(FEATURE_COLUMNS),
        "model_version": MODEL_VERSION,
        "tuned_models": sorted(tuned_parameters),
    }

    X = frame[FEATURE_COLUMNS]
    direction_models = _fit_classifiers(X, frame[DIRECTION_TARGET_COLUMN])
    volatility_thresholds = _volatility_thresholds(frame[NEXT_VOLATILITY_COLUMN])
    volatility_y = _volatility_classes(frame[NEXT_VOLATILITY_COLUMN], volatility_thresholds)
    volatility_models = _fit_classifiers(X, volatility_y)
    for name, model in direction_models.items():
        save_model(model, f"direction_{CLASSIFIER_FILENAMES[name]}", normalized_symbol)
    for name, model in volatility_models.items():
        save_model(model, f"volatility_{CLASSIFIER_FILENAMES[name]}", normalized_symbol)

    latest_features = _latest_feature_row(data)
    results["Classification"] = {
        "direction": _classifier_prediction(
            direction_models,
            latest_features,
            evaluation["direction_metrics"],
            DIRECTION_LABELS,
        ),
        "volatility": {
            **_classifier_prediction(
                volatility_models,
                latest_features,
                evaluation["volatility_metrics"],
                VOLATILITY_LABELS,
            ),
            "thresholds": {
                "low_medium": safe_round(volatility_thresholds[0], 4),
                "medium_high": safe_round(volatility_thresholds[1], 4),
                "unit": "absolute next-day return (%)",
            },
        },
    }

    importance = {}
    reference_count = max(20, int(len(frame) * 0.20))
    X_reference = X.iloc[-reference_count:]
    y_reference = frame[TARGET_COLUMN].iloc[-reference_count:]
    for name in ["Linear Regression", "ElasticNet", "Decision Tree", "Random Forest", "Gradient Boosting"]:
        importance[name] = _extract_feature_importance(
            trained_models[name], X_reference, y_reference
        )[:15]
    results["Feature Importance"] = importance

    save_metadata(normalized_symbol, create_data_fingerprint(data), results)
    return results


def load_saved_results(symbol, data: pd.DataFrame):
    normalized_symbol = normalize_symbol(symbol)
    metadata = load_metadata(normalized_symbol)
    if metadata is None:
        return None
    if metadata.get("model_version") != MODEL_VERSION:
        return None
    if metadata.get("data_fingerprint") != create_data_fingerprint(data):
        return None
    if metadata.get("feature_columns") != FEATURE_COLUMNS:
        return None

    saved_metrics = metadata.get("metrics", {})
    saved_bands = metadata.get("error_bands", {})
    saved_backtest = metadata.get("backtest", [])
    if not isinstance(saved_backtest, list) or not saved_backtest:
        return None

    results: dict[str, Any] = {}
    for model_name, filename in MODEL_FILENAMES.items():
        model = load_model(filename, normalized_symbol)
        metrics = saved_metrics.get(model_name)
        if model is None or not isinstance(metrics, dict):
            return None
        results[model_name] = {
            "prediction": predict_next_day(model, data),
            "metrics": metrics,
            "error_bands": saved_bands.get(model_name, {}),
        }

    baseline_metrics = saved_metrics.get(BASELINE_KEY)
    if not isinstance(baseline_metrics, dict):
        return None
    latest_close = safe_float(build_enriched_data(data)["Close"].iloc[-1])
    results[BASELINE_KEY] = {
        "prediction": round(latest_close, 2),
        "metrics": baseline_metrics,
        "error_bands": saved_bands.get(BASELINE_KEY, {}),
    }
    results["Backtest"] = saved_backtest
    results["Evaluation"] = metadata.get("evaluation", {})
    results["Classification"] = metadata.get("classification", {})
    results["Feature Importance"] = metadata.get("feature_importance", {})
    return results


# ---------------------------------------------------------------------------
# Ranking, consensus, confidence
# ---------------------------------------------------------------------------

def _available_model_names(results: dict[str, Any]) -> list[str]:
    return [name for name in MODEL_FILENAMES if isinstance(results.get(name), dict)]


def rank_models(results: dict[str, Any]) -> list[str]:
    def ranking_key(model_name: str):
        metrics = results[model_name].get("metrics", {})
        return (
            safe_float(metrics.get("rmse"), float("inf")),
            safe_float(metrics.get("mae"), float("inf")),
            -safe_float(metrics.get("directional_accuracy"), 0.0),
            -safe_float(metrics.get("r2_score"), float("-inf")),
        )
    return sorted(_available_model_names(results), key=ranking_key)


def calculate_consensus_weights(results: dict[str, Any]) -> dict[str, float]:
    models = _available_model_names(results)
    if not models:
        return {}
    baseline_rmse = safe_float(results.get(BASELINE_KEY, {}).get("metrics", {}).get("rmse"), 0.0)
    baseline_beaters = [
        name
        for name in models
        if baseline_rmse > 0
        and safe_float(results[name].get("metrics", {}).get("rmse"), float("inf")) < baseline_rmse
    ]
    eligible = baseline_beaters or models[:]
    raw_weights = {
        name: 1.0 / max(safe_float(results[name]["metrics"].get("rmse"), 0.0), 1e-9)
        for name in eligible
    }
    total = sum(raw_weights.values())
    weights = {name: 0.0 for name in models}
    if total <= 0:
        equal = 1.0 / len(eligible)
        for name in eligible:
            weights[name] = equal
    else:
        for name, value in raw_weights.items():
            weights[name] = value / total
    return weights


def _add_consensus_to_backtest(rows, weights):
    enriched_rows = []
    active = [name for name, weight in weights.items() if weight > 0]
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item[CONSENSUS_KEY] = safe_round(
            sum(safe_float(item.get(name)) * weights[name] for name in active), 4
        )
        enriched_rows.append(item)
    return enriched_rows


def _confidence_summary(results, best_model, consensus, error_margin, weights):
    baseline_rmse = safe_float(results.get(BASELINE_KEY, {}).get("metrics", {}).get("rmse"), 0.0)
    best_rmse = safe_float(results[best_model].get("metrics", {}).get("rmse"), baseline_rmse)
    current_price = max(safe_float(results.get(BASELINE_KEY, {}).get("prediction"), 0.0), 1e-9)
    improvement = max(0.0, (baseline_rmse - best_rmse) / baseline_rmse) if baseline_rmse > 0 else 0.0
    active_predictions = [
        safe_float(results[name].get("prediction"))
        for name, weight in weights.items()
        if weight > 0
    ]
    disagreement = np.std(active_predictions) / current_price if active_predictions else 1.0
    relative_band = error_margin / current_price
    score = 35 + improvement * 45 - min(disagreement * 400, 20) - min(relative_band * 180, 30)
    score = int(np.clip(round(score), 5, 95))
    label = "High" if score >= 72 else "Moderate" if score >= 48 else "Low"
    return {
        "label": label,
        "score": score,
        "method": "Baseline improvement, model agreement, and empirical error width",
        "note": "This is model-evidence quality, not the probability that the forecast is correct.",
    }


def build_prediction_response(symbol, results, models_retrained):
    ranking = rank_models(results)
    if not ranking:
        raise ValueError("No valid forecasting models are available.")
    best_model = ranking[0]
    weights = calculate_consensus_weights(results)
    active = [name for name, weight in weights.items() if weight > 0]
    consensus = round(sum(safe_float(results[name]["prediction"]) * weights[name] for name in active), 2)
    error_margin = sum(
        safe_float(
            results[name].get("error_bands", {}).get("p80"),
            results[name].get("metrics", {}).get("rmse", 0.0),
        ) * weights[name]
        for name in active
    )

    baseline_metrics = results.get(BASELINE_KEY, {}).get("metrics", {})
    best_metrics = results[best_model].get("metrics", {})
    best_beats_baseline = (
        safe_float(best_metrics.get("rmse"), float("inf"))
        < safe_float(baseline_metrics.get("rmse"), float("inf"))
        and safe_float(best_metrics.get("mae"), float("inf"))
        < safe_float(baseline_metrics.get("mae"), float("inf"))
    )

    return {
        "symbol": normalize_symbol(symbol),
        **{name: results[name] for name in _available_model_names(results)},
        BASELINE_KEY: results.get(BASELINE_KEY),
        "Regression Models": _available_model_names(results),
        "Consensus Prediction": consensus,
        "Consensus Method": "Inverse-RMSE weighting of baseline-beating models",
        "Consensus Eligible Models": active,
        "Consensus Weights": {name: safe_round(weight * 100, 2) for name, weight in weights.items()},
        "Prediction Interval": {
            "lower": round(max(0.0, consensus - error_margin), 2),
            "upper": round(consensus + error_margin, 2),
            "coverage": f"{int(PREDICTION_INTERVAL_QUANTILE * 100)}% empirical error band",
            "method": "Weighted walk-forward absolute-residual quantile",
        },
        "Confidence": _confidence_summary(results, best_model, consensus, error_margin, weights),
        "Best Model": best_model,
        "Model Ranking": ranking,
        "Best Model Beats Baseline": bool(best_beats_baseline),
        "Models Retrained": bool(models_retrained),
        "Backtest": _add_consensus_to_backtest(results.get("Backtest", []), weights),
        "Evaluation": results.get("Evaluation", {}),
        "Classification": results.get("Classification", {}),
        "Feature Importance": results.get("Feature Importance", {}),
        "Current Price": results.get(BASELINE_KEY, {}).get("prediction"),
        "Generated At": datetime.now(timezone.utc).isoformat(),
        "Disclaimer": "Experimental research output; not investment advice.",
    }


def get_model_status(symbol: str) -> dict[str, Any]:
    metadata = load_metadata(symbol) or {}
    metrics = metadata.get("metrics", {})
    baseline_rmse = safe_float(metrics.get(BASELINE_KEY, {}).get("rmse"), 0.0)
    models = []
    for name, filename in MODEL_FILENAMES.items():
        rmse = safe_float(metrics.get(name, {}).get("rmse"), 0.0)
        models.append({
            "model": name,
            "trained": os.path.exists(get_model_path(filename, symbol)),
            "rmse": rmse or None,
            "beats_baseline": bool(baseline_rmse and rmse and rmse < baseline_rmse),
        })
    return {
        "symbol": normalize_symbol(symbol),
        "model_version": metadata.get("model_version", MODEL_VERSION),
        "trained_at": metadata.get("trained_at"),
        "tuned_models": metadata.get("evaluation", {}).get("tuned_models", []),
        "models": models,
    }


def predict(symbol, data: pd.DataFrame):
    normalized_symbol = normalize_symbol(symbol)
    try:
        validate_market_data(data)
        results = load_saved_results(normalized_symbol, data)
        models_retrained = False
        if results is None:
            results = train_models(data, normalized_symbol)
            models_retrained = True
        return build_prediction_response(normalized_symbol, results, models_retrained)
    except Exception as error:
        return {"symbol": normalized_symbol, "error": str(error)}
