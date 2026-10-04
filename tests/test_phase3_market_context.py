from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from services.market_data.base import Instrument, MarketDataError, Quote
from services.market_data.context import ProviderMode
from services.market_data.instruments import InstrumentCatalogue
from services.market_data.manager import ProviderManager


REQUIRED_CONTEXT = {
    "requested_symbol",
    "resolved_instrument_key",
    "exchange",
    "instrument_type",
    "provider",
    "credential_mode",
    "timeframe",
    "as_of",
    "received_at",
    "is_live",
    "is_stale",
    "fallback_used",
    "fallback_reason",
    "request_id",
}


class FakeProvider:
    def __init__(self, name: str, *, error: str | None = None) -> None:
        self.name = name
        self.error = error
        self.credential_mode = "fixture"
        self.quote_calls = 0
        self.history_calls = 0

    def is_configured(self) -> bool:
        return True

    def get_quote(self, symbol: str) -> Quote:
        self.quote_calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return Quote(symbol=symbol, price=100.0, timestamp="2026-08-30T09:15:00+05:30", source=self.name)

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        self.history_calls += 1
        if self.error:
            raise RuntimeError(self.error)
        index = pd.date_range("2026-08-28", periods=3, freq="D", tz="Asia/Kolkata")
        frame = pd.DataFrame(
            {"Open": [99, 100, 101], "High": [101, 102, 103], "Low": [98, 99, 100], "Close": [100, 101, 102], "Volume": [10, 20, 30]},
            index=index,
        )
        frame.attrs["source"] = self.name
        return frame


@pytest.fixture
def instrument(monkeypatch):
    item = Instrument(
        symbol="RELIANCE",
        name="Reliance Industries",
        exchange="NSE",
        segment="NSE_EQ",
        instrument_type="EQ",
        instrument_key="NSE_EQ|INE002A01018",
    )
    monkeypatch.setattr("services.market_data.manager.CATALOGUE.resolve", lambda symbol, exchange=None: item)
    return item


def _manager(monkeypatch, tmp_path, mode: ProviderMode, order: list[str]) -> ProviderManager:
    monkeypatch.setenv("STOCKPILOT_PROVIDER_MODE", mode.value)
    monkeypatch.setenv("STOCKPILOT_PROVIDER_ORDER", ",".join(order))
    monkeypatch.setattr("services.market_data.manager.CACHE_DIR", tmp_path)
    manager = ProviderManager()
    manager.order = order
    manager._provider_intervals = {name: 0.0 for name in manager.providers}
    return manager


def test_default_mode_and_order_are_live_only(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_PROVIDER_MODE", raising=False)
    monkeypatch.delenv("STOCKPILOT_PROVIDER_ORDER", raising=False)
    manager = ProviderManager()
    assert manager.provider_mode is ProviderMode.LIVE_ONLY
    assert manager.order == ["upstox", "yfinance", "nse", "demo"]


def test_offline_demo_is_explicit_and_implicit_demo_mode(monkeypatch, tmp_path, instrument):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.OFFLINE_DEMO, ["upstox"])
    demo = FakeProvider("synthetic-demo")
    manager.providers["demo"] = demo
    quote = manager.get_quote("RELIANCE")
    assert demo.quote_calls == 1
    assert quote.context["provider"] == "demo"
    assert quote.context["provider_mode"] == "OFFLINE_DEMO"
    assert quote.context["is_live"] is False
    assert quote.context["fallback_used"] is False


def test_live_only_never_calls_fallback_chain_after_upstox_failure(monkeypatch, tmp_path, instrument):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.LIVE_ONLY, ["upstox", "nse", "demo", "yfinance"])
    upstox = FakeProvider("upstox", error="token rejected")
    nse = FakeProvider("nse")
    demo = FakeProvider("demo")
    yfinance = FakeProvider("yfinance")
    manager.providers.update({"upstox": upstox, "nse": nse, "demo": demo, "yfinance": yfinance})
    with pytest.raises(MarketDataError, match="RuntimeError"):
        manager.get_quote("RELIANCE")
    assert upstox.quote_calls == 1
    assert nse.quote_calls == 0
    assert demo.quote_calls == 0
    assert yfinance.quote_calls == 0


def test_fallback_allowed_never_uses_synthetic_demo(monkeypatch, tmp_path, instrument):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.FALLBACK_ALLOWED, ["upstox", "demo"])
    manager.providers["upstox"] = FakeProvider("upstox", error="upstream unavailable")
    demo = FakeProvider("synthetic-demo")
    manager.providers["demo"] = demo
    with pytest.raises(MarketDataError, match="RuntimeError"):
        manager.get_quote("RELIANCE")
    assert demo.quote_calls == 0


def test_quote_and_history_context_have_required_fields_and_provider_truth(monkeypatch, tmp_path, instrument):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.LIVE_ONLY, ["upstox"])
    provider = FakeProvider("Upstox fixture")
    manager.providers["upstox"] = provider

    quote = manager.get_quote("reliance.ns")
    history = manager.get_history("RELIANCE", "1D", "1mo")

    assert REQUIRED_CONTEXT <= quote.context.keys()
    assert REQUIRED_CONTEXT <= history.attrs["context"].keys()
    assert quote.context["requested_symbol"] == "reliance.ns"
    assert quote.context["provider"] == history.attrs["context"]["provider"] == "upstox"
    assert quote.context["resolved_instrument_key"] == "NSE_EQ|INE002A01018"
    assert history.attrs["context"]["timeframe"] == "1D"
    assert history.attrs["provider"] == "upstox"


def test_quote_context_uses_requested_timeframe(monkeypatch, tmp_path, instrument):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.LIVE_ONLY, ["upstox"])
    manager.providers["upstox"] = FakeProvider("Upstox fixture")
    quote = manager.get_quote("RELIANCE", timeframe="15m")
    assert quote.context["timeframe"] == "15m"


def test_market_backed_api_surfaces_expose_context(monkeypatch):
    from api import main as api_main
    from api import deps as api_deps
    from api.routers import analytics as analytics_router
    from api.routers import forecasting as forecasting_router
    from api.routers import market as market_router

    index = pd.date_range("2026-05-01", periods=90, freq="D")
    frame = pd.DataFrame(
        {
            "Open": range(90, 180),
            "High": range(92, 182),
            "Low": range(89, 179),
            "Close": range(91, 181),
            "Volume": [1000] * 90,
        },
        index=index,
    )
    context = {field: None for field in REQUIRED_CONTEXT}
    context.update({"requested_symbol": "RELIANCE", "provider": "upstox", "timeframe": "1D", "is_live": True, "is_stale": False, "fallback_used": False, "request_id": "phase3-test"})
    frame.attrs["context"] = context
    frame.attrs["provider"] = "upstox"
    frame.attrs["source"] = "Upstox"
    quote = Quote(symbol="RELIANCE", price=180.0, timestamp=index[-1].isoformat(), source="Upstox", context=context)

    monkeypatch.setattr(api_main.MANAGER, "get_quote", lambda symbol: quote)
    monkeypatch.setattr(api_main.MANAGER, "get_history", lambda symbol, timeframe="1D", window="1y": frame.copy())
    monkeypatch.setattr(api_deps, "forecast_range", lambda *args, **kwargs: {"symbol": "RELIANCE", "forecast": {}})
    monkeypatch.setattr(forecasting_router, "save_range_forecast", lambda *args, **kwargs: 7)

    assert market_router.quote_endpoint("RELIANCE")["context"] == context
    assert market_router.history_endpoint("RELIANCE", "1D", "1mo", 20)["context"] == context
    assert analytics_router.indicators_endpoint("RELIANCE", "1D", "1mo")["context"] == context
    assert forecasting_router.prediction_endpoint("RELIANCE", "1mo", "1D", 0.8, False, None)["context"] == context
    assert forecasting_router.save_prediction_endpoint("RELIANCE", "1mo", "1D", 0.8, {"id": 1})["context"] == context


def test_cache_keys_are_namespaced_by_mode_and_order(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path, ProviderMode.LIVE_ONLY, ["upstox"])
    live_key = manager._cache_key("quote", "RELIANCE")
    manager.provider_mode = ProviderMode.OFFLINE_DEMO
    demo_key = manager._cache_key("quote", "RELIANCE")
    manager.provider_mode = ProviderMode.FALLBACK_ALLOWED
    manager.order = ["upstox", "demo"]
    fallback_key = manager._cache_key("quote", "RELIANCE")
    assert len({live_key, demo_key, fallback_key}) == 3


def _index_row(symbol: str, exchange: str, key: str | None) -> dict[str, object]:
    return {
        "segment": f"{exchange}_INDEX",
        "exchange": exchange,
        "instrument_type": "INDEX",
        "trading_symbol": symbol,
        "name": symbol,
        "instrument_key": key,
    }


def _assert_core_index_keys(catalogue: InstrumentCatalogue) -> None:
    expected = {
        "NIFTY 50": "NSE_INDEX|Nifty 50",
        "NIFTY BANK": "NSE_INDEX|Nifty Bank",
        "SENSEX": "BSE_INDEX|SENSEX",
    }
    for symbol, key in expected.items():
        item = catalogue.resolve(symbol)
        assert item is not None
        assert item.instrument_key == key


def test_cached_master_merge_preserves_core_index_keys(monkeypatch, tmp_path):
    rows = [
        _index_row("NIFTY 50", "NSE", None),
        _index_row("NIFTY 50", "NSE", "NSE_INDEX|Nifty 50"),
        _index_row("NIFTY BANK", "NSE", "NSE_INDEX|Nifty Bank"),
        _index_row("SENSEX", "BSE", "BSE_INDEX|SENSEX"),
    ]
    (tmp_path / "instruments_india.json").write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr("services.market_data.instruments.MARKET_DIR", tmp_path)
    catalogue = InstrumentCatalogue()
    catalogue.load()
    _assert_core_index_keys(catalogue)


def test_refreshed_master_merge_preserves_core_index_keys(monkeypatch, tmp_path):
    nse_rows = [
        _index_row("NIFTY 50", "NSE", "NSE_INDEX|Nifty 50"),
        _index_row("NIFTY BANK", "NSE", "NSE_INDEX|Nifty Bank"),
    ]
    bse_rows = [_index_row("SENSEX", "BSE", "BSE_INDEX|SENSEX")]

    class Response:
        def __init__(self, rows):
            self.content = gzip.compress(json.dumps(rows).encode("utf-8"))

        def raise_for_status(self):
            return None

    monkeypatch.setattr("services.market_data.instruments.MARKET_DIR", tmp_path)
    monkeypatch.setattr(
        "services.market_data.instruments.requests.get",
        lambda url, **kwargs: Response(nse_rows if "NSE" in url else bse_rows),
    )
    catalogue = InstrumentCatalogue()
    catalogue.refresh_from_upstox()
    _assert_core_index_keys(catalogue)


def test_upstox_index_aliases_resolve_to_stockpilot_canonical_symbols(monkeypatch, tmp_path):
    rows = [
        _index_row("NIFTY", "NSE", "NSE_INDEX|Nifty 50"),
        _index_row("BANKNIFTY", "NSE", "NSE_INDEX|Nifty Bank"),
        _index_row("SENSEX", "BSE", "BSE_INDEX|SENSEX"),
    ]
    (tmp_path / "instruments_india.json").write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr("services.market_data.instruments.MARKET_DIR", tmp_path)

    catalogue = InstrumentCatalogue()

    _assert_core_index_keys(catalogue)
