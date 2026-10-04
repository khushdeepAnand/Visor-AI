"""Read-only Upstox quote, history, instrument, and V3 authorization checks."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable, TypeVar

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from services.market_data.streaming.upstox_stream import UpstoxStreamAdapter  # noqa: E402
from services.market_data.upstox import UpstoxProvider  # noqa: E402
from services.market_data.upstox_auth import UpstoxRequestError, decode_token_metadata  # noqa: E402
from services.market_data.instruments import CATALOGUE  # noqa: E402


SYMBOLS = ("RELIANCE", "INFY", "NIFTY 50", "NIFTY BANK", "SENSEX")
T = TypeVar("T")


def _timed(call: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    result = call()
    return result, round((time.perf_counter() - started) * 1000, 2)


def _failure(endpoint: str, mode: str, error: Exception, latency_ms: float) -> dict[str, object]:
    diagnostic = getattr(error, "diagnostic", None)
    if diagnostic is None and isinstance(error.__cause__, UpstoxRequestError):
        diagnostic = error.__cause__.diagnostic
    return {
        "endpoint": endpoint,
        "credential_mode": mode,
        "classification": diagnostic.classification if diagnostic else "endpoint_error",
        "http_status": diagnostic.http_status if diagnostic else None,
        "error_code": diagnostic.error_code if diagnostic else None,
        "freshness": "unavailable",
        "latency_ms": latency_ms,
        "ok": False,
    }


def _quote_freshness(timestamp: str, max_age_seconds: float) -> tuple[str, bool]:
    parsed = pd.to_datetime(timestamp, utc=True, errors="coerce")
    if pd.isna(parsed):
        return "timestamp_unavailable", True
    age = max(0.0, (datetime.now(timezone.utc) - parsed.to_pydatetime()).total_seconds())
    return f"age_seconds={round(age, 1)}", age > max_age_seconds


def _next_actions(mode: str, classifications: set[str]) -> list[str]:
    actions: list[str] = []
    if "missing" in classifications:
        actions.append("Run CONFIGURE_UPSTOX.bat and enter a newly generated Upstox Analytics Token.")
    if "expired" in classifications and mode == "access":
        actions.append("Generate a new daily OAuth access token, rerun CONFIGURE_UPSTOX.bat, then rerun VERIFY_UPSTOX_LIVE.bat.")
    if "expired" in classifications and mode == "analytics":
        actions.append("Generate a new Analytics Token in Upstox Developer Apps, save it with CONFIGURE_UPSTOX.bat, then rerun verification.")
    if "revoked_or_invalid" in classifications and mode == "analytics":
        actions.append("The Analytics Token was rejected or revoked. Generate a new Analytics Token; do not replace it with API key/secret values.")
    if "revoked_or_invalid" in classifications and mode in {"access", "explicit"}:
        actions.append("The OAuth access token was rejected. Complete OAuth again and save the new daily token locally.")
    if "permission_denied" in classifications:
        actions.append("Confirm the credential is an Upstox token with Market Quote, Historical Data, and WebSocket read permissions.")
    if "rate_limited" in classifications:
        actions.append("Wait for the broker rate-limit window to reset, then rerun verification once.")
    if classifications & {"network_error", "timeout"}:
        actions.append("Check internet, DNS, proxy, and firewall access to api.upstox.com, then rerun verification.")
    if "endpoint_error" in classifications:
        actions.append("Check the sanitized HTTP status/error code against current Upstox documentation; no response body was retained.")
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-instruments", action="store_true")
    parser.add_argument("--max-quote-age-seconds", type=float, default=300.0)
    args = parser.parse_args()

    provider = UpstoxProvider()
    stream = UpstoxStreamAdapter()
    metadata = decode_token_metadata(provider.token)
    evidence: dict[str, object] = {
        "check": "StockPilot read-only Upstox diagnostics",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "credential_mode": provider.credential_mode,
        "token_metadata": metadata.to_dict(),
        "orders_attempted": 0,
        "results": [],
    }
    results: list[dict[str, object]] = evidence["results"]  # type: ignore[assignment]

    if not provider.is_configured():
        results.append({
            "endpoint": "configuration",
            "credential_mode": provider.credential_mode,
            "classification": "missing",
            "http_status": None,
            "error_code": None,
            "freshness": "unavailable",
            "latency_ms": 0.0,
            "ok": False,
        })
    else:
        if args.refresh_instruments:
            started = time.perf_counter()
            try:
                refresh, latency = _timed(lambda: CATALOGUE.refresh_from_upstox(timeout=provider.timeout))
                results.append({
                    "endpoint": "public Upstox instrument master",
                    "rows": refresh.get("rows"),
                    "credential_mode": provider.credential_mode,
                    "classification": "valid",
                    "http_status": None,
                    "error_code": None,
                    "freshness": "catalogue_resolved",
                    "latency_ms": latency,
                    "ok": bool(refresh.get("rows")),
                })
            except Exception as error:
                results.append(_failure("public Upstox instrument master", provider.credential_mode, error, round((time.perf_counter() - started) * 1000, 2)))

        for symbol in SYMBOLS:
            started = time.perf_counter()
            try:
                instrument, latency = _timed(partial(provider._instrument, symbol))
                matched = instrument.symbol.upper() == symbol.upper() and bool(instrument.instrument_key)
                results.append({
                    "endpoint": "instrument resolution",
                    "symbol": symbol,
                    "resolved_symbol": instrument.symbol,
                    "exchange": instrument.exchange,
                    "credential_mode": provider.credential_mode,
                    "classification": "valid" if matched else "endpoint_error",
                    "http_status": None,
                    "error_code": None,
                    "freshness": "catalogue_resolved",
                    "latency_ms": latency,
                    "ok": matched,
                })
            except Exception as error:
                results.append(_failure("instrument resolution", provider.credential_mode, error, round((time.perf_counter() - started) * 1000, 2)) | {"symbol": symbol})
                continue

            started = time.perf_counter()
            try:
                quote, latency = _timed(partial(provider.get_quote, symbol))
                freshness, stale = _quote_freshness(quote.timestamp, args.max_quote_age_seconds)
                matched = quote.source == "Upstox" and quote.symbol.upper() == symbol.upper()
                quote_diagnostic = provider.last_diagnostics["quote"]
                results.append(quote_diagnostic.to_dict() | {
                    "symbol": symbol,
                    "returned_symbol": quote.symbol,
                    "source": quote.source,
                    "credential_mode": provider.credential_mode,
                    "freshness": freshness,
                    "is_stale": stale,
                    "latency_ms": latency,
                    "ok": matched and quote.price > 0 and not stale,
                })
            except Exception as error:
                results.append(_failure("/v2/market-quote/quotes", provider.credential_mode, error, round((time.perf_counter() - started) * 1000, 2)) | {"symbol": symbol})

            started = time.perf_counter()
            try:
                history, latency = _timed(
                    partial(provider.get_history, symbol, "1D", "1mo")
                )
                matched = history.attrs.get("source") == "Upstox" and str(history.attrs.get("symbol", "")).upper() == symbol.upper()
                latest_candle = history.index[-1].isoformat() if len(history.index) else None
                stale = bool(history.attrs.get("is_stale", True)) or latest_candle is None
                history_diagnostic = provider.last_diagnostics["history"]
                results.append(history_diagnostic.to_dict() | {
                    "symbol": symbol,
                    "returned_symbol": history.attrs.get("symbol"),
                    "source": history.attrs.get("source"),
                    "credential_mode": provider.credential_mode,
                    "freshness": latest_candle or "unavailable",
                    "is_stale": stale,
                    "latency_ms": latency,
                    "rows": len(history),
                    "ok": matched and not history.empty and not stale,
                })
            except Exception as error:
                results.append(_failure("/v3/historical-candle", provider.credential_mode, error, round((time.perf_counter() - started) * 1000, 2)) | {"symbol": symbol})

        started = time.perf_counter()
        try:
            websocket_url, latency = _timed(stream._authorize)
            stream_diagnostic = stream.last_diagnostic
            results.append((stream_diagnostic.to_dict() if stream_diagnostic else {}) | {
                "endpoint": "/v3/feed/market-data-feed/authorize",
                "credential_mode": stream.credential_mode,
                "freshness": "one_time_authorization_received",
                "latency_ms": latency,
                "protobuf_decoder": stream._proto is not None,
                "ok": str(websocket_url).startswith("wss://") and stream._proto is not None,
            })
            del websocket_url
        except Exception as error:
            results.append(_failure("/v3/feed/market-data-feed/authorize", stream.credential_mode, error, round((time.perf_counter() - started) * 1000, 2)))

    failures = [result for result in results if not result.get("ok")]
    classifications = {str(result.get("classification")) for result in failures}
    evidence["ok"] = not failures
    evidence["next_actions"] = _next_actions(provider.credential_mode, classifications)
    print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
