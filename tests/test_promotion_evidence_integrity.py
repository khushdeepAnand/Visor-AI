"""Invalid or adverse evidence must never promote a forecasting model."""
from types import SimpleNamespace

import pytest

from forecasting.promotion_gate import (
    check_conditional_coverage_gate, check_diebold_mariano_gate,
    check_mase_gate, check_winkler_gate, promotion_gate_summary,
)


@pytest.mark.parametrize("statistic,p_value", [(3.0, .001), (0.0, .001), (-3.0, -.01), (-float("inf"), .001)])
def test_dm_requires_finite_evidence_in_favour_of_candidate(statistic, p_value):
    result = SimpleNamespace(reject_null=True, p_value=p_value, dm_statistic=statistic)
    assert not check_diebold_mariano_gate(result, .05)[0]


def test_missing_direction_is_not_evidence_of_superiority():
    assert not check_diebold_mariano_gate(SimpleNamespace(reject_null=True, p_value=.001), .05)[0]


@pytest.mark.parametrize("value", [-1.0, -float("inf"), float("nan")])
def test_invalid_mase_fails_closed(value):
    assert not check_mase_gate(value, 1.0)[0]


@pytest.mark.parametrize("model,baseline", [(-1., 2.), (1., float("inf")), (float("nan"), 2.)])
def test_invalid_interval_scores_fail_closed(model, baseline):
    assert not check_winkler_gate(model, baseline, .05, 100)[0]


def test_nan_conditional_coverage_is_not_a_pass():
    assert not check_conditional_coverage_gate({"overall": .8, "high_vol": float("nan")}, .8, .1)[0]


def test_empty_tier_summary_cannot_claim_a_pass():
    assert promotion_gate_summary({})["overall_passed"] is False
