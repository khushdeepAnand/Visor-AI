from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

from api import main as api_main
from services import auth_api
from services.market_data.base import ProviderUnavailableError, assess_corporate_actions
from services.market_data.manager import ProviderManager
from services.news import get_news


ORIGIN = "http://localhost:3000"


def test_security_headers_cover_api_responses(monkeypatch):
    client = TestClient(api_main.app)
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

    monkeypatch.setenv("STOCKPILOT_ENV", "production")
    secure = TestClient(api_main.app, base_url="https://testserver").get("/")
    assert secure.headers["strict-transport-security"].startswith("max-age=")


def test_logout_revokes_the_server_side_session(temp_db):
    client = TestClient(api_main.app)
    registered = client.post(
        "/api/v1/auth/register",
        json={"name": "Session User", "email": "session@example.com", "password": "StrongPass9", "date_of_birth": "1985-06-15"},
    )
    assert registered.status_code == 200
    assert len(client.get("/api/v1/auth/sessions").json()["items"]) == 1
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_all_invalidates_other_bearer_tokens(temp_db):
    client = TestClient(api_main.app)
    user = client.post(
        "/api/v1/auth/register",
        json={"name": "All Sessions", "email": "all-sessions@example.com", "password": "StrongPass9", "date_of_birth": "1985-06-15"},
    ).json()["user"]
    second_token = auth_api.create_access_token(user)
    assert client.post("/api/v1/auth/logout-all", headers={"Origin": ORIGIN}).status_code == 200
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {second_token}"})
    assert response.status_code == 401


def test_provider_health_and_failure_demotion_are_public_and_secret_free():
    manager = ProviderManager()
    first = manager.order[0]
    manager._failure_streaks[first] = 3
    assert manager.effective_order()[-1] == first
    response = TestClient(api_main.app).get("/api/v1/market/providers/health")
    assert response.status_code == 200
    assert "effective_order" in response.json()
    assert "access_token" not in response.text.lower()


def test_provider_health_reports_runtime_fallback_and_failure_metrics():
    manager = ProviderManager()
    first, second = manager.order[:2]
    manager._record_failure(first, ProviderUnavailableError("unavailable"), latency_ms=22.5)
    manager._record_success(
        second,
        latency_ms=10.25,
        fallback_used=True,
        data_as_of="2026-09-25T09:15:00+05:30",
    )

    metrics = manager.health()["metrics"]
    assert metrics["requests"] == 2
    assert metrics["failures"] == 1
    assert metrics["fallback_rate"] == 1.0
    assert metrics["providers"][first]["last_latency_ms"] == 22.5
    assert metrics["providers"][second]["data_age_seconds"] is not None


def test_ratio_like_discontinuity_is_flagged_for_review():
    frame = pd.DataFrame(
        {"Close": [100.0, 102.0, 51.0, 52.0]},
        index=pd.date_range("2026-01-01", periods=4, freq="D"),
    )
    result = assess_corporate_actions(frame)
    assert result["corporate_action_status"] == "suspected"
    assert result["corporate_action_candidates"][0]["nearest_action_ratio"] == 0.5


def test_news_is_explicitly_unavailable_without_an_approved_source(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_NEWS_RSS_URL", raising=False)
    result = get_news("RELIANCE")
    assert result["status"] == "unavailable"
    assert result["reason"] == "news_source_not_configured"
    assert result["items"] == []
