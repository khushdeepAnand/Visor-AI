"""Sign in with Apple OIDC implementation with fixture-friendly boundaries."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import jwt
import requests

AUTHORIZATION_ENDPOINT = "https://appleid.apple.com/auth/authorize"
TOKEN_ENDPOINT = "https://appleid.apple.com/auth/token"
JWKS_ENDPOINT = "https://appleid.apple.com/auth/keys"
ISSUER = "https://appleid.apple.com"


class AppleOAuthConfigurationError(RuntimeError):
    pass


class AppleOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppleOAuthConfig:
    client_id: str
    team_id: str
    key_id: str
    private_key: str
    redirect_uri: str


def get_apple_oauth_config(
    environment: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> AppleOAuthConfig | None:
    values = environment or os.environ
    fields = {
        "client_id": str(values.get("APPLE_CLIENT_ID", "")).strip(),
        "team_id": str(values.get("APPLE_TEAM_ID", "")).strip(),
        "key_id": str(values.get("APPLE_KEY_ID", "")).strip(),
        "redirect_uri": str(values.get("APPLE_REDIRECT_URI", "")).strip(),
    }
    private_key = str(values.get("APPLE_PRIVATE_KEY", "")).replace("\\n", "\n").strip()
    key_path = str(values.get("APPLE_PRIVATE_KEY_PATH", "")).strip()
    if not private_key and key_path:
        try:
            private_key = Path(key_path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            if required:
                raise AppleOAuthConfigurationError("Apple private key could not be read.") from exc
    if all(fields.values()) and private_key:
        return AppleOAuthConfig(private_key=private_key, **fields)
    if required:
        raise AppleOAuthConfigurationError("Sign in with Apple is not fully configured.")
    return None


def generate_client_secret(config: AppleOAuthConfig, *, now: int | None = None, lifetime_seconds: int = 300) -> str:
    issued = int(time.time() if now is None else now)
    lifetime = max(60, min(int(lifetime_seconds), 15_777_000))
    return str(jwt.encode(
        {"iss": config.team_id, "iat": issued, "exp": issued + lifetime, "aud": ISSUER, "sub": config.client_id},
        config.private_key,
        algorithm="ES256",
        headers={"kid": config.key_id},
    ))


def build_authorization_url(state: str, nonce: str, *, config: AppleOAuthConfig) -> str:
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode({
        'client_id': config.client_id,
        'redirect_uri': config.redirect_uri,
        'response_type': 'code id_token',
        'response_mode': 'form_post',
        'scope': 'name email',
        'state': state,
        'nonce': nonce,
    })}"


def _decode_id_token(token: str, config: AppleOAuthConfig, nonce: str) -> dict[str, Any]:
    try:
        signing_key = jwt.PyJWKClient(JWKS_ENDPOINT).get_signing_key_from_jwt(token)
        return dict(jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=config.client_id,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        ))
    except Exception as exc:
        raise AppleOAuthError("Apple identity validation failed.") from exc


def exchange_code_for_profile(
    code: str,
    nonce: str,
    *,
    config: AppleOAuthConfig,
    user_payload: str | None = None,
    post: Callable[..., Any] = requests.post,
    decoder: Callable[[str, AppleOAuthConfig, str], dict[str, Any]] = _decode_id_token,
) -> dict[str, Any]:
    if not code:
        raise AppleOAuthError("Apple did not return an authorization code.")
    response = post(
        TOKEN_ENDPOINT,
        data={
            "client_id": config.client_id,
            "client_secret": generate_client_secret(config),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.redirect_uri,
        },
        timeout=15,
    )
    if hasattr(response, "raise_for_status"):
        try:
            response.raise_for_status()
        except Exception as exc:
            raise AppleOAuthError("Apple rejected the authorization code.") from exc
    token_data = response.json()
    if token_data.get("error") or not token_data.get("id_token"):
        raise AppleOAuthError("Apple did not return a usable identity token.")
    claims = decoder(str(token_data["id_token"]), config, nonce)
    if str(claims.get("nonce") or "") != nonce:
        raise AppleOAuthError("Apple nonce validation failed.")
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    verified = str(claims.get("email_verified", "")).lower() in {"true", "1"}
    supplied: dict[str, Any] = {}
    if user_payload:
        try:
            supplied = json.loads(user_payload)
        except json.JSONDecodeError:
            supplied = {}
    email = email or str(supplied.get("email") or "").strip().lower()
    raw_name = supplied.get("name")
    name_block: dict[str, Any] = raw_name if isinstance(raw_name, dict) else {}
    name = " ".join(str(name_block.get(key) or "").strip() for key in ("firstName", "lastName")).strip()
    if not subject or not email or not verified:
        raise AppleOAuthError("Apple did not return a verified account identity.")
    return {"apple_id": subject, "email": email, "name": name or email.split("@", 1)[0], "email_verified": True}
