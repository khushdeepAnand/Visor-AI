"""Common contract for native broker WebSocket streaming adapters.

Every broker adapter (Upstox) implements ``StreamAdapter``
and yields ticks already normalized into StockPilot's existing quote schema,
so the rest of the app (the hub, the frontend websocket contract) never has
to know which broker is behind a symbol.

Streaming is strictly *optional*: ``LiveQuoteHub`` only uses a
``StreamAdapter`` when one is configured and its dependencies are actually
available (e.g. a real broker access token *and* a decodable feed format).
Anything short of that -- missing token, missing protobuf schema, a broken
connection -- must fail closed into the existing REST-polling loop rather
than silently emitting wrong or fabricated ticks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("stockpilot.streaming")


class StreamUnavailableError(RuntimeError):
    """Raised when a stream cannot be started at all (missing token, missing decoder, ...).

    The hub treats this as a permanent-for-this-process signal to use REST
    polling for the affected symbols; it is not retried in a tight loop.
    """


class StreamAuthExpiredError(RuntimeError):
    """Raised when the broker rejects re-authorization (token expired/revoked).

    Broker access tokens (Upstox) are not silently
    refreshable server-side -- they require the user (or an OAuth flow the
    app doesn't run unattended) to mint a new one. The hub surfaces this
    as an explicit stale/source indicator and falls back to REST polling
    rather than looping forever on a dead token.
    """


@dataclass(slots=True)
class NormalizedTick:
    """The common shape every broker adapter must produce before it reaches the hub."""

    symbol: str
    price: float
    source: str
    timestamp: str
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    depth: dict[str, Any] | None = None
    open_interest: float | None = None
    implied_volatility: float | None = None
    greeks: dict[str, float] | None = None
    backend_received_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "price": self.price,
            "source": self.source,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "previous_close": self.previous_close,
            "volume": self.volume,
            "depth": self.depth,
            "open_interest": self.open_interest,
            "implied_volatility": self.implied_volatility,
            "greeks": self.greeks,
            # Measured backend-receive time, in epoch seconds. The frontend
            # stamps its own receive time and diffs against this to report a
            # real backend->browser latency instead of a fabricated number.
            "backend_received_at": self.backend_received_at,
        }
        return payload


@dataclass(slots=True)
class BackoffPolicy:
    initial_seconds: float = 1.0
    max_seconds: float = 30.0
    multiplier: float = 2.0


def backoff_delays(policy: BackoffPolicy | None = None) -> "Callable[[], float]":
    """Returns a stateful callable producing successive backoff delays.

    Call ``next_delay()`` each time a (re)connect attempt fails; call
    ``reset()`` once a connection is established and stays up long enough to
    be considered healthy.
    """
    policy = policy or BackoffPolicy()
    current = {"value": policy.initial_seconds}

    def next_delay() -> float:
        delay = current["value"]
        current["value"] = min(delay * policy.multiplier, policy.max_seconds)
        return delay

    def reset() -> None:
        current["value"] = policy.initial_seconds

    next_delay.reset = reset  # type: ignore[attr-defined]
    return next_delay


class StreamAdapter(ABC):
    """Base class for a broker-native WebSocket market-data stream."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether credentials/decoder dependencies needed for streaming are present."""

    @abstractmethod
    async def run(
        self,
        symbols: Callable[[], set[str]],
        on_tick: Callable[[NormalizedTick], Awaitable[None]],
        stop: asyncio.Event,
    ) -> None:
        """Run the adapter until ``stop`` is set.

        ``symbols`` is called to get the *current* set of instrument symbols
        that should be subscribed -- adapters must re-diff and
        subscribe/unsubscribe as that set changes across reconnects, not just
        subscribe once at startup.

        Must raise ``StreamUnavailableError`` if it cannot even start (e.g.
        missing decoder), and ``StreamAuthExpiredError`` if the broker
        rejects the token outright. Any other connection failure should be
        retried internally using backoff until ``stop`` is set.
        """
