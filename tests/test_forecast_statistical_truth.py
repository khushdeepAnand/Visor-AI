from __future__ import annotations

import math

import numpy as np

from forecasting.interval_forecast import (
    DataSufficiencyReport,
    _adaptive_splits,
    _evaluate_published_predictions,
    _interval_half_width,
)
from services.forecast_presentation import present_forecast
from services.model_registry import model_registry


def _positions(part: slice, size: int) -> set[int]:
    return set(range(*part.indices(size)))


def test_train_meta_calibration_test_folds_are_distinct_and_chronological() -> None:
    size = 200
    train, meta, calibration, test = _adaptive_splits(size)
    folds = [_positions(part, size) for part in (train, meta, calibration, test)]

    assert all(folds)
    assert set.union(*folds) == set(range(size))
    assert sum(len(fold) for fold in folds) == size
    assert max(folds[0]) < min(folds[1]) < max(folds[1]) < min(folds[2])
    assert max(folds[2]) < min(folds[3])


def test_rmse_scores_the_exact_published_blend() -> None:
    actual = np.array([10.0, 20.0, 30.0])
    published = np.array([8.0, 22.0, 35.0])
    metrics = _evaluate_published_predictions(
        actual,
        published,
        published - 6.0,
        published + 6.0,
        confidence_level=0.8,
    )

    assert metrics.rmse == math.sqrt(np.mean((actual - published) ** 2))
    assert metrics.mae == np.mean(np.abs(actual - published))


def test_interval_width_has_no_maximum_cap() -> None:
    half_width = _interval_half_width(
        conformal_q=75.0,
        recent_sigma=50.0,
        reference_price=100.0,
        z_score=2.0,
    )

    assert half_width == 100.0
    assert half_width > 0.5 * 100.0


def test_data_sufficiency_has_explicit_evidence_states() -> None:
    cases = [
        (300, 50, "A"),
        (150, 25, "B"),
        (50, 8, "C"),
        (29, 5, "none"),
    ]
    for supervised_rows, validation_samples, expected in cases:
        report = DataSufficiencyReport.from_counts(
            raw_rows=supervised_rows + 10,
            cleaned_rows=supervised_rows + 5,
            supervised_rows=supervised_rows,
            validation_samples=validation_samples,
        )
        assert report.evidence_grade == expected


def test_low_history_language_is_honest_and_zones_are_unavailable() -> None:
    result = {
        "symbol": "NEWCO",
        "forecast": {"low": 80.0, "median": 100.0, "high": 120.0, "confidence_level": 0.8},
        "current_price": 100.0,
        "validation": {
            "empirical_coverage": 1.0,
            "nominal_coverage": 0.8,
            "samples": 8,
            "beats_naive_baseline": True,
        },
        "drift": {"drift_detected": False},
        "evidence": {"grade": "C", "summary": "Only limited history is available."},
        "forecast_status": "limited_history",
    }

    payload = present_forecast(result)

    assert payload["confidence"]["level"] == "low"
    assert "not dependable" in payload["confidence"]["summary"]
    assert payload["observation_zone"] is None
    assert payload["risk_zone"] is None
    assert "limited historical evidence" in payload["zones_unavailable_reason"]
    assert payload["forecast_status"] == "limited_history"


def test_quantile_models_are_registered_as_challengers_only() -> None:
    quantile = next(entry for entry in model_registry() if entry["name"] == "Direct quantile regression")

    assert quantile["status"] == "experimental"
    assert "do not determine published bounds" in quantile["role"]
