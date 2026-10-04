from __future__ import annotations

import time
from urllib.parse import parse_qs, quote, urlsplit

import pyotp
from fastapi.testclient import TestClient

import database
from api import main as api_main
from api.routers import auth as auth_router


ORIGIN = "http://localhost:3000"
PASSWORD = "StrongPass9!x"


def _register(client: TestClient, email: str = "mfa@example.com") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "MFA User",
            "email": email,
            "password": PASSWORD,
            "date_of_birth": "1985-06-15",
        },
    )
    assert response.status_code == 200, response.text


def _enable(client: TestClient) -> tuple[str, list[str]]:
    setup = client.post("/api/v1/auth/mfa/setup", headers={"Origin": ORIGIN})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enabled = client.post(
        "/api/v1/auth/mfa/enable",
        json={"code": pyotp.TOTP(secret).at(time.time() - 30)},
        headers={"Origin": ORIGIN},
    )
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["recovery_codes"]


def test_totp_secret_is_encrypted_and_recovery_codes_are_hashed(temp_db):
    client = TestClient(api_main.app)
    _register(client)
    secret, recovery_codes = _enable(client)

    connection = database.get_connection()
    encrypted = connection.execute("SELECT encrypted_totp_secret FROM user_mfa").fetchone()[0]
    stored_codes = [row[0] for row in connection.execute("SELECT code_hash FROM mfa_recovery_codes")]
    connection.close()

    assert secret not in encrypted
    assert all(code not in stored_codes for code in recovery_codes)
    assert len(set(stored_codes)) == 10
    me = client.get("/api/v1/auth/me").json()["user"]
    assert me["mfa_enabled"] is True
    assert secret not in str(me)


def test_login_requires_mfa_before_creating_session_and_challenge_is_single_use(temp_db):
    client = TestClient(api_main.app)
    _register(client)
    secret, _ = _enable(client)
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "mfa@example.com", "password": PASSWORD, "next": "/portfolio"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["mfa_required"] is True
    assert isinstance(body.get("anomalies"), list)  # login anomaly detection
    assert "stockpilot_session" not in client.cookies
    assert client.get("/api/v1/auth/me").status_code == 401

    challenge_cookie = client.cookies.get("stockpilot_mfa_challenge")
    verified = client.post("/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200, verified.text
    assert verified.json()["next"] == "/portfolio"
    assert client.get("/api/v1/auth/me").status_code == 200

    replay = TestClient(api_main.app)
    replay.cookies.set("stockpilot_mfa_challenge", challenge_cookie, path="/api/v1/auth/mfa")
    assert replay.post("/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 401


def test_recovery_code_is_consumed_once(temp_db):
    client = TestClient(api_main.app)
    _register(client)
    _, recovery_codes = _enable(client)
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    first_code = recovery_codes[0]
    client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": PASSWORD})
    accepted = client.post("/api/v1/auth/mfa/verify", json={"code": first_code})
    assert accepted.status_code == 200
    assert accepted.json()["method"] == "recovery"
    assert client.get("/api/v1/auth/mfa").json()["recovery_codes_remaining"] == 9

    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    client.post("/api/v1/auth/login", json={"email": "mfa@example.com", "password": PASSWORD})
    reused = client.post("/api/v1/auth/mfa/verify", json={"code": first_code})
    assert reused.status_code == 401


def test_session_list_exposes_coarse_label_without_user_agent_or_ip(temp_db):
    client = TestClient(
        api_main.app,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 unique-marker",
            "X-Forwarded-For": "198.51.100.42",
        },
    )
    _register(client, "device@example.com")
    body = client.get("/api/v1/auth/sessions").json()["items"][0]
    assert body["device_label"] == "Chrome on Windows"
    assert body["current"] is True
    assert "198.51.100.42" not in str(body)
    assert "unique-marker" not in str(body)


def test_oauth_for_existing_mfa_account_redirects_to_challenge_without_session(temp_db, monkeypatch):
    client = TestClient(api_main.app, follow_redirects=False)
    _register(client, "oauth-mfa@example.com")
    _enable(client)
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    config = {
        "client_id": "client.apps.googleusercontent.com",
        "client_secret": "secret",
        "redirect_uri": "http://localhost:8000/api/v1/auth/google/callback",
    }
    monkeypatch.setattr(auth_router, "get_google_oauth_config", lambda required=False: config)
    monkeypatch.setattr(
        auth_router,
        "build_google_authorization_url",
        lambda state, nonce, config: f"https://accounts.example/auth?state={quote(state)}&nonce={quote(nonce)}",
    )
    monkeypatch.setattr(
        auth_router,
        "exchange_google_code",
        lambda *args, **kwargs: {
            "google_id": "oauth-mfa-subject",
            "email": "oauth-mfa@example.com",
            "name": "MFA User",
        },
    )
    start = client.get("/api/v1/auth/google/start?next=/portfolio")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/api/v1/auth/google/callback?code=fixture&state={quote(state)}")

    assert callback.status_code == 303
    assert callback.headers["location"].startswith("/login?")
    assert "mfa=required" in callback.headers["location"]
    assert "stockpilot_session" not in client.cookies
    assert client.cookies.get("stockpilot_mfa_challenge")
