"""Tests for the harness-level promotion gate on the stacking meta-learner.

The gate is the thing that keeps an unproven learner out of production, so the
tests are mostly about it refusing to promote, not about it promoting.
"""
from __future__ import annotations

import numpy as np
import pytest

from forecasting.evaluation_harness import evaluate_walk_forward_stacking


def _informative(n: int, *, seed: int = 7):
    """One leg tracks truth, the other is signal-free noise."""
    rng = np.random.default_rng(seed)
    truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
    return {"a": truth + rng.normal(0, 0.3, n), "b": 500 + rng.normal(0, 8, n)}, truth


def _collinear(n: int, *, seed: int = 7):
    """Both legs track truth, one rescaled; neither dominates."""
    rng = np.random.default_rng(seed)
    truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
    return (
        {"a": truth + rng.normal(0, 0.3, n), "b": truth * 1.15 + rng.normal(0, 0.3, n)},
        truth,
    )


def _identical(n: int, *, seed: int = 3):
    rng = np.random.default_rng(seed)
    truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
    return {"a": truth.copy(), "b": truth.copy()}, truth


class TestPromotionGate:
    def test_unavailable_history_never_promotes(self):
        predictions, truth = _informative(10)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 10, min_train=40
        )
        assert result["available"] is False
        assert result["promote"] is False
        assert "not enough history" in result["reason"]

    def test_promotes_when_learned_stack_clears_the_baseline(self):
        predictions, truth = _informative(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        assert result["available"] is True
        assert result["beats_equal_weight"] is True
        assert result["enough_evidence"] is True
        assert result["promote"] is True
        assert result["oos_mae"] < result["equal_weight_mae"]
        assert result["improvement_pct"] > 0

    def test_refuses_to_promote_a_tie(self):
        predictions, truth = _identical(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        assert result["available"] is True
        assert result["promote"] is False
        assert "did not beat" in result["note"]

    def test_refuses_to_promote_on_thin_evidence(self):
        predictions, truth = _informative(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=185, folds=4
        )
        assert result["available"] is True
        assert result["scored"] < 30
        assert result["enough_evidence"] is False
        assert result["promote"] is False
        assert "not enough to promote" in result["note"]

    def test_reports_directional_accuracy_for_both_arms(self):
        predictions, truth = _informative(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        assert 0.0 <= result["directional_accuracy"] <= 1.0
        assert 0.0 <= result["equal_weight_directional_accuracy"] <= 1.0

    def test_echoes_the_run_configuration(self):
        predictions, truth = _informative(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4, shrinkage=0.25
        )
        assert result["min_train"] == 40
        assert result["folds"] == 4
        assert result["shrinkage"] == 0.25
        assert "walk-forward" in result["basis"]
        assert result["promotion_rule"]

    def test_weights_are_reported_and_normalised(self):
        predictions, truth = _informative(200)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        assert set(result["weights"]) == {"a", "b"}
        assert sum(result["weights"].values()) == pytest.approx(1.0)
        assert sum(result["effective_weights"].values()) == pytest.approx(1.0)

    def test_predictions_are_opt_in(self):
        predictions, truth = _informative(200)
        lean = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        verbose = evaluate_walk_forward_stacking(
            predictions,
            truth.tolist(),
            [1] * 200,
            min_train=40,
            folds=4,
            keep_predictions=True,
        )
        assert "predictions" not in lean
        assert len(verbose["predictions"]) == verbose["scored"]
        assert len(verbose["scored_indices"]) == verbose["scored"]
        assert len(verbose["baseline_predictions"]) == verbose["scored"]

    def test_regime_overrides_flow_through(self):
        predictions, truth = _informative(200)
        regimes = ["calm"] * 100 + ["crisis"] * 100
        prior = {"calm": {"a": 0.5, "b": 0.5}, "crisis": {"a": 0.5, "b": 0.5}}
        result = evaluate_walk_forward_stacking(
            predictions,
            truth.tolist(),
            [1] * 200,
            regimes=regimes,
            regime_prior=prior,
            min_train=40,
            folds=4,
        )
        assert result["available"] is True
        assert "crisis" in result["regime_weights"]
        assert sum(result["regime_weights"]["crisis"].values()) == pytest.approx(1.0)

    def test_baseline_of_zero_does_not_divide_by_zero(self):
        predictions, truth = _identical(60)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 60, min_train=20, folds=2
        )
        assert result["available"] is True
        assert result["improvement_pct"] is None or isinstance(
            result["improvement_pct"], float
        )

    def test_collinear_stack_is_not_promoted_on_noise(self):
        """A 1.15x rescale of the same signal is not an edge worth shipping."""
        predictions, truth = _collinear(200, seed=11)
        result = evaluate_walk_forward_stacking(
            predictions, truth.tolist(), [1] * 200, min_train=40, folds=4
        )
        assert result["available"] is True
        if result["beats_equal_weight"]:
            # A win is allowed, but only if it is not a rounding-scale artefact.
            assert result["improvement_pct"] > 1.0
        else:
            assert result["promote"] is False