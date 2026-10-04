"""Google OAuth 2.0/OpenID Connect integration using Authlib.

Credentials are always read from environment variables. No secrets are stored
in source code or in the StockPilot database.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Callable, Mapping

import jwt

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SCOPES = ["openid", "email", "profile"]


class GoogleOAuthConfigurationError(RuntimeError):
    pass


class GoogleOAuthError(RuntimeError):
    """Google sign-in failure.

    ``code`` is a stable, public-safe classification of *where* the flow broke
    (token exchange, id-token validation, account linking, ...). It never
    carries exception text, so provider responses or credentials embedded in a
    message cannot leak past the sanitized logger.
    """

    def __init__(self, message: str, code: str = "google_sign_in_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def get_google_oauth_config(
    environment: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> GoogleOAuthConfig | None:
    # An explicitly supplied mapping is an isolated configuration source. This
    # keeps tests and diagnostics from falling through to process credentials.
    values = os.environ if environment is None else environment
    client_id = str(values.get("GOOGLE_CLIENT_ID", "")).strip()
    client_secret = str(values.get("GOOGLE_CLIENT_SECRET", "")).strip()
    redirect_uri = str(values.get("GOOGLE_REDIRECT_URI", "")).strip()

    if client_id and client_secret and redirect_uri:
        return GoogleOAuthConfig(client_id, client_secret, redirect_uri)

    if required:
        missing = [
            name
            for name, value in (
                ("GOOGLE_CLIENT_ID", client_id),
                ("GOOGLE_CLIENT_SECRET", client_secret),
                ("GOOGLE_REDIRECT_URI", redirect_uri),
            )
            if not value
        ]
        raise GoogleOAuthConfigurationError(
            "Google sign-in is not configured. Missing: " + ", ".join(missing)
        )
    return None


def _required_config(config: GoogleOAuthConfig | None) -> GoogleOAuthConfig:
    resolved = config or get_google_oauth_config(required=True)
    if resolved is None:
        raise GoogleOAuthConfigurationError("Google sign-in is not configured.")
    return resolved


def generate_oauth_state(
    config: GoogleOAuthConfig | None = None,
    *,
    now: int | None = None,
) -> str:
    """Create a short-lived signed OAuth state token safe across page reloads."""

    config = _required_config(config)
    timestamp = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(24)
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(
        config.client_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload}.{encoded_signature}"


def validate_oauth_state(
    state: str,
    config: GoogleOAuthConfig | None = None,
    *,
    max_age_seconds: int = 600,
    now: int | None = None,
) -> bool:
    """Validate state integrity and age without depending on Streamlit session state."""

    config = _required_config(config)
    try:
        timestamp_text, nonce, supplied_signature = str(state).split(".", 2)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False

    current_time = int(time.time() if now is None else now)
    if timestamp > current_time + 30 or current_time - timestamp > max_age_seconds:
        return False

    payload = f"{timestamp}.{nonce}"
    expected = hmac.new(
        config.client_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_signature = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    return hmac.compare_digest(supplied_signature, expected_signature)


def _default_session_factory(**kwargs):
    try:
        from authlib.integrations.requests_client import OAuth2Session
    except ImportError as error:  # pragma: no cover - dependency guard
        raise GoogleOAuthConfigurationError(
            "Google sign-in requires Authlib. Install the declared project requirements."
        ) from error
    return OAuth2Session(**kwargs)


def build_authorization_url(
    state: str,
    *,
    nonce: str | None = None,
    config: GoogleOAuthConfig | None = None,
    session_factory: Callable[..., Any] | None = None,
) -> str:
    config = _required_config(config)
    factory = session_factory or _default_session_factory
    client = factory(
        client_id=config.client_id,
        client_secret=config.client_secret,
        scope=SCOPES,
        redirect_uri=config.redirect_uri,
        state=state,
        token_endpoint_auth_method="client_secret_post",
    )
    parameters = {"state": state, "prompt": "select_account"}
    if nonce:
        parameters["nonce"] = nonce
    url, _ = client.create_authorization_url(AUTHORIZATION_ENDPOINT, **parameters)
    return str(url)


def _decode_id_token(token: str, config: GoogleOAuthConfig, nonce: str) -> dict[str, Any]:
    try:
        signing_key = jwt.PyJWKClient(JWKS_ENDPOINT).get_signing_key_from_jwt(token)
        claims = dict(jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=config.client_id,
            leeway=120,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        ))
    except (jwt.ExpiredSignatureError, jwt.ImmatureSignatureError) as exc:
        raise GoogleOAuthError("The Google identity token is expired; please sign in again.", code="google_jwt_expired") from exc
    except Exception as exc:
        raise GoogleOAuthError("Google identity validation failed.", code="google_jwt_invalid") from exc
    if str(claims.get("iss") or "") not in ISSUERS or str(claims.get("nonce") or "") != nonce:
        raise GoogleOAuthError("Google issuer or nonce validation failed.", code="google_identity_validation_failed")
    return claims


def _google_token_error_code(exc: Exception) -> str:
    """Reduce an upstream token-exchange failure to a public-safe classification.

    Google's OAuth token endpoint returns fixed error enums (``invalid_grant``,
    ``invalid_client``, ...), never user content, so mapping them into the
    logged code is safe while still telling an operator exactly what happened.
    Any other exception type collapses to a generic exchange-failure code.
    """
    error = getattr(exc, "error", None)
    if isinstance(error, str) and error.strip():
        return ("google_token_" + error.strip().lower().replace("_", "-"))[:60]
    try:
        body = getattr(exc, "json", lambda: None)()
    except Exception:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
    if isinstance(error, str) and error.strip():
        return ("google_token_" + error.strip().lower().replace("_", "-"))[:60]
    return "google_token_exchange_failed"


def exchange_code_for_profile(
    code: str,
    state: str,
    *,
    config: GoogleOAuthConfig | None = None,
    session_factory: Callable[..., Any] | None = None,
    expected_nonce: str | None = None,
    id_token_decoder: Callable[[str, GoogleOAuthConfig, str], dict[str, Any]] = _decode_id_token,
) -> dict[str, Any]:
    """Exchange a Google authorization code and return a verified profile."""

    if not code:
        raise GoogleOAuthError("Google did not return an authorization code.", code="google_code_missing")

    config = _required_config(config)
    factory = session_factory or _default_session_factory
    client = factory(
        client_id=config.client_id,
        client_secret=config.client_secret,
        scope=SCOPES,
        redirect_uri=config.redirect_uri,
        state=state,
        token_endpoint_auth_method="client_secret_post",
    )
    try:
        token = client.fetch_token(
            TOKEN_ENDPOINT,
            code=code,
            grant_type="authorization_code",
        )
    except Exception as exc:
        raise GoogleOAuthError(
            "Google token exchange failed; please try signing in again.",
            code=_google_token_error_code(exc),
        ) from exc
    if expected_nonce:
        id_token = str((token or {}).get("id_token") or "")
        if not id_token:
            raise GoogleOAuthError("Google did not return an identity token.", code="google_id_token_missing")
        profile = id_token_decoder(id_token, config, expected_nonce)
    else:
        response = client.get(USERINFO_ENDPOINT)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        profile = response.json()

    email = str(profile.get("email", "")).strip().lower()
    google_id = str(profile.get("sub", "")).strip()
    if not email or not google_id:
        raise GoogleOAuthError("Google did not return a usable account identity.", code="google_identity_incomplete")
    if profile.get("email_verified") is not True:
        raise GoogleOAuthError("The Google account email is not verified.", code="google_email_unverified")

    return {
        "google_id": google_id,
        "email": email,
        "name": str(profile.get("name") or email.split("@", 1)[0]).strip(),
        "picture": str(profile.get("picture") or "").strip(),
    }
