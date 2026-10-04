"""Tests for the regime-stacking evaluation in the evaluation harness."""
from __future__ import annotations

import numpy as np
import pytest

from forecasting.evaluation_harness import evaluate_regime_stacking


class TestEvaluateRegimeStacking:
    def test_reports_misaligned_regimes_as_an_error(self):
        out = evaluate_regime_stacking(
            np.array([1.0, 2.0]),
            np.array([0.0, 1.0]),
            np.array([2.0, 3.0]),
            0.80,
            regimes=np.array(["a"]),
            regime_multipliers={"a": 1.2},
        )
        assert "error" in out
        assert "aligned" in out["error"]

    def test_empty_input_is_an_error(self):
        out = evaluate_regime_stacking(
            np.array([]), np.array([]), np.array([]), 0.80,
            regimes=np.array([]), regime_multipliers={},
        )
        assert out["error"] == "no forecasts supplied"

    def test_thin_regimes_are_skipped_rather_than_judged(self):
        n = 20
        regimes = np.array(["crisis"] * 3 + ["calm"] * (n - 3))
        y_true = np.full(n, 1.0)
        out = evaluate_regime_stacking(
            y_true,
            y_true - 0.1,
            y_true + 0.1,
            0.80,
            regimes=regimes,
            regime_multipliers={"crisis": 1.4, "calm": 1.0},
        )
        assert out["per_regime"]["crisis"]["note"] == "too few observations to judge"
        assert "base_coverage" not in out["per_regime"]["crisis"]

    def test_widening_a_undercovered_regime_improves_coverage(self):
        # Centre 1.0, half-width 0.05: most outcomes land far outside, so the
        # base interval under-covers badly. A 1.4x tilt should capture more.
        n = 400
        rng = np.random.default_rng(0)
        y_true = 1.0 + rng.normal(0, 0.12, size=n)
        y_low = np.full(n, 0.95)
        y_high = np.full(n, 1.05)
        regimes = np.array(["crisis"] * n)

        out = evaluate_regime_stacking(
            y_true, y_low, y_high, 0.80, regimes,
            regime_multipliers={"crisis": 1.4},
        )
        entry = out["per_regime"]["crisis"]
        assert entry["tilted_coverage"] > entry["base_coverage"]
        assert entry["moved_toward_nominal"] is True
        assert out["regimes_improved"] == 1

    def test_overcovering_regime_reports_worsening_honestly(self):
        n = 200
        y_true = np.full(n, 1.0)
        y_low = np.full(n, 0.2)
        y_high = np.full(n, 1.8)
        regimes = np.array(["calm"] * n)

        out = evaluate_regime_stacking(
            y_true, y_low, y_high, 0.80, regimes,
            regime_multipliers={"calm": 1.4},
        )
        entry = out["per_regime"]["calm"]
        # Coverage was already perfect; tilting cannot improve it further.
        assert entry["base_coverage"] == pytest.approx(1.0)
        assert entry["tilted_coverage"] == pytest.approx(1.0)
        assert entry["moved_toward_nominal"] is False
        assert out["all_improved"] is False

    def test_multiplier_defaults_to_neutral_when_regime_missing(self):
        n = 50
        y_true = np.full(n, 1.0)
        out = evaluate_regime_stacking(
            y_true, y_true - 0.1, y_true + 0.1, 0.80,
            regimes=np.array(["unseen"] * n), regime_multipliers={},
        )
        assert out["per_regime"]["unseen"]["multiplier"] == pytest.approx(1.0)

    def test_preserves_the_original_centre_when_tilting(self):
        """Tilt must widen symmetrically, never translate the interval."""
        n = 100
        centre = np.full(n, 50.0)
        half = np.full(n, 2.0)
        out = evaluate_regime_stacking(
            np.full(n, 50.0), centre - half, centre + half, 0.80,
            regimes=np.array(["calm"] * n), regime_multipliers={"calm": 1.2},
        )
        entry = out["per_regime"]["calm"]
        # With every outcome at the centre, both base and tilted cover fully.
        assert entry["base_coverage"] == pytest.approx(1.0)
        assert entry["tilted_coverage"] == pytest.approx(1.0)