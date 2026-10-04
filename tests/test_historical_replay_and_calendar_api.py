from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from api import main as api_main
from middleware.observability import RATE_LIMITER
from services import admin_registry


def _client() -> TestClient:
    RATE_LIMITER.clear()
    return TestClient(api_main.app)


def test_historical_challenges_end_to_end(temp_db):
    client = _client()
    register = client.post(
        "/api/v1/auth/register",
        json={"name": "Replay Tester", "email": "replay-e2e@example.com", "password": "Correct-Horse-9-Battery", "date_of_birth": "1985-06-15"},
    )
    assert register.status_code == 200

    listing = client.get("/api/v1/paper/challenges/historical")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) >= 3
    assert "outcome_close" not in items[0]  # never leaked before reveal

    key = items[0]["key"]
    choice = items[0]["choices"][0]
    origin = {"Origin": "http://localhost:3000"}
    reveal = client.post(f"/api/v1/paper/challenges/historical/{key}/reveal", json={"choice": choice}, headers=origin)
    assert reveal.status_code == 200
    body = reveal.json()
    assert body["key"] == key
    assert body["choice"]["choice"] == choice
    assert "outcome_close" in body

    mine = client.get("/api/v1/paper/challenges/historical/mine")
    assert mine.status_code == 200
    assert any(entry["key"] == key for entry in mine.json()["items"])

    bad_choice = client.post(f"/api/v1/paper/challenges/historical/{key}/reveal", json={"choice": "NOT_A_CHOICE"}, headers=origin)
    assert bad_choice.status_code == 422

    bad_key = client.post("/api/v1/paper/challenges/historical/NOT_A_KEY/reveal", json={"choice": "HOLD"}, headers=origin)
    assert bad_key.status_code == 404


def test_historical_challenges_reveal_requires_auth(temp_db):
    client = _client()
    response = client.post(
        "/api/v1/paper/challenges/historical/NIFTY_COVID_CRASH_2020/reveal",
        json={"choice": "HOLD"},
    )
    assert response.status_code == 401


def test_calendar_refresh_endpoint_requires_auth(temp_db):
    client = _client()
    response = client.post("/api/v1/market/calendar/refresh/2027", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 401


def test_calendar_refresh_endpoint_reports_configured_source_failure(temp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_NSE_CALENDAR_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.delenv("STOCKPILOT_NSE_CALENDAR_URL", raising=False)
    monkeypatch.setenv(admin_registry.ADMIN_EMAILS_VAR, "calendar-admin@example.com")
    client = _client()
    register = client.post(
        "/api/v1/auth/register",
        json={"name": "Calendar Admin", "email": "calendar-admin@example.com", "password": "Correct-Horse-9-Battery", "date_of_birth": "1985-06-15"},
    )
    assert register.status_code == 200
    admin_registry.bootstrap_admins()

    # No STOCKPILOT_NSE_CALENDAR_URL configured -> the endpoint must fail
    # loudly (503) rather than silently pretending it refreshed something.
    response = client.post(
        "/api/v1/market/calendar/refresh/2027",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 503
