"""Tests for per-tier evaluation harness."""
from __future__ import annotations

import numpy as np
import pytest

from forecasting.evaluation_harness import (
    pinball_loss,
    crps_gaussian,
    pit_values,
    pit_uniformity_test,
    mase,
    directional_accuracy,
    diebold_mariano_test,
    conditional_coverage_checks,
    evaluate_tier,
    evaluate_all_tiers,
    evaluation_summary,
)


def test_pinball_loss():
    """Test pinball loss computation."""
    y_true = np.array([100.0, 105.0, 95.0, 110.0])
    y_pred = np.array([102.0, 103.0, 98.0, 108.0])

    # Median (0.5)
    loss_median = pinball_loss(y_true, y_pred, 0.5)
    assert loss_median >= 0

    # Lower quantile
    loss_low = pinball_loss(y_true, y_pred, 0.1)
    assert loss_low >= 0

    # Upper quantile
    loss_high = pinball_loss(y_true, y_pred, 0.9)
    assert loss_high >= 0


def test_crps_gaussian():
    """Test CRPS for Gaussian."""
    y_true = np.array([100.0, 105.0, 95.0])
    mu = np.array([102.0, 103.0, 98.0])
    sigma = np.array([2.0, 3.0, 2.5])

    crps = crps_gaussian(y_true, mu, sigma)
    assert crps >= 0
    assert np.isfinite(crps)


def test_pit_values():
    """Test PIT value computation."""
    y_true = np.array([100.0, 105.0, 95.0, 110.0])
    y_low = np.array([95.0, 100.0, 90.0, 105.0])
    y_high = np.array([105.0, 110.0, 100.0, 115.0])

    pit = pit_values(y_true, y_low, y_high, 0.80)
    assert len(pit) == 4
    assert np.all(pit >= 0) and np.all(pit <= 1)

    # Test clipping
    y_true_out = np.array([80.0, 120.0])
    y_low_out = np.array([95.0, 105.0])
    y_high_out = np.array([105.0, 115.0])
    pit_out = pit_values(y_true_out, y_low_out, y_high_out, 0.80)
    assert np.all(pit_out >= 0) and np.all(pit_out <= 1)


def test_pit_uniformity_test():
    """Test PIT uniformity (KS test)."""
    # Uniform PIT values should pass
    pit_uniform = np.random.uniform(0, 1, 1000)
    stat, p = pit_uniformity_test(pit_uniform)
    assert p > 0.01  # Should not reject uniformity

    # Non-uniform should fail
    pit_skewed = np.random.beta(2, 5, 1000)
    stat, p = pit_uniformity_test(pit_skewed)
    assert p < 0.05  # Should reject uniformity


def test_mase():
    """Test MASE computation."""
    y_true = np.array([100.0, 105.0, 95.0, 110.0, 108.0])
    y_pred = np.array([102.0, 103.0, 98.0, 108.0, 106.0])
    y_naive = np.array([99.0, 100.0, 105.0, 95.0, 110.0])

    mase_val = mase(y_true, y_pred, y_naive)
    assert mase_val >= 0
    assert np.isfinite(mase_val)

    # MASE should be <= 1 if model beats naive
    # Our model has smaller errors
    assert mase_val < 1.5


def test_directional_accuracy():
    """Test directional accuracy."""
    y_true = np.array([105.0, 95.0, 110.0, 100.0])
    y_pred = np.array([103.0, 98.0, 108.0, 102.0])
    y_ref = np.array([100.0, 100.0, 100.0, 100.0])

    acc = directional_accuracy(y_true, y_pred, y_ref)
    assert 0 <= acc <= 1


def test_diebold_mariano_test():
    """Test Diebold-Mariano test."""
    # Two equal loss series
    loss1 = np.array([1.0, 2.0, 1.5, 1.2, 0.8, 1.1, 1.3, 0.9, 1.4, 1.0])
    loss2 = np.array([1.1, 1.9, 1.6, 1.3, 0.9, 1.2, 1.2, 1.0, 1.5, 1.1])

    result = diebold_mariano_test(loss1, loss2, alternative="two-sided")
    assert hasattr(result, 'dm_statistic')
    assert hasattr(result, 'p_value')
    assert hasattr(result, 'reject_null')
    assert 0 <= result.p_value <= 1

    # Test with clearly different losses (add small noise to avoid zero variance)
    loss3 = np.array([0.5 + np.random.normal(0, 0.01) for _ in range(30)])
    loss4 = np.array([2.0 + np.random.normal(0, 0.01) for _ in range(30)])
    result2 = diebold_mariano_test(loss3, loss4, alternative="less")
    assert bool(result2.reject_null) is True


def test_conditional_coverage_checks():
    """Test conditional coverage checks."""
    y_true = np.array([100.0, 105.0, 95.0, 110.0, 108.0, 102.0, 98.0, 112.0, 101.0, 115.0])
    y_low = np.array([95.0, 100.0, 90.0, 105.0, 103.0, 97.0, 93.0, 107.0, 96.0, 110.0])
    y_high = np.array([105.0, 110.0, 100.0, 115.0, 113.0, 107.0, 103.0, 117.0, 106.0, 120.0])

    # No regimes/events
    result = conditional_coverage_checks(y_true, y_low, y_high, 0.80)
    assert "overall" in result
    assert 0 <= result["overall"] <= 1

    # With regimes (need at least 5 samples per regime for meaningful check)
    regimes = np.array(["high_vol", "high_vol", "high_vol", "high_vol", "high_vol", "low_vol", "low_vol", "low_vol", "low_vol", "low_vol"])
    result2 = conditional_coverage_checks(y_true, y_low, y_high, 0.80, regimes=regimes)
    assert "regime_high_vol" in result2
    assert "regime_low_vol" in result2


def test_evaluate_tier():
    """Test complete tier evaluation."""
    forecasts = [
        {"symbol": "A", "actual": 105.0, "median": 103.0, "low": 98.0, "high": 108.0},
        {"symbol": "B", "actual": 95.0, "median": 97.0, "low": 92.0, "high": 102.0},
        {"symbol": "C", "actual": 110.0, "median": 108.0, "low": 103.0, "high": 113.0},
        {"symbol": "D", "actual": 100.0, "median": 102.0, "low": 97.0, "high": 107.0},
    ]

    baseline = [
        {"symbol": "A", "actual": 105.0, "median": 100.0, "low": 95.0, "high": 105.0},
        {"symbol": "B", "actual": 95.0, "median": 90.0, "low": 85.0, "high": 95.0},
        {"symbol": "C", "actual": 110.0, "median": 105.0, "low": 100.0, "high": 110.0},
        {"symbol": "D", "actual": 100.0, "median": 98.0, "low": 93.0, "high": 103.0},
    ]

    result = evaluate_tier("T3", ["A", "B", "C", "D"], forecasts, 0.80, baseline_forecasts=baseline)

    assert result.tier == "T3"
    assert result.n_symbols == 4
    assert result.n_forecasts == 4
    assert 0 <= result.coverage <= 1
    assert result.winkler_score >= 0
    assert result.mase >= 0
    assert result.mae >= 0
    assert result.rmse >= 0
    assert 0 <= result.directional_accuracy <= 1
    assert result.pit.n_samples == 4
    assert isinstance(result.conditional_coverage, dict)
    assert result.diebold_mariano is not None
    assert hasattr(result, 'promotion_gate_passed')


def test_evaluate_all_tiers():
    """Test evaluation across all tiers."""
    forecasts_t3 = [
        {"symbol": "A", "actual": 105.0, "median": 103.0, "low": 98.0, "high": 108.0},
        {"symbol": "B", "actual": 95.0, "median": 97.0, "low": 92.0, "high": 102.0},
    ]
    forecasts_t2 = [
        {"symbol": "C", "actual": 110.0, "median": 108.0, "low": 103.0, "high": 113.0},
    ]

    results = evaluate_all_tiers({
        "T3": forecasts_t3,
        "T2": forecasts_t2,
    }, 0.80)

    assert "T3" in results
    assert "T2" in results
    assert results["T3"].n_forecasts == 2
    assert results["T2"].n_forecasts == 1


def test_evaluation_summary():
    """Test evaluation summary generation."""
    forecasts = [
        {"symbol": "A", "actual": 105.0, "median": 103.0, "low": 98.0, "high": 108.0},
        {"symbol": "B", "actual": 95.0, "median": 97.0, "low": 92.0, "high": 102.0},
    ]

    results = evaluate_all_tiers({"T3": forecasts}, 0.80)
    summary = evaluation_summary(results)

    assert "evaluation_timestamp" in summary
    assert "tiers" in summary
    assert "overall" in summary
    assert summary["overall"]["total_forecasts"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])