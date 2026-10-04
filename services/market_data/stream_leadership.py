"""Redis-backed stream leadership for multi-worker deployments.

When Redis is disabled/unavailable StockPilot keeps its single-process behavior.
With Redis active, a short renewable lease is held per normalized symbol. Only
that process includes the symbol in its upstream broker subscription; normalized
ticks are published to Redis for follower workers.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from contextlib import suppress
from typing import Any, AsyncIterator


class StreamLeadership:
    def __init__(self, *, client: Any | None = None, lease_seconds: float | None = None) -> None:
        self.enabled = os.getenv("STOCKPILOT_REDIS_STREAM_LEADERSHIP", "false").lower() in {"1", "true", "yes", "on"}
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        lease_value: float | str = (
            lease_seconds
            if lease_seconds is not None
            else os.getenv("STOCKPILOT_STREAM_LEASE_SECONDS") or "6"
        )
        self.lease_seconds = max(2.0, float(lease_value))
        self.identity = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
        self._client = client
        self.error: str | None = None
        self._owned: set[str] = set()
        if client is not None:
            self.enabled = True
        elif self.enabled:
            try:
                import redis.asyncio as redis_async
                self._client = redis_async.Redis.from_url(self.url, decode_responses=True)
            except Exception as exc:
                self.error = str(exc)
                self._client = None

    @property
    def active(self) -> bool:
        return self.enabled and self._client is not None

    def owns(self, symbol: str) -> bool:
        return not self.active or symbol in self._owned

    def owned_symbols(self, candidates: set[str]) -> set[str]:
        return set(candidates) if not self.active else set(candidates).intersection(self._owned)

    @staticmethod
    def _lease_key(symbol: str) -> str:
        return f"sp:stream:leader:{symbol}"

    @staticmethod
    def _channel(symbol: str) -> str:
        return f"sp:stream:tick:{symbol}"

    async def try_acquire(self, symbol: str) -> bool:
        client = self._client
        if not self.enabled or client is None:
            self._owned.add(symbol)
            return True
        try:
            ok = await client.set(self._lease_key(symbol), self.identity, nx=True, px=int(self.lease_seconds * 1000))
            if ok:
                self._owned.add(symbol)
                return True
            current = await client.get(self._lease_key(symbol))
            if current == self.identity:
                self._owned.add(symbol)
                return await self.renew(symbol)
            self._owned.discard(symbol)
            return False
        except Exception as exc:
            self.error = str(exc)
            self._owned.add(symbol)
            return True

    async def renew(self, symbol: str) -> bool:
        client = self._client
        if not self.enabled or client is None:
            self._owned.add(symbol)
            return True
        try:
            key = self._lease_key(symbol)
            current = await client.get(key)
            if current != self.identity:
                self._owned.discard(symbol)
                return False
            await client.pexpire(key, int(self.lease_seconds * 1000))
            self._owned.add(symbol)
            return True
        except Exception as exc:
            self.error = str(exc)
            self._owned.add(symbol)
            return True

    async def release(self, symbol: str) -> None:
        self._owned.discard(symbol)
        client = self._client
        if not self.enabled or client is None:
            return
        try:
            key = self._lease_key(symbol)
            if await client.get(key) == self.identity:
                await client.delete(key)
        except Exception as exc:
            self.error = str(exc)

    async def publish(self, symbol: str, payload: dict[str, Any]) -> None:
        client = self._client
        if not self.enabled or client is None:
            return
        try:
            await client.publish(self._channel(symbol), json.dumps(payload, default=str))
        except Exception as exc:
            self.error = str(exc)

    async def messages(self, symbol: str) -> AsyncIterator[dict[str, Any]]:
        client = self._client
        if not self.enabled or client is None:
            return
        pubsub = client.pubsub()
        await pubsub.subscribe(self._channel(symbol))
        try:
            while True:
                item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if item and item.get("data"):
                    raw = item["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        parsed = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(parsed, dict):
                        yield parsed
                await asyncio.sleep(0.05)
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(self._channel(symbol))
            with suppress(Exception):
                await pubsub.close()

    async def close(self) -> None:
        for symbol in list(self._owned):
            await self.release(symbol)
        client = self._client
        if self.enabled and client is not None and hasattr(client, "aclose"):
            with suppress(Exception):
                await client.aclose()

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self.active,
            "identity": self.identity if self.active else None,
            "owned_symbols": sorted(self._owned),
            "lease_seconds": self.lease_seconds,
            "error": self.error,
        }


STREAM_LEADERSHIP = StreamLeadership()
