from __future__ import annotations

import asyncio
import time

import pytest

from services.market_data.base import Quote
from services.market_data.live_hub import LiveQuoteHub, STREAM_STALE_AFTER_SECONDS
from services.market_data.streaming.base import NormalizedTick


@pytest.mark.asyncio
async def test_healthy_stream_suppresses_rest_polling(monkeypatch):
    hub = LiveQuoteHub()

    async def _stream_never_disables():
        await asyncio.sleep(3600)  # pretend the stream task is alive and healthy

    hub._run_stream_forever = _stream_never_disables  # type: ignore[method-assign]

    rest_calls = 0

    def quote(_symbol):
        nonlocal rest_calls
        rest_calls += 1
        return Quote(symbol="RELIANCE", price=1.0, timestamp="t", source="test")

    monkeypatch.setattr("services.market_data.live_hub.MANAGER.get_quote", quote)

    # Pre-seed a healthy stream tick *before* subscribing, so the pump's very
    # first iteration already sees the stream as healthy (avoids a race with
    # the pump task's first loop iteration).
    hub._last_tick_at["RELIANCE"] = time.monotonic()
    queue = await hub.subscribe("RELIANCE")

    await asyncio.sleep(1.2)  # long enough for >=1 REST pump cycle if it weren't suppressed
    assert rest_calls == 0

    await hub.unsubscribe("RELIANCE", queue)
    await hub.shutdown()


@pytest.mark.asyncio
async def test_stale_stream_falls_back_to_rest_polling(monkeypatch):
    hub = LiveQuoteHub()
    rest_calls = 0

    def quote(_symbol):
        nonlocal rest_calls
        rest_calls += 1
        return Quote(symbol="RELIANCE", price=1.0, timestamp="t", source="test")

    monkeypatch.setattr("services.market_data.live_hub.MANAGER.get_quote", quote)

    queue = await hub.subscribe("RELIANCE")
    # A tick from long ago -- past STREAM_STALE_AFTER_SECONDS -- must not
    # suppress REST polling.
    hub._last_tick_at["RELIANCE"] = time.monotonic() - (STREAM_STALE_AFTER_SECONDS + 5)

    payload = await asyncio.wait_for(queue.get(), timeout=2.5)
    assert payload["price"] == 1.0
    assert payload["stream"] == "rest_poll"
    assert rest_calls >= 1

    await hub.unsubscribe("RELIANCE", queue)
    await hub.shutdown()


@pytest.mark.asyncio
async def test_on_stream_tick_fans_out_to_subscribers_without_touching_rest(monkeypatch):
    hub = LiveQuoteHub()
    rest_calls = 0

    def quote(_symbol):
        nonlocal rest_calls
        rest_calls += 1
        return Quote(symbol="RELIANCE", price=999.0, timestamp="t", source="test")

    monkeypatch.setattr("services.market_data.live_hub.MANAGER.get_quote", quote)

    queue = await hub.subscribe("RELIANCE")
    tick = NormalizedTick(symbol="RELIANCE", price=2500.5, source="Upstox", timestamp="2026-08-10T10:00:00+05:30")
    await hub._on_stream_tick(tick)

    payload = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert payload["price"] == 2500.5
    assert payload["source"] == "Upstox"
    assert payload["stream"] == "native"
    assert payload["context"]["requested_symbol"] == "RELIANCE"
    assert payload["context"]["provider"] == "upstox"
    assert payload["context"]["timeframe"] == "stream"
    assert payload["context"]["as_of"] == "2026-08-10T10:00:00+05:30"
    assert "backend_received_at" in payload  # real measured time, not a fabricated latency claim
    assert rest_calls == 0

    await hub.unsubscribe("RELIANCE", queue)
    await hub.shutdown()


@pytest.mark.asyncio
async def test_stream_never_configured_disables_permanently_and_rest_still_works(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    hub = LiveQuoteHub()

    def quote(_symbol):
        return Quote(symbol="RELIANCE", price=42.0, timestamp="t", source="test")

    monkeypatch.setattr("services.market_data.live_hub.MANAGER.get_quote", quote)

    queue = await hub.subscribe("RELIANCE")
    payload = await asyncio.wait_for(queue.get(), timeout=2.5)
    assert payload["price"] == 42.0
    assert hub._stream_disabled_permanently is True

    await hub.unsubscribe("RELIANCE", queue)
    await hub.shutdown()
