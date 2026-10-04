"""FastAPI/Next.js cutover smoke tests (network-free)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api import main as api_main
from services.market_data.demo import DemoIndianProvider
from services.market_data.context import ProviderMode

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_is_retired_and_nextjs_frontend_exists():
    assert not (ROOT / "app.py").exists()
    assert not (ROOT / "ui").exists()
    assert not (ROOT / "charts").exists()
    assert (ROOT / "frontend" / "app" / "page.tsx").exists()
    assert (ROOT / "frontend" / "components" / "TerminalChart.tsx").exists()


def test_fastapi_root_ready_and_system(temp_db):
    client = TestClient(api_main.app)
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/ready").json()["status"] == "ready"
    system = client.get("/api/v1/system")
    assert system.status_code == 200
    body = system.json()
    assert body["version"].startswith("18.")
    assert body["instrument_count"] > 100
    assert set(body["range_forecasting"]["training_windows"]) >= {"1w", "1mo", "3mo", "1y", "5y"}


def test_demo_market_search_quote_and_history(monkeypatch, tmp_path):
    demo = DemoIndianProvider()
    monkeypatch.setitem(api_main.MANAGER.providers, "demo", demo)
    monkeypatch.setattr(api_main.MANAGER, "order", ["demo"])
    monkeypatch.setattr(api_main.MANAGER, "provider_mode", ProviderMode.OFFLINE_DEMO)
    monkeypatch.setattr("services.market_data.manager.CACHE_DIR", tmp_path)
    api_main.MANAGER.memory.clear()
    client = TestClient(api_main.app)
    search = client.get("/api/v1/market/search?q=RELIANCE")
    assert search.status_code == 200
    assert any(x["symbol"] == "RELIANCE" for x in search.json()["results"])
    quote = client.get("/api/v1/market/quote/RELIANCE")
    assert quote.status_code == 200
    assert quote.json()["source"].startswith("StockPilot DEMO")
    hist = client.get("/api/v1/market/history/RELIANCE?timeframe=5m&window=1mo&limit=200")
    assert hist.status_code == 200
    assert len(hist.json()["candles"]) == 200


def test_global_or_crypto_symbols_are_rejected():
    client = TestClient(api_main.app)
    for symbol in ["AAPL", "BTC-USD", "GC=F"]:
        response = client.get(f"/api/v1/market/quote/{symbol}")
        assert response.status_code in {400, 404, 422}
