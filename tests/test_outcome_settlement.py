"""Immutable automatic forecast outcome ledger and settlement wiring."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
import api.routers.admin as admin_router
import api.routers.forecasting as forecasting_router
import database
from api import scheduler as scheduler_module
from services import admin_registry
from services.outcome_settlement import settle_due_forecasts


PASSWORD = "StrongPass9!x"


def _user(email: str = "ledger@example.com") -> int:
    conn = database.get_connection()
    cursor = conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)", ("Ledger User", email, "hash"))
    user_id = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def _forecast(target: str, *, provider: str = "upstox") -> dict:
    return {
        "forecast": {"low": 100.0, "median": 105.0, "high": 110.0, "confidence_level": 0.8},
        "training": {"training_window": "1y", "timeframe": "1D", "rows": 300},
        "generated_at": "2026-01-01T10:00:00+00:00",
        "data_timestamp": "2026-01-01T00:00:00+00:00",
        "feature_timestamp": "2026-01-01T00:00:00+00:00",
        "target_timestamp": target,
        "forecast_status": "available",
        "evidence": {"grade": "A", "summary": "sufficient"},
        "context": {"provider": provider, "as_of": "2026-01-01T00:00:00+00:00", "is_stale": False},
    }


class HistoryManager:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.history_calls = 0

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        del symbol, timeframe, window
        self.history_calls += 1
        return self.frame.copy()

    def get_quote(self, symbol: str, timeframe: str = "quote"):
        del symbol, timeframe
        raise RuntimeError("quote should not be needed")


def _history(*, stale: bool = False, provider: str = "upstox") -> pd.DataFrame:
    frame = pd.DataFrame(
        {"Open": [106.0], "High": [109.0], "Low": [104.0], "Close": [107.0], "Volume": [1000]},
        index=pd.to_datetime(["2026-01-02T00:00:00+00:00"]),
    )
    frame.attrs.update({
        "provider": provider,
        "source": provider,
        "is_stale": stale,
        "context": {"provider": provider, "is_stale": stale, "provider_mode": "LIVE_ONLY"},
    })
    return frame


def test_forecast_and_final_outcome_are_immutable_and_idempotent(temp_db):
    prediction_id = database.save_range_forecast(_user(), "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    conn = database.get_connection()
    before = conn.execute(
        "SELECT symbol,forecast_low,forecast_high,origin_timestamp,target_timestamp,snapshot_hash FROM prediction_history WHERE id=?",
        (prediction_id,),
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="forecast snapshot is immutable"):
        conn.execute("UPDATE prediction_history SET forecast_low=1 WHERE id=?", (prediction_id,))
    conn.close()

    assert database.settle_range_forecast_automatically(
        prediction_id,
        107.0,
        provider="upstox",
        data_timestamp="2026-01-02T00:00:00+00:00",
        evidence={"method": "history"},
    ) is True
    assert database.settle_range_forecast_automatically(
        prediction_id,
        999.0,
        provider="upstox",
        data_timestamp="2026-01-02T00:00:00+00:00",
    ) is False

    conn = database.get_connection()
    after = conn.execute(
        "SELECT symbol,forecast_low,forecast_high,origin_timestamp,target_timestamp,snapshot_hash,actual_price,outcome_status,result_hash FROM prediction_history WHERE id=?",
        (prediction_id,),
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="forecast outcome is immutable"):
        conn.execute("UPDATE prediction_history SET actual_price=999 WHERE id=?", (prediction_id,))
    conn.close()
    assert after[:6] == before
    assert after[6] == 107.0
    assert after[7] == "settled"
    assert len(after[8]) == 64


def test_settlement_processes_due_but_not_future_forecasts(temp_db):
    user_id = _user()
    due = database.save_range_forecast(user_id, "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    future = database.save_range_forecast(user_id, "TCS", _forecast("2026-01-04T00:00:00+00:00"))
    result = settle_due_forecasts(
        now=datetime(2026, 1, 3, tzinfo=timezone.utc),
        manager=HistoryManager(_history()),
    )
    assert result["due"] == 1
    assert result["not_due"] == 1
    assert result["settled"] == 1
    conn = database.get_connection()
    rows = dict(conn.execute("SELECT id,outcome_status FROM prediction_history WHERE id IN (?,?)", (due, future)).fetchall())
    conn.close()
    assert rows[due] == "settled"
    assert rows[future] == "pending"


@pytest.mark.parametrize(
    ("frame", "reason"),
    [
        (_history(stale=True), "stale_market_data"),
        (_history(provider="demo_india"), "demo_market_data"),
        (_history().iloc[0:0], "target_data_absent"),
    ],
)
def test_untrusted_or_absent_target_is_unverifiable_and_excluded(temp_db, frame, reason):
    user_id = _user()
    prediction_id = database.save_range_forecast(user_id, "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    result = settle_due_forecasts(
        now=datetime(2026, 1, 3, tzinfo=timezone.utc),
        manager=HistoryManager(frame),
    )
    assert result["unverifiable"] == 1
    details = database.get_prediction_details(user_id, limit=1)[0]
    assert details["outcome_status"] == "unverifiable"
    assert details["outcome_evidence"]["reason"] == reason
    assert database.get_settled_range_forecasts(user_id) == []


def test_temporarily_absent_target_remains_pending_during_grace_period(temp_db):
    user_id = _user()
    prediction_id = database.save_range_forecast(user_id, "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    result = settle_due_forecasts(
        now=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
        manager=HistoryManager(_history().iloc[0:0]),
    )
    assert result["deferred"] == 1
    details = database.get_prediction_details(user_id, limit=1)[0]
    assert details["id"] == prediction_id
    assert details["outcome_status"] == "pending"


def test_official_calibration_excludes_manual_and_reports_counts(temp_db):
    user_id = _user()
    official = database.save_range_forecast(user_id, "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    manual = database.save_range_forecast(user_id, "TCS", _forecast("2026-01-02T00:00:00+00:00"))
    database.settle_range_forecast_automatically(
        official, 107.0, provider="upstox", data_timestamp="2026-01-02T00:00:00+00:00"
    )
    database.settle_range_forecast(manual, 107.0)
    rows = database.get_settled_range_forecasts(user_id)
    counts = database.get_forecast_outcome_counts(user_id)
    assert [row["id"] for row in rows] == [official]
    assert counts["coverage_numerator"] == 1
    assert counts["calibration_denominator"] == 1
    assert counts["excluded_non_official"] == 1
    calibration = forecasting_router.prediction_calibration_endpoint(limit=500, user={"id": user_id})
    assert calibration["overall"]["coverage_numerator"] == 1
    assert calibration["overall"]["calibration_denominator"] == 1
    assert calibration["track_record"] == counts


def test_manual_settlement_route_is_authorized_but_never_mutates(temp_db):
    client = TestClient(api_main.app)
    client.headers.update({"Origin": "http://localhost:3000"})
    assert client.post("/api/v1/predictions/1/settle", json={"actual_price": 999}).status_code == 401
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ledger User", "email": "owner@example.com", "password": PASSWORD, "date_of_birth": "1985-06-15"},
    )
    user_id = response.json()["user"]["id"]
    prediction_id = database.save_range_forecast(user_id, "RELIANCE", _forecast("2026-01-02T00:00:00+00:00"))
    rejected = client.post(f"/api/v1/predictions/{prediction_id}/settle", json={"actual_price": 999})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "automatic_settlement_only"
    conn = database.get_connection()
    row = conn.execute("SELECT actual_price,outcome_status FROM prediction_history WHERE id=?", (prediction_id,)).fetchone()
    conn.close()
    assert row == (None, "pending")


def test_admin_can_trigger_settlement_but_normal_user_cannot(temp_db, monkeypatch):
    admin_email = "ledger-admin@example.com"
    monkeypatch.setenv(admin_registry.ADMIN_EMAILS_VAR, admin_email)
    monkeypatch.setattr(admin_router, "settle_due_forecasts", lambda: {"settled": 2})
    client = TestClient(api_main.app)
    client.headers.update({"Origin": "http://localhost:3000"})
    client.post("/api/v1/auth/register", json={"name": "User", "email": "normal@example.com", "password": PASSWORD, "date_of_birth": "1985-06-15"})
    assert client.post("/api/v1/admin/maintenance/settle_forecast_outcomes").status_code == 403
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/register", json={"name": "Admin", "email": admin_email, "password": PASSWORD, "date_of_birth": "1985-06-15"})
    admin_registry.bootstrap_admins()
    client.post("/api/v1/auth/login", json={"email": admin_email, "password": PASSWORD})
    response = client.post("/api/v1/admin/maintenance/settle_forecast_outcomes")
    assert response.status_code == 200
    assert response.json()["result"]["settled"] == 2


def test_scheduler_registers_automatic_settlement_job(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_SETTLEMENT_INTERVAL_MINUTES", "7")
    scheduler = scheduler_module.create_scheduler()
    job = scheduler.get_job("stockpilot-forecast-outcome-settlement")
    assert job is not None
    assert job.func is scheduler_module.settle_due_forecast_outcomes
    assert str(job.trigger) == "interval[0:07:00]"


def test_blocked_forecast_is_excluded_from_automatic_settlement(temp_db):
    user_id = _user()
    forecast = _forecast("2026-01-02T00:00:00+00:00")
    forecast["forecast_status"] = "drift_blocked"
    prediction_id = database.save_range_forecast(user_id, "RELIANCE", forecast)
    assert database.get_pending_range_forecasts() == []
    details = database.get_prediction_details(user_id, limit=1)[0]
    assert details["id"] == prediction_id
    assert details["outcome_status"] == "excluded"
