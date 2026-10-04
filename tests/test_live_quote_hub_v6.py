from __future__ import annotations

import asyncio

import pytest

from services.market_data.live_hub import LiveQuoteHub
from services.market_data.base import Quote


@pytest.mark.asyncio
async def test_many_subscribers_share_one_symbol_pump(monkeypatch):
    hub = LiveQuoteHub()
    calls = 0

    def quote(_symbol):
        nonlocal calls
        calls += 1
        return Quote(symbol="RELIANCE", price=2500.0, timestamp="2026-08-10T09:15:00+05:30", source="test")

    monkeypatch.setattr("services.market_data.live_hub.MANAGER.get_quote", quote)
    queues = [await hub.subscribe("RELIANCE") for _ in range(30)]
    payloads = await asyncio.gather(*(asyncio.wait_for(q.get(), timeout=2.5) for q in queues))
    assert all(item["price"] == 2500.0 for item in payloads)
    assert len(hub._tasks) == 1
    assert calls <= 2
    for queue in queues:
        await hub.unsubscribe("RELIANCE", queue)
    await hub.shutdown()
