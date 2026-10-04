"""Tests for multi-leg option payoff arithmetic.

The payoff numbers here are checked against textbook closed forms, and the
unbounded-risk cases are checked explicitly, because a strategy builder that
understates the loss on a naked short leg would be actively dangerous.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from derivatives.payoff import MAX_LEGS, PayoffError, build_payoff, normalize_leg  # noqa: E402


def _payoff_at(result, price: float) -> float:
    """Nearest plotted payoff to the requested price."""
    return min(result["curve"], key=lambda point: abs(point["price"] - price))["payoff"]


# -- validation ------------------------------------------------------------


def test_empty_strategy_is_rejected():
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(legs=[], spot=100.0)
    assert excinfo.value.code == "payoff_legs_required"


def test_too_many_legs_is_rejected():
    legs = [{"type": "call", "side": "buy", "strike": 100 + index, "premium": 1.0} for index in range(MAX_LEGS + 1)]
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(legs=legs, spot=100.0)
    assert excinfo.value.code == "payoff_legs_too_many"


def test_option_leg_requires_a_strike():
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(legs=[{"type": "call", "side": "buy", "premium": 5.0}], spot=100.0)
    assert excinfo.value.code == "payoff_leg_strike_required"


def test_unsupported_leg_type_is_rejected():
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(legs=[{"type": "swaption", "side": "buy", "premium": 1.0}], spot=100.0)
    assert excinfo.value.code == "payoff_leg_type_unsupported"


def test_invalid_spot_and_quantity_are_rejected():
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(legs=[{"type": "call", "side": "buy", "strike": 100, "premium": 1.0}], spot=0)
    assert excinfo.value.code == "payoff_spot_invalid"
    with pytest.raises(PayoffError) as excinfo:
        build_payoff(
            legs=[{"type": "call", "side": "buy", "strike": 100, "premium": 1.0, "quantity": 0}],
            spot=100.0,
        )
    assert excinfo.value.code == "payoff_leg_quantity_invalid"


def test_leg_aliases_are_normalized():
    leg = normalize_leg({"type": "CE", "side": "SHORT", "strike": 100, "premium": 4, "lot_size": 50}, 0)
    assert leg["type"] == "call"
    assert leg["side"] == "sell"
    assert leg["contracts"] == 50


# -- single legs -----------------------------------------------------------


def test_long_call_has_bounded_loss_and_unbounded_profit():
    result = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0, "lot_size": 50}],
        spot=100.0,
    )
    assert result["max_loss"]["unbounded"] is False
    assert result["max_loss"]["value"] == pytest.approx(-250.0)  # 5 * 50 premium paid
    assert result["max_profit"]["unbounded"] is True
    assert result["breakevens"] == [105.0]
    assert result["position"] == "debit"
    assert result["net_premium"] == pytest.approx(250.0)


def test_short_put_worst_case_is_reported_at_a_price_of_zero_not_at_the_grid_edge():
    """A narrow plotted window must not understate a naked short put's loss."""
    result = build_payoff(
        legs=[{"type": "put", "side": "sell", "strike": 100.0, "premium": 4.0, "lot_size": 25}],
        spot=100.0,
        grid_span=0.05,  # plots only 95..105, far above the true worst case
    )
    # A put's downside is finite only because price cannot go below zero:
    # (0 - 100) * 25 + 4 * 25 = -2400.
    assert result["max_loss"]["unbounded"] is False
    assert result["max_loss"]["value"] == pytest.approx(-2400.0)
    assert result["max_loss"]["at_price"] == pytest.approx(0.0)
    assert result["max_loss"]["outside_plotted_range"] is True
    assert "falls to zero" in result["max_loss"]["note"]
    assert "not because" in result["max_loss"]["note"]
    assert result["max_profit"]["unbounded"] is False
    assert result["max_profit"]["value"] == pytest.approx(100.0)  # 4 * 25 credit
    assert result["position"] == "credit"
    # The short put's payoff falls as the price falls, i.e. rises with price.
    assert result["tails"]["downside_slope_per_point"] == pytest.approx(25.0)


def test_long_put_profit_is_bounded_by_the_zero_floor():
    result = build_payoff(
        legs=[{"type": "put", "side": "buy", "strike": 100.0, "premium": 4.0}],
        spot=100.0,
        grid_span=0.05,
    )
    assert result["max_profit"]["unbounded"] is False
    assert result["max_profit"]["value"] == pytest.approx(96.0)  # 100 strike - 4 premium
    assert result["max_profit"]["outside_plotted_range"] is True
    assert result["max_loss"]["value"] == pytest.approx(-4.0)
    assert result["tails"]["downside_slope_per_point"] == pytest.approx(-1.0)


def test_short_call_loss_is_unbounded():
    result = build_payoff(
        legs=[{"type": "call", "side": "sell", "strike": 100.0, "premium": 3.0}],
        spot=100.0,
    )
    assert result["max_loss"]["unbounded"] is True
    assert result["tails"]["upside_slope_per_point"] == pytest.approx(-1.0)


def test_long_future_payoff_is_linear_with_a_finite_floor():
    result = build_payoff(
        legs=[{"type": "future", "side": "buy", "premium": 100.0, "lot_size": 10}],
        spot=100.0,
    )
    assert result["max_profit"]["unbounded"] is True
    assert result["max_loss"]["unbounded"] is False
    assert result["max_loss"]["value"] == pytest.approx(-1000.0)  # entry 100 * 10 units
    assert result["max_loss"]["at_price"] == pytest.approx(0.0)
    assert result["max_loss"]["outside_plotted_range"] is True
    assert _payoff_at(result, 110.0) == pytest.approx(100.0, abs=1.0)  # 10 points * 10 units


def test_spread_best_case_beyond_the_grid_is_still_reported():
    result = build_payoff(
        legs=[
            {"type": "call", "side": "buy", "strike": 100.0, "premium": 6.0},
            {"type": "call", "side": "sell", "strike": 110.0, "premium": 2.0},
        ],
        spot=100.0,
        grid_span=0.05,  # plots only 95..105, below the short strike
    )
    assert result["max_profit"]["value"] == pytest.approx(6.0)
    assert result["max_profit"]["outside_plotted_range"] is True
    assert "outside the plotted range" in result["max_profit"]["note"]


# -- spreads ---------------------------------------------------------------


def test_bull_call_spread_is_bounded_on_both_sides():
    result = build_payoff(
        legs=[
            {"type": "call", "side": "buy", "strike": 100.0, "premium": 6.0},
            {"type": "call", "side": "sell", "strike": 110.0, "premium": 2.0},
        ],
        spot=100.0,
        grid_span=0.30,
    )
    assert result["max_loss"]["unbounded"] is False
    assert result["max_profit"]["unbounded"] is False
    assert result["max_loss"]["value"] == pytest.approx(-4.0)  # net debit
    assert result["max_profit"]["value"] == pytest.approx(6.0)  # 10 wide - 4 debit
    assert result["breakevens"] == [104.0]
    assert result["risk_reward_ratio"] == pytest.approx(1.5, abs=0.01)


def test_long_straddle_has_two_breakevens():
    result = build_payoff(
        legs=[
            {"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0},
            {"type": "put", "side": "buy", "strike": 100.0, "premium": 5.0},
        ],
        spot=100.0,
        grid_span=0.25,
    )
    assert result["breakevens"] == [90.0, 110.0]
    assert result["max_profit"]["unbounded"] is True
    assert result["payoff_at_spot"] == pytest.approx(-10.0)


def test_iron_condor_is_fully_bounded():
    result = build_payoff(
        legs=[
            {"type": "put", "side": "buy", "strike": 90.0, "premium": 1.0},
            {"type": "put", "side": "sell", "strike": 95.0, "premium": 2.5},
            {"type": "call", "side": "sell", "strike": 105.0, "premium": 2.5},
            {"type": "call", "side": "buy", "strike": 110.0, "premium": 1.0},
        ],
        spot=100.0,
        grid_span=0.30,
    )
    assert result["max_profit"]["unbounded"] is False
    assert result["max_loss"]["unbounded"] is False
    assert result["max_profit"]["value"] == pytest.approx(3.0)  # net credit
    assert result["max_loss"]["value"] == pytest.approx(-2.0)  # 5 wide - 3 credit
    assert len(result["breakevens"]) == 2


def test_costs_reduce_every_point_on_the_curve():
    base = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
    )
    with_costs = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
        costs=2.0,
    )
    assert with_costs["payoff_at_spot"] == pytest.approx(base["payoff_at_spot"] - 2.0)
    assert with_costs["costs_applied"] == 2.0


# -- optional model valuation ---------------------------------------------


def test_valuation_block_is_absent_unless_assumptions_are_supplied():
    result = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
    )
    assert result["valuation"]["state"] == "not_requested"


def test_valuation_reuses_black_scholes_and_is_labelled_non_predictive():
    result = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
        volatility=0.25,
        days_to_expiry=30,
    )
    valuation = result["valuation"]
    assert valuation["state"] == "available"
    assert valuation["model"] == "black_scholes"
    assert valuation["is_predictive"] is False
    assert valuation["legs"][0]["theoretical_price"] > 0
    assert valuation["assumptions"]["volatility"] == 0.25


def test_valuation_rejects_impossible_assumptions_without_failing_the_payoff():
    result = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
        volatility=0.0,
        days_to_expiry=30,
    )
    assert result["valuation"]["state"] == "unavailable"
    assert result["valuation"]["reason"] == "volatility_must_be_positive"
    assert result["max_loss"]["value"] == pytest.approx(-5.0)


# -- boundaries ------------------------------------------------------------


def test_margin_is_reported_as_unavailable_rather_than_guessed():
    result = build_payoff(
        legs=[{"type": "put", "side": "sell", "strike": 100.0, "premium": 4.0}],
        spot=100.0,
    )
    assert result["margin"]["state"] == "unavailable"
    assert result["margin"]["reason"] == "broker_risk_parameters_not_held"


def test_payoff_is_never_labelled_as_a_forecast_or_recommendation():
    result = build_payoff(
        legs=[{"type": "call", "side": "buy", "strike": 100.0, "premium": 5.0}],
        spot=100.0,
    )
    assert result["is_forecast"] is False
    assert result["is_recommendation"] is False
    assert any("not a forecast" in text.lower() for text in result["disclosures"])
    assert any("cannot place" in text.lower() for text in result["disclosures"])
    forbidden = {"probability_of_profit", "expected_value", "win_rate", "recommendation"}
    assert forbidden.isdisjoint(result.keys())
