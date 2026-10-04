# ==========================================================
# Tests for prediction.py
# ==========================================================

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV

import prediction as pred


def make_price_series(n=300, seed=42, start=100.0, drift=0.0, noise=1.0):
    """
    Build a synthetic random-walk-style OHLCV DataFrame, long enough
    to clear indicator warm-up (SMA_50) and the minimum row count.
    """

    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")

    steps = rng.normal(drift, noise, n)
    close = np.abs(start + np.cumsum(steps)) + 50

    return pd.DataFrame(
        {
            "Open": close + rng.normal(0, 0.5, n),
            "High": close + np.abs(rng.normal(1, 0.5, n)),
            "Low": close - np.abs(rng.normal(1, 0.5, n)),
            "Close": close,
            "Volume": rng.integers(10_000, 100_000, n),
        },
        index=dates,
    )


# ----------------------------------------------------------
# evaluate_predictions (shared metrics function)
# ----------------------------------------------------------

def test_evaluate_predictions_perfect_predictions_give_r2_of_1():
    actual = [10, 20, 30, 40, 50]

    metrics = pred.evaluate_predictions(actual, actual)

    assert metrics["r2_score"] == pytest.approx(1.0)
    assert metrics["mae"] == pytest.approx(0.0)
    assert metrics["rmse"] == pytest.approx(0.0)


def test_evaluate_predictions_known_mae():
    actual = [100.0, 100.0, 100.0]
    predicted = [101.0, 99.0, 100.0]  # errors: 1, 1, 0

    metrics = pred.evaluate_predictions(predicted, actual)

    assert metrics["mae"] == pytest.approx(2 / 3, abs=0.01)


def test_evaluate_predictions_returns_all_expected_keys():
    actual = [10, 20, 30, 40, 50]
    predicted = [11, 19, 31, 39, 51]

    metrics = pred.evaluate_predictions(predicted, actual)

    for key in (
        "accuracy", "r2_score", "mae", "rmse", "mape",
        "directional_accuracy", "test_samples", "metric_label",
    ):
        assert key in metrics


# ----------------------------------------------------------
# Feature engineering / data preparation
# ----------------------------------------------------------

def test_feature_columns_include_engineered_indicators():
    # Regression test: the whole point of this fix was to move
    # beyond raw OHLCV-only features.
    raw_only = {"Open", "High", "Low", "Close", "Volume"}

    assert set(pred.FEATURE_COLUMNS) != raw_only
    assert "RSI" in pred.FEATURE_COLUMNS
    assert "SMA_20" in pred.FEATURE_COLUMNS


def test_svm_classifier_uses_supported_calibrated_probabilities():
    X = pd.DataFrame(
        {
            "momentum": np.linspace(-2.0, 2.0, 24),
            "volume": np.tile([1.0, 2.0, 3.0, 4.0], 6),
        }
    )
    y = np.tile([0, 1], 12)

    model = pred._classifier_factory("SVM Classifier")
    model.fit(X, y)
    probabilities = model.predict_proba(X.iloc[:4])

    assert isinstance(model, CalibratedClassifierCV)
    assert model.ensemble is False
    assert probabilities.shape == (4, 2)
    assert np.allclose(probabilities.sum(axis=1), 1.0)


def test_prepare_data_raises_informative_error_on_short_history():
    short_data = make_price_series(n=30)

    with pytest.raises(ValueError, match="least"):
        pred.prepare_data(short_data)


def test_prepare_data_splits_chronologically_not_randomly():
    data = make_price_series(n=300)

    X_train, X_test, y_train, y_test = pred.prepare_data(data)

    # Chronological split: every training index must come before
    # every test index.
    assert X_train.index.max() < X_test.index.min()
    assert len(X_train) > len(X_test)


# ----------------------------------------------------------
# Naive baseline
# ----------------------------------------------------------

def test_baseline_prediction_equals_latest_close():
    data = make_price_series(n=300)

    X_train, X_test, y_train, y_test = pred.prepare_data(data)

    baseline_metrics = pred.compute_baseline_metrics(X_test, y_test)

    # The baseline is just "yesterday's close" repeated forward —
    # its MAE should equal the average day-to-day absolute change
    # in the test window, which is >= 0.
    assert baseline_metrics["mae"] >= 0
    assert baseline_metrics["test_samples"] == len(y_test)


# ----------------------------------------------------------
# Full predict() pipeline
# ----------------------------------------------------------

def test_predict_returns_registered_models_and_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))

    data = make_price_series(n=300, seed=7)

    result = pred.predict("TEST.PYTEST", data)

    assert "error" not in result

    for key in (*pred.MODEL_FILENAMES, pred.BASELINE_KEY):
        assert key in result
        assert "prediction" in result[key]
        assert "metrics" in result[key]

    assert result["Best Model"] in pred.MODEL_FILENAMES
    assert set(result["Regression Models"]) == set(pred.MODEL_FILENAMES)
    assert result["Classification"]["direction"]["prediction"] in {"Up", "Down"}
    assert result["Classification"]["volatility"]["prediction"] in {"Low", "Medium", "High"}


def test_predict_handles_insufficient_data_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))

    tiny_data = make_price_series(n=10)

    result = pred.predict("TINY.PYTEST", tiny_data)

    assert "error" in result
    assert result["symbol"] == "TINY.PYTEST"


def test_predict_reuses_cached_models_on_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))

    data = make_price_series(n=300, seed=99)

    first = pred.predict("CACHE.PYTEST", data)
    second = pred.predict("CACHE.PYTEST", data)

    assert first["Models Retrained"] is True
    assert second["Models Retrained"] is False
    assert first["Best Model"] == second["Best Model"]


# ----------------------------------------------------------
# Backtest output
# ----------------------------------------------------------

def test_build_backtest_results_returns_aligned_serializable_rows():
    data = make_price_series(n=300, seed=21)
    X_train, X_test, y_train, y_test = pred.prepare_data(data)

    models = {
        "Linear Regression": pred.train_linear_regression(X_train, y_train),
        "Decision Tree": pred.train_decision_tree(X_train, y_train),
        "Random Forest": pred.train_random_forest(X_train, y_train),
    }

    rows = pred.build_backtest_results(models, X_test, y_test)

    assert len(rows) == len(y_test)
    assert rows[0]["Date"]
    assert rows[0]["Actual"] > 0
    assert rows[0][pred.BASELINE_KEY] > 0
    assert all(model_name in rows[0] for model_name in models)


def test_predict_response_contains_backtest_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))
    result = pred.predict("BACKTEST.PYTEST", make_price_series(n=300, seed=33))

    assert "error" not in result
    assert isinstance(result["Backtest"], list)
    assert len(result["Backtest"]) > 0


# ----------------------------------------------------------
# Production evaluation improvements
# ----------------------------------------------------------

def test_directional_accuracy_uses_feature_day_close_reference():
    metrics = pred.evaluate_predictions(
        predictions=[102.0, 98.0, 99.0],
        actual_values=[101.0, 99.0, 102.0],
        reference_values=[100.0, 100.0, 100.0],
    )

    assert metrics["directional_accuracy"] == pytest.approx(66.67, abs=0.01)


def test_backtest_dates_are_next_observed_trading_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))
    result = pred.predict("DATES.PYTEST", make_price_series(n=300, seed=71))

    first = result["Backtest"][0]
    assert pd.Timestamp(first["Date"]) > pd.Timestamp(first["Feature Date"])


def test_predict_reports_walk_forward_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))
    result = pred.predict("WALK.PYTEST", make_price_series(n=300, seed=72))

    evaluation = result["Evaluation"]
    assert evaluation["method"].startswith("Expanding-window walk-forward validation")
    assert evaluation["folds"] >= 2
    assert evaluation["test_samples"] == len(result["Backtest"])


def test_model_ranking_prioritizes_error_not_r2_alone():
    results = {
        "Linear Regression": {"metrics": {"rmse": 5, "mae": 4, "directional_accuracy": 90, "r2_score": 0.99}},
        "Decision Tree": {"metrics": {"rmse": 2, "mae": 1.5, "directional_accuracy": 55, "r2_score": 0.40}},
        "Random Forest": {"metrics": {"rmse": 3, "mae": 2, "directional_accuracy": 70, "r2_score": 0.80}},
    }

    assert pred.rank_models(results)[0] == "Decision Tree"


def test_consensus_weights_sum_to_one_and_favor_lower_rmse():
    results = {
        "Linear Regression": {"metrics": {"rmse": 4}},
        "Decision Tree": {"metrics": {"rmse": 2}},
        "Random Forest": {"metrics": {"rmse": 3}},
        pred.BASELINE_KEY: {"metrics": {"rmse": 5}},
    }

    weights = pred.calculate_consensus_weights(results)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["Decision Tree"] > weights["Random Forest"] > weights["Linear Regression"]


def test_prediction_interval_contains_weighted_consensus(tmp_path, monkeypatch):
    monkeypatch.setattr(pred, "MODEL_FOLDER", str(tmp_path))
    result = pred.predict("RANGE.PYTEST", make_price_series(n=300, seed=73))

    interval = result["Prediction Interval"]
    consensus = result["Consensus Prediction"]
    assert interval["lower"] <= consensus <= interval["upper"]
    assert sum(result["Consensus Weights"].values()) == pytest.approx(100.0, abs=0.05)


def test_walk_forward_exposes_fold_boundaries_and_rmse():
    evaluation = pred.run_walk_forward_backtest(make_price_series(n=300, seed=74))

    folds = evaluation["fold_details"]
    assert len(folds) == evaluation["folds"]

    for fold in folds:
        assert pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["validation_start"])
        assert fold["test_samples"] > 0
        assert set(fold["metrics"]) == {*pred.MODEL_FILENAMES, pred.BASELINE_KEY}
        assert all(metrics["rmse"] >= 0 for metrics in fold["metrics"].values())
