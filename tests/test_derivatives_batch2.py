"""Batch 2 coverage: strategy templates, multi-leg P&L, OI heatmap, SPAN margin, IV stats.

Covers the new derivatives surface end-to-end through the API where it is
exposed, and at module level where the API only wraps it. Every assertion
checks real computed structure (strikes, breakevens, calibration values),
not just key presence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from derivatives import iv_stats as ivs
from derivatives.oi_heatmap import build_oi_heatmap
from derivatives.options_engine import black_scholes
from derivatives.span_margin import estimate_span_margin
from derivatives.strategy_templates import (
    TEMPLATES,
    TemplateError,
    build_template,
    calendar_payoff,
    list_templates,
)


@pytest.fixture
def client():
    return TestClient(api_main.app)


def _build(client: TestClient, payload: dict) -> dict:
    body = {"spot": 100.0, "volatility": 0.2, "days_to_expiry": 30}
    body.update(payload)
    return client.post("/api/v1/derivatives/strategies/build", json=body)


def _oi_rows(with_previous: bool = False) -> list[dict]:
    rows = [
        {"strike": 90, "option_type": "call", "open_interest": 100},
        {"strike": 90, "option_type": "put", "open_interest": 500},
        {"strike": 100, "option_type": "call", "open_interest": 600},
        {"strike": 100, "option_type": "put", "open_interest": 700},
        {"strike": 110, "option_type": "call", "open_interest": 900},
        {"strike": 110, "option_type": "put", "open_interest": 100},
    ]
    if with_previous:
        previous = {90: (50, 600), 100: (550, 700), 110: (1000, 50)}
        for row in rows:
            call_prev, put_prev = previous[row["strike"]]
            row["previous_open_interest"] = call_prev if row["option_type"] == "call" else put_prev
    return rows


# ---------------------------------------------------------------------------
# Strategy templates: catalog
# ---------------------------------------------------------------------------


def test_template_catalog_lists_twelve_structures(client):
    response = client.get("/api/v1/derivatives/strategies/templates")
    assert response.status_code == 200
    body = response.json()
    assert len(body["templates"]) == len(TEMPLATES) == 12
    assert body["max_legs"] == 4
    names = {item["name"] for item in body["templates"]}
    assert {"iron_condor", "calendar_call", "straddle", "put_ratio_spread"} <= names
    for item in body["templates"]:
        assert item["label"] and item["description"]
        assert isinstance(item["params"], dict)
        assert item["legs"] in (2, 4)
    assert body["disclosures"]


# ---------------------------------------------------------------------------
# Strategy templates: build (API)
# ---------------------------------------------------------------------------


def test_iron_condor_build_returns_structured_legs_payoff_and_margin(client):
    response = _build(client, {"template": "iron_condor"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["template"] == "iron_condor"
    assert body["label"] == "Iron condor"
    assert body["is_forecast"] is False and body["is_recommendation"] is False

    legs = body["legs"]
    assert len(legs) == 4
    assert [(leg["side"], leg["type"], leg["strike"]) for leg in legs] == [
        ("sell", "put", 95.0),
        ("buy", "put", 90.0),
        ("sell", "call", 105.0),
        ("buy", "call", 110.0),
    ]
    assert all(leg["premium"] > 0 for leg in legs)
    assert all(leg["contracts"] == 1 for leg in legs)
    assert all(leg["premium_basis"] == "model" for leg in legs)
    assert body["premium_basis"] == ["model", "model", "model", "model"]
    assert body["metadata"]["all_model_priced"] is True
    assert body["metadata"]["all_user_supplied"] is False
    assert body["metadata"]["strike_step"] == 1.0

    payoff = body["payoff"]
    assert body["payoff_unavailable_reason"] is None
    assert payoff is not None
    assert len(payoff["breakevens"]) == 2
    assert 93.5 < payoff["breakevens"][0] < 94.5
    assert 105.5 < payoff["breakevens"][1] < 106.5
    assert payoff["position"] == "credit"
    assert payoff["max_profit"]["unbounded"] is False
    assert payoff["max_loss"]["unbounded"] is False
    # A condor earns at most its net credit and loses at most the wing width.
    assert payoff["max_profit"]["value"] == pytest.approx(-payoff["net_premium"], abs=0.02)
    assert -5.0 <= payoff["max_loss"]["value"] < 0
    assert payoff["max_profit"]["value"] > 0

    margin = body["margin"]
    assert body["margin_unavailable_reason"] is None
    assert margin is not None
    assert margin["basis"] == "span_style_estimate"
    assert margin["is_official_requirement"] is False
    assert margin["scenarios_evaluated"] == 36
    # Defined-risk: the requirement cannot exceed one wing width per contract.
    assert 0 < margin["total_margin"] <= 5.0 + 1e-6
    assert margin["total_margin"] >= margin["short_premium_floor"] > 0
    assert margin["strategy_offset"] >= 0
    assert margin["worst_scenario"] is not None
    assert margin["worst_scenario"]["underlying_move_pct"] in (-15.0, -10.0, -5.0, -3.0, 0.0, 3.0, 5.0, 10.0, 15.0)

    disclosures = body["disclosures"]
    assert any("not a recommendation" in item for item in disclosures)
    assert any("SPAN-style estimate" in item for item in disclosures)


def test_user_supplied_premium_is_labelled_per_leg(client):
    response = _build(client, {"template": "iron_condor", "premiums": {"1": 0.55}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["premium_basis"][0] == "user"
    assert body["premium_basis"][1:] == ["model", "model", "model"]
    assert body["legs"][0]["premium"] == 0.55
    assert body["legs"][0]["premium_basis"] == "user"
    assert body["metadata"]["all_model_priced"] is False
    assert body["metadata"]["all_user_supplied"] is False


def test_unknown_template_is_rejected_with_stable_code(client):
    response = _build(client, {"template": "iron_condor_ultra"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "template_unknown"
    assert detail["retryable"] is False


def test_missing_premium_without_volatility_is_rejected(client):
    response = client.post(
        "/api/v1/derivatives/strategies/build",
        json={"template": "straddle", "spot": 100.0},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "template_premium_required"
    assert "volatility" in detail["message"]


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_builds_with_payoff_and_margin(client, name):
    response = _build(client, {"template": name})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template"] == name
    assert len(body["legs"]) == TEMPLATES[name]["legs"]
    assert all(basis == "model" for basis in body["premium_basis"])
    if name.startswith("calendar_"):
        assert [leg["days_to_expiry"] for leg in body["legs"]] == [30, 90]
        assert body["payoff"] is not None
        assert body["payoff"]["basis"] == "near_expiry_intrinsic_plus_far_mark_to_model"
    else:
        assert body["payoff"] is not None
        assert isinstance(body["payoff"]["breakevens"], list)
        assert body["payoff"]["max_profit"] is not None
        assert body["payoff"]["max_loss"] is not None
    assert body["margin"] is not None
    assert body["margin"]["basis"] == "span_style_estimate"


def test_calendar_without_volatility_degrades_with_disclosed_reasons(client):
    response = _build(
        client,
        {"template": "calendar_call", "volatility": None, "premiums": {"1": 2.5, "2": 4.7}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["premium_basis"] == ["user", "user"]
    assert body["payoff"] is None
    assert body["payoff_unavailable_reason"] == "calendar_curve_requires_volatility_to_mark_the_deferred_leg"
    assert body["margin"] is None
    assert body["margin_unavailable_reason"] == "margin_scan_requires_volatility"


# ---------------------------------------------------------------------------
# Calendar payoff (module)
# ---------------------------------------------------------------------------


def test_calendar_payoff_curve_and_label():
    built = build_template("calendar_call", spot=100.0, volatility=0.2, days_to_expiry=30)
    assert [leg["days_to_expiry"] for leg in built["legs"]] == [30, 90]
    assert built["legs"][0]["side"] == "sell"
    assert built["legs"][1]["side"] == "buy"
    assert built["legs"][0]["strike"] == built["legs"][1]["strike"]

    payoff = calendar_payoff(built, volatility=0.2)
    assert payoff["basis"] == "near_expiry_intrinsic_plus_far_mark_to_model"
    assert len(payoff["curve"]) == 121
    assert len(payoff["breakevens"]) >= 1
    assert payoff["max_loss"] <= payoff["payoff_at_spot"] <= payoff["max_profit"]
    assert payoff["is_forecast"] is False
    assert "not a quote" in payoff["label"].lower()


def test_calendar_rejects_inverted_expiry_window():
    with pytest.raises(TemplateError) as excinfo:
        build_template(
            "calendar_call",
            spot=100.0,
            params={"near_days": 90, "far_days": 30},
            volatility=0.2,
        )
    assert excinfo.value.code == "template_calendar_dte_invalid"


# ---------------------------------------------------------------------------
# Live P&L endpoint
# ---------------------------------------------------------------------------


def test_pnl_endpoint_mixes_user_and_model_marks(client):
    response = client.post(
        "/api/v1/derivatives/strategies/pnl",
        json={
            "legs": [
                {"type": "call", "side": "buy", "strike": 100, "premium": 2.5, "mark_price": 3.0},
                {"type": "put", "side": "buy", "strike": 100, "premium": 2.0},
            ],
            "spot_now": 100.0,
            "volatility_now": 0.25,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_pnl_basis"] == "all_legs_valued"

    user_leg, model_leg = body["legs"]
    assert user_leg["mark_basis"] == "user_mark"
    assert user_leg["current_mark"] == 3.0
    assert user_leg["pnl"] == pytest.approx(0.5)
    assert model_leg["mark_basis"] == "model_mark"
    assert model_leg["current_mark"] > 0
    assert model_leg["pnl"] == pytest.approx(model_leg["current_mark"] - 2.0, abs=0.01)
    assert body["total_pnl"] == pytest.approx(user_leg["pnl"] + model_leg["pnl"])
    assert body["is_forecast"] is False and body["is_recommendation"] is False
    assert "Not a forecast" in body["disclaimer"]


def test_pnl_endpoint_reports_unvalued_leg_as_partial(client):
    response = client.post(
        "/api/v1/derivatives/strategies/pnl",
        json={
            "legs": [
                {"type": "call", "side": "buy", "strike": 100, "premium": 2.5},
                {"type": "call", "side": "buy", "strike": 101, "premium": 2.0, "mark_price": 2.2},
            ],
            "spot_now": 100.0,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    unavailable, valued = body["legs"]
    assert unavailable["mark_basis"] == "unavailable"
    assert unavailable["current_mark"] is None
    assert unavailable["pnl"] is None
    assert unavailable["mark_unavailable_reason"]
    assert valued["mark_basis"] == "user_mark"
    assert valued["pnl"] == pytest.approx(0.2)
    assert body["total_pnl"] == pytest.approx(0.2)
    assert body["total_pnl_basis"] == "partial"


def test_pnl_endpoint_rejects_invalid_strikes(client):
    response = client.post(
        "/api/v1/derivatives/strategies/pnl",
        json={"legs": [{"type": "call", "side": "buy", "strike": 0, "premium": 1}], "spot_now": 100.0},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# OI heatmap (module + API)
# ---------------------------------------------------------------------------


def test_oi_heatmap_walls_totals_and_null_changes():
    result = build_oi_heatmap(_oi_rows(), spot_price=100.0, expiry="2026-10-29")
    assert result["rows_count"] == 6
    assert result["landmarks"]["call_wall"] == 110.0
    assert result["landmarks"]["put_wall"] == 100.0
    assert result["landmarks"]["nearest_support"] == 90.0
    assert result["landmarks"]["nearest_resistance"] == 110.0
    assert result["totals"]["call_oi"] == 1600.0
    assert result["totals"]["put_oi"] == 1300.0
    assert result["totals"]["put_call_ratio"] == pytest.approx(1300 / 1600)
    assert result["totals"]["change_basis"] is None
    assert result["totals"]["put_call_ratio_change"] is None
    assert all(row["call_oi_change"] is None and row["put_oi_change"] is None for row in result["rows"])
    assert result["landmarks"]["top_buildups"] == []
    assert result["landmarks"]["top_unwinds"] == []
    assert result["pin_risk"]["level"] == "high"
    assert result["pin_risk"]["basis"] == "oi_clustering_descriptive"
    assert result["pin_risk"]["max_pain"] == 100.0
    assert result["pin_risk"]["max_oi_strike"] == 100.0
    assert result["pin_risk"]["distance_pct"] == 0.0
    assert result["is_forecast"] is False and result["is_recommendation"] is False
    assert len(result["disclosures"]) >= 4


def test_oi_heatmap_previous_oi_drives_changes_buildups_and_unwinds():
    result = build_oi_heatmap(_oi_rows(with_previous=True), spot_price=100.0)
    assert result["totals"]["change_basis"] == "previous_open_interest_supplied"
    assert result["totals"]["put_call_ratio_change"] == pytest.approx((1300 / 1600) - (1350 / 1600), abs=1e-4)

    rows = {row["strike"]: row for row in result["rows"]}
    assert rows[90.0]["call_oi_change"] == 50.0
    assert rows[90.0]["put_oi_change"] == -100.0
    assert rows[100.0]["put_oi_change"] == 0.0
    assert rows[110.0]["call_oi_change"] == -100.0

    buildups = {(item["strike"], item["option_type"]): item["change"] for item in result["landmarks"]["top_buildups"]}
    assert buildups[(90.0, "call")] == 50.0
    assert buildups[(110.0, "put")] == 50.0
    assert all(change > 0 for change in buildups.values())

    unwinds = {(item["strike"], item["option_type"]): item["change"] for item in result["landmarks"]["top_unwinds"]}
    assert unwinds[(90.0, "put")] == -100.0
    assert unwinds[(110.0, "call")] == -100.0
    assert all(change < 0 for change in unwinds.values())


@pytest.mark.parametrize(
    ("spot", "expected"),
    [(100.0, "high"), (102.0, "moderate"), (107.0, "low")],
)
def test_oi_heatmap_pin_risk_level_scales_with_distance(spot, expected):
    result = build_oi_heatmap(_oi_rows(), spot_price=spot)
    assert result["pin_risk"]["level"] == expected
    assert result["pin_risk"]["distance_pct"] >= 0
    if spot != 100.0:
        assert result["pin_risk"]["distance_pct"] > 0


def test_oi_heatmap_multi_expiry_matrix():
    rows = [dict(row, expiry="2026-10-29") for row in _oi_rows()]
    rows.append({"strike": 100, "option_type": "call", "open_interest": 200, "expiry": "2026-11-26"})
    result = build_oi_heatmap(rows, spot_price=100.0)
    assert result["expiries"] == ["2026-10-29", "2026-11-26"]
    assert result["matrix"] is not None
    assert len(result["matrix"]) == 3
    at_the_money = next(row for row in result["matrix"] if row["strike"] == 100.0)
    assert at_the_money["2026-10-29"]["call"]["oi"] == 600.0
    assert at_the_money["2026-11-26"]["call"]["oi"] == 200.0
    # Aggregated view merges both expiries into the strike row.
    merged = next(row for row in result["rows"] if row["strike"] == 100.0)
    assert merged["call_oi"] == 800.0


def test_oi_heatmap_rejects_empty_and_unusable_rows():
    with pytest.raises(ValueError, match="at least one"):
        build_oi_heatmap([])
    with pytest.raises(ValueError, match="No usable rows"):
        build_oi_heatmap(
            ["junk", {"strike": "abc", "option_type": "call", "open_interest": 1}]
        )


def test_oi_heatmap_endpoint(client):
    response = client.post(
        "/api/v1/derivatives/oi-heatmap",
        json={"rows": _oi_rows(), "spot_price": 100.0, "expiry": "2026-10-29"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows_count"] == 6
    assert body["landmarks"]["call_wall"] == 110.0
    assert body["pin_risk"]["level"] == "high"
    assert body["is_forecast"] is False


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"strike": -1, "option_type": "call", "open_interest": 10}],
        [{"option_type": "call", "open_interest": 10}],
        [{"strike": 100, "option_type": "call", "open_interest": -5}],
    ],
)
def test_oi_heatmap_endpoint_rejects_bad_rows(client, rows):
    response = client.post("/api/v1/derivatives/oi-heatmap", json={"rows": rows})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# SPAN-style margin (module)
# ---------------------------------------------------------------------------


def _leg(kind: str, side: str, strike: float, premium: float) -> dict:
    return {"type": kind, "side": side, "strike": strike, "premium": premium, "quantity": 1, "lot_size": 1}


def test_span_margin_long_only_is_roughly_the_premium():
    premium = black_scholes(
        spot=100, strike=100, days_to_expiry=30, volatility=0.2,
        risk_free_rate=0.065, option_type="call",
    )["theoretical_price"]
    result = estimate_span_margin(
        [_leg("call", "buy", 100.0, premium)], spot=100.0, volatility=0.2, days_to_expiry=30,
    )
    assert result["basis"] == "span_style_estimate"
    assert result["is_official_requirement"] is False
    assert result["total_margin"] == pytest.approx(premium, rel=0.02)
    assert result["short_premium_floor"] == 0.0
    assert result["per_leg"][0]["standalone_margin"] == pytest.approx(premium, abs=0.01)
    assert result["per_leg"][0]["derivation"] == "capital_at_risk"
    assert result["strategy_offset"] >= 0
    assert result["scenarios_evaluated"] == 36
    assert any("not the exchange's official SPAN" in item for item in result["disclosures"])
    assert any("paper positions only" in item for item in result["disclosures"])


def test_span_margin_iron_condor_capped_at_wing_width():
    built = build_template("iron_condor", spot=100.0, volatility=0.2, days_to_expiry=30)
    result = estimate_span_margin(
        built["legs"], spot=100.0, volatility=0.2, days_to_expiry=30,
    )
    width = 5.0  # wing width in index points per contract
    contracts = built["legs"][0]["contracts"]
    assert result["total_margin"] <= width * contracts + 1e-6
    assert result["total_margin"] >= result["short_premium_floor"] > 0
    assert result["sum_standalone"] >= result["total_margin"]
    assert result["strategy_offset"] == round(result["sum_standalone"] - result["total_margin"], 2)
    assert result["strategy_offset"] > 0
    assert result["worst_scenario"]["loss"] == pytest.approx(result["total_margin"], abs=0.01) \
        or result["total_margin"] == result["short_premium_floor"]
    assert result["base_portfolio_value"] == pytest.approx(0.0, abs=0.01)


def test_span_margin_short_premium_floor_binds_for_deep_otm_short():
    result = estimate_span_margin(
        [_leg("put", "sell", 60.0, 0.02)], spot=100.0, volatility=0.2, days_to_expiry=1,
    )
    assert result["short_premium_floor"] == 0.02
    assert result["total_margin"] == 0.02
    assert result["worst_scenario"] is None  # no scanned scenario loses money
    assert result["per_leg"][0]["derivation"] == "max(short_premium, single_leg_scan)"


def test_span_margin_naked_short_risk_exceeds_premium():
    result = estimate_span_margin(
        [_leg("call", "sell", 100.0, 2.36)], spot=100.0, volatility=0.2, days_to_expiry=30,
    )
    assert result["total_margin"] > 2.36
    assert result["short_premium_floor"] == 2.36
    assert result["worst_scenario"] is not None
    assert result["worst_scenario"]["underlying_move_pct"] > 0
    assert result["per_leg"][0]["derivation"] == "max(short_premium, single_leg_scan)"


def test_span_margin_validation_errors():
    with pytest.raises(ValueError, match="at least one"):
        estimate_span_margin([], spot=100.0, volatility=0.2, days_to_expiry=30)
    with pytest.raises(ValueError, match="Spot"):
        estimate_span_margin([_leg("call", "buy", 100.0, 1.0)], spot=0.0, volatility=0.2, days_to_expiry=30)
    with pytest.raises(ValueError, match="Volatility"):
        estimate_span_margin([_leg("call", "buy", 100.0, 1.0)], spot=100.0, volatility=0.0, days_to_expiry=30)


# ---------------------------------------------------------------------------
# IV rank / IV percentile (module + API)
# ---------------------------------------------------------------------------


def test_record_and_compute_iv_statistics():
    base = datetime.now(timezone.utc) - timedelta(days=5)
    values = [0.18, 0.22, 0.19, 0.25, 0.30, 0.28]
    for offset, value in enumerate(values):
        assert ivs.record_atm_iv(
            "BATCH2TEST", value, spot=100.0, source="unit", at=base + timedelta(hours=offset)
        ) is True

    stats = ivs.iv_statistics("BATCH2TEST", lookback_days=365)
    assert stats["market_iv"]["available"] is True
    assert stats["observations"] == 6
    assert stats["lifetime_observations"] == 6
    assert stats["market_iv"]["current_iv"] == pytest.approx(0.28)
    assert stats["market_iv"]["min_iv"] == pytest.approx(0.18)
    assert stats["market_iv"]["max_iv"] == pytest.approx(0.30)
    # (0.28 - 0.18) / (0.30 - 0.18)
    assert stats["market_iv"]["iv_rank"] == pytest.approx(0.8333, abs=0.001)
    # Four observations strictly below, one tie: (4 + 0.5) / 6
    assert stats["market_iv"]["iv_percentile"] == pytest.approx(0.75, abs=0.001)
    assert stats["market_iv"]["mean_iv"] == pytest.approx(sum(values) / len(values), abs=1e-4)
    assert stats["market_iv"]["sources"] == ["unit"]
    assert len(stats["series"]) == 6
    assert stats["series"][0]["iv"] == pytest.approx(0.18)
    assert stats["is_forecast"] is False and stats["is_recommendation"] is False


def test_iv_statistics_respects_lookback_window():
    base = datetime.now(timezone.utc) - timedelta(days=5)
    for offset in range(3):
        ivs.record_atm_iv("BATCH2WINDOW", 0.2 + 0.01 * offset, at=base + timedelta(hours=offset))

    inside = ivs.iv_statistics("BATCH2WINDOW", lookback_days=365)
    assert inside["market_iv"]["available"] is True

    outside = ivs.iv_statistics("BATCH2WINDOW", lookback_days=1)
    assert outside["market_iv"]["available"] is False
    assert outside["observations"] == 0
    assert outside["lifetime_observations"] == 3
    assert outside["market_iv"]["reason"] == "no_recorded_iv_observations"


def test_record_atm_iv_dedupe_window_and_validation():
    at = datetime.now(timezone.utc) - timedelta(days=1)
    assert ivs.record_atm_iv("BATCH2DEDUPE", 0.2, at=at) is True
    # Same underlying inside the 30-minute window is dropped.
    assert ivs.record_atm_iv("BATCH2DEDUPE", 0.21, at=at + timedelta(minutes=10)) is False
    # Beyond the window it records again.
    assert ivs.record_atm_iv("BATCH2DEDUPE", 0.21, at=at + timedelta(minutes=31)) is True

    assert ivs.record_atm_iv("BATCH2INVALID", 99.0) is False
    assert ivs.record_atm_iv("BATCH2INVALID", 0.0) is False
    assert ivs.record_atm_iv("BATCH2INVALID", float("nan")) is False
    assert ivs.record_atm_iv("", 0.2) is False


def test_record_atm_iv_never_raises_on_storage_failure(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ivs, "get_connection", boom)
    assert ivs.record_atm_iv("BATCH2FAIL", 0.2) is False


def test_iv_statistics_without_history_is_labelled_unavailable():
    stats = ivs.iv_statistics("BATCH2NEVER", lookback_days=365)
    assert stats["market_iv"]["available"] is False
    assert stats["market_iv"]["reason"] == "no_recorded_iv_observations"
    assert stats["market_iv"]["hint"]
    assert stats["realized_vol_fallback"]["available"] is False
    assert stats["realized_vol_fallback"]["reason"] == "insufficient_price_history"


def test_realized_vol_fallback_is_never_labelled_implied():
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 250)))
    frame = pd.DataFrame({"close": closes})

    stats = ivs.iv_statistics("BATCH2RV", lookback_days=120, price_history=frame)
    fallback = stats["realized_vol_fallback"]
    assert fallback["basis"] == "realized_volatility_proxy"
    assert fallback["is_implied"] is False
    assert fallback["current_realized_vol"] > 0
    assert 0.0 <= fallback["realized_vol_percentile"] <= 1.0
    assert fallback["observations"] >= 10
    assert "not implied volatility" in fallback["label"]
    # Market IV block stays separately unavailable: bases are never merged.
    assert stats["market_iv"]["available"] is False

    short = ivs.iv_statistics("BATCH2RV", lookback_days=120, price_history=pd.DataFrame({"close": [1.0, 2.0, 3.0]}))
    assert short["realized_vol_fallback"]["available"] is False
    assert short["realized_vol_fallback"]["reason"] == "insufficient_price_history"


def test_iv_stats_endpoint_reports_recorded_history(client):
    base = datetime.now(timezone.utc) - timedelta(days=2)
    for offset, value in enumerate([0.20, 0.24, 0.21]):
        ivs.record_atm_iv("BATCH2API", value, at=base + timedelta(hours=offset))

    response = client.get("/api/v1/derivatives/iv-stats/BATCH2API?include_rv_fallback=false")
    assert response.status_code == 200
    body = response.json()
    assert body["underlying"] == "BATCH2API"
    assert body["market_iv"]["available"] is True
    assert body["market_iv"]["iv_rank"] == pytest.approx(0.25, abs=1e-4)
    assert body["market_iv"]["iv_percentile"] == pytest.approx(0.5, abs=1e-4)
    assert body["is_forecast"] is False


def test_iv_stats_endpoint_without_history(client):
    response = client.get("/api/v1/derivatives/iv-stats/BATCH2EMPTY?include_rv_fallback=false")
    assert response.status_code == 200
    body = response.json()
    assert body["market_iv"]["available"] is False
    assert body["market_iv"]["reason"] == "no_recorded_iv_observations"
