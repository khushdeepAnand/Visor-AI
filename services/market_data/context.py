"""Shared provenance contract for market-data-backed responses."""
from __future__ import annotations

import os
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .base import Instrument


class ProviderMode(str, Enum):
    LIVE_ONLY = "LIVE_ONLY"
    OFFLINE_DEMO = "OFFLINE_DEMO"
    FALLBACK_ALLOWED = "FALLBACK_ALLOWED"

    @classmethod
    def configured(cls) -> "ProviderMode":
        value = os.getenv("STOCKPILOT_PROVIDER_MODE", cls.LIVE_ONLY.value).strip().upper()
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(
                f"STOCKPILOT_PROVIDER_MODE must be one of {[mode.value for mode in cls]}."
            ) from exc


_REQUEST_ID: ContextVar[str | None] = ContextVar("stockpilot_request_id", default=None)


def set_request_id(request_id: str) -> Token:
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token) -> None:
    _REQUEST_ID.reset(token)


def request_id() -> str:
    return _REQUEST_ID.get() or uuid.uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_market_context(
    *,
    requested_symbol: str,
    instrument: Instrument | None,
    provider: str,
    credential_mode: str,
    timeframe: str,
    as_of: str | None,
    is_live: bool,
    is_stale: bool,
    fallback_used: bool,
    fallback_reason: str | None,
    provider_mode: ProviderMode | str | None = None,
) -> dict[str, Any]:
    mode = provider_mode or ProviderMode.configured()
    mode_value = mode.value if isinstance(mode, ProviderMode) else str(mode)
    return {
        "requested_symbol": str(requested_symbol),
        "resolved_instrument_key": instrument.instrument_key if instrument else None,
        "exchange": instrument.exchange if instrument else None,
        "instrument_type": instrument.instrument_type if instrument else None,
        "provider": provider,
        "credential_mode": credential_mode,
        "timeframe": timeframe,
        "as_of": as_of,
        "received_at": utc_now(),
        "is_live": bool(is_live),
        "is_stale": bool(is_stale),
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "request_id": request_id(),
        "provider_mode": mode_value,
    }


def renew_market_context(
    context: dict[str, Any],
    *,
    is_stale: bool | None = None,
    fallback_used: bool | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    renewed = dict(context)
    renewed["received_at"] = utc_now()
    renewed["request_id"] = request_id()
    if is_stale is not None:
        renewed["is_stale"] = bool(is_stale)
        if is_stale:
            renewed["is_live"] = False
    if fallback_used is not None:
        renewed["fallback_used"] = bool(fallback_used)
    if fallback_reason is not None:
        renewed["fallback_reason"] = fallback_reason
    return renewed
