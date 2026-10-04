from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.market_data.streaming.base import (
    NormalizedTick,
    StreamAuthExpiredError,
    StreamUnavailableError,
    backoff_delays,
)
from services.market_data.streaming.upstox_stream import UpstoxStreamAdapter


# ---------------------------------------------------------------------------
# Backoff policy
# ---------------------------------------------------------------------------

def test_backoff_delays_grow_and_cap_then_reset():
    next_delay = backoff_delays()
    seen = [next_delay() for _ in range(6)]
    assert seen == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]  # caps at max_seconds=30
    next_delay.reset()
    assert next_delay() == 1.0


# ---------------------------------------------------------------------------
# Configuration gating -- must fail closed, never guess a decoder
# ---------------------------------------------------------------------------

def test_not_configured_without_token(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    adapter = UpstoxStreamAdapter(token=None)
    assert adapter.is_configured() is False


def test_not_configured_without_proto_module(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKPILOT_UPSTOX_PROTO_MODULE", str(tmp_path / "does-not-exist.py"))
    adapter = UpstoxStreamAdapter(token="fake-token")
    assert adapter._proto is None
    assert adapter.is_configured() is False


def test_configured_when_token_and_decoder_present(monkeypatch, tmp_path):
    proto_file = tmp_path / "fake_pb2.py"
    proto_file.write_text("class FeedResponse:\n    pass\n")
    monkeypatch.setenv("STOCKPILOT_UPSTOX_PROTO_MODULE", str(proto_file))
    adapter = UpstoxStreamAdapter(token="fake-token")
    assert adapter.is_configured() is True


def test_run_raises_stream_unavailable_when_not_configured(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    adapter = UpstoxStreamAdapter(token=None)

    async def go():
        with pytest.raises(StreamUnavailableError):
            await adapter.run(lambda: set(), AsyncMock(), asyncio.Event())

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Authorize flow
# ---------------------------------------------------------------------------

def test_authorize_success_returns_wss_url():
    adapter = UpstoxStreamAdapter(token="fake-token")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"data": {"authorized_redirect_uri": "wss://example.invalid/feed"}}
    with patch("requests.get", return_value=fake_response):
        assert adapter._authorize() == "wss://example.invalid/feed"


def test_authorize_401_raises_auth_expired():
    adapter = UpstoxStreamAdapter(token="stale-token")
    fake_response = MagicMock()
    fake_response.status_code = 401
    with patch("requests.get", return_value=fake_response):
        with pytest.raises(StreamAuthExpiredError):
            adapter._authorize()


def test_authorize_network_failure_raises_unavailable():
    import requests as requests_module

    adapter = UpstoxStreamAdapter(token="fake-token")
    with patch("requests.get", side_effect=requests_module.ConnectionError("no route")):
        with pytest.raises(StreamUnavailableError):
            adapter._authorize()


def test_authorize_without_token_raises_unavailable(monkeypatch):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    adapter = UpstoxStreamAdapter(token=None)
    with pytest.raises(StreamUnavailableError):
        adapter._authorize()


# ---------------------------------------------------------------------------
# Subscribe message framing
# ---------------------------------------------------------------------------

def test_subscribe_message_shape():
    raw = UpstoxStreamAdapter._subscribe_message(["NSE_EQ|INE002A01018"], "sub", mode="full")
    payload = json.loads(raw.decode("utf-8"))
    assert payload["method"] == "sub"
    assert payload["data"] == {"mode": "full", "instrumentKeys": ["NSE_EQ|INE002A01018"]}
    assert "guid" in payload


# ---------------------------------------------------------------------------
# Decode glue -- exercised against a duck-typed fake message, since the real
# schema is only available once an operator drops in Upstox's generated file.
# ---------------------------------------------------------------------------

class _FakeLtpc:
    def __init__(self, ltp: float, cp: float, ltt: str = "1700000000000"):
        self.ltp = ltp
        self.cp = cp
        self.ltt = ltt


class _FakeMarketFF:
    def __init__(self, ltpc: _FakeLtpc):
        self.ltpc = ltpc


class _FakeFullFeed:
    def __init__(self, ltpc: _FakeLtpc):
        self.marketFF = _FakeMarketFF(ltpc)


class _FakeFeed:
    def __init__(self, ltpc: _FakeLtpc):
        self.ltpc = ltpc
        self.fullFeed = _FakeFullFeed(ltpc)

    def HasField(self, name: str) -> bool:
        return name == "fullFeed"


class _FakeFeedResponse:
    def __init__(self, feeds: dict):
        self.feeds = feeds

    def ParseFromString(self, raw: bytes) -> None:
        pass  # test injects self.feeds directly; no real wire decoding needed


def test_decode_maps_instrument_keys_to_symbols_and_normalizes():
    adapter = UpstoxStreamAdapter(token="fake-token")
    fake_response = _FakeFeedResponse({"NSE_EQ|INE002A01018": _FakeFeed(_FakeLtpc(ltp=2500.5, cp=2480.0))})
    adapter._proto = SimpleNamespace(FeedResponse=lambda: fake_response)

    ticks = adapter._decode(b"irrelevant", {"NSE_EQ|INE002A01018": "RELIANCE"})
    assert len(ticks) == 1
    tick = ticks[0]
    assert isinstance(tick, NormalizedTick)
    assert tick.symbol == "RELIANCE"
    assert tick.source == "Upstox"
    assert tick.price == 2500.5
    assert tick.previous_close == 2480.0
    assert tick.change == pytest.approx(20.5)


def test_decode_skips_unknown_instrument_keys():
    adapter = UpstoxStreamAdapter(token="fake-token")
    fake_response = _FakeFeedResponse({"NSE_EQ|UNKNOWN": _FakeFeed(_FakeLtpc(ltp=100.0, cp=99.0))})
    adapter._proto = SimpleNamespace(FeedResponse=lambda: fake_response)

    ticks = adapter._decode(b"irrelevant", {})  # empty mapping -- nothing resolves
    assert ticks == []


def test_decode_without_a_loaded_proto_raises_unavailable():
    adapter = UpstoxStreamAdapter(token="fake-token")
    adapter._proto = None
    with pytest.raises(StreamUnavailableError):
        adapter._decode(b"irrelevant", {})


# ---------------------------------------------------------------------------
# Reconnect/backoff and resubscribe behavior of run()
# ---------------------------------------------------------------------------

class _FakeWebSocket:
    """Minimal async context-manager fake standing in for a websockets connection."""

    def __init__(self, recv_values):
        self._recv_values = list(recv_values)
        self.sent: list[bytes] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self):
        if not self._recv_values:
            await asyncio.sleep(3600)  # block "forever" so the test can stop() it
        return self._recv_values.pop(0)


def test_run_subscribes_then_decodes_and_calls_on_tick(monkeypatch):
    proto_file_dir = None  # not needed -- we inject adapter._proto directly

    adapter = UpstoxStreamAdapter(token="fake-token")
    fake_response = _FakeFeedResponse({"NSE_EQ|INE002A01018": _FakeFeed(_FakeLtpc(ltp=101.0, cp=100.0))})
    adapter._proto = SimpleNamespace(FeedResponse=lambda: fake_response)

    fake_ws = _FakeWebSocket(recv_values=[b"tick-1"])
    ticks_received: list[NormalizedTick] = []

    async def on_tick(tick: NormalizedTick) -> None:
        ticks_received.append(tick)
        stop.set()  # end the test as soon as one tick round-trips

    def fake_connect(url, max_size=None):
        return fake_ws

    class _FakeCatalogueItem:
        instrument_key = "NSE_EQ|INE002A01018"

    async def go():
        with patch.object(adapter, "_authorize", return_value="wss://example.invalid/feed"):
            with patch("websockets.connect", side_effect=fake_connect):
                with patch(
                    "services.market_data.instruments.CATALOGUE.resolve",
                    return_value=_FakeCatalogueItem(),
                ):
                    await asyncio.wait_for(
                        adapter.run(lambda: {"RELIANCE"}, on_tick, stop), timeout=2.0
                    )

    stop = asyncio.Event()
    asyncio.run(go())

    assert len(ticks_received) == 1
    assert ticks_received[0].symbol == "RELIANCE"
    assert ticks_received[0].price == 101.0
    sent_sub = json.loads(fake_ws.sent[0].decode("utf-8"))
    assert sent_sub["method"] == "sub"
    assert sent_sub["data"]["instrumentKeys"] == ["NSE_EQ|INE002A01018"]


def test_run_does_not_retry_on_auth_expired(monkeypatch):
    adapter = UpstoxStreamAdapter(token="fake-token")
    adapter._proto = SimpleNamespace(FeedResponse=lambda: _FakeFeedResponse({}))

    async def go():
        with patch.object(adapter, "_authorize", side_effect=StreamAuthExpiredError("expired")):
            with pytest.raises(StreamAuthExpiredError):
                await adapter.run(lambda: set(), AsyncMock(), asyncio.Event())

    asyncio.run(go())


def test_run_reconnects_with_backoff_after_a_dropped_connection(monkeypatch):
    adapter = UpstoxStreamAdapter(token="fake-token")
    adapter._proto = SimpleNamespace(FeedResponse=lambda: _FakeFeedResponse({}))

    attempts = {"count": 0}
    stop = asyncio.Event()

    def flaky_connect(url, max_size=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("dropped")
        stop.set()
        return _FakeWebSocket(recv_values=[])

    async def go():
        with patch.object(adapter, "_authorize", return_value="wss://example.invalid/feed"):
            with patch("websockets.connect", side_effect=flaky_connect):
                await asyncio.wait_for(
                    adapter.run(lambda: set(), AsyncMock(), stop), timeout=3.0
                )

    asyncio.run(go())
    assert attempts["count"] == 2  # first attempt failed, second succeeded and set stop


def test_stream_prefers_analytics_token_when_both_are_present(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "daily-access")
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "long-lived-analytics")
    adapter = UpstoxStreamAdapter()
    assert adapter.token == "long-lived-analytics"
    assert adapter.credential_mode == "analytics"
    assert adapter.is_configured() is True


def test_bundled_official_proto_decoder_round_trip():
    adapter = UpstoxStreamAdapter(token="fake-token")
    assert adapter._proto is not None
    msg = adapter._proto.FeedResponse()
    feed = msg.feeds["NSE_EQ|INE002A01018"]
    feed.ltpc.ltp = 2500.5
    feed.ltpc.cp = 2480.0
    feed.ltpc.ltt = 1700000000000
    raw = msg.SerializeToString()
    ticks = adapter._decode(raw, {"NSE_EQ|INE002A01018": "RELIANCE"})
    assert len(ticks) == 1
    assert ticks[0].symbol == "RELIANCE"
    assert ticks[0].price == 2500.5
    assert ticks[0].previous_close == 2480.0
