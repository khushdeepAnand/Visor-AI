"""Contracts consumed by the native Lightweight Charts frontend."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import main as api_main
from services.market_data.context import ProviderMode
from services.market_data.demo import DemoIndianProvider

ROOT = Path(__file__).resolve().parents[1]


def _demo(monkeypatch, tmp_path):
    monkeypatch.setitem(api_main.MANAGER.providers, "demo", DemoIndianProvider())
    monkeypatch.setattr(api_main.MANAGER, "order", ["demo"])
    monkeypatch.setattr(api_main.MANAGER, "provider_mode", ProviderMode.OFFLINE_DEMO)
    monkeypatch.setattr("services.market_data.manager.CACHE_DIR", tmp_path)
    api_main.MANAGER.memory.clear()


def test_chart_history_contract_is_serializable_ohlcv(monkeypatch, tmp_path):
    _demo(monkeypatch, tmp_path)
    body = TestClient(api_main.app).get("/api/v1/market/history/INFY?timeframe=15m&window=3mo&limit=120").json()
    assert len(body["candles"]) == 120
    candle = body["candles"][0]
    assert set(candle) == {"time", "open", "high", "low", "close", "volume"}
    json.dumps(body)


def test_frontend_uses_native_lightweight_charts_and_segmented_timeframes():
    source = (ROOT / "frontend" / "components" / "TerminalChart.tsx").read_text(encoding="utf-8")
    assert 'from "lightweight-charts"' in source
    for tf in ["1m", "5m", "15m", "1h", "4h", "1D", "1W"]:
        assert f'"{tf}"' in source
    assert "<select" not in source.lower()
    assert "/ws/quotes/" in source


def test_frontend_chart_has_forecast_band_and_session_vwap():
    source = (ROOT / "frontend" / "components" / "TerminalChart.tsx").read_text(encoding="utf-8")
    assert "forecast.research_range.low" in source
    assert "forecast.research_range.high" in source
    assert "forecast.research_range.median_reference" in source
    assert 'item.time.slice(0, 10)' in source  # VWAP accumulator resets per trading date.
