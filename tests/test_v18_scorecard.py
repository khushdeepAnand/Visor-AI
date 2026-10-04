import database
from services.scorecard import build_public_scorecard


def test_public_scorecard_works_on_actual_empty_schema(temp_db):
    card = build_public_scorecard()
    assert card["overall"]["total_forecasts"] == 0
    assert card["overall"]["coverage"] is None


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
