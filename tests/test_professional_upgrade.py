"""Coverage for the professional reliability upgrade."""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import authentication as auth
import database
import prediction_lstm
from api import main as api_main
from api import deps as api_deps


def _market_frame(rows=100):
    dates = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = np.linspace(100, 115, rows)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1000, 2000, rows),
        },
        index=dates,
    )


def test_login_temporarily_locks_after_repeated_failures(temp_db, monkeypatch):
    monkeypatch.setattr(auth, "MAX_FAILED_ATTEMPTS", 3)
    auth.register_user("Locked User", "locked@example.com", "Passw0rd123", "1985-06-15")

    for _ in range(3):
        success, _ = auth.login_user("locked@example.com", "WrongPass1")
        assert success is False

    success, message = auth.login_user("locked@example.com", "Passw0rd123")
    assert success is False
    assert "too many failed attempts" in message.lower()


def test_password_reset_token_is_one_time(temp_db):
    auth.register_user("Reset User", "reset@example.com", "Passw0rd123", "1985-06-15")
    token = auth.issue_password_reset_token("reset@example.com")
    assert token

    success, _ = auth.reset_password(token, "NewPassw0rd9")
    assert success is True
    success, _ = auth.reset_password(token, "AnotherPass9")
    assert success is False
    assert auth.login_user("reset@example.com", "NewPassw0rd9")[0] is True


def test_database_health_and_symbol_catalogue(temp_db):
    connection = database.get_connection()
    connection.execute(
        "INSERT INTO symbols(name, symbol, exchange, country, sector) VALUES (?, ?, ?, ?, ?)",
        ("Reliance Industries", "RELIANCE", "NSE", "India", "Technology"),
    )
    connection.commit()
    count = connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    connection.close()

    assert count == 1
    assert database.database_health_check()["integrity"] == "ok"


def test_lstm_sequence_builder_is_chronological():
    X = np.arange(30, dtype=float).reshape(10, 3)
    y = np.arange(10, dtype=float)
    sequences, targets = prediction_lstm.build_sequences(X, y, lookback=4)

    assert sequences.shape == (6, 4, 3)
    assert targets.tolist() == [4, 5, 6, 7, 8, 9]
    assert np.array_equal(sequences[0], X[:4])


def test_fastapi_prediction_contract(monkeypatch):
    frame = _market_frame(180)
    monkeypatch.setattr(api_main.MANAGER, "get_history", lambda symbol, timeframe="1D", window="1y": frame)
    monkeypatch.setattr(
        api_deps,
        "forecast_range",
        lambda symbol, data, **kwargs: {
            "symbol": symbol,
            "forecast_status": "model_supported",
            "forecast": {"low": 113.0, "median": 116.0, "high": 119.0, "confidence_level": 0.8, "currency": "INR"},
            "validation": {"empirical_coverage": 0.8, "samples": 120, "beats_naive_baseline": True},
            "evidence": {"grade": "A"},
            "training": {"training_window": kwargs.get("training_window")},
        },
    )
    client = TestClient(api_main.app)
    response = client.get("/api/v1/predict/RELIANCE?training_window=1y&timeframe=1D&confidence=.8")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "RELIANCE"
    assert set(body["research_range"]) >= {"low", "median_reference", "high", "confidence_level"}
    assert body["research_range"]["low"] <= body["research_range"]["median_reference"] <= body["research_range"]["high"]
