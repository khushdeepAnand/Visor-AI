"""Sanitized Upstox token metadata, failure classification, and GET retries."""
from __future__ import annotations

import base64
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from .base import ProviderUnavailableError


CLASSIFICATIONS = {
    "missing",
    "expired",
    "revoked_or_invalid",
    "permission_denied",
    "rate_limited",
    "endpoint_error",
    "network_error",
    "timeout",
    "valid",
}
RETRYABLE_CLASSIFICATIONS = {"rate_limited", "endpoint_error", "network_error", "timeout"}
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True, slots=True)
class TokenMetadata:
    configured: bool
    expires_at: str | None
    is_expired: bool | None
    verification: str = "not_checked"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UpstoxDiagnostic:
    endpoint: str
    classification: str
    http_status: int | None = None
    error_code: str | None = None
    attempts: int = 1
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


#: Proactive warning horizon: flag a token a week before it will expire so a
#: single-user deployment never wakes up to "data stopped working overnight".
EXPIRY_WARN_BEFORE_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class TokenExpiryWarning:
    configured: bool
    status: str
    expires_at: str | None
    seconds_remaining: int | None
    warn_before_seconds: int = EXPIRY_WARN_BEFORE_SECONDS

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def expiry_warning(
    token: str | None,
    *,
    now: datetime | None = None,
    warn_before_seconds: int = EXPIRY_WARN_BEFORE_SECONDS,
) -> TokenExpiryWarning:
    """Report how close a configured Upstox token is to dying, before it dies.

    This is the proactive duplicate of ``decode_token_metadata``: it answers
    "reauthorise soon" without waiting for a 401, because a 401 is the failure
    mode the dossier attributes to expired tokens.
    """
    meta = decode_token_metadata(token, now=now)
    if not meta.configured:
        return TokenExpiryWarning(configured=False, status="missing", expires_at=None, seconds_remaining=None)
    if meta.expires_at is None:
        return TokenExpiryWarning(configured=True, status="not_decodable", expires_at=None, seconds_remaining=None)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    expires_at = datetime.fromisoformat(meta.expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = int((expires_at - reference.astimezone(timezone.utc)).total_seconds())
    status = "expired" if meta.is_expired else ("expiring_soon" if remaining < int(warn_before_seconds) else "valid")
    return TokenExpiryWarning(
        configured=True,
        status=status,
        expires_at=meta.expires_at,
        seconds_remaining=max(0, remaining),
        warn_before_seconds=int(warn_before_seconds),
    )


class UpstoxRequestError(ProviderUnavailableError):
    """A broker error whose message contains only sanitized diagnostic fields."""

    def __init__(self, diagnostic: UpstoxDiagnostic) -> None:
        if diagnostic.classification not in CLASSIFICATIONS:
            raise ValueError("Unsupported Upstox classification")
        self.diagnostic = diagnostic
        fields = [
            f"endpoint={diagnostic.endpoint}",
            f"classification={diagnostic.classification}",
        ]
        if diagnostic.http_status is not None:
            fields.append(f"http_status={diagnostic.http_status}")
        if diagnostic.error_code:
            fields.append(f"error_code={diagnostic.error_code}")
        super().__init__("Upstox request failed (" + ", ".join(fields) + ")")


class BrokerOrderBlockedError(PermissionError):
    """Raised if any code attempts to use an Upstox live-order endpoint."""


def assert_upstox_request_allowed(method: str, url: str) -> None:
    """Fail closed for every Upstox place/modify/cancel/GTT order route."""

    parsed = urlsplit(str(url))
    hostname = (parsed.hostname or "").lower()
    if hostname != "upstox.com" and not hostname.endswith(".upstox.com"):
        return
    segments = {segment for segment in parsed.path.lower().split("/") if segment}
    order_action = bool(segments & {"place", "modify", "cancel"})
    order_resource = bool(segments & {"order", "orders", "gtt"})
    if "gtt" in segments or (order_resource and order_action):
        raise BrokerOrderBlockedError("Live Upstox broker order endpoints are disabled.")


def decode_token_metadata(token: str | None, *, now: datetime | None = None) -> TokenMetadata:
    """Decode local JWT expiry metadata without validating or trusting the token."""
    if not token:
        return TokenMetadata(configured=False, expires_at=None, is_expired=None)
    try:
        segments = token.split(".")
        if len(segments) != 3:
            return TokenMetadata(configured=True, expires_at=None, is_expired=None)
        payload_segment = segments[1] + "=" * (-len(segments[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment).decode("utf-8"))
        expiry = float(payload["exp"])
        expires_at = datetime.fromtimestamp(expiry, tz=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return TokenMetadata(
            configured=True,
            expires_at=expires_at.isoformat(),
            is_expired=expires_at <= reference.astimezone(timezone.utc),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return TokenMetadata(configured=True, expires_at=None, is_expired=None)


def classify_http_failure(status: int, token: str | None) -> str:
    if status == 401:
        return "expired" if decode_token_metadata(token).is_expired is True else "revoked_or_invalid"
    if status == 403:
        return "permission_denied"
    if status == 429:
        return "rate_limited"
    return "endpoint_error"


def _error_code(response: requests.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None
    candidates: list[object] = []
    if isinstance(payload, dict):
        candidates.extend(payload.get(key) for key in ("code", "error_code", "errorCode"))
        errors = payload.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            candidates.extend(errors[0].get(key) for key in ("code", "error_code", "errorCode"))
    for candidate in candidates:
        value = str(candidate or "")
        if _SAFE_CODE.fullmatch(value):
            return value
    return None


def _retry_after_seconds(response: requests.Response | None, attempt: int, jitter: Callable[[], float]) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(5.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("X-Ratelimit-Reset")
        if reset:
            try:
                return min(5.0, max(0.0, float(reset) - time.time()))
            except ValueError:
                pass
    base = min(4.0, 0.25 * (2 ** (attempt - 1)))
    return min(5.0, base + min(0.25, base * 0.25) * jitter())


def request_json(
    *,
    endpoint: str,
    url: str,
    token: str | None,
    timeout: float,
    params: dict[str, str] | None = None,
    max_attempts: int = 3,
    getter: Callable[..., requests.Response] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> tuple[dict[str, Any], UpstoxDiagnostic]:
    """Issue a bounded, read-only Upstox GET and return sanitized diagnostics."""
    assert_upstox_request_allowed("GET", url)
    if not token:
        raise UpstoxRequestError(UpstoxDiagnostic(endpoint=endpoint, classification="missing", attempts=0))
    get = getter or requests.get
    attempts = max(1, min(max_attempts, 3))
    started = time.perf_counter()
    last: UpstoxDiagnostic | None = None
    for attempt in range(1, attempts + 1):
        response: requests.Response | None = None
        try:
            response = get(
                url,
                params=params,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
            status = int(response.status_code)
            if 200 <= status < 300:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("JSON response is not an object")
                return payload, UpstoxDiagnostic(
                    endpoint=endpoint,
                    classification="valid",
                    http_status=status,
                    attempts=attempt,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            classification = classify_http_failure(status, token)
            last = UpstoxDiagnostic(
                endpoint=endpoint,
                classification=classification,
                http_status=status,
                error_code=_error_code(response),
                attempts=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except requests.Timeout:
            last = UpstoxDiagnostic(
                endpoint=endpoint,
                classification="timeout",
                attempts=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except requests.RequestException:
            last = UpstoxDiagnostic(
                endpoint=endpoint,
                classification="network_error",
                attempts=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            last = UpstoxDiagnostic(
                endpoint=endpoint,
                classification="endpoint_error",
                http_status=getattr(response, "status_code", None),
                attempts=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        if last.classification not in RETRYABLE_CLASSIFICATIONS or attempt == attempts:
            raise UpstoxRequestError(last)
        if last.classification == "endpoint_error" and (last.http_status or 0) < 500:
            raise UpstoxRequestError(last)
        sleeper(_retry_after_seconds(response, attempt, jitter))
    raise UpstoxRequestError(last or UpstoxDiagnostic(endpoint=endpoint, classification="endpoint_error"))
