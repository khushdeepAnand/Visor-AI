from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from analytics.pattern_engine import detect_chart_patterns
from derivatives.futures_engine import analyze_futures_contract, analyze_futures_term_structure, classify_open_interest
from derivatives.options_engine import analyze_option_chain, binomial_tree, black_scholes, implied_volatility


def test_black_scholes_call_and_put_are_positive():
    call = black_scholes(spot=100, strike=100, days_to_expiry=365, volatility=0.20, risk_free_rate=0.05, option_type="call")
    put = black_scholes(spot=100, strike=100, days_to_expiry=365, volatility=0.20, risk_free_rate=0.05, option_type="put")
    assert call["theoretical_price"] == pytest.approx(10.45, abs=0.08)
    assert put["theoretical_price"] == pytest.approx(5.57, abs=0.08)
    assert 0 < call["delta"] < 1
    assert -1 < put["delta"] < 0
    assert call["gamma"] > 0


def test_implied_volatility_recovers_input_volatility():
    premium = black_scholes(spot=100, strike=105, days_to_expiry=45, volatility=0.32, option_type="call")["theoretical_price"]
    recovered = implied_volatility(market_price=premium, spot=100, strike=105, days_to_expiry=45, option_type="call")
    assert recovered == pytest.approx(0.32, abs=1e-4)


def test_option_chain_analysis_pcr_walls_and_max_pain():
    rows = [
        {"strike": 90, "option_type": "call", "open_interest": 100},
        {"strike": 90, "option_type": "put", "open_interest": 500},
        {"strike": 100, "option_type": "call", "open_interest": 600},
        {"strike": 100, "option_type": "put", "open_interest": 700},
        {"strike": 110, "option_type": "call", "open_interest": 900},
        {"strike": 110, "option_type": "put", "open_interest": 100},
    ]
    result = analyze_option_chain(rows, spot_price=101)
    assert result["put_call_ratio"] == pytest.approx(1300 / 1600)
    assert result["call_wall"]["strike"] == 110
    assert result["put_wall"]["strike"] == 100
    assert result["max_pain_strike"] in {90, 100, 110}
    assert "not a calibrated" in result["disclaimer"]


@pytest.mark.parametrize(
    ("price", "oi", "expected"),
    [
        (1, 1, "Long buildup"),
        (-1, 1, "Short buildup"),
        (1, -1, "Short covering"),
        (-1, -1, "Long unwinding"),
    ],
)
def test_open_interest_classification(price, oi, expected):
    assert classify_open_interest(price, oi) == expected


def test_futures_analysis_contract():
    result = analyze_futures_contract(
        spot_price=100,
        futures_price=102,
        days_to_expiry=30,
        open_interest=1200,
        previous_open_interest=1000,
        previous_futures_price=101,
    )
    assert result["basis"] == 2
    assert result["classification"] == "Long buildup"
    assert "theoretical_fair_value" in result


def test_futures_term_structure_uses_quoted_prices_for_roll_cost():
    result = analyze_futures_term_structure(
        spot_price=100,
        contracts=[
            {"label": "Far", "days_to_expiry": 90, "futures_price": 106, "open_interest": 200},
            {"label": "Near", "days_to_expiry": 30, "futures_price": 102, "open_interest": 500},
            {"label": "Next", "days_to_expiry": 60, "futures_price": 104, "open_interest": 300},
        ],
    )
    assert result["curve_state"] == "contango"
    assert [item["label"] for item in result["contracts"]] == ["Near", "Next", "Far"]
    assert result["rollovers"][0]["long_roll_cost_points"] == 2
    assert result["rollovers"][0]["long_roll_cost_pct"] == pytest.approx(1.960784, abs=1e-6)
    assert result["contracts"][0]["open_interest_share_pct"] == 50
    assert result["is_predictive"] is False


def test_futures_term_structure_classifies_backwardation_and_mixed_curves():
    backwardation = analyze_futures_term_structure(
        spot_price=100,
        contracts=[
            {"label": "Near", "days_to_expiry": 20, "futures_price": 105},
            {"label": "Next", "days_to_expiry": 50, "futures_price": 103},
            {"label": "Far", "days_to_expiry": 80, "futures_price": 101},
        ],
    )
    mixed = analyze_futures_term_structure(
        spot_price=100,
        contracts=[
            {"label": "Near", "days_to_expiry": 20, "futures_price": 101},
            {"label": "Next", "days_to_expiry": 50, "futures_price": 103},
            {"label": "Far", "days_to_expiry": 80, "futures_price": 102},
        ],
    )
    assert backwardation["curve_state"] == "backwardation"
    assert backwardation["rollovers"][0]["long_roll_cost_pct"] < 0
    assert mixed["curve_state"] == "mixed"


def test_futures_term_structure_rejects_ambiguous_contract_order():
    with pytest.raises(ValueError, match="unique"):
        analyze_futures_term_structure(
            spot_price=100,
            contracts=[
                {"label": "Near", "days_to_expiry": 30, "futures_price": 101},
                {"label": "Next", "days_to_expiry": 30, "futures_price": 102},
            ],
        )


def test_futures_term_structure_api_contract():
    response = TestClient(api_main.app).post(
        "/api/v1/derivatives/futures/term-structure",
        json={
            "spot_price": 22000,
            "contracts": [
                {"label": "Near", "days_to_expiry": 12, "futures_price": 22040, "open_interest": 1000},
                {"label": "Next", "days_to_expiry": 40, "futures_price": 22120, "open_interest": 600},
            ],
            "annual_risk_free_rate": 0.065,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["curve_state"] == "contango"
    assert body["contracts"][0]["label"] == "Near"
    assert body["rollovers"][0]["from_label"] == "Near"
    assert "future price prediction" in body["data_status"]


def _pattern_frame(rows=100):
    dates = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = 100 + np.sin(np.arange(rows) / 6) * 4 + np.linspace(0, 2, rows)
    high = close + 1
    low = close - 1
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": 1000}, index=dates)


def test_pattern_engine_returns_support_resistance_and_disclaimer():
    result = detect_chart_patterns(_pattern_frame())
    assert result["support"] < result["resistance"]
    assert isinstance(result["patterns"], list)
    assert "not probabilities" in result["disclaimer"]


def test_pattern_engine_rejects_short_history():
    with pytest.raises(ValueError, match="30"):
        detect_chart_patterns(_pattern_frame(20))


def test_binomial_european_converges_to_black_scholes_reference():
    bsm = black_scholes(spot=100, strike=100, days_to_expiry=365, volatility=0.20, risk_free_rate=0.05, option_type="call")["theoretical_price"]
    tree = binomial_tree(spot=100, strike=100, days_to_expiry=365, volatility=0.20, risk_free_rate=0.05, option_type="call", american=False, steps=800)
    assert tree["theoretical_price"] == pytest.approx(bsm, abs=0.03)


def _scenario_payload(index: bool = True) -> dict:
    payload = {
        "spot": 24000.0,
        "strike": 24000.0,
        "days_to_expiry": 30,
        "volatility": 0.20,
        "risk_free_rate": 0.065,
        "option_type": "call",
    }
    payload["underlying_type"] = "index" if index else "stock"
    return payload


def test_option_scenarios_endpoint_returns_three_labelled_ladders_without_internals():
    body = TestClient(api_main.app).post("/api/v1/derivatives/options/scenarios", json=_scenario_payload(index=True)).json()
    assert body["model_label"] == "Analytical scenario ladder"
    assert body["exercise_type"] == "European exercise estimate"
    assert [s["label"] for s in body["scenarios"]] == ["Bear", "Base", "Bull"]
    assert all(s["reference_price"] for s in body["scenarios"])
    assert all("delta" in s["greeks"] for s in body["scenarios"])
    for internal in ("d1", "d2", "pricing_model", "steps"):
        assert internal not in json.dumps(body)


def test_option_scenarios_stock_uses_american_exercise():
    body = TestClient(api_main.app).post("/api/v1/derivatives/options/scenarios", json=_scenario_payload(index=False)).json()
    assert body["exercise_type"] == "American exercise estimate"
    assert len(body["scenarios"]) == 3
