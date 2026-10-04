"""One immutable ledger row per saved horizon, and per-horizon calibration grouping."""
from __future__ import annotations

import database
from forecasting.calibration_monitor import group_by_horizon


def _user() -> int:
    conn = database.get_connection()
    cur = conn.execute(
        "INSERT INTO users(name,email,password) VALUES(?,?,?)",
        ("Ledger User", "ledger@example.com", "hash"),
    )
    uid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return uid


def _payload(horizon: int) -> dict:
    return {
        "forecast": {"low": 100.0 - horizon, "median": 105.0, "high": 110.0 + horizon, "confidence_level": 0.8},
        "horizon": {"bars": horizon, "sessions": horizon, "timeframe": "1D", "label": f"{horizon} sessions ahead"},
        "target_timestamp": f"2026-02-{2 + horizon:02d}T00:00:00",
        "training": {"training_window": "1y", "timeframe": "1D"},
        "forecast_status": "model_supported",
    }


def test_save_range_forecast_records_horizon(temp_db):
    uid = _user()
    for horizon in (1, 3):
        database.save_range_forecast(uid, "RELIANCE", _payload(horizon))
    details = database.get_prediction_details(uid, limit=10)
    assert [int(row["horizon_sessions"]) for row in details] == [3, 1]
    assert [row["horizon"] for row in details] == ["3", "1"]
    pending = database.get_pending_range_forecasts()
    pending_uid = [row for row in pending if row["user_id"] == uid]
    assert {int(row["horizon_sessions"]) for row in pending_uid} == {1, 3}
    assert all(row["horizon"] == str(row["horizon_sessions"]) for row in pending_uid)


def _settle(pid: int, actual: float) -> None:
    database.settle_range_forecast_automatically(
        pid,
        actual,
        provider="test-upstox",
        data_timestamp="2026-02-20T09:15:00+05:30",
        evidence={"source": "test"},
    )


def test_settled_rows_group_by_horizon_with_numerators(temp_db):
    uid = _user()
    p1 = database.save_range_forecast(uid, "RELIANCE", _payload(1))
    p3 = database.save_range_forecast(uid, "RELIANCE", _payload(3))
    _settle(p1, 107.0)  # inside [99, 111]
    _settle(p3, 120.0)  # outside [97, 113]
    rows = database.get_settled_range_forecasts(uid, limit=100)
    assert {int(row["horizon_sessions"]) for row in rows} == {1, 3}
    grouped = group_by_horizon(rows)
    by = {group["horizon"]: group for group in grouped}
    assert by["1"]["settled_forecasts"] == 1
    assert by["1"]["empirical_coverage"] == 1.0
    assert by["3"]["settled_forecasts"] == 1
    assert by["3"]["empirical_coverage"] == 0.0


def test_legacy_rows_without_horizon_count_as_one_session(temp_db):
    uid = _user()
    legacy = {"forecast": {"low": 90.0, "median": 100.0, "high": 110.0, "confidence_level": 0.8}}
    pid = database.save_range_forecast(uid, "TCS", legacy)
    _settle(pid, 105.0)
    rows = database.get_settled_range_forecasts(uid, limit=100)
    assert rows, "automatic settlement should make the row eligible for calibration"
    # Legacy payloads carry no horizon, so the ledger defaults them to 1 session.
    assert int(rows[0]["horizon_sessions"]) == 1
    grouped = group_by_horizon(rows)
    assert grouped[0]["horizon"] == "1"
    assert grouped[0]["settled_forecasts"] == 1