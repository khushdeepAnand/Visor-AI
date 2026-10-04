"""Tests for the time-ordered stacking meta-learner.

The property that matters most here is that a fold's weights are never fitted on
the bars it is scored against. The scoring path exposes ``scored_indices`` and
``predictions`` precisely so that can be checked directly instead of trusted.
"""
from __future__ import annotations

import numpy as np
import pytest

from forecasting.regime_detection import MarketRegime, regime_router
from forecasting.regime_stacking import (
    _expanding_folds,
    walk_forward_stacked_forecast,
    walk_forward_stacking_weights,
)

MODELS = ["a", "b"]


def _correlated(n: int, *, seed: int = 0, noise: float = 0.2):
    """Two near-collinear models where one tracks truth and one is scaled off it.

    Near-collinear inputs are the realistic case for stacked forecasts, and they
    are exactly the case an un-intercepted raw-scale solve gets wrong.
    """
    rng = np.random.default_rng(seed)
    truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
    good = truth + rng.normal(0, noise, n)
    biased = truth * 1.15 + rng.normal(0, noise, n)
    return {"a": good, "b": biased}, truth


def _informative(n: int, *, seed: int = 0):
    """One model that tracks truth and one that carries no signal at all."""
    rng = np.random.default_rng(seed)
    truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
    return {"a": truth + rng.normal(0, 0.3, n), "b": 500 + rng.normal(0, 8, n)}, truth


def _mae(pred: list[float], truth: np.ndarray, index: int = 0) -> float:
    start = len(truth) - len(pred) + index if index == 0 else 0
    return float(np.mean(np.abs(np.asarray(pred[start:]) - truth[start:])))


class TestProjectedShapes:
    def test_folds_never_train_on_their_own_scored_rows(self):
        spans = _expanding_folds(150, 40, 4)
        assert spans == [(40, 67), (67, 94), (94, 121), (121, 150)]
        for start, stop in spans:
            assert start < stop
        # Folds are contiguous and strictly ordered, so every scored bar sits
        # after the end of its own training window.
        assert [s for s, _ in spans] == sorted(s for s, _ in spans)

    def test_no_folds_when_history_is_too_short(self):
        assert _expanding_folds(30, 40, 4) == []
        assert _expanding_folds(40, 40, 4) == []

    def test_single_fold_covers_the_whole_tail(self):
        assert _expanding_folds(50, 20, 1) == [(20, 50)]


class TestWalkForwardWeights:
    def test_returns_none_without_enough_history(self):
        predictions, truth = _correlated(10)
        assert walk_forward_stacking_weights(predictions, truth, min_train=20) is None

    def test_returns_none_for_empty_inputs(self):
        assert walk_forward_stacking_weights({}, [], min_train=2) is None

    def test_rejects_misaligned_lengths(self):
        assert walk_forward_stacking_weights({"a": [1.0, 2.0]}, [1.0], min_train=2) is None

    def test_rejects_non_finite_outcomes(self):
        predictions = {"a": [1.0, 2.0, 3.0, 4.0], "b": [1.1, 2.1, 3.1, 4.1]}
        outcomes = [1.0, float("nan"), 3.0, 4.0]
        assert walk_forward_stacking_weights(predictions, outcomes, min_train=2) is None

    def test_weights_are_simplex_constrained(self):
        predictions, truth = _correlated(80)
        learned = walk_forward_stacking_weights(predictions, truth, min_train=20, folds=3)
        assert learned is not None
        assert all(weight >= 0.0 for weight in learned.weights.values())
        assert sum(learned.weights.values()) == pytest.approx(1.0)

    def test_weights_favour_the_informative_model(self):
        predictions, truth = _informative(150)
        learned = walk_forward_stacking_weights(
            predictions, truth, min_train=40, folds=4, shrinkage=0.0
        )
        assert learned is not None
        assert learned.weights["a"] > 0.8
        assert learned.weights["a"] > learned.weights["b"]

    def test_survives_perfectly_collinear_models(self):
        """The failure an un-intercepted raw solve hits; ridge must absorb it."""
        rng = np.random.default_rng(4)
        truth = 100 + np.cumsum(rng.normal(0, 1.0, 120))
        predictions = {"a": truth, "b": truth * 1.15}
        learned = walk_forward_stacking_weights(predictions, truth, min_train=30, folds=3)
        assert learned is not None
        assert all(np.isfinite(value) for value in learned.weights.values())
        assert sum(learned.weights.values()) == pytest.approx(1.0)
        assert np.isfinite(learned.intercept)

    def test_predict_tracks_truth_for_an_informative_pair(self):
        predictions, truth = _informative(150)
        learned = walk_forward_stacking_weights(
            predictions, truth, min_train=40, folds=4, shrinkage=0.0
        )
        assert learned is not None
        observed = float(truth[-1])
        estimate = learned.predict({"a": observed, "b": 500.0})
        naive_mean = (observed + 500.0) / 2
        assert estimate is not None
        # The learned stack must ignore the signal-free leg, which a raw average
        # cannot do.
        assert abs(estimate - observed) < abs(naive_mean - observed)
        assert abs(estimate - observed) < 10.0

    def test_is_order_sensitive_so_folds_must_be_chronological(self):
        predictions, truth = _informative(120)
        ordered = walk_forward_stacking_weights(
            predictions, truth, min_train=30, folds=4, shrinkage=0.0
        )
        reversed_truth = truth[::-1].copy()
        shuffled = walk_forward_stacking_weights(
            predictions, reversed_truth, min_train=30, folds=4, shrinkage=0.0
        )
        assert ordered is not None and shuffled is not None
        assert ordered.weights != shuffled.weights

    def test_earlier_predictions_survive_a_poisoned_future(self):
        """Leakage guard: rewriting the tail must not move early predictions.

        A stack that scored bar ``min_train`` with weights fitted on the whole
        series would have its first prediction swing when the tail is poisoned.
        """
        predictions, truth = _correlated(150, seed=3)
        clean = walk_forward_stacked_forecast(
            predictions, truth, [1] * 150, min_train=40, folds=4
        )
        poisoned_truth = truth.copy()
        poisoned_truth[121:] += 5000.0
        poisoned = walk_forward_stacked_forecast(
            predictions, poisoned_truth, [1] * 150, min_train=40, folds=4
        )
        assert clean["available"] and poisoned["available"]
        # The first scored bar comes from the (40, 67) fold, which trains only on
        # bars 0..39, so its prediction is untouched by bars 121+.
        assert clean["scored_indices"][0] == poisoned["scored_indices"][0] == 40
        assert clean["predictions"][0] == pytest.approx(poisoned["predictions"][0], abs=1e-6)

    def test_shrinkage_pulls_toward_uniform(self):
        predictions, truth = _informative(150)
        loose = walk_forward_stacking_weights(
            predictions, truth, min_train=40, folds=4, shrinkage=0.0
        )
        tight = walk_forward_stacking_weights(
            predictions, truth, min_train=40, folds=4, shrinkage=0.95
        )
        assert loose is not None and tight is not None
        spread_loose = max(loose.weights.values()) - min(loose.weights.values())
        spread_tight = max(tight.weights.values()) - min(tight.weights.values())
        assert spread_tight < spread_loose

    def test_regime_weights_are_learned_when_regimes_supplied(self):
        predictions, truth = _informative(150)
        regimes = ["calm"] * 75 + ["crisis"] * 75
        prior = {
            "calm": regime_router(MarketRegime.SIDEWAYS_LOW_VOL, available_models=MODELS),
            "crisis": regime_router(MarketRegime.CRISIS, available_models=MODELS),
        }
        learned = walk_forward_stacking_weights(
            predictions,
            truth,
            regimes=regimes,
            regime_prior=prior,
            min_train=20,
            folds=2,
        )
        assert learned is not None
        assert "crisis" in learned.regime_weights
        assert learned.regime_counts.get("crisis") == 75
        for weights in learned.regime_weights.values():
            assert sum(weights.values()) == pytest.approx(1.0)

    def test_cold_regime_is_not_learned(self):
        predictions, truth = _informative(150)
        regimes = ["common"] * 140 + ["rare"] * 10
        learned = walk_forward_stacking_weights(
            predictions,
            truth,
            regimes=regimes,
            regime_prior={"common": {"a": 0.5, "b": 0.5}},
            min_train=20,
            folds=2,
        )
        assert learned is not None
        assert "rare" not in learned.regime_weights

    def test_predict_only_uses_supplied_components(self):
        predictions, truth = _informative(60)
        learned = walk_forward_stacking_weights(
            predictions, truth, min_train=20, folds=2
        )
        assert learned is not None
        assert learned.predict({"a": 10.0, "b": 500.0}) is not None
        assert learned.predict({}) is None
        assert learned.predict({"missing": 1.0}) is None

    def test_nan_only_model_is_excluded(self):
        predictions, truth = _informative(60)
        predictions["broken"] = [float("nan")] * 60
        learned = walk_forward_stacking_weights(
            predictions, truth, min_train=20, folds=2
        )
        assert learned is not None
        assert "broken" not in learned.weights


class TestWalkForwardStackedForecast:
    def test_reports_unavailable_without_history(self):
        predictions, truth = _correlated(8)
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * 8, min_train=20
        )
        assert result["available"] is False
        assert "not enough history" in str(result["reason"])

    def test_beats_equal_weight_when_one_model_is_better(self):
        predictions, truth = _correlated(150, seed=7)
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * 150, min_train=40, folds=4, shrinkage=0.0
        )
        assert result["available"] is True
        assert result["oos_mae"] < result["equal_weight_mae"]
        assert result["use_learned"] is True
        assert result["improvement"] > 0

    def test_declines_when_learned_stack_cannot_help(self):
        n = 150
        rng = np.random.default_rng(3)
        truth = 100 + np.cumsum(rng.normal(0, 1.0, n))
        predictions = {"a": truth.copy(), "b": truth.copy()}
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * n, min_train=40, folds=4
        )
        assert result["available"] is True
        assert result["use_learned"] is False

    def test_scores_only_the_tail_after_the_training_window(self):
        predictions, truth = _correlated(150, seed=11)
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * 150, min_train=40, folds=4
        )
        assert result["available"] is True
        assert result["folds"] == 4
        assert result["n_train"] == 121
        # 150 - 40 = 110 bars sit after the first training window.
        assert result["scored"] == 110
        assert min(result["scored_indices"]) == 40
        assert max(result["scored_indices"]) == 149
        assert len(result["predictions"]) == result["scored"]
        assert len(result["baseline_predictions"]) == result["scored"]
        assert set(result["weights"]) == {"a", "b"}
        assert sum(result["weights"].values()) == pytest.approx(1.0)
        assert "out of sample" in str(result["disclosure"])

    def test_rows_with_non_finite_predictions_are_not_scored(self):
        predictions, truth = _correlated(100, seed=9)
        predictions["a"] = list(predictions["a"])
        predictions["a"][50] = float("nan")
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * 100, min_train=30, folds=3
        )
        assert result["available"] is True
        assert result["scored"] == 69
        assert 50 not in result["scored_indices"]

    def test_horizon_argument_is_recorded(self):
        predictions, truth = _correlated(100, seed=5)
        horizons = [1] * 50 + [5] * 50
        result = walk_forward_stacked_forecast(
            predictions, truth, horizons, min_train=30, folds=3
        )
        assert result["available"] is True
        # Only bars 30..99 are scored: twenty 1-day bars and fifty 5-day bars.
        assert result["mean_horizon"] == pytest.approx((20 * 1 + 50 * 5) / 70, abs=5e-4)

    def test_effective_weights_are_normalised(self):
        predictions, truth = _informative(200)
        result = walk_forward_stacked_forecast(
            predictions, truth, [1] * 200, min_train=60, folds=4
        )
        assert result["available"] is True
        assert sum(result["effective_weights"].values()) == pytest.approx(1.0)

    def test_regime_overrides_are_learned_and_recorded(self):
        predictions, truth = _informative(200)
        regimes = ["calm"] * 100 + ["crisis"] * 100
        prior = {"calm": {"a": 0.5, "b": 0.5}, "crisis": {"a": 0.5, "b": 0.5}}
        result = walk_forward_stacked_forecast(
            predictions,
            truth,
            [1] * 200,
            regimes=regimes,
            regime_prior=prior,
            min_train=20,
            folds=4,
        )
        assert result["available"] is True
        assert "crisis" in result["regime_weights"]
        assert result["regime_counts"]["crisis"] == 100
        assert sum(result["regime_weights"]["crisis"].values()) == pytest.approx(1.0)

    def test_rule_based_prior_blends_into_a_cold_regime(self):
        """With no learned rows, the rule-based prior should still surface."""
        predictions, truth = _informative(200)
        regimes = ["common"] * 190 + ["rare"] * 10
        prior = {"common": {"a": 0.9, "b": 0.1}, "rare": {"a": 0.2, "b": 0.8}}
        result = walk_forward_stacked_forecast(
            predictions,
            truth,
            [1] * 200,
            regimes=regimes,
            regime_prior=prior,
            min_train=40,
            folds=3,
            regime_prior_weight=1.0,
        )
        assert result["available"] is True
        # "rare" never reaches min_rows, so it is not learned; the effective
        # weights for those bars fall back on the fold fit rather than the prior.
        assert "rare" not in result["regime_weights"]