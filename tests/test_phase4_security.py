from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

import authentication
import database
from api import main as api_main
from api.routers import system as system_router
from middleware.observability import RATE_LIMITER
from middleware.security import parse_cors_origins
from services import auth_api
from services.market_data.upstox_auth import (
    BrokerOrderBlockedError,
    assert_upstox_request_allowed,
)
from services.paper_trading_v6 import place_order


ALLOWED_ORIGIN = "http://localhost:3000"


def test_jwt_secret_is_required_outside_explicit_local_development(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_JWT_SECRET", raising=False)
    monkeypatch.setenv("STOCKPILOT_PROVIDER_MODE", "LIVE_ONLY")
    for environment in ("", "release", "production"):
        monkeypatch.setenv("STOCKPILOT_ENV", environment)
        with pytest.raises(RuntimeError, match="32 bytes"):
            auth_api.validate_jwt_configuration()

    monkeypatch.setenv("STOCKPILOT_ENV", "development")
    assert auth_api._secret() == auth_api.LOCAL_DEVELOPMENT_KEY
    monkeypatch.setenv("STOCKPILOT_PROVIDER_MODE", "OFFLINE_DEMO")
    with pytest.raises(RuntimeError, match="32 bytes"):
        auth_api.validate_jwt_configuration()

    monkeypatch.setenv("STOCKPILOT_ENV", "production")
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "x" * 31)
    with pytest.raises(RuntimeError, match="32 bytes"):
        auth_api.validate_jwt_configuration()
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "phase4-test-material-0123456789abcdef")
    auth_api.validate_jwt_configuration()


def test_cors_rejects_wildcards_and_non_origins():
    with pytest.raises(ValueError):
        parse_cors_origins("*")
    with pytest.raises(ValueError):
        parse_cors_origins("https://example.com/app")
    assert parse_cors_origins("HTTPS://Example.COM:443,http://localhost:3000") == [
        "https://example.com",
        ALLOWED_ORIGIN,
    ]


def test_cookie_csrf_allows_configured_origin_and_rejects_missing_or_foreign(temp_db):
    RATE_LIMITER.clear()
    client = TestClient(api_main.app)
    registered = client.post(
        "/api/v1/auth/register",
        json={"name": "CSRF User", "email": "csrf@example.com", "password": "StrongPass9", "date_of_birth": "1985-06-15"},
    )
    assert registered.status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"Origin": ALLOWED_ORIGIN}).status_code == 200


def test_bearer_authenticated_write_does_not_require_origin(temp_db):
    RATE_LIMITER.clear()
    client = TestClient(api_main.app)
    registered = client.post(
        "/api/v1/auth/register",
        json={"name": "Bearer User", "email": "bearer@example.com", "password": "StrongPass9", "date_of_birth": "1985-06-15"},
    )
    token = auth_api.create_access_token(registered.json()["user"])
    scenarios = client.get("/api/v1/paper/challenges/historical").json()["items"]
    response = client.post(
        f"/api/v1/paper/challenges/historical/{scenarios[0]['key']}/reveal",
        json={"choice": scenarios[0]["choices"][0]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_rate_limits_are_endpoint_scoped_and_ignore_untrusted_forwarding(monkeypatch):
    RATE_LIMITER.clear()
    monkeypatch.setenv("STOCKPILOT_LOGIN_RATE_LIMIT", "1")
    monkeypatch.setenv("STOCKPILOT_REGISTER_RATE_LIMIT", "1")
    monkeypatch.setenv("STOCKPILOT_FORECAST_RATE_LIMIT", "1")
    monkeypatch.setenv("STOCKPILOT_TRUST_PROXY", "false")
    client = TestClient(api_main.app)
    first = client.post("/api/v1/auth/login", json={}, headers={"X-Forwarded-For": "198.51.100.1"})
    blocked = client.post("/api/v1/auth/login", json={}, headers={"X-Forwarded-For": "198.51.100.2"})
    separate = client.post("/api/v1/auth/register", json={})
    forecast_first = client.get("/api/v1/predict/AAPL")
    forecast_blocked = client.get("/api/v1/predict/AAPL")
    assert first.status_code == 422
    assert blocked.status_code == 429
    assert separate.status_code == 422
    assert forecast_first.status_code == 422
    assert forecast_blocked.status_code == 429


def test_provider_error_text_is_redacted(monkeypatch):
    marker = "C:/private/provider.py token=secret Traceback"
    monkeypatch.setattr(api_main.MANAGER, "get_quote", lambda symbol: (_ for _ in ()).throw(RuntimeError(marker)))
    response = TestClient(api_main.app).get("/api/v1/market/quote/RELIANCE")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "market_data_unavailable"
    assert detail["retryable"] is True
    assert detail["support_id"]
    assert marker not in response.text


def test_health_system_and_metrics_redact_internal_details(temp_db, monkeypatch):
    marker = "C:/private/users.sqlite provider said token=secret Traceback ENV=production"
    database_health = lambda: {"status": "Operational", "users": 42, "database": marker, "error": marker}
    monkeypatch.setattr(
        api_main,
        "database_health_check",
        database_health,
    )
    monkeypatch.setattr(system_router, "database_health_check", database_health)
    monkeypatch.setattr(
        system_router,
        "market_status",
        lambda: {
            "exchange": "NSE",
            "timezone": "Asia/Kolkata",
            "regular_session": "09:15-15:30 IST",
            "is_open": False,
            "reason": "Closed",
            "timestamp": "2026-01-01T00:00:00+05:30",
            "calendar_source": marker,
        },
    )
    client = TestClient(api_main.app)
    responses = [
        client.get("/api/v1/health"),
        client.get("/api/v1/system"),
        client.get("/api/v1/metrics"),
    ]
    assert all(response.status_code == 200 for response in responses)
    assert all(marker not in response.text for response in responses)
    assert "users" not in responses[0].text
    assert responses[0].json()["market_data"]["provider_mode"] in {"LIVE_ONLY", "OFFLINE_DEMO", "FALLBACK_ALLOWED"}
    assert "requests_by_path" not in responses[2].json()


def test_first_run_initializes_schema_only_in_temporary_database(isolated_database, monkeypatch):
    assert not isolated_database.exists()
    monkeypatch.setenv("STOCKPILOT_ENV", "test")
    monkeypatch.setenv("STOCKPILOT_PROVIDER_MODE", "LIVE_ONLY")
    with TestClient(api_main.app) as client:
        assert client.get("/api/v1/ready").status_code == 200
    assert isolated_database.exists()
    with sqlite3.connect(isolated_database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "auth_login_attempts", "paper_orders", "prediction_history"}.issubset(tables)


@pytest.mark.parametrize(
    "method,url",
    [
        ("POST", "https://api.upstox.com/v2/order/place"),
        ("POST", "https://api.upstox.com/v3/order/place"),
        ("PUT", "https://api.upstox.com/v2/order/modify"),
        ("DELETE", "https://api.upstox.com/v2/order/cancel"),
        ("POST", "https://api.upstox.com/v3/order/gtt/place"),
        ("PUT", "https://api.upstox.com/v3/order/gtt/modify"),
        ("DELETE", "https://api.upstox.com/v3/order/gtt/cancel"),
        ("POST", "https://api.upstox.com/v2/order/multi/place"),
        ("POST", "https://api-hft.upstox.com/v2/order/place"),
        ("DELETE", "https://api-v2.upstox.com/v2/orders/cancel"),
    ],
)
def test_all_upstox_live_order_routes_are_denied(method, url):
    with pytest.raises(BrokerOrderBlockedError):
        assert_upstox_request_allowed(method, url)


def test_upstox_market_data_gets_remain_allowed():
    assert_upstox_request_allowed("GET", "https://api.upstox.com/v2/market-quote/quotes")
    assert_upstox_request_allowed("GET", "https://api.upstox.com/v3/historical-candle/key/days/1/2026-01-02/2026-01-01")


def test_paper_order_cannot_invoke_live_broker_http(temp_db, monkeypatch):
    connection = database.get_connection()
    user_id = connection.execute(
        "INSERT INTO users(name,email,password) VALUES(?,?,?)",
        ("Paper Safety", "paper-safety@example.com", "hash"),
    ).lastrowid
    connection.commit()
    connection.close()
    calls = []

    def reject_http(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Paper trading attempted broker HTTP")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_http)
    result = place_order(
        user_id=user_id,
        symbol="RELIANCE",
        side="BUY",
        quantity=1,
        market_quote={"symbol": "RELIANCE", "price": 100.0, "previous_close": 100.0},
    )
    assert result["simulation_notice"].startswith("Paper trade only")
    assert calls == []
    paper_sources = [
        Path(api_main.__file__).parents[1] / "services" / "paper_trading.py",
        Path(api_main.__file__).parents[1] / "services" / "paper_trading_v6.py",
    ]
    assert all("api.upstox.com" not in path.read_text(encoding="utf-8").lower() for path in paper_sources)


def test_https_session_cookie_security_attributes(temp_db):
    RATE_LIMITER.clear()
    client = TestClient(api_main.app, base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Cookie User", "email": "cookie@example.com", "password": "StrongPass9", "date_of_birth": "1985-06-15"},
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


def test_unknown_login_uses_one_stable_dummy_hash(temp_db, monkeypatch):
    checked = []

    def capture(password, password_hash):
        checked.append(password_hash)
        return False

    monkeypatch.setattr(authentication, "verify_password", capture)
    success, message = authentication.login_user("unknown@example.com", "StrongPass9")
    assert success is False
    assert message == "Invalid email or password."
    assert checked == [authentication.DUMMY_PASSWORD_HASH]
