"""Tests for regime-conditional stacking."""
from __future__ import annotations

import numpy as np
import pytest

from forecasting.regime_detection import MarketRegime, regime_router
from forecasting.regime_stacking import (
    regime_width_multipliers,
    soft_regime_weights,
    stack_regime_components,
)

MODELS = [
    "per_stock_cqr",
    "pooled_cross_sectional",
    "volatility_model",
    "regime_adaptive",
    "trend_following",
    "mean_reversion",
]


class TestSoftRegimeWeights:
    def test_weights_sum_to_one_over_available_only(self):
        weights = soft_regime_weights(
            regime_router(MarketRegime.CRISIS, available_models=MODELS),
            available=["per_stock_cqr", "volatility_model"],
            probability=1.0,
        )
        assert set(weights) == {"per_stock_cqr", "volatility_model"}
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_low_probability_shrinks_toward_uniform(self):
        weights = soft_regime_weights(
            regime_router(MarketRegime.CRISIS, available_models=MODELS),
            available=MODELS,
            probability=0.0,
        )
        assert all(
            w == pytest.approx(1.0 / len(MODELS), abs=1e-6) for w in weights.values()
        )

    def test_deterministic_weights_beat_equal_weight_at_full_probability(self):
        crisis = soft_regime_weights(
            regime_router(MarketRegime.CRISIS, available_models=MODELS),
            available=MODELS,
            probability=1.0,
        )
        assert crisis["per_stock_cqr"] < crisis["volatility_model"]

    def test_no_available_models_returns_empty(self):
        assert soft_regime_weights({}, available=[]) == {}

    def test_non_finite_or_negative_regime_weight_falls_back_to_uniform(self):
        weights = soft_regime_weights(
            {"good": 2.0, "bad": float("nan"), "negative": -5.0},
            available=["good", "bad", "negative"],
            probability=1.0,
        )
        assert sum(weights.values()) == pytest.approx(1.0)
        # Each bad model recovers only its uniform share.
        assert weights["bad"] < weights["good"]


class TestRegimeWidthMultipliers:
    def test_crisis_widens_and_low_vol_tightens(self):
        crisis = regime_width_multipliers(MarketRegime.CRISIS, probability=1.0)
        low_vol = regime_width_multipliers(
            MarketRegime.SIDEWAYS_LOW_VOL, probability=1.0
        )
        assert crisis["multiplier"] > 1.0
        assert low_vol["multiplier"] < 1.0

    def test_zero_probability_is_neutral(self):
        result = regime_width_multipliers(MarketRegime.CRISIS, probability=0.0)
        assert result["multiplier"] == pytest.approx(1.0)

    def test_multiplier_is_clamped_to_safe_band(self):
        for regime in MarketRegime:
            out = regime_width_multipliers(regime, probability=1.0)
            assert 0.90 <= out["multiplier"] <= 1.50

    def test_unclassified_is_neutral(self):
        out = regime_width_multipliers(MarketRegime.UNCLASSIFIED, probability=1.0)
        assert out["multiplier"] == pytest.approx(1.0)


class TestStackRegimeComponents:
    def test_weighted_blend_matches_manual_contributions(self):
        stack = stack_regime_components(
            {"a": 10.0, "b": 20.0},
            {"a": 1.0, "b": 3.0},
            probability=1.0,
        )
        assert stack.applied
        assert stack.weights == {"a": pytest.approx(0.25), "b": pytest.approx(0.75)}
        assert stack.value == pytest.approx(10 * 0.25 + 20 * 0.75)

    def test_unusable_components_are_dropped_with_reasons(self):
        stack = stack_regime_components(
            {"a": 10.0, "nan": float("nan"), "neg": -1.0, "text": "oops"},
            {"a": 1.0, "nan": 1.0, "neg": 1.0, "text": 1.0},
            probability=1.0,
        )
        assert stack.applied
        assert stack.dropped == {
            "nan": "not_finite",
            "neg": "negative",
            "text": "not_numeric",
        }
        assert set(stack.weights) == {"a"}

    def test_dropped_component_weight_is_redistributed_not_lost(self):
        stack = stack_regime_components(
            {"a": 10.0, "b": float("nan")},
            {"a": 1.0, "b": 3.0},
            probability=1.0,
        )
        assert sum(stack.weights.values()) == pytest.approx(1.0)
        assert stack.weights["a"] == pytest.approx(1.0)

    def test_returns_fallback_when_nothing_is_usable(self):
        stack = stack_regime_components(
            {"a": float("nan")}, {}, probability=1.0, fallback=7.0
        )
        assert not stack.applied
        assert stack.value == pytest.approx(7.0)
        assert "usable" in stack.reason

    def test_no_components_returns_fallback(self):
        stack = stack_regime_components({}, {}, probability=1.0)
        assert not stack.applied
        assert stack.value is None

    def test_min_components_gate_blocks_single_component_blend(self):
        stack = stack_regime_components(
            {"a": 5.0}, {"a": 1.0}, probability=1.0, min_components=2
        )
        assert not stack.applied

    def test_zero_weighted_sum_degrades_to_uniform(self):
        stack = stack_regime_components(
            {"a": 10.0, "b": 20.0}, {"a": 0.0, "b": 0.0}, probability=1.0
        )
        assert stack.applied
        assert stack.weights["a"] == pytest.approx(0.5)
        assert stack.weights["b"] == pytest.approx(0.5)

    def test_result_is_finite_and_positive(self):
        stack = stack_regime_components(
            {"a": 1e9, "b": 1.0}, {"a": 1.0, "b": 1.0}, probability=1.0
        )
        assert np.isfinite(stack.value)
        assert stack.value > 0