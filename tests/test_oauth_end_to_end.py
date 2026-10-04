from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import api.main as api_main
import api.routers.auth as auth_router
import authentication
from services.apple_oauth import (
    AppleOAuthConfig,
    AppleOAuthError,
    build_authorization_url as build_apple_url,
    exchange_code_for_profile as exchange_apple_code,
    generate_client_secret,
    get_apple_oauth_config,
)
from services.google_oauth import GoogleOAuthConfig, GoogleOAuthError, exchange_code_for_profile
from services.oauth_state import OAuthStateError, consume_transaction, issue_transaction, reset_used_transactions


GOOGLE_CONFIG = GoogleOAuthConfig("google-client", "google-secret", "http://127.0.0.1:3000/api/v1/auth/google/callback")


def _apple_config() -> AppleOAuthConfig:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return AppleOAuthConfig(
        client_id="com.example.stockpilot",
        team_id="TEAM123456",
        key_id="KEY123456",
        private_key=pem,
        redirect_uri="https://example.test/api/v1/auth/apple/callback",
    )


@pytest.fixture(autouse=True)
def oauth_state_reset():
    reset_used_transactions()


def test_oauth_transaction_is_browser_bound_expiring_and_one_time(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "fixture-state-key-with-at-least-thirty-two-bytes")
    transaction = issue_transaction("google", "/portfolio", now=1_000)
    decoded = consume_transaction(transaction.state, "google", expected_state=transaction.state, now=1_001)
    assert decoded.next_path == "/portfolio"
    assert decoded.nonce
    with pytest.raises(OAuthStateError):
        consume_transaction(transaction.state, "google", expected_state=transaction.state, now=1_002)
    other = issue_transaction("google", "https://attacker.example", now=2_000)
    assert other.next_path == "/"


def test_google_requires_verified_email_and_validated_nonce_claims():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sub": "subject", "email": "person@example.com"}

    class Session:
        def __init__(self, **_kwargs):
            pass

        def fetch_token(self, *_args, **_kwargs):
            return {"access_token": "fixture"}

        def get(self, *_args):
            return Response()

    with pytest.raises(GoogleOAuthError):
        exchange_code_for_profile("code", "state", config=GOOGLE_CONFIG, session_factory=Session)

    class IdSession(Session):
        def fetch_token(self, *_args, **_kwargs):
            return {"id_token": "fixture-id-token"}

    profile = exchange_code_for_profile(
        "code",
        "state",
        config=GOOGLE_CONFIG,
        session_factory=IdSession,
        expected_nonce="nonce-1",
        id_token_decoder=lambda _token, _config, nonce: {
            "sub": "subject", "email": "person@example.com", "email_verified": True, "nonce": nonce,
        },
    )
    assert profile["google_id"] == "subject"


def test_apple_client_secret_and_profile_validation():
    config = _apple_config()
    token = generate_client_secret(config, now=1_000)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims == {
        "iss": config.team_id,
        "iat": 1_000,
        "exp": 1_300,
        "aud": "https://appleid.apple.com",
        "sub": config.client_id,
    }
    assert "response_mode=form_post" in build_apple_url("state", "nonce", config=config)

    class TokenResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id_token": "fixture-id-token"}

    profile = exchange_apple_code(
        "code",
        "nonce-1",
        config=config,
        user_payload=json.dumps({"name": {"firstName": "Ada", "lastName": "Lovelace"}}),
        post=lambda *_args, **_kwargs: TokenResponse(),
        decoder=lambda _token, _config, nonce: {
            "sub": "apple-subject", "email": "relay@example.com", "email_verified": "true", "nonce": nonce,
        },
    )
    assert profile == {
        "apple_id": "apple-subject",
        "email": "relay@example.com",
        "name": "Ada Lovelace",
        "email_verified": True,
    }


def test_apple_rejects_nonce_mismatch():
    class TokenResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id_token": "fixture"}

    with pytest.raises(AppleOAuthError):
        exchange_apple_code(
            "code", "expected", config=_apple_config(),
            post=lambda *_args, **_kwargs: TokenResponse(),
            decoder=lambda *_args: {
                "sub": "subject", "email": "relay@example.com", "email_verified": True, "nonce": "wrong",
            },
        )


def test_generic_identity_rejects_unverified_email_and_resolves_by_subject(temp_db):
    refused = authentication.login_or_register_oauth_user(
        provider="google", name="Person", email="person@example.com",
        provider_subject="subject-1", email_verified=False,
    )
    assert refused[0] is False
    first = authentication.login_or_register_oauth_user(
        provider="google", name="Person", email="person@example.com",
        provider_subject="subject-1", email_verified=True,
    )
    second = authentication.login_or_register_oauth_user(
        provider="google", name="Changed", email="new-address@example.com",
        provider_subject="subject-1", email_verified=True,
    )
    assert first[0] is second[0] is True
    assert first[1]["id"] == second[1]["id"]


def test_google_api_callback_creates_session_and_preserves_destination(temp_db, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "fixture-state-key-with-at-least-thirty-two-bytes")
    monkeypatch.setattr(auth_router, "get_google_oauth_config", lambda required=False: GOOGLE_CONFIG)
    monkeypatch.setattr(
        auth_router,
        "build_google_authorization_url",
        lambda state, nonce, config: f"https://accounts.example/auth?state={quote(state)}&nonce={quote(nonce)}",
    )
    monkeypatch.setattr(auth_router, "exchange_google_code", lambda *args, **kwargs: {
        "google_id": "google-subject", "email": "google@example.com", "name": "Google Person",
    })
    client = TestClient(api_main.app, follow_redirects=False)
    start = client.get("/api/v1/auth/google/start?next=/portfolio")
    assert start.status_code == 303
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/api/v1/auth/google/callback?code=fixture&state={quote(state)}")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/onboarding?next=%2Fportfolio"
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == "google@example.com"


def test_apple_api_callback_creates_session(temp_db, monkeypatch):
    config = _apple_config()
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "fixture-state-key-with-at-least-thirty-two-bytes")
    client = TestClient(api_main.app, follow_redirects=False)
    start = client.get("/api/v1/auth/apple/start?next=/watchlist")
    assert start.status_code == 404


def test_oauth_configuration_status_never_returns_values(monkeypatch):
    monkeypatch.setattr(auth_router, "get_google_oauth_config", lambda required=False: GOOGLE_CONFIG)
    body = TestClient(api_main.app).get("/api/v1/auth/oauth/status").json()
    assert body == {"google": {"configured": True}, "apple": {"configured": False}}
    assert "client" not in json.dumps(body).lower()


def test_apple_config_remains_credential_gated():
    assert get_apple_oauth_config({}) is None
