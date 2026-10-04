"""Tests for split-conformal calibration and walk-forward scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.conformal_calibration import (
    CALIBRATION_DISCLOSURES,
    MIN_SESSIONS,
    CalibrationError,
    calibrate_symbol,
    calibrated_range,
    calibration_report,
    conformal_half_width,
    describe_methodology,
    measured_coverage,
    walk_forward_errors,
)


def _random_walk(sessions: int = 420, seed: int = 7, start: float = 1000.0) -> pd.DataFrame:
    generator = np.random.default_rng(seed)
    steps = generator.normal(loc=0.0, scale=0.012, size=sessions)
    closes = start * np.exp(np.cumsum(steps))
    index = pd.date_range("2024-01-01", periods=sessions, freq="B")
    return pd.DataFrame({"close": closes}, index=index)


def _trending(sessions: int = 420, seed: int = 11, start: float = 500.0) -> pd.DataFrame:
    generator = np.random.default_rng(seed)
    steps = generator.normal(loc=0.0015, scale=0.004, size=sessions)
    closes = start * np.exp(np.cumsum(steps))
    index = pd.date_range("2024-01-01", periods=sessions, freq="B")
    return pd.DataFrame({"close": closes}, index=index)


def test_walk_forward_uses_no_future_information():
    closes = _random_walk(sessions=200)["close"].to_numpy()
    scored = walk_forward_errors(closes, horizon=1, model="random_walk")
    # The random-walk prediction for each scored session must equal the close of
    # the session immediately before it.
    train = 25
    expected = closes[train - 1 : closes.size - 1]
    assert np.allclose(scored["predictions"], expected)
    assert scored["actuals"].size == scored["predictions"].size


def test_mutating_the_tail_cannot_change_earlier_predictions():
    closes = _random_walk(sessions=220)["close"].to_numpy()
    baseline = walk_forward_errors(closes, horizon=3, model="damped_drift")
    tampered = closes.copy()
    tampered[-20:] = tampered[-20:] * 3.0
    after = walk_forward_errors(tampered, horizon=3, model="damped_drift")
    shared = min(baseline["predictions"].size, after["predictions"].size) - 25
    assert shared > 0
    assert np.allclose(baseline["predictions"][:shared], after["predictions"][:shared])


def test_half_width_grows_with_confidence():
    generator = np.random.default_rng(3)
    residuals = generator.normal(scale=5.0, size=400)
    widths = [conformal_half_width(residuals, level) for level in (0.5, 0.68, 0.8, 0.9, 0.95)]
    assert widths == sorted(widths)
    assert widths[0] > 0


def test_coverage_holds_when_the_price_level_rises():
    # Rupee residuals grow with the price level, so a half-width calibrated on
    # early, cheaper sessions would under-cover later ones. Log-space
    # calibration is what keeps measured coverage near target here.
    result = calibrate_symbol(_trending(), symbol="TREND", horizon=5, confidence=0.9)
    assert result.coverage >= 0.9 - 0.05
    assert result.half_width_pct > 0


def test_measured_coverage_matches_the_calibrated_level():
    generator = np.random.default_rng(5)
    calibration = generator.normal(scale=4.0, size=600)
    holdout = generator.normal(scale=4.0, size=600)
    half_width = conformal_half_width(calibration, 0.8)
    coverage = measured_coverage(holdout, half_width)
    assert 0.72 <= coverage <= 0.88


def test_zero_width_interval_covers_nothing():
    assert measured_coverage([1.0, -2.0, 3.0], 0.0) == 0.0


def test_calibrate_symbol_reports_measured_quality():
    result = calibrate_symbol(_random_walk(), symbol="tcs", horizon=5, confidence=0.8)
    assert result.symbol == "TCS"
    assert result.sessions_used == 420
    assert result.mae > 0
    assert result.rmse >= result.mae
    assert result.baseline_mae > 0
    assert result.half_width > 0
    assert 0.6 <= result.coverage <= 1.0
    assert result.target_coverage == 0.8
    assert result.support_state in {"model_supported", "baseline_only", "low_evidence", "abstained"}


def test_random_walk_model_never_claims_skill_over_itself():
    result = calibrate_symbol(_random_walk(), symbol="INFY", model="random_walk")
    assert result.skill_vs_baseline == 0.0
    assert result.support_state == "baseline_only"
    assert "random walk" in (result.reason or "")


def test_drift_model_can_earn_support_on_a_trending_series():
    result = calibrate_symbol(_trending(), symbol="TREND", horizon=5, confidence=0.8)
    assert result.skill_vs_baseline > 0
    assert result.support_state == "model_supported"
    assert result.evidence_tier in {"A", "B"}
    assert result.reason is None


def test_short_history_is_refused_with_a_code():
    with pytest.raises(CalibrationError) as excinfo:
        calibrate_symbol(_random_walk(sessions=MIN_SESSIONS - 10), symbol="SHORT")
    assert excinfo.value.code == "calibration_insufficient_history"


def test_bad_horizon_and_confidence_are_refused():
    frame = _random_walk()
    with pytest.raises(CalibrationError) as horizon_error:
        calibrate_symbol(frame, horizon=0)
    assert horizon_error.value.code == "calibration_horizon_invalid"
    with pytest.raises(CalibrationError) as confidence_error:
        calibrate_symbol(frame, confidence=0.77)
    assert confidence_error.value.code == "calibration_confidence_invalid"


def test_missing_close_column_is_refused():
    frame = pd.DataFrame({"open": [1.0, 2.0, 3.0]})
    with pytest.raises(CalibrationError) as excinfo:
        calibrate_symbol(frame)
    assert excinfo.value.code == "calibration_history_unavailable"


def test_non_positive_closes_are_a_data_quality_failure():
    frame = _random_walk(sessions=200)
    frame.iloc[10, 0] = -5.0
    with pytest.raises(CalibrationError) as excinfo:
        calibrate_symbol(frame)
    assert excinfo.value.code == "calibration_data_quality_failed"


def test_calibrated_range_is_ordered_and_labelled():
    payload = calibrated_range(_trending(), symbol="TREND", horizon=5, confidence=0.9)
    assert payload["refused"] is False
    assert payload["range"]["low"] < payload["as_of_close"] < payload["range"]["high"]
    assert payload["range"]["low"] > 0
    assert payload["range"]["basis"] == "split_conformal_on_walk_forward_log_residuals"
    assert payload["is_forecast"] is False
    assert payload["is_recommendation"] is False
    assert payload["quality"]["disclosures"] == list(CALIBRATION_DISCLOSURES)


def test_baseline_only_range_is_labelled_as_baseline():
    payload = calibrated_range(_random_walk(), symbol="RW", model="random_walk")
    assert payload["quality"]["support_state"] == "baseline_only"
    assert payload["range"] is not None


def test_report_separates_calibrated_from_skipped_symbols():
    frames = {"GOOD": _trending(), "SHORT": _random_walk(sessions=60)}

    def loader(symbol: str) -> pd.DataFrame:
        if symbol == "BROKEN":
            raise RuntimeError("provider_not_configured")
        return frames[symbol]

    report = calibration_report(["good", "short", "broken", ""], history_loader=loader)
    assert [row["symbol"] for row in report["calibrated"]] == ["GOOD"]
    codes = {row["symbol"]: row["code"] for row in report["skipped"]}
    assert codes["SHORT"] == "calibration_insufficient_history"
    assert codes["BROKEN"] == "calibration_history_unavailable"
    assert report["coverage_summary"]["symbols_requested"] == 3
    assert report["is_forecast"] is False


def test_methodology_denies_point_predictions():
    methodology = describe_methodology()
    assert methodology["produces_point_prediction"] is False
    assert methodology["produces_recommendation"] is False
    assert methodology["baseline"] == "random_walk"
    assert methodology["validation"] == "walk_forward_expanding_window"
    assert len(methodology["lookahead_controls"]) >= 3
