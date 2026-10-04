import sqlite3

import authentication
import database
from services.google_oauth import (
    GoogleOAuthConfig,
    build_authorization_url,
    exchange_code_for_profile,
    get_google_oauth_config,
    generate_oauth_state,
    validate_oauth_state,
)


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "sub": "google-123",
            "email": "oauth@example.com",
            "email_verified": True,
            "name": "OAuth User",
        }


class FakeOAuthSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.fetched = None

    def create_authorization_url(self, endpoint, **kwargs):
        return f"{endpoint}?state={kwargs['state']}", kwargs["state"]

    def fetch_token(self, endpoint, **kwargs):
        self.fetched = (endpoint, kwargs)
        return {"access_token": "test"}

    def get(self, endpoint):
        return FakeResponse()


def test_google_oauth_config_requires_all_environment_values():
    assert get_google_oauth_config({}) is None
    config = get_google_oauth_config(
        {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "GOOGLE_REDIRECT_URI": "http://localhost:8501",
        }
    )
    assert config.redirect_uri == "http://localhost:8501"



def test_signed_oauth_state_survives_reload_and_rejects_tampering():
    config = GoogleOAuthConfig("client", "secret", "http://localhost:8501")
    state = generate_oauth_state(config, now=1_000)
    assert validate_oauth_state(state, config, now=1_100) is True
    assert validate_oauth_state(state + "tampered", config, now=1_100) is False
    assert validate_oauth_state(state, config, now=2_000) is False

def test_authorization_and_exchange_use_standard_endpoints_without_network():
    config = GoogleOAuthConfig("client", "secret", "http://localhost:8501")
    url = build_authorization_url(
        "state-1", config=config, session_factory=FakeOAuthSession
    )
    assert "accounts.google.com" in url
    assert "state=state-1" in url

    profile = exchange_code_for_profile(
        "code-1", "state-1", config=config, session_factory=FakeOAuthSession
    )
    assert profile["email"] == "oauth@example.com"
    assert profile["google_id"] == "google-123"


def test_google_user_creation_and_existing_password_account_link(temp_db):
    success, user, action = authentication.login_or_register_google_user(
        "Google User", "google@example.com", "gid-new"
    )
    assert success is True and action == "created"
    assert user["email"] == "google@example.com"
    password_login = authentication.login_user("google@example.com", "AnyPassword1")
    assert password_login[0] is False
    assert "external sign-in" in password_login[1]

    assert authentication.register_user(
        "Password User", "linked@example.com", "StrongPass1", "1985-06-15"
    )[0] is True
    success, linked_user, action = authentication.login_or_register_google_user(
        "Google Name", "linked@example.com", "gid-link"
    )
    assert success is True and action == "linked"
    assert authentication.login_user("linked@example.com", "StrongPass1")[0] is True

    connection = database.get_connection()
    row = connection.execute(
        "SELECT auth_provider, google_id FROM users WHERE email = ?",
        ("linked@example.com",),
    ).fetchone()
    connection.close()
    assert row == ("password+google", "gid-link")


def test_users_table_migration_adds_oauth_columns(temp_db):
    connection = sqlite3.connect(temp_db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    connection.close()
    assert {"auth_provider", "google_id"}.issubset(columns)
