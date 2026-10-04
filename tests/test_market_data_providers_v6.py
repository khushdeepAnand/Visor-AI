from __future__ import annotations

import pandas as pd
import pytest

from services.market_data.base import Instrument, ProviderUnavailableError
from services.market_data.demo import DemoIndianProvider
from services.market_data.upstox import UpstoxProvider
from services.market_data.licensed_vendor import TrueDataProvider


class Response:
    def __init__(self, payload): self.payload=payload; self.text=""; self.status_code=200; self.headers={}
    def raise_for_status(self): return None
    def json(self): return self.payload


def test_demo_provider_contract():
    p=DemoIndianProvider(); q=p.get_quote("RELIANCE"); h=p.get_history("RELIANCE","5m","1mo")
    assert q.symbol == "RELIANCE" and q.price > 0 and q.depth
    assert list(h.columns) == ["Open","High","Low","Close","Volume"]
    assert h.index.is_monotonic_increasing and not h.empty


def test_upstox_history_contract(monkeypatch):
    p=UpstoxProvider(token="jwt")
    instrument=Instrument(symbol="RELIANCE",name="Reliance",exchange="NSE",segment="NSE_EQ",instrument_type="EQ",instrument_key="NSE_EQ|INE002A01018")
    monkeypatch.setattr(p,"_instrument",lambda symbol:instrument)
    payload={"data":{"candles":[["2026-01-05T09:15:00+05:30",100,102,99,101,1000,0],["2026-01-05T09:20:00+05:30",101,103,100,102,1200,0]]}}
    monkeypatch.setattr("services.market_data.upstox_auth.requests.get",lambda *a,**k:Response(payload))
    h=p.get_history("RELIANCE","5m","1w")
    assert len(h)==2 and h.iloc[-1].Close==102 and h.attrs["source"]=="Upstox"


@pytest.mark.live
def test_live_provider_smoke_is_manual():
    pytest.skip("Requires a user broker token and live network; run manually after credentials are configured.")


def test_upstox_prefers_long_lived_analytics_token_for_read_only_market_data(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "daily-access")
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "long-lived-analytics")
    p = UpstoxProvider()
    assert p.token == "long-lived-analytics"
    assert p.credential_mode == "analytics"
    assert p.headers["Authorization"] == "Bearer long-lived-analytics"
    assert p.access_headers["Authorization"] == "Bearer daily-access"
    health = p.health()
    assert health["configured"] is True
    assert health["credential_mode"] == "analytics"


def test_licensed_vendor_quote_requires_vendor_timestamp(monkeypatch):
    monkeypatch.setenv("TRUEDATA_GATEWAY_URL", "https://feed.example")
    monkeypatch.setenv("TRUEDATA_GATEWAY_TOKEN", "secret")
    provider = TrueDataProvider()
    monkeypatch.setattr(
        "services.market_data.licensed_vendor.requests.get",
        lambda *args, **kwargs: Response({"data": {"price": 123.45}}),
    )

    with pytest.raises(ProviderUnavailableError, match="vendor timestamp"):
        provider.get_quote("RELIANCE")


def test_licensed_vendor_normalizes_timestamp_and_does_not_claim_streaming(monkeypatch):
    monkeypatch.setenv("TRUEDATA_GATEWAY_URL", "https://feed.example")
    monkeypatch.setenv("TRUEDATA_GATEWAY_TOKEN", "secret")
    provider = TrueDataProvider()
    monkeypatch.setattr(
        "services.market_data.licensed_vendor.requests.get",
        lambda *args, **kwargs: Response({"data": {"price": 123.45, "timestamp": "2026-09-25T09:15:00+05:30"}}),
    )

    quote = provider.get_quote("RELIANCE")
    assert quote.timestamp == "2026-09-25T03:45:00+00:00"
    assert provider.health()["supports_live_stream"] is False
