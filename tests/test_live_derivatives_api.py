from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app


def test_live_option_chain_endpoint_preserves_explicit_fallback(monkeypatch):
    expected = {"underlying":"NIFTY 50","expiry":"2026-08-27","source":"instrument_master_only","is_live":False,"is_stale":True,"rows":[]}
    monkeypatch.setattr("api.main.LIVE_DERIVATIVES.live_chain", lambda underlying, expiry: expected)
    with TestClient(app) as client:
        response=client.get("/api/v1/derivatives/options/live-chain/NIFTY%2050?expiry=2026-08-27")
    assert response.status_code == 200
    assert response.json()["is_live"] is False
    assert response.json()["source"] == "instrument_master_only"


def test_margin_endpoint_exposes_approximation_flag(monkeypatch):
    monkeypatch.setattr("api.main.LIVE_DERIVATIVES.margin", lambda request: {"source":"StockPilot approximation","approximate_margin":True,"required_margin":100.0,"final_margin":100.0,"is_stale":True})
    with TestClient(app) as client:
        response=client.post("/api/v1/derivatives/margin",json={"symbol":"RELIANCE","quantity":1,"instrument_type":"FUTURE","price":2500,"lot_size":1})
    assert response.status_code == 200
    assert response.json()["approximate_margin"] is True


def test_stream_health_endpoint_has_fallback_state():
    with TestClient(app) as client:
        response=client.get("/api/v1/market/stream-health")
    assert response.status_code == 200
    body=response.json()
    assert "native_stream_active" in body
    assert "rest_fallback" in body
