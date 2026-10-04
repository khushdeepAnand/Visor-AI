"""Optional, explicitly experimental LSTM-with-attention forecasting pipeline.

TensorFlow is intentionally kept out of the default core requirements.
Install requirements-optional.txt and enable this model only after it beats the
same naive baseline used by prediction.py on chronological validation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

from prediction import FEATURE_COLUMNS, TARGET_COLUMN, build_supervised_frame, safe_float

LOOKBACK = 30
RANDOM_STATE = 42


def tensorflow_available() -> bool:
    try:
        import tensorflow  # noqa: F401
        return True
    except ImportError:
        return False


def build_sequences(X: np.ndarray, y: np.ndarray, lookback: int = LOOKBACK):
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if len(X) != len(y):
        raise ValueError("X and y must have equal length")
    sequences, targets = [], []
    for index in range(lookback, len(X)):
        sequences.append(X[index - lookback:index])
        targets.append(y[index])
    return np.asarray(sequences, dtype=np.float32), np.asarray(targets, dtype=np.float32)


def _tensorflow():
    try:
        import tensorflow as tf
    except ImportError as error:
        raise RuntimeError(
            "The experimental LSTM requires TensorFlow. Install requirements-optional.txt."
        ) from error
    tf.keras.utils.set_random_seed(RANDOM_STATE)
    try:
        tf.config.experimental.enable_op_determinism()
    except Exception:
        pass
    return tf


def build_model(feature_count: int, lookback: int = LOOKBACK):
    """Build an LSTM encoder with self-attention and residual normalization."""

    tf = _tensorflow()
    inputs = tf.keras.layers.Input(shape=(lookback, feature_count), name="market_sequence")
    encoded = tf.keras.layers.LSTM(64, return_sequences=True, name="lstm_encoder")(inputs)
    encoded = tf.keras.layers.Dropout(0.20, name="encoder_dropout")(encoded)
    attention = tf.keras.layers.MultiHeadAttention(
        num_heads=4,
        key_dim=16,
        dropout=0.10,
        name="temporal_self_attention",
    )(encoded, encoded)
    encoded = tf.keras.layers.LayerNormalization(name="attention_norm")(encoded + attention)
    encoded = tf.keras.layers.LSTM(32, return_sequences=True, name="lstm_decoder")(encoded)
    pooled = tf.keras.layers.GlobalAveragePooling1D(name="temporal_pooling")(encoded)
    pooled = tf.keras.layers.Dropout(0.20, name="head_dropout")(pooled)
    hidden = tf.keras.layers.Dense(24, activation="relu", name="forecast_head")(pooled)
    outputs = tf.keras.layers.Dense(1, name="next_close")(hidden)
    model = tf.keras.Model(inputs=inputs, outputs=outputs, name="stockpilot_lstm_attention")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001, clipnorm=1.0),
        loss=tf.keras.losses.Huber(),
        metrics=[tf.keras.metrics.MeanAbsoluteError(name="mae")],
    )
    return model


def evaluate_experimental_lstm(
    data: pd.DataFrame,
    *,
    lookback: int = LOOKBACK,
    epochs: int = 40,
    batch_size: int = 32,
) -> dict[str, Any]:
    tf = _tensorflow()
    frame = build_supervised_frame(data)
    split = max(lookback + 20, int(len(frame) * 0.80))
    if len(frame) - split < 10:
        raise ValueError("Not enough chronological validation rows for the LSTM.")

    feature_scaler = StandardScaler().fit(frame[FEATURE_COLUMNS].iloc[:split])
    target_scaler = StandardScaler().fit(frame[[TARGET_COLUMN]].iloc[:split])
    X_scaled = feature_scaler.transform(frame[FEATURE_COLUMNS])
    y_scaled = target_scaler.transform(frame[[TARGET_COLUMN]]).reshape(-1)
    X_sequence, y_sequence = build_sequences(X_scaled, y_scaled, lookback)
    target_indices = np.arange(lookback, len(frame))
    train_mask = target_indices < split
    test_mask = ~train_mask

    model = build_model(len(FEATURE_COLUMNS), lookback)
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=6, restore_best_weights=True, min_delta=1e-5
        )
    ]
    history = model.fit(
        X_sequence[train_mask],
        y_sequence[train_mask],
        validation_split=0.15,
        epochs=max(1, int(epochs)),
        batch_size=max(8, int(batch_size)),
        callbacks=callbacks,
        shuffle=False,
        verbose=0,
    )
    predicted_scaled = model.predict(X_sequence[test_mask], verbose=0).reshape(-1, 1)
    predictions = target_scaler.inverse_transform(predicted_scaled).reshape(-1)
    actual = frame[TARGET_COLUMN].iloc[target_indices[test_mask]].to_numpy(dtype=float)
    baseline = frame["Close"].iloc[target_indices[test_mask]].to_numpy(dtype=float)
    rmse = float(np.sqrt(mean_squared_error(actual, predictions)))
    baseline_rmse = float(np.sqrt(mean_squared_error(actual, baseline)))
    mae = float(mean_absolute_error(actual, predictions))
    direction_accuracy = float(np.mean(np.sign(predictions - baseline) == np.sign(actual - baseline)) * 100)
    return {
        "model": model,
        "feature_scaler": feature_scaler,
        "target_scaler": target_scaler,
        "metrics": {
            "rmse": rmse,
            "mae": mae,
            "directional_accuracy": direction_accuracy,
            "test_samples": int(test_mask.sum()),
        },
        "baseline_rmse": baseline_rmse,
        "eligible_for_consensus": False,
        "promotion_gate": {"promoted": False, "reason": "Single holdout only; run multi-fold chronological gate before promotion"},
        "epochs_trained": len(history.history.get("loss", [])),
        "label": "Experimental",
        "architecture": "LSTM encoder + multi-head temporal self-attention",
    }




def chronological_fold_boundaries(
    row_count: int,
    *,
    folds: int = 3,
    initial_train_fraction: float = 0.55,
) -> list[tuple[int, int, int]]:
    """Return deterministic expanding-window ``(train_end, test_start, test_end)`` folds.

    Indices are positional and test regions never overlap training data. The
    helper is intentionally framework-free so CI can verify chronology even
    when optional TensorFlow is not installed.
    """
    if folds < 3:
        raise ValueError("At least three chronological folds are required for promotion.")
    if row_count < 60:
        raise ValueError("At least 60 supervised rows are required for the LSTM promotion harness.")
    if not 0.40 <= initial_train_fraction <= 0.80:
        raise ValueError("initial_train_fraction must be between 0.40 and 0.80")
    first_train_end = max(30, int(row_count * initial_train_fraction))
    remaining = row_count - first_train_end
    test_size = remaining // folds
    if test_size < 5:
        raise ValueError("Not enough out-of-sample rows per chronological fold.")
    result: list[tuple[int, int, int]] = []
    for fold in range(folds):
        test_start = first_train_end + fold * test_size
        test_end = row_count if fold == folds - 1 else test_start + test_size
        result.append((test_start, test_start, test_end))
    return result


def evaluate_lstm_chronological_gate(
    data: pd.DataFrame,
    *,
    folds: int = 3,
    lookback: int = LOOKBACK,
    epochs: int = 30,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Train/evaluate a fresh LSTM on expanding chronological folds.

    Scaling is fit on each fold's training prefix only. The function returns
    fold metrics and the production promotion decision; it does not modify the
    production ensemble by itself.
    """
    tf = _tensorflow()
    frame = build_supervised_frame(data).reset_index(drop=True)
    boundaries = chronological_fold_boundaries(len(frame), folds=folds)
    fold_metrics: list[dict[str, float]] = []
    for fold_index, (train_end, test_start, test_end) in enumerate(boundaries, start=1):
        if train_end <= lookback:
            raise ValueError("Training prefix is shorter than the requested lookback.")
        feature_scaler = StandardScaler().fit(frame[FEATURE_COLUMNS].iloc[:train_end])
        target_scaler = StandardScaler().fit(frame[[TARGET_COLUMN]].iloc[:train_end])
        X_scaled = feature_scaler.transform(frame[FEATURE_COLUMNS].iloc[:test_end])
        y_scaled = target_scaler.transform(frame[[TARGET_COLUMN]].iloc[:test_end]).reshape(-1)
        X_sequence, y_sequence = build_sequences(X_scaled, y_scaled, lookback)
        target_indices = np.arange(lookback, test_end)
        train_mask = target_indices < train_end
        test_mask = (target_indices >= test_start) & (target_indices < test_end)

        model = build_model(len(FEATURE_COLUMNS), lookback)
        callback = tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=5, restore_best_weights=True, min_delta=1e-5
        )
        model.fit(
            X_sequence[train_mask],
            y_sequence[train_mask],
            validation_split=0.15,
            epochs=max(1, int(epochs)),
            batch_size=max(8, int(batch_size)),
            callbacks=[callback],
            shuffle=False,
            verbose=0,
        )
        predicted_scaled = model.predict(X_sequence[test_mask], verbose=0).reshape(-1, 1)
        predictions = target_scaler.inverse_transform(predicted_scaled).reshape(-1)
        actual = frame[TARGET_COLUMN].iloc[target_indices[test_mask]].to_numpy(dtype=float)
        baseline = frame["Close"].iloc[target_indices[test_mask]].to_numpy(dtype=float)
        fold_metrics.append({
            "fold": float(fold_index),
            "rmse": float(np.sqrt(mean_squared_error(actual, predictions))),
            "baseline_rmse": float(np.sqrt(mean_squared_error(actual, baseline))),
            "mae": float(mean_absolute_error(actual, predictions)),
            "samples": float(len(actual)),
        })
        tf.keras.backend.clear_session()

    gate = promotion_gate(fold_metrics)
    return {
        "fold_metrics": fold_metrics,
        "promotion_gate": gate,
        "eligible_for_consensus": bool(gate["promoted"]),
        "transformer_allowed": bool(gate["promoted"]),
        "note": "Transformer work remains conditional on this LSTM gate passing.",
    }

def promotion_gate(
    fold_metrics: list[dict[str, float]],
    *,
    min_folds: int = 3,
    min_rmse_improvement: float = 0.05,
    min_median_improvement: float = 0.075,
) -> dict[str, Any]:
    """Deterministic production gate for chronological LSTM evaluations.

    Every fold must beat the naive previous-close baseline by the configured
    margin and the median improvement must clear a stronger threshold. A single
    holdout can never promote the model.
    """
    if len(fold_metrics) < min_folds:
        return {"promoted": False, "reason": f"Need at least {min_folds} chronological folds", "folds": len(fold_metrics)}
    improvements: list[float] = []
    for row in fold_metrics:
        rmse = float(row["rmse"]); baseline = float(row["baseline_rmse"])
        if baseline <= 0:
            return {"promoted": False, "reason": "Invalid baseline RMSE", "folds": len(fold_metrics)}
        improvements.append((baseline - rmse) / baseline)
    median = float(np.median(improvements))
    promoted = min(improvements) >= min_rmse_improvement and median >= min_median_improvement
    return {
        "promoted": bool(promoted),
        "folds": len(fold_metrics),
        "fold_improvements": improvements,
        "median_improvement": median,
        "minimum_improvement": min(improvements),
        "required_minimum": min_rmse_improvement,
        "required_median": min_median_improvement,
        "reason": "Promotion gate passed" if promoted else "LSTM did not beat the naive baseline stably across folds",
    }


def save_experimental_lstm(result: dict[str, Any], symbol: str, folder: str | os.PathLike[str]) -> Path:
    root = Path(folder) / str(symbol).replace(".", "_").replace("/", "_")
    root.mkdir(parents=True, exist_ok=True)
    result["model"].save(root / "lstm_experimental.keras")
    joblib.dump(result["feature_scaler"], root / "lstm_feature_scaler.pkl")
    joblib.dump(result["target_scaler"], root / "lstm_target_scaler.pkl")
    metadata = {
        "label": "Experimental",
        "architecture": "LSTM encoder + multi-head temporal self-attention",
        "metrics": result["metrics"],
        "baseline_rmse": safe_float(result["baseline_rmse"]),
        "eligible_for_consensus": bool(result["eligible_for_consensus"]),
        "lookback": LOOKBACK,
        "feature_columns": FEATURE_COLUMNS,
    }
    (root / "lstm_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return root
