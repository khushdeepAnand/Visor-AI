from __future__ import annotations

import math

import pytest

from derivatives.futures_engine import analyze_futures_contract
from derivatives.options_engine import analyze_option_chain, analyze_option_scenarios, validate_option_chain


def _futures_result(**updates):
    inputs = {
        "spot_price": 100,
        "futures_price": 101,
        "days_to_expiry": 30,
        "open_interest": 1200,
        "previous_open_interest": 1000,
        "previous_futures_price": 100,
        "annual_risk_free_rate": 0.06,
        "annual_carry_yield": 0.02,
        "contract_symbol": "TEST-FUT",
        "expiry": "2026-09-30",
        "as_of": "2026-09-05T10:00:00Z",
        "lot_size": 50,
        "volume": 400,
        "bid_price": 100.95,
        "ask_price": 101.05,
    }
    return analyze_futures_contract(**{**inputs, **updates})


def test_futures_is_explicitly_analytical_and_preserves_contract_metadata():
    result = _futures_result()

    assert result["analysis_type"] == "analytical_fair_value_basis_carry"
    assert "non-predictive" in result["analysis_label"].lower()
    assert result["is_predictive"] is False
    assert "not a future market-price target" in result["target_semantics"]["meaning"]
    assert result["as_of"] == "2026-09-05T10:00:00Z"
    assert result["contract_metadata"] == {
        "contract_symbol": "TEST-FUT",
        "expiry": "2026-09-30",
        "days_to_expiry": 30,
        "lot_size": 50.0,
    }
    assert result["oi_price_regime"]["label"] == "Long buildup"
    assert result["oi_price_regime"]["is_predictive"] is False
    assert "predict" in result["interpretation"].lower()


def test_futures_stress_ladders_have_expected_one_factor_monotonicity():
    scenarios = _futures_result()["stress_scenarios"]

    spot_values = [row["analytical_fair_value"] for row in scenarios["spot"]]
    rate_values = [row["analytical_fair_value"] for row in scenarios["rate"]]
    carry_values = [row["analytical_fair_value"] for row in scenarios["carry_yield"]]
    buy_values = [row["effective_buy_price"] for row in scenarios["slippage"]]
    sell_values = [row["effective_sell_price"] for row in scenarios["slippage"]]

    assert spot_values == sorted(spot_values)
    assert rate_values == sorted(rate_values)
    assert carry_values == sorted(carry_values, reverse=True)
    assert buy_values == sorted(buy_values)
    assert sell_values == sorted(sell_values, reverse=True)
    assert "not price targets or forecasts" in scenarios["semantics"]


def test_futures_expiry_day_fair_value_and_invalid_quote_warnings():
    result = _futures_result(days_to_expiry=0, bid_price=102, ask_price=101, volume=0)
    warning_codes = {warning["code"] for warning in result["warnings"]}

    assert result["theoretical_fair_value"] == result["spot_price"]
    assert result["annualized_basis_pct"] is None
    assert result["liquidity"]["market_condition_valid"] is False
    assert {"CROSSED_MARKET", "ZERO_VOLUME"}.issubset(warning_codes)


def test_futures_rejects_non_finite_market_inputs():
    with pytest.raises(ValueError, match="finite"):
        _futures_result(futures_price=math.nan)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_option_scenarios_are_analytical_and_sane(option_type):
    result = analyze_option_scenarios(
        spot=100,
        strike=100,
        days_to_expiry=30,
        volatility=0.25,
        risk_free_rate=0.05,
        option_type=option_type,
        market_price=4.5,
        as_of="2026-09-05T10:00:00Z",
    )
    spot_prices = [row["analytical_premium"] for row in result["scenarios"]["spot"]]
    vol_prices = [row["analytical_premium"] for row in result["scenarios"]["volatility"]]
    buys = [row["effective_buy_premium"] for row in result["scenarios"]["slippage"]]
    sells = [row["effective_sell_premium"] for row in result["scenarios"]["slippage"]]

    assert result["analysis_label"] == "Analytical option scenario ladder (non-predictive)"
    assert result["is_predictive"] is False
    assert result["historical_premium_validation"]["performed"] is False
    assert "no historical premium validation" in result["snapshot_comparison"]["semantics"].lower()
    assert spot_prices == sorted(spot_prices, reverse=option_type == "put")
    assert vol_prices == sorted(vol_prices)
    assert buys == sorted(buys)
    assert sells == sorted(sells, reverse=True)


def _quality_rows():
    return [
        {
            "underlying": "TEST",
            "expiry": "2026-09-30",
            "strike": 100,
            "option_type": "call",
            "open_interest": 100,
            "volume": 0,
            "bid": 6,
            "ask": 5,
            "quote_timestamp": "2026-09-05T08:00:00Z",
        },
        {
            "underlying": "TEST",
            "expiry": "2026-09-30",
            "strike": 100,
            "option_type": "put",
            "open_interest": 0,
            "volume": 0,
            "bid": 4,
            "ask": 8,
            "quote_timestamp": "2026-09-05T08:00:00Z",
        },
        {
            "underlying": "TEST",
            "expiry": "2026-09-30",
            "strike": 110,
            "option_type": "call",
            "open_interest": -5,
            "volume": 10,
            "bid": 1,
            "ask": 1.1,
            "quote_timestamp": "2026-09-05T09:59:00Z",
        },
        {
            "underlying": "TEST",
            "expiry": "2026-09-30",
            "strike": 110,
            "option_type": "put",
            "open_interest": 50,
            "volume": 10,
            "bid": 10,
            "ask": 10.2,
            "quote_timestamp": "2026-09-05T09:59:00Z",
        },
    ]


def test_option_chain_reports_crossed_stale_illiquid_spread_and_oi_defects():
    result = analyze_option_chain(
        _quality_rows(),
        spot_price=102,
        as_of="2026-09-05T10:00:00Z",
        stale_after_seconds=900,
        wide_spread_pct=20,
    )
    codes = {warning["code"] for warning in result["warnings"]}

    assert {"CROSSED_MARKET", "STALE_QUOTE", "WIDE_SPREAD", "ZERO_OPEN_INTEREST", "INVALID_OPEN_INTEREST"}.issubset(codes)
    assert result["validation"]["market_data_valid"] is False
    assert result["validation"]["crossed_row_count"] == 1
    assert result["validation"]["wide_spread_row_count"] == 1
    assert result["validation"]["is_stale"] is True
    assert result["validation"]["excluded_row_count"] == 1
    assert result["contract_metadata"]["underlying"] == "TEST"
    assert result["historical_premium_validation"]["performed"] is False
    assert "not forecasts" in result["settlement_scenarios"]["semantics"]


def test_option_chain_missing_quote_evidence_is_disclosed():
    rows = [
        {"strike": 100, "option_type": "call", "open_interest": 10},
        {"strike": 100, "option_type": "put", "open_interest": 20},
    ]
    quality = validate_option_chain(rows, as_of="2026-09-05T10:00:00Z")
    codes = {warning["code"] for warning in quality["warnings"]}

    assert quality["market_data_valid"] is None
    assert quality["is_stale"] is None
    assert {"SPREAD_UNAVAILABLE", "VOLUME_UNAVAILABLE", "QUOTE_TIME_UNAVAILABLE"}.issubset(codes)


def test_option_chain_requires_valid_call_and_put_evidence():
    with pytest.raises(ValueError, match="valid call and one valid put"):
        analyze_option_chain([{"strike": 100, "option_type": "call", "open_interest": 10}])
