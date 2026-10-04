from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import prediction
from api import main as api_main
from middleware.observability import RATE_LIMITER, SlidingWindowRateLimiter
from models.advanced_boosters import optional_booster_status
from services.sentiment import score_headline


def _frame(rows=140):
    dates = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = np.linspace(100, 125, rows) + np.sin(np.arange(rows) / 5)
    return pd.DataFrame(
        {"Open": close - 0.2, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000},
        index=dates,
    )


def test_api_request_ids_readiness_and_metrics(temp_db):
    RATE_LIMITER.clear()
    client = TestClient(api_main.app)
    response = client.get("/api/v1/ready", headers={"X-Request-ID": "test-request"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert "X-Response-Time-Ms" in response.headers
    metrics = client.get("/api/v1/metrics").json()
    assert metrics["request_count"] >= 1


def test_api_position_size_and_derivatives_endpoints():
    RATE_LIMITER.clear()
    client = TestClient(api_main.app)
    sizing = client.post(
        "/api/v1/risk/position-size",
        json={"account_value": 100000, "entry_price": 100, "stop_loss": 95},
    )
    assert sizing.status_code == 200
    assert sizing.json()["shares"] == 200

    greeks = client.post(
        "/api/v1/derivatives/options/greeks",
        json={"spot": 100, "strike": 100, "days_to_expiry": 30, "volatility": 0.2, "option_type": "call"},
    )
    assert greeks.status_code == 200
    assert greeks.json()["delta"] > 0


def test_api_patterns_endpoint(monkeypatch):
    RATE_LIMITER.clear()
    monkeypatch.setattr(api_main.MANAGER, "get_history", lambda symbol, timeframe="1D", window="1y": _frame())
    client = TestClient(api_main.app)
    response = client.get("/api/v1/patterns/RELIANCE?timeframe=1D&window=1y")
    assert response.status_code == 200
    assert response.json()["symbol"] == "RELIANCE"
    assert "support" in response.json()


def test_sliding_window_rate_limiter_blocks_after_limit():
    limiter = SlidingWindowRateLimiter()
    assert limiter.allow("client", limit=2, window_seconds=60, now=1)[0]
    assert limiter.allow("client", limit=2, window_seconds=60, now=2)[0]
    allowed, retry = limiter.allow("client", limit=2, window_seconds=60, now=3)
    assert not allowed
    assert retry > 0
    assert limiter.allow("client", limit=2, window_seconds=60, now=62)[0]


def test_optional_model_registry_and_feature_count():
    status = optional_booster_status()
    assert {item["model"] for item in status} == {"LightGBM", "CatBoost"}
    assert len(prediction.FEATURE_COLUMNS) >= 60
    assert {"ADX", "CMF", "MFI", "VWAP", "Donchian_Position"}.issubset(prediction.FEATURE_COLUMNS)


def test_finbert_request_safely_falls_back_when_unavailable(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_SENTIMENT_BACKEND", "finbert")
    result = score_headline("Company reports strong profit growth")
    assert result["label"] in {"Positive", "Neutral", "Negative"}
    assert result["backend"] in {"lexicon", "finbert"}
