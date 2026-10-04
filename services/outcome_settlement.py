"""Automatic, authoritative settlement for immutable forecast outcomes."""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pandas as pd

from database import (
    get_pending_range_forecasts,
    mark_range_forecast_unverifiable,
    settle_range_forecast_automatically,
)
from services.market_data.manager import MANAGER

LOGGER = logging.getLogger(__name__)

_SETTLEMENT_WINDOWS = {
    "1m": "1w",
    "5m": "1mo",
    "15m": "1mo",
    "1h": "3mo",
    "4h": "3mo",
    "1D": "1y",
    "1W": "5y",
}


def _utc_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _is_demo(*values: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return "demo" in text or "synthetic" in text or "offline_demo" in text


def _history_metadata(frame: pd.DataFrame) -> tuple[str, bool, bool]:
    context = cast(dict[str, Any], frame.attrs.get("context")) if isinstance(frame.attrs.get("context"), dict) else {}
    provider = str(frame.attrs.get("provider") or context.get("provider") or frame.attrs.get("source") or "unknown")
    stale = bool(frame.attrs.get("is_stale", False) or context.get("is_stale", False))
    demo = _is_demo(
        provider,
        frame.attrs.get("source"),
        context.get("provider_mode"),
        context.get("credential_mode"),
    )
    return provider, stale, demo


def _quote_values(quote: Any) -> tuple[float | None, str | None, str, bool, bool]:
    item = cast(dict[str, Any], quote.to_dict() if hasattr(quote, "to_dict") else dict(quote or {}))
    context = cast(dict[str, Any], item.get("context")) if isinstance(item.get("context"), dict) else {}
    provider = str(context.get("provider") or item.get("source") or "unknown")
    stale = bool(item.get("is_stale", False) or context.get("is_stale", False))
    demo = _is_demo(provider, item.get("source"), context.get("provider_mode"), context.get("credential_mode"))
    price_value = item.get("price")
    try:
        price = float(price_value) if price_value is not None else None
    except (TypeError, ValueError):
        price = None
    if price is not None and (not math.isfinite(price) or price <= 0):
        price = None
    return price, cast(str | None, item.get("timestamp")), provider, stale, demo


def _quote_tolerance(timeframe: str) -> timedelta:
    return {
        "1m": timedelta(minutes=5),
        "5m": timedelta(minutes=15),
        "15m": timedelta(hours=1),
        "1h": timedelta(hours=4),
        "4h": timedelta(hours=12),
        "1D": timedelta(days=2),
        "1W": timedelta(days=8),
    }.get(timeframe, timedelta(hours=1))


def _history_tolerance(timeframe: str) -> timedelta:
    if timeframe in {"1m", "5m", "15m", "1h", "4h"}:
        return timedelta(days=4)
    if timeframe == "1W":
        return timedelta(days=14)
    return timedelta(days=10)


def _automatic_settle_from_quote(row: dict[str, Any], target: pd.Timestamp, now: pd.Timestamp, manager: Any) -> bool:
    timeframe = str(row.get("timeframe") or "1D")
    tolerance = _quote_tolerance(timeframe)
    if now - target > tolerance:
        return False
    quote = manager.get_quote(row["symbol"], timeframe=timeframe)
    price, observed_at, provider, stale, demo = _quote_values(quote)
    quote_time = _utc_timestamp(observed_at)
    if (
        stale
        or demo
        or provider.strip().lower() in {"", "unknown"}
        or price is None
        or quote_time is None
        or quote_time < target
        or now - quote_time > tolerance
    ):
        return False
    return cast(bool, settle_range_forecast_automatically(
        row["id"],
        price,
        provider=provider,
        data_timestamp=str(observed_at),
        evidence={"method": "authoritative_quote", "target_timestamp": str(row["target_timestamp"])},
    ))


def settle_due_forecasts(*, now: datetime | pd.Timestamp | None = None, manager: Any = MANAGER, limit: int = 1000, user_id: int | None = None) -> dict[str, int]:
    """Settle all due pending forecasts once, never substituting untrusted data."""
    effective_now = _utc_timestamp(now or datetime.now(timezone.utc))
    assert effective_now is not None
    summary = {
        "examined": 0,
        "due": 0,
        "not_due": 0,
        "settled": 0,
        "unverifiable": 0,
        "deferred": 0,
        "skipped_missing_target": 0,
        "already_final": 0,
        "failed": 0,
    }
    history_cache: dict[tuple[str, str, str], pd.DataFrame | Exception] = {}

    pending = get_pending_range_forecasts(limit=limit) if user_id is None else get_pending_range_forecasts(limit=limit, user_id=int(user_id))
    for row in pending:
        summary["examined"] += 1
        target = _utc_timestamp(row.get("target_timestamp"))
        if target is None:
            summary["skipped_missing_target"] += 1
            continue
        if target > effective_now:
            summary["not_due"] += 1
            continue
        summary["due"] += 1
        timeframe = str(row.get("timeframe") or "1D")
        window = _SETTLEMENT_WINDOWS.get(timeframe, str(row.get("training_window") or "1y"))
        key = (str(row["symbol"]), timeframe, window)
        if key not in history_cache:
            try:
                history_cache[key] = manager.get_history(row["symbol"], timeframe=timeframe, window=window)
            except Exception as exc:
                history_cache[key] = exc
        history = history_cache[key]

        reason = "target_data_absent"
        evidence: dict[str, Any] = {"target_timestamp": str(row["target_timestamp"]), "timeframe": timeframe}
        try:
            if isinstance(history, Exception):
                reason = "market_history_unavailable"
                evidence["error_type"] = type(history).__name__
            elif history.empty:
                reason = "target_data_absent"
            else:
                provider, stale, demo = _history_metadata(history)
                evidence["provider"] = provider
                context = cast(dict[str, Any], history.attrs.get("context")) if isinstance(history.attrs.get("context"), dict) else {}
                evidence["data_timestamp"] = (
                    context.get("as_of")
                    or history.attrs.get("fetched_at")
                    or pd.Timestamp(history.index[-1]).isoformat()
                )
                if stale:
                    reason = "stale_market_data"
                elif demo:
                    reason = "demo_market_data"
                elif provider.strip().lower() in {"", "unknown"}:
                    reason = "unverified_provider"
                else:
                    index = pd.to_datetime(history.index, utc=True, errors="coerce")
                    positions = [position for position, timestamp in enumerate(index) if pd.notna(timestamp) and timestamp >= target]
                    if positions and pd.Timestamp(index[positions[0]]) - target <= _history_tolerance(timeframe):
                        position = positions[0]
                        actual = float(history.iloc[position]["Close"])
                        observed_at = pd.Timestamp(index[position]).isoformat()
                        if math.isfinite(actual) and actual > 0:
                            changed = settle_range_forecast_automatically(
                                row["id"],
                                actual,
                                provider=provider,
                                data_timestamp=observed_at,
                                evidence={**evidence, "method": "authoritative_history_close"},
                            )
                            summary["settled" if changed else "already_final"] += 1
                            continue
                        reason = "invalid_target_price"

            try:
                if reason == "target_data_absent" and _automatic_settle_from_quote(row, target, effective_now, manager):
                    summary["settled"] += 1
                    continue
            except Exception as exc:
                evidence["quote_error_type"] = type(exc).__name__

            grace_seconds = max(0, min(int(os.getenv("STOCKPILOT_SETTLEMENT_GRACE_SECONDS", "21600")), 172800))
            if reason in {"target_data_absent", "market_history_unavailable"} and (effective_now - target).total_seconds() < grace_seconds:
                summary["deferred"] += 1
                continue

            changed = mark_range_forecast_unverifiable(row["id"], reason=reason, evidence=evidence)
            summary["unverifiable" if changed else "already_final"] += 1
        except Exception:
            summary["failed"] += 1
            LOGGER.exception("Automatic forecast settlement failed for ledger row %s", row.get("id"))

    return summary
