"""Live quote fan-out for WebSocket clients.

One shared upstream feeds many browser subscribers per symbol, same as
before. What's new: if a native broker streaming adapter (see
``services.market_data.streaming``) is configured and healthy, a single
WebSocket connection to the broker feeds *every* subscribed symbol and the
per-symbol REST poller stands down. If the stream has not delivered a tick
recently (never configured, disconnected, token expired, ...), REST polling
resumes automatically for that symbol -- the hub never depends on the stream
being up.

Multi-instance leadership (so N API processes share one upstream broker
subscription via Redis) is a separate concern, layered on top of this by
``services.market_data.stream_leadership``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from contextlib import suppress
from typing import Any

from services.market_calendar import market_status
from services.market_data.streaming.base import (
    NormalizedTick,
    StreamAuthExpiredError,
    StreamUnavailableError,
)
from .manager import MANAGER
from .context import build_market_context
from .instruments import CATALOGUE
from .stream_leadership import STREAM_LEADERSHIP

logger = logging.getLogger("stockpilot.live_hub")

# How long a stream can go quiet before a symbol's REST fallback poller
# resumes actually hitting the broker's REST API again.
STREAM_STALE_AFTER_SECONDS = float(os.getenv("STOCKPILOT_STREAM_STALE_AFTER_SECONDS", "5.0"))


def _build_stream_adapter():
    provider = os.getenv("STOCKPILOT_STREAM_PROVIDER", "upstox").strip().lower()
    if provider in ("", "none", "off"):
        return None
    if provider == "upstox":
        from services.market_data.streaming.upstox_stream import UpstoxStreamAdapter

        return UpstoxStreamAdapter()
    logger.warning("Unknown STOCKPILOT_STREAM_PROVIDER=%s; native streaming disabled.", provider)
    return None


class LiveQuoteHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

        self._stream_adapter = _build_stream_adapter()
        self._stream_task: asyncio.Task | None = None
        self._stream_stop = asyncio.Event()
        self._stream_disabled_permanently = False
        self._last_tick_at: dict[str, float] = {}
        self._leadership_tasks: dict[str, asyncio.Task] = {}

    # -- streaming --------------------------------------------------------

    def _stream_is_healthy_for(self, symbol: str) -> bool:
        if self._stream_disabled_permanently or self._stream_adapter is None:
            return False
        last = self._last_tick_at.get(symbol)
        return last is not None and (time.monotonic() - last) < STREAM_STALE_AFTER_SECONDS

    async def _on_stream_tick(self, tick: NormalizedTick) -> None:
        self._last_tick_at[tick.symbol] = time.monotonic()
        payload = tick.to_dict()
        status = market_status()
        payload["market_status"] = status.get("status") or ("open" if status.get("is_open") else "closed")
        payload["stream"] = "native"
        provider_name = tick.source.strip().lower()
        provider = MANAGER.providers.get(provider_name)
        credential_mode = MANAGER._credential_mode(provider_name, provider) if provider is not None else "configured"
        payload["context"] = build_market_context(
            requested_symbol=tick.symbol,
            instrument=CATALOGUE.resolve(tick.symbol),
            provider=provider_name,
            credential_mode=credential_mode,
            timeframe="stream",
            as_of=tick.timestamp or None,
            is_live=True,
            is_stale=False,
            fallback_used=False,
            fallback_reason=None,
            provider_mode=MANAGER.provider_mode,
        )
        self._fan_out(tick.symbol, payload)
        if STREAM_LEADERSHIP.owns(tick.symbol):
            await STREAM_LEADERSHIP.publish(tick.symbol, payload)

    def _fan_out(self, symbol: str, payload: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(symbol, ())):
            if q.full():
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            with suppress(asyncio.QueueFull):
                q.put_nowait(payload)

    async def _run_stream_forever(self) -> None:
        if self._stream_adapter is None or not self._stream_adapter.is_configured():
            self._stream_disabled_permanently = True
            return
        try:
            await self._stream_adapter.run(
                symbols=lambda: STREAM_LEADERSHIP.owned_symbols(set(self._subscribers.keys())),
                on_tick=self._on_stream_tick,
                stop=self._stream_stop,
            )
        except (StreamUnavailableError, StreamAuthExpiredError) as exc:
            logger.warning(
                "Native %s stream disabled for this process: %s. Falling back to REST polling.",
                self._stream_adapter.name,
                exc,
            )
            self._stream_disabled_permanently = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Native stream task crashed unexpectedly; falling back to REST polling.")
            self._stream_disabled_permanently = True

    async def _coordinate_symbol(self, symbol: str) -> None:
        """Maintain a renewable lease or consume the elected leader's Redis ticks."""
        if not STREAM_LEADERSHIP.active:
            await STREAM_LEADERSHIP.try_acquire(symbol)
            return
        message_task: asyncio.Task | None = None

        async def consume() -> None:
            async for payload in STREAM_LEADERSHIP.messages(symbol):
                payload["stream"] = "redis_relay"
                payload["redis_relay"] = True
                self._last_tick_at[symbol] = time.monotonic()
                self._fan_out(symbol, payload)

        try:
            while symbol in self._subscribers:
                leader = await STREAM_LEADERSHIP.try_acquire(symbol)
                if leader:
                    if message_task:
                        message_task.cancel(); message_task = None
                    await STREAM_LEADERSHIP.renew(symbol)
                elif message_task is None or message_task.done():
                    message_task = asyncio.create_task(consume(), name=f"redis-ticks:{symbol}")
                await asyncio.sleep(max(0.5, STREAM_LEADERSHIP.lease_seconds / 3.0))
        except asyncio.CancelledError:
            pass
        finally:
            if message_task:
                message_task.cancel()
                await asyncio.gather(message_task, return_exceptions=True)
            await STREAM_LEADERSHIP.release(symbol)

    # -- subscription lifecycle -------------------------------------------

    async def subscribe(self, symbol: str) -> asyncio.Queue[dict[str, Any]]:
        normalized = MANAGER.normalize_symbol(symbol)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)
        async with self._lock:
            self._subscribers[normalized].add(queue)
            if normalized not in self._tasks or self._tasks[normalized].done():
                self._tasks[normalized] = asyncio.create_task(self._pump(normalized), name=f"quote:{normalized}")
            if normalized not in self._leadership_tasks or self._leadership_tasks[normalized].done():
                self._leadership_tasks[normalized] = asyncio.create_task(self._coordinate_symbol(normalized), name=f"leader:{normalized}")
            if self._stream_adapter is not None and self._stream_task is None:
                self._stream_task = asyncio.create_task(self._run_stream_forever(), name="quote-stream")
        return queue

    async def unsubscribe(self, symbol: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        normalized = MANAGER.normalize_symbol(symbol)
        async with self._lock:
            self._subscribers[normalized].discard(queue)
            if not self._subscribers[normalized]:
                task = self._tasks.pop(normalized, None)
                self._subscribers.pop(normalized, None)
                self._last_tick_at.pop(normalized, None)
                if task:
                    task.cancel()
                leader_task = self._leadership_tasks.pop(normalized, None)
                if leader_task:
                    leader_task.cancel()

    async def _pump(self, symbol: str) -> None:
        try:
            while True:
                if STREAM_LEADERSHIP.active and not STREAM_LEADERSHIP.owns(symbol):
                    await asyncio.sleep(0.25)
                    continue
                if self._stream_is_healthy_for(symbol):
                    # The native stream is actively delivering ticks for this
                    # symbol -- don't also hammer the broker's REST API.
                    await asyncio.sleep(0.5)
                    continue
                try:
                    quote = await asyncio.to_thread(MANAGER.get_quote, symbol)
                    payload = quote.to_dict()
                    status = market_status()
                    payload["market_status"] = status.get("status") or ("open" if status.get("is_open") else "closed")
                    payload["stream"] = "rest_poll"
                except Exception as exc:
                    payload = {"symbol": symbol, "error": "Market data is temporarily unavailable.", "stream": "rest_poll"}
                self._fan_out(symbol, payload)
                if STREAM_LEADERSHIP.owns(symbol):
                    await STREAM_LEADERSHIP.publish(symbol, payload)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    def health(self) -> dict[str, Any]:
        adapter_configured = bool(self._stream_adapter is not None and self._stream_adapter.is_configured())
        active_native = adapter_configured and not self._stream_disabled_permanently and self._stream_task is not None and not self._stream_task.done()
        return {
            "provider": getattr(self._stream_adapter, "name", None),
            "adapter_configured": adapter_configured,
            "native_stream_active": active_native,
            "rest_fallback": not active_native,
            "subscribed_symbols": sorted(self._subscribers.keys()),
            "recent_native_symbols": sorted(symbol for symbol in self._last_tick_at if self._stream_is_healthy_for(symbol)),
            "leadership": STREAM_LEADERSHIP.health(),
        }

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values()) + list(self._leadership_tasks.values())
            self._tasks.clear()
            self._leadership_tasks.clear()
            self._subscribers.clear()
        self._stream_stop.set()
        if self._stream_task:
            tasks.append(self._stream_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await STREAM_LEADERSHIP.close()


LIVE_QUOTE_HUB = LiveQuoteHub()
