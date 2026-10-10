import database
import pytest
from services.scorecard import build_public_scorecard


def test_public_scorecard_works_on_actual_empty_schema(temp_db):
    card = build_public_scorecard()
    assert card["overall"]["total_forecasts"] == 0
    assert card["overall"]["coverage"] is None
    assert card["next_day"]["evidence_tier"] == "insufficient"
    assert card["next_day"]["coverage"] is None


def test_scorecard_uses_real_settlements_and_does_not_invent_promotion(temp_db):
    conn = database.get_connection()
    uid = conn.execute("INSERT INTO users(name,email,password) VALUES ('Example','score@example.com','hash')").lastrowid
    conn.commit()
    conn.close()
    for index in range(6):
        payload = {
            "forecast": {"low": 90, "median": 100 + index, "high": 120, "confidence_level": 0.9},
            "horizon": {"bars": 1, "sessions": 1, "timeframe": "1D"},
            "training": {"training_window": "1y", "timeframe": "1D"},
            "tier": "T3", "forecast_status": "model_supported",
            "target_timestamp": f"2026-02-{10+index:02d}T00:00:00",
        }
        pid = database.save_range_forecast(uid, "TCS", payload)
        database.settle_range_forecast_automatically(pid, 100 + index, provider="test-upstox", data_timestamp="2026-03-01T09:15:00+05:30", evidence={"source": "test"})
    card = build_public_scorecard(tier_filter="T3")
    assert card["overall"]["total_forecasts"] == 6
    assert card["tiers"]["T3"]["mase"] == 0.0
    assert card["tiers"]["T3"]["target_coverage"] == 0.9
    assert card["promotion_gates"]["T3"]["passed"] is False
    assert card["next_day"]["total_forecasts"] == 6
    assert card["next_day"]["target_coverage"] == .9
    assert card["next_day"]["mase"] == 0


def test_next_day_excludes_intraday_and_longer_horizons(monkeypatch):
    import json
    from services import scorecard
    rows = []
    for timeframe, horizon, hit in (("1D", 1, 1), ("5m", 1, 0), ("1D", 5, 0)):
        rows.append({"symbol": "TCS", "timeframe": timeframe, "horizon_sessions": horizon,
                     "actual_price": 100, "coverage_hit": hit, "winkler_score": 20,
                     "confidence_level": .8, "forecast_low": 90, "forecast_median": 100,
                     "forecast_high": 110, "payload_json": json.dumps({"tier": "T3"})})
    monkeypatch.setattr(scorecard, "get_settled_rows_for_quality", lambda **kwargs: [dict(row) for row in rows])
    monkeypatch.setattr(scorecard, "active_promotion_receipt", lambda: None)
    card = build_public_scorecard()
    assert card["overall"]["total_forecasts"] == 3
    assert card["next_day"]["total_forecasts"] == 1
    assert card["next_day"]["coverage"] == 1
    assert card["next_day"]["mean_pinball_loss"] == pytest.approx(2 / 3, abs=.0001)
