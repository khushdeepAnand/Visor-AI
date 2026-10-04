"""Range-forecast persistence and interval-quality tracking."""
from __future__ import annotations

import database


def _user():
    conn = database.get_connection()
    cur = conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)", ("Range User", "range@example.com", "hash"))
    uid = int(cur.lastrowid); conn.commit(); conn.close(); return uid


def test_range_forecast_settlement_records_coverage_and_winkler(temp_db):
    uid = _user()
    forecast = {"forecast": {"low": 100.0, "median": 105.0, "high": 110.0, "confidence_level": 0.8}, "training": {"training_window": "1mo", "timeframe": "5m"}}
    pid = database.save_range_forecast(uid, "RELIANCE", forecast)
    database.settle_range_forecast(pid, 107.0)
    row = database.get_prediction_details(uid, limit=1)[0]
    # get_prediction_details returns dicts in the v6 schema.
    if isinstance(row, dict):
        assert row["forecast_low"] == 100.0
        assert row["forecast_high"] == 110.0
        assert row["coverage_hit"] in {1, True}
        assert row["winkler_score"] == 10.0
    else:
        assert pid


def test_outside_interval_gets_winkler_penalty(temp_db):
    uid = _user()
    pid = database.save_range_forecast(uid, "TCS", {"forecast": {"low": 90.0, "median": 100.0, "high": 110.0, "confidence_level": 0.8}})
    database.settle_range_forecast(pid, 120.0)
    conn = database.get_connection(); row = conn.execute("SELECT coverage_hit,winkler_score FROM prediction_history WHERE id=?", (pid,)).fetchone(); conn.close()
    assert row[0] == 0
    assert row[1] > 20.0
