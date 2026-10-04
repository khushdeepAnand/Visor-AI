"""Proactive Upstox token-expiry warning (L1)."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from services.market_data.upstox_auth import (
    EXPIRY_WARN_BEFORE_SECONDS,
    expiry_warning,
)


def make_token(exp_delta: timedelta | None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {"exp": None} if exp_delta is None else {"exp": (datetime.now(timezone.utc) + exp_delta).timestamp()}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.signature"


def test_missing_token_reports_missing() -> None:
    warning = expiry_warning(None)
    assert warning.configured is False
    assert warning.status == "missing"


def test_unreadable_token_reports_not_decodable() -> None:
    warning = expiry_warning("garbage")
    assert warning.configured is True
    assert warning.status == "not_decodable"
    assert warning.expires_at is None


def test_expired_token_reports_expired() -> None:
    warning = expiry_warning(make_token(timedelta(seconds=-60)))
    assert warning.status == "expired"
    assert warning.seconds_remaining == 0


def test_token_inside_warning_horizon_reports_expiring_soon() -> None:
    warning = expiry_warning(make_token(timedelta(days=2)))
    assert warning.status == "expiring_soon"
    assert warning.seconds_remaining is not None
    assert 0 < warning.seconds_remaining < EXPIRY_WARN_BEFORE_SECONDS


def test_token_well_outside_warning_horizon_reports_valid() -> None:
    warning = expiry_warning(make_token(timedelta(days=60)))
    assert warning.status == "valid"
    assert warning.seconds_remaining is not None
    assert warning.seconds_remaining > EXPIRY_WARN_BEFORE_SECONDS


def test_warning_horizon_is_configurable() -> None:
    warning = expiry_warning(make_token(timedelta(days=2)), warn_before_seconds=10 * 24 * 3600)
    assert warning.status == "expiring_soon"