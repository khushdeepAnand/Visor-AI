"""Upstox V3 Market Data Feed streaming adapter.

Real connection flow (per Upstox's V3 Market Data Feed docs):
  1. ``GET /v3/feed/market-data-feed/authorize`` with the bearer access token
     returns a one-time ``wss://`` URL.
  2. Connect to that URL with a WebSocket client.
  3. Send the JSON control payload encoded as a binary WebSocket message, as
     required by the V3 documentation.
  4. Incoming market ticks are Protobuf-encoded per Upstox's published
     ``MarketDataFeedV3.proto`` schema.

Decoding is intentionally based only on Upstox's official V3 schema. Drop the
official generated
``MarketDataFeedV3_pb2.py`` (from the .proto file linked in their docs) at
the path in ``STOCKPILOT_UPSTOX_PROTO_MODULE`` (default:
``services/market_data/streaming/upstox_proto/MarketDataFeedV3_pb2.py``) and
this adapter will use it automatically. Until that file is present,
``is_configured()`` returns False and the hub uses REST polling instead --
correct behavior, not a missing feature.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .base import (
    BackoffPolicy,
    NormalizedTick,
    StreamAdapter,
    StreamAuthExpiredError,
    StreamUnavailableError,
    backoff_delays,
)
from ..upstox_auth import UpstoxDiagnostic, UpstoxRequestError, request_json

logger = logging.getLogger("stockpilot.streaming.upstox")

_DEFAULT_PROTO_MODULE_PATH = (
    Path(__file__).resolve().parent / "upstox_proto" / "MarketDataFeedV3_pb2.py"
)


def _load_proto_decoder() -> Any | None:
    """Load Upstox's official generated protobuf module if an operator dropped it in.

    Returns the module (with a ``FeedResponse`` message class) or None if it
    isn't present -- never a hand-rolled guess at the schema.
    """
    override = os.getenv("STOCKPILOT_UPSTOX_PROTO_MODULE")
    path = Path(override) if override else _DEFAULT_PROTO_MODULE_PATH
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("stockpilot_upstox_feed_pb2", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "FeedResponse"):
            logger.warning("Upstox proto module at %s has no FeedResponse message; ignoring.", path)
            return None
        return module
    except Exception:
        logger.exception("Failed to load Upstox protobuf decoder from %s", path)
        return None


class UpstoxStreamAdapter(StreamAdapter):
    name = "upstox"
    authorize_url = "https://api.upstox.com/v3/feed/market-data-feed/authorize"

    def __init__(self, token: str | None = None, timeout: float = 10.0) -> None:
        # Prefer the long-lived read-only Analytics Token for Market Data Feed
        # V3. Upstox documents WebSocket market data as supported by this token.
        self.token = token or os.getenv("UPSTOX_ANALYTICS_TOKEN") or os.getenv("UPSTOX_ACCESS_TOKEN")
        self.credential_mode = (
            "explicit" if token else "analytics" if os.getenv("UPSTOX_ANALYTICS_TOKEN") else "access" if os.getenv("UPSTOX_ACCESS_TOKEN") else "none"
        )
        self.timeout = timeout
        self._proto = _load_proto_decoder()
        self.last_diagnostic: UpstoxDiagnostic | None = None

    def is_configured(self) -> bool:
        return bool(self.token) and self._proto is not None

    def _authorize(self) -> str:
        try:
            payload, diagnostic = request_json(
                endpoint="/v3/feed/market-data-feed/authorize",
                url=self.authorize_url,
                token=self.token,
                timeout=self.timeout,
            )
            self.last_diagnostic = diagnostic
            data = payload.get("data", {})
            wss_url = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        except UpstoxRequestError as exc:
            self.last_diagnostic = exc.diagnostic
            if exc.diagnostic.classification in {"expired", "revoked_or_invalid"}:
                raise StreamAuthExpiredError(str(exc)) from exc
            raise StreamUnavailableError(str(exc)) from exc
        if not wss_url:
            raise StreamUnavailableError("Upstox authorize response had no websocket URL.")
        return wss_url

    def _decode(self, raw: bytes, instrument_to_symbol: dict[str, str]) -> list[NormalizedTick]:
        if self._proto is None:
            raise StreamUnavailableError("No protobuf decoder loaded for the Upstox feed.")
        message = self._proto.FeedResponse()
        message.ParseFromString(raw)
        ticks: list[NormalizedTick] = []
        for instrument_key, feed in message.feeds.items():
            symbol = instrument_to_symbol.get(instrument_key)
            if symbol is None:
                continue

            ltpc = None
            volume = None
            oi = None
            iv = None
            depth = None
            greeks = None
            open_price = high = low = None

            if feed.HasField("fullFeed"):
                full_feed = feed.fullFeed
                # NSE/BSE equity/F&O full feed. Index feeds expose indexFF instead.
                has_market_ff = full_feed.HasField("marketFF") if hasattr(full_feed, "HasField") else hasattr(full_feed, "marketFF")
                has_index_ff = full_feed.HasField("indexFF") if hasattr(full_feed, "HasField") else hasattr(full_feed, "indexFF")
                if has_market_ff:
                    full = full_feed.marketFF
                    ltpc = full.ltpc
                    volume = float(full.vtt) if getattr(full, "vtt", 0) else None
                    oi = float(full.oi) if getattr(full, "oi", 0) else None
                    iv = float(full.iv) if getattr(full, "iv", 0) else None
                    levels = getattr(getattr(full, "marketLevel", None), "bidAskQuote", [])
                    if levels:
                        depth = {
                            "bids": [[float(level.bidP), int(level.bidQ)] for level in levels if getattr(level, "bidP", 0)],
                            "asks": [[float(level.askP), int(level.askQ)] for level in levels if getattr(level, "askP", 0)],
                        }
                    og = getattr(full, "optionGreeks", None)
                    if og is not None:
                        greeks = {name: float(getattr(og, name)) for name in ("delta", "theta", "gamma", "vega", "rho")}
                    for candle in getattr(getattr(full, "marketOHLC", None), "ohlc", []):
                        if getattr(candle, "interval", "") == "1d":
                            open_price, high, low = float(candle.open), float(candle.high), float(candle.low)
                            break
                elif has_index_ff:
                    full = full_feed.indexFF
                    ltpc = full.ltpc
                    for candle in getattr(getattr(full, "marketOHLC", None), "ohlc", []):
                        if getattr(candle, "interval", "") == "1d":
                            open_price, high, low = float(candle.open), float(candle.high), float(candle.low)
                            break
            elif feed.HasField("firstLevelWithGreeks"):
                first = feed.firstLevelWithGreeks
                ltpc = first.ltpc
                volume = float(first.vtt) if getattr(first, "vtt", 0) else None
                oi = float(first.oi) if getattr(first, "oi", 0) else None
                iv = float(first.iv) if getattr(first, "iv", 0) else None
                d = getattr(first, "firstDepth", None)
                if d is not None:
                    depth = {
                        "bids": [[float(d.bidP), int(d.bidQ)]] if getattr(d, "bidP", 0) else [],
                        "asks": [[float(d.askP), int(d.askQ)]] if getattr(d, "askP", 0) else [],
                    }
                og = getattr(first, "optionGreeks", None)
                if og is not None:
                    greeks = {name: float(getattr(og, name)) for name in ("delta", "theta", "gamma", "vega", "rho")}
            elif feed.HasField("ltpc"):
                ltpc = feed.ltpc

            if ltpc is None:
                continue
            price = float(ltpc.ltp)
            if price <= 0:
                continue
            previous = float(ltpc.cp) if ltpc.cp else price
            ticks.append(NormalizedTick(
                symbol=symbol, price=price, source="Upstox",
                timestamp=self._timestamp(ltpc.ltt),
                change=price - previous,
                change_pct=((price / previous) - 1) * 100 if previous else None,
                open=open_price, high=high, low=low, previous_close=previous,
                volume=volume, depth=depth, open_interest=oi,
                implied_volatility=iv, greeks=greeks,
            ))
        return ticks

    @staticmethod
    def _subscribe_message(instrument_keys: list[str], method: str, mode: str = "full") -> bytes:
        return json.dumps(
            {"guid": str(uuid.uuid4()), "method": method, "data": {"mode": mode, "instrumentKeys": instrument_keys}}
        ).encode("utf-8")

    async def run(
        self,
        symbols: Callable[[], set[str]],
        on_tick: Callable[[NormalizedTick], Awaitable[None]],
        stop: asyncio.Event,
    ) -> None:
        if not self.is_configured():
            raise StreamUnavailableError(
                "Upstox streaming is not configured: missing access token or protobuf decoder module."
            )
        from services.market_data.instruments import CATALOGUE

        import websockets

        next_delay = backoff_delays(BackoffPolicy())
        while not stop.is_set():
            try:
                wss_url = self._authorize()
                async with websockets.connect(wss_url, max_size=2**22) as ws:
                    next_delay.reset()  # type: ignore[attr-defined]
                    subscribed: set[str] = set()
                    instrument_to_symbol: dict[str, str] = {}
                    while not stop.is_set():
                        wanted = set(symbols())
                        instrument_to_symbol = {}
                        keys_for: dict[str, str] = {}
                        for sym in wanted:
                            item = CATALOGUE.resolve(sym)
                            if item and item.instrument_key:
                                keys_for[sym] = item.instrument_key
                                instrument_to_symbol[item.instrument_key] = sym
                        wanted_keys = set(keys_for.values())
                        to_add = wanted_keys - subscribed
                        to_remove = subscribed - wanted_keys
                        if to_add:
                            await ws.send(self._subscribe_message(sorted(to_add), "sub"))
                        if to_remove:
                            await ws.send(self._subscribe_message(sorted(to_remove), "unsub"))
                        subscribed = wanted_keys

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            continue  # loop back around to re-check the wanted symbol set
                        if isinstance(raw, str):
                            continue  # heartbeat/control frames are not ticks
                        for tick in self._decode(raw, instrument_to_symbol):
                            await on_tick(tick)
            except StreamAuthExpiredError:
                raise  # not retryable without a human minting a new token
            except StreamUnavailableError:
                raise  # missing decoder etc. -- not retryable
            except Exception as exc:
                delay = next_delay()
                logger.warning("Upstox stream disconnected (%s); reconnecting in %.1fs", exc, delay)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    continue
    @staticmethod
    def _timestamp(value: object) -> str:
        if value in (None, ""):
            return ""
        try:
            epoch = float(str(value))
            if epoch > 10_000_000_000:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except ValueError:
                return ""
