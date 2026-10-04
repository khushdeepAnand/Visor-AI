"""Short-lived, one-time OAuth transactions with safe return paths."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


class OAuthStateError(ValueError):
    pass


@dataclass(frozen=True)
class OAuthTransaction:
    provider: str
    nonce: str
    next_path: str
    state: str


_USED: dict[str, int] = {}
_LOCK = threading.Lock()
_LOCAL_STATE_KEY = secrets.token_urlsafe(32)


def safe_next_path(value: str | None) -> str:
    path = str(value or "/").strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    if path.startswith(("/login", "/register", "/api/")):
        return "/"
    return path[:500]


def _secret(explicit: str | None = None) -> bytes:
    value: str = explicit or os.getenv("STOCKPILOT_JWT_SECRET", "") or ""
    environment = os.getenv("STOCKPILOT_ENV", "").strip().lower()
    provider_mode = os.getenv("STOCKPILOT_PROVIDER_MODE", "LIVE_ONLY").strip().upper()
    if not value and environment in {"development", "test"} and provider_mode != "OFFLINE_DEMO":
        value = _LOCAL_STATE_KEY
    if len(value.encode("utf-8")) < 32:
        raise OAuthStateError("OAuth state signing is unavailable.")
    return value.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_transaction(
    provider: str,
    next_path: str | None,
    *,
    secret: str | None = None,
    now: int | None = None,
) -> OAuthTransaction:
    issued = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(24)
    payload: dict[str, Any] = {
        "provider": str(provider).lower(),
        "nonce": nonce,
        "next": safe_next_path(next_path),
        "iat": issued,
        "exp": issued + 600,
        "jti": secrets.token_urlsafe(18),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(_secret(secret), encoded.encode("ascii"), hashlib.sha256).digest())
    state = f"{encoded}.{signature}"
    return OAuthTransaction(payload["provider"], nonce, payload["next"], state)


def consume_transaction(
    state: str,
    provider: str,
    *,
    expected_state: str | None = None,
    secret: str | None = None,
    now: int | None = None,
) -> OAuthTransaction:
    if expected_state is None or not hmac.compare_digest(str(state), str(expected_state)):
        raise OAuthStateError("OAuth state is not bound to this browser.")
    try:
        encoded, supplied = str(state).split(".", 1)
        expected = _b64(hmac.new(_secret(secret), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise OAuthStateError("OAuth state signature is invalid.")
        payload = json.loads(_unb64(encoded))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, OAuthStateError):
            raise
        raise OAuthStateError("OAuth state is malformed.") from exc
    current = int(time.time() if now is None else now)
    if payload.get("provider") != str(provider).lower() or current < int(payload.get("iat", 0)) - 30 or current > int(payload.get("exp", 0)):
        raise OAuthStateError("OAuth state is expired or belongs to another provider.")
    jti = str(payload.get("jti") or "")
    if not jti:
        raise OAuthStateError("OAuth state is incomplete.")
    with _LOCK:
        for used_jti, expiry in list(_USED.items()):
            if expiry < current:
                _USED.pop(used_jti, None)
        if jti in _USED:
            raise OAuthStateError("OAuth callback was already used.")
        _USED[jti] = int(payload["exp"])
    return OAuthTransaction(str(payload["provider"]), str(payload["nonce"]), safe_next_path(payload.get("next")), str(state))


def reset_used_transactions() -> None:
    with _LOCK:
        _USED.clear()
