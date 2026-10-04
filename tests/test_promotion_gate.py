"""Tests for extended promotion gate."""
from __future__ import annotations

import pytest

from forecasting.promotion_gate import (
    PromotionGateConfig,
    PromotionGateResult,
    DEFAULT_CONFIG,
    check_coverage_gate,
    check_mase_gate,
    check_winkler_gate,
    check_diebold_mariano_gate,
    check_conditional_coverage_gate,
    check_min_forecasts_gate,
    check_pinball_gate,
    run_promotion_gate,
    run_all_tier_gates,
    promotion_gate_summary,
)


class MockDMResult:
    """Mock Diebold-Mariano result."""
    def __init__(self, reject_null: bool, p_value: float):
        self.reject_null = reject_null
        self.p_value = p_value


class MockPinballLoss:
    """Mock pinball loss result."""
    def __init__(self, loss: float):
        self.loss = loss


def test_check_coverage_gate():
    """Test coverage gate."""
    # Within tolerance
    passed, msg = check_coverage_gate(0.78, 0.80, 0.05)
    assert passed is True
    assert "within" in msg

    # Outside tolerance
    passed, msg = check_coverage_gate(0.70, 0.80, 0.05)
    assert passed is False
    assert "outside" in msg


def test_check_mase_gate():
    """Test MASE gate."""
    # Good MASE
    passed, msg = check_mase_gate(0.85, 1.0)
    assert passed is True
    assert "not worse" in msg

    # Bad MASE
    passed, msg = check_mase_gate(1.15, 1.0)
    assert passed is False
    assert "worse" in msg


def test_check_winkler_gate():
    """Test Winkler gate."""
    # Model better than baseline
    passed, msg = check_winkler_gate(5.0, 6.0, 0.05, 100)
    assert passed is True
    assert "improvement" in msg

    # Model worse than baseline
    passed, msg = check_winkler_gate(7.0, 6.0, 0.05, 100)
    assert passed is False
    assert ">" in msg


def test_check_diebold_mariano_gate():
    """Test Diebold-Mariano gate."""
    # Significant result
    dm = MockDMResult(reject_null=True, p_value=0.01)
    passed, msg = check_diebold_mariano_gate(dm, 0.05)
    assert passed is True
    assert "rejects" in msg

    # Not significant
    dm = MockDMResult(reject_null=False, p_value=0.10)
    passed, msg = check_diebold_mariano_gate(dm, 0.05)
    assert passed is False
    assert "cannot reject" in msg

    # No DM result
    passed, msg = check_diebold_mariano_gate(None, 0.05)
    assert passed is False
    assert "No Diebold-Mariano" in msg


def test_missing_winkler_baseline_cannot_pass():
    assert check_winkler_gate(1.0, 0.0, 0.05, 100)[0] is False


def test_check_conditional_coverage_gate():
    """Test conditional coverage gate."""
    # All within tolerance
    cond = {"overall": 0.78, "regime_high_vol": 0.75, "regime_low_vol": 0.80}
    passed, msg = check_conditional_coverage_gate(cond, 0.80, 0.10)
    assert passed is True
    assert "within tolerance" in msg

    # One outside tolerance
    cond = {"overall": 0.78, "regime_high_vol": 0.60}
    passed, msg = check_conditional_coverage_gate(cond, 0.80, 0.10)
    assert passed is False
    assert "failures" in msg


def test_check_min_forecasts_gate():
    """Test minimum forecasts gate."""
    passed, msg = check_min_forecasts_gate(50, 30, "T3")
    assert passed is True

    passed, msg = check_min_forecasts_gate(20, 30, "T3")
    assert passed is False


def test_check_pinball_gate():
    """Test pinball loss gate."""
    class MockPL:
        def __init__(self, loss):
            self.loss = loss

    losses = [MockPL(0.02), MockPL(0.03), MockPL(0.01)]

    # Within cap
    passed, msg = check_pinball_gate(losses, 0.05)
    assert passed is True

    # Over cap
    passed, msg = check_pinball_gate(losses, 0.01)
    assert passed is False

    # No cap
    passed, msg = check_pinball_gate(losses, None)
    assert passed is True


def test_run_promotion_gate_all_pass():
    """Test promotion gate with all checks passing."""
    from forecasting.promotion_gate import run_promotion_gate

    class MockDMResult:
        def __init__(self):
            self.reject_null = True
            self.p_value = 0.01

    class MockPinballLoss:
        def __init__(self, loss):
            self.loss = loss

    result = run_promotion_gate(
        tier="T3",
        empirical_coverage=0.79,
        target_coverage=0.80,
        mase=0.85,
        model_winkler=5.0,
        baseline_winkler=6.0,
        dm_result=MockDMResult(),
        n_forecasts=100,
        conditional_coverage={"overall": 0.79, "regime_high_vol": 0.76},
        pinball_losses=[type('obj', (object,), {'loss': 0.02})()],
    )

    assert result.passed is True
    assert result.tier == "T3"
    assert all(result.checks.values())


def test_run_promotion_gate_fail_coverage():
    """Test promotion gate fails on coverage."""
    from forecasting.promotion_gate import run_promotion_gate

    class MockDMResult:
        def __init__(self):
            self.reject_null = True
            self.p_value = 0.01

    result = run_promotion_gate(
        tier="T3",
        empirical_coverage=0.60,  # Way below target
        target_coverage=0.80,
        mase=0.85,
        model_winkler=5.0,
        baseline_winkler=6.0,
        dm_result=MockDMResult(),
        n_forecasts=100,
        conditional_coverage={"overall": 0.60},
        pinball_losses=[],
    )

    assert result.passed is False
    assert result.checks["coverage"] is False


def test_run_promotion_gate_fail_mase():
    """Test promotion gate fails on MASE."""
    from forecasting.promotion_gate import run_promotion_gate

    class MockDMResult:
        def __init__(self):
            self.reject_null = True
            self.p_value = 0.01

    result = run_promotion_gate(
        tier="T3",
        empirical_coverage=0.79,
        target_coverage=0.80,
        mase=1.15,  # Worse than naive
        model_winkler=5.0,
        baseline_winkler=6.0,
        dm_result=MockDMResult(),
        n_forecasts=100,
        conditional_coverage=None,
        pinball_losses=[],
    )

    assert result.passed is False
    assert result.checks["mase"] is False


def test_run_promotion_gate_fail_dm():
    """Test promotion gate fails on Diebold-Mariano."""
    from forecasting.promotion_gate import run_promotion_gate

    class MockDMResult:
        def __init__(self):
            self.reject_null = False
            self.p_value = 0.50

    result = run_promotion_gate(
        tier="T3",
        empirical_coverage=0.79,
        target_coverage=0.80,
        mase=0.85,
        model_winkler=5.0,
        baseline_winkler=6.0,
        dm_result=MockDMResult(),  # Not significant
        n_forecasts=100,
        conditional_coverage=None,
        pinball_losses=[],
    )

    assert result.passed is False
    assert result.checks["diebold_mariano"] is False


def test_run_all_tier_gates():
    """Test running gates for all tiers."""
    from forecasting.promotion_gate import run_all_tier_gates, PromotionGateConfig

    class MockEvalResult:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class MockDMResult:
        def __init__(self):
            self.reject_null = True
            self.p_value = 0.01

    tier_results = {
        "T3": MockEvalResult(
            coverage=0.79,
            target_coverage=0.80,
            mase=0.85,
            winkler_score=5.0,
            baseline_winkler=6.0,
            n_forecasts=100,
            diebold_mariano=MockDMResult(),
            conditional_coverage={"overall": 0.79},
            pinball_losses=[],
        ),
"T2": MockEvalResult(
                coverage=0.78,  # Clearly within ±0.05 of 0.80
                target_coverage=0.80,
                mase=0.90,
                winkler_score=6.0,
                baseline_winkler=7.0,
                n_forecasts=50,
                diebold_mariano=MockDMResult(),
                conditional_coverage=None,
                pinball_losses=[],
            ),
    }

    config = PromotionGateConfig(min_forecasts_per_tier=30)
    results = run_all_tier_gates(tier_results, config)

    assert "T3" in results
    assert "T2" in results
    assert results["T3"].passed is True
    assert results["T2"].passed is True  # All checks should pass


def test_promotion_gate_summary():
    """Test promotion gate summary."""
    from forecasting.promotion_gate import promotion_gate_summary, PromotionGateResult, PromotionGateConfig

    results = {
        "T3": PromotionGateResult(
            tier="T3",
            passed=True,
            checks={"coverage": True, "mase": True},
            details={},
            reason="T3: All promotion gate checks passed",
        ),
        "T2": PromotionGateResult(
            tier="T2",
            passed=False,
            checks={"coverage": False, "mase": True},
            details={},
            reason="T2: FAILED checks: coverage",
        ),
    }

    summary = promotion_gate_summary(results)

    assert summary["tiers_total"] == 2
    assert summary["tiers_passed"] == 1
    assert summary["overall_passed"] is False
    assert "T3" in summary["tier_results"]
    assert "T2" in summary["tier_results"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
