"""Tests for the statistical assessment block: probability, agreement, regime,
confidence, data quality, explanation (master prompt § probability / regime /
agreement / confidence).

Every assertion checks that the numbers are *computed from model output or
indicator data* — never hardcoded — and that publishable statistics are gated
off when the forecast status is blocked.
"""
from __future__ import annotations

import math
import types

import numpy as np
import pandas as pd
import pytest

from forecasting.interval_forecast import (
    _build_assessment,
    _calibrated_probability,
    _confidence_score_report,
    _data_quality_report,
    _market_regime_report,
    _member_directional_vote,
    _model_agreement_report,
    _volatility_percentile,
    forecast_range,
)
from services.forecast_presentation import public_forecast


# ---------------------------------------------------------------------------
# Probability
# ---------------------------------------------------------------------------


def test_member_directional_vote_counts_members_above_reference() -> None:
    matrix = np.array([[101.0, 100.5, 99.0, 100.2, 98.8, 100.9]])
    up, members = _member_directional_vote(matrix, reference=100.0)
    assert members == 6
    assert up == pytest.approx(4 / 6)  # three of six above (ties none)


def test_member_directional_vote_unusable_when_reference_invalid() -> None:
    matrix = np.array([[101.0, 100.5]])
    up, members = _member_directional_vote(matrix, reference=0.0)
    assert up == 0.5
    assert members == 0


def test_calibrated_probability_is_identity_without_skill() -> None:
    assert _calibrated_probability(0.7, None) == pytest.approx(0.7)
    assert _calibrated_probability(0.7, 0.0) == pytest.approx(0.7)


def test_calibrated_probability_amplifies_with_skill_and_clips() -> None:
    # Positive skill pulls toward the vote; never above the ceiling.
    amplified = _calibrated_probability(0.7, 0.3)
    assert 0.7 < amplified <= 0.95
    assert _calibrated_probability(1.0, 0.5) == pytest.approx(0.95)
    # Negative skill shrinks the vote toward 50%; never below the floor.
    shrunk = _calibrated_probability(0.7, -0.3)
    assert 0.5 <= shrunk < 0.7
    assert _calibrated_probability(0.0, 0.5) == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


def test_model_agreement_high_for_tight_ensemble() -> None:
    matrix = np.array([[100.2, 100.3, 100.1, 100.25, 100.15, 100.3]])
    report = _model_agreement_report(matrix, reference=100.0, expected_vol_pct=1.0)
    assert report["available"] is True
    assert report["score"] > 0.9
    assert report["level"] == "high"
    assert report["member_count"] == 6


def test_model_agreement_low_for_dispersed_ensemble() -> None:
    matrix = np.array([[108.0, 92.0, 106.0, 94.0, 103.0, 97.0]])
    report = _model_agreement_report(matrix, reference=100.0, expected_vol_pct=1.0)
    assert report["available"] is True
    assert report["score"] < 0.5
    assert report["level"] == "low"


def test_model_agreement_unavailable_below_two_members() -> None:
    report = _model_agreement_report(np.array([[100.5, np.nan]]), reference=100.0, expected_vol_pct=1.0)
    assert report["available"] is False


# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------


def _enriched(rows: int = 14, *, bearish: bool = False, seed: int = 3) -> pd.DataFrame:
    """Indicator-bearing frame observable at the latest bar (no future data)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-02", periods=rows, freq="B")
    close = 100.0 + np.arange(rows) * 0.2
    offset = -0.6 if bearish else 0.6
    return pd.DataFrame(
        {
            "Close": close,
            "SMA_20": close - offset,
            "SMA_50": close - offset * 2.0,
            "EMA_20": close - offset * 0.5,
            "EMA_50": close - offset * 1.5,
            "MACD_Histogram": -0.1 if bearish else 0.1,
            "ADX": 28.0,
            "ATR_Pct": 1.2 + rng.normal(0, 0.05, rows),
            "RSI": 40.0 if bearish else 60.0,
            "Close_to_SMA20_Pct": -0.4 if bearish else 0.4,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )


def test_market_regime_picks_trend_and_volatility() -> None:
    bullish = _market_regime_report(_enriched())
    assert bullish["available"] is True
    assert bullish["trend"] == "strong_bullish"
    assert "bullish" in bullish["label"].lower()
    assert bullish["volatility"] in {"high", "normal", "low"}
    assert 0.0 <= bullish["volatility_percentile"] <= 1.0
    assert bullish["signals"]
    bearish = _market_regime_report(_enriched(bearish=True))
    assert bearish["trend"] == "strong_bearish"
    assert "bearish" in bearish["label"].lower()


def test_market_regime_unclassified_without_indicators() -> None:
    bare = pd.DataFrame(
        {"Close": [100.0, 101.0, 102.0]},
        index=pd.date_range("2026-01-02", periods=3, freq="B"),
    )
    report = _market_regime_report(bare)
    assert report["available"] is False
    assert report["label"] == "unclassified"


def test_volatility_percentile_ranks_recent_atr() -> None:
    # Latest ATR% is far above its own history -> percentile ~1 (the latest
    # bar itself is excluded from the comparison, so it is 60/61, not 1.0).
    series = np.r_[np.full(60, 1.0), 3.0]
    assert _volatility_percentile(series) == pytest.approx(1.0 - 1.0 / 61.0, abs=0.001)
    # Flat history with a distinct trough-style tail is still inside [0, 1].
    flat = np.full(40, 1.0)
    assert _volatility_percentile(flat) is None or 0.0 <= _volatility_percentile(flat) <= 1.0


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def test_data_quality_strong_when_inputs_clean() -> None:
    report = _data_quality_report(
        missingness_ratio=0.0,
        zero_volume_ratio=0.0,
        duplicate_rows=0,
        feature_aligned=True,
        corporate_action_status="clean",
    )
    assert report["score"] == 1.0
    assert report["level"] == "strong"
    assert report["notes"] == []


def test_data_quality_degrades_with_input_defects() -> None:
    report = _data_quality_report(
        missingness_ratio=0.15,
        zero_volume_ratio=0.1,
        duplicate_rows=2,
        feature_aligned=False,
        corporate_action_status="review_required",
    )
    assert report["score"] < 0.6
    assert report["level"] in {"limited", "blocked"}
    assert any("rejected" in note for note in report["notes"])


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------


def test_confidence_score_weighted_and_capped() -> None:
    report = _confidence_score_report(
        evidence_grade="A",
        empirical_coverage=0.84,
        nominal_coverage=0.80,
        beats_naive=True,
        mae_improvement_pct=5.0,
        agreement_score=0.9,
        drift_detected=False,
        data_quality_score=1.0,
        blocked=False,
    )
    assert 0.0 <= report["score"] <= 100.0
    assert report["level"] in {"high", "moderate", "low"}
    assert report["components"]["evidence"] == 40.0
    assert report["basis"]


def test_confidence_score_drift_and_blocked_caps() -> None:
    kwargs = dict(
        evidence_grade="A",
        empirical_coverage=0.80,
        nominal_coverage=0.80,
        beats_naive=True,
        mae_improvement_pct=20.0,
        agreement_score=1.0,
        data_quality_score=1.0,
    )
    uncapped = _confidence_score_report(blocked=False, drift_detected=False, **kwargs)
    assert uncapped["score"] > 40.0
    drifted = _confidence_score_report(blocked=False, drift_detected=True, **kwargs)
    assert drifted["score"] <= 30.0
    blocked = _confidence_score_report(blocked=True, drift_detected=False, **kwargs)
    assert blocked["score"] <= 20.0


# ---------------------------------------------------------------------------
# Assessment assembly + gating
# ---------------------------------------------------------------------------


def _metrics() -> types.SimpleNamespace:
    return types.SimpleNamespace(coverage=0.84, nominal_coverage=0.80, direction_balanced_accuracy=0.55)


def _assessment_kwargs(**overrides: object) -> dict[str, object]:
    matrix = np.array([[100.4, 100.6, 100.2, 100.5, 100.3, 100.7]])
    kwargs: dict[str, object] = {
        "forecast_status": "model_supported",
        "current_price": 100.0,
        "median": 101.0,
        "recent_scale": 1.0,
        "latest_matrix": matrix,
        "direction_balanced_accuracy": 0.55,
        "enriched": _enriched(),
        "liquidity_proxy": "normal",
        "zero_volume_ratio": 0.0,
        "missingness_ratio": 0.0,
        "duplicate_rows": 0,
        "feature_aligned": True,
        "corporate_action_status": "clean",
        "evidence_grade": "A",
        "metrics": _metrics(),
        "beats_naive": True,
        "mae_improvement_pct": 5.0,
        "drift_detected": False,
    }
    kwargs.update(overrides)
    return kwargs


def test_build_assessment_publishable_fields_are_numbered() -> None:
    assessment = _build_assessment(**_assessment_kwargs())
    assert assessment["available"] is True
    probability = assessment["probability"]
    assert probability is not None
    assert probability["up"] + probability["down"] == pytest.approx(1.0)
    assert 0.05 <= probability["up"] <= 0.95
    assert assessment["expected_return_pct"] == pytest.approx(1.0, abs=0.01)
    assert assessment["expected_volatility_pct"] == pytest.approx(1.0, abs=0.01)
    assert assessment["model_agreement"]["available"] is True
    assert 0.0 <= assessment["confidence_score"]["score"] <= 100.0
    assert assessment["market_regime"]["available"] is True
    assert len(assessment["explanation"]["reasons"]) > 0


def test_build_assessment_gates_statistics_when_blocked() -> None:
    assessment = _build_assessment(**_assessment_kwargs(forecast_status="abstained"))
    # Statistics derived from model output are withheld for a blocked status…
    assert assessment["probability"] is None
    assert assessment["expected_return_pct"] is None
    assert assessment["expected_volatility_pct"] is None
    assert assessment["model_agreement"] is None
    # …but context (regime, quality, confidence, explanation) is still shown.
    assert assessment["market_regime"]["available"] is True
    assert assessment["data_quality"]["level"] in {"strong", "limited", "blocked"}
    assert assessment["confidence_score"]["score"] <= 20.0
    assert len(assessment["explanation"]["reasons"]) > 0


# ---------------------------------------------------------------------------
# End-to-end: forecast_range surface + presentation mapping
# ---------------------------------------------------------------------------


def _synthetic_frame(rows: int = 420, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    phase = np.arange(rows) * 2 * np.pi / 8
    close = 150 + 15 * np.sin(phase) + rng.normal(0, 0.1, rows)
    open_ = close + rng.normal(0, 0.05, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.3,
            "Low": np.minimum(open_, close) - 0.3,
            "Close": close,
            "Volume": 1_000_000 + np.sin(phase + 1) * 100_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )


def test_forecast_range_emits_assessment_top_level_and_per_horizon() -> None:
    result = forecast_range(
        "RELIANCE",
        _synthetic_frame(),
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
        horizons=(1, 2),
    )
    assert result["forecast_status"] == "model_supported"
    assessment = result["assessment"]
    assert assessment["available"] is True
    assert 0.05 <= assessment["probability"]["up"] <= 0.95
    assert assessment["expected_return_pct"] is not None
    assert assessment["expected_volatility_pct"] is not None
    assert assessment["model_agreement"]["available"] is True
    assert assessment["market_regime"]["available"] is True
    assert 0.0 <= assessment["confidence_score"]["score"] <= 100.0
    assert len(assessment["explanation"]["reasons"]) >= 3

    ladder = result["multi_horizon"]["horizons"]
    assert len(ladder) == 2
    for entry in ladder:
        per = entry["assessment"]
        assert per["available"] is True
        if entry["forecast_status"] in {"abstained", "drift_blocked", "data_quality_blocked"}:
            assert per["expected_volatility_pct"] is None
        else:
            assert per["expected_volatility_pct"] is not None


def test_public_forecast_maps_assessment_and_keeps_gating() -> None:
    result = forecast_range(
        "RELIANCE",
        _synthetic_frame(),
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
    )
    payload = public_forecast(result)
    assessment = payload["assessment"]
    assert assessment["probability"] is not None
    assert assessment["probability"]["up"] + assessment["probability"]["down"] == pytest.approx(1.0)
    assert assessment["expected_return_pct"] is not None
    assert assessment["expected_volatility_pct"] is not None
    assert assessment["model_agreement"] is not None
    assert assessment["market_regime"] is not None
    assert assessment["confidence_score"] is not None
    assert assessment["explanation"] is not None

    # A blocked result must carry no publishable statistics.
    blocked = dict(result)
    blocked["forecast_status"] = "abstained"
    blocked["abstention_reason"] = "trust gate blocked for this test."
    blocked_payload = public_forecast(blocked)
    gated = blocked_payload["assessment"]
    assert gated["probability"] is None
    assert gated["expected_return_pct"] is None
    assert gated["expected_volatility_pct"] is None
    assert gated["model_agreement"] is None
    assert gated["market_regime"] is not None
    assert gated["confidence_score"] is not None
