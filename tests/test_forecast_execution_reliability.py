"""Forecast cache, worker lifecycle, diagnostics, and API contract coverage."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import api.main as api_main
import api.deps as api_deps
from services import admin_registry
from services.forecast_execution import (
    ForecastExecutionService,
    ForecastJobManager,
    ForecastJobQueueFull,
    ForecastJobQuotaExceeded,
)
from services.market_data.context import ProviderMode
from services.market_data.manager import ProviderManager


PASSWORD = "StrongPass9!x"
ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def isolated_optional_market_context(monkeypatch):
    # These worker/contract tests inject the primary forecaster. Its optional
    # contextual feeds must also be isolated from workstation provider secrets.
    monkeypatch.setattr(api_deps, "_fetch_index_history", lambda *_args: None)
    monkeypatch.setattr(api_deps, "_fetch_vix_history", lambda *_args: None)
    monkeypatch.setattr(api_deps, "_expected_move_for_symbol", lambda *_args: None)


def _frame(rows: int = 20) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq="D", tz="UTC")
    close = np.linspace(100.0, 110.0, rows)
    frame = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(rows, 1000.0),
        },
        index=index,
    )
    frame.attrs.update({"source": "test", "provider": "test", "is_stale": False, "context": {}})
    return frame


def _forecast_result() -> dict:
    return {
        "symbol": "RELIANCE",
        "forecast_status": "model_supported",
        "forecast": {
            "low": 100.0,
            "median": 105.0,
            "high": 110.0,
            "confidence_level": 0.8,
            "currency": "INR",
        },
        "validation": {"empirical_coverage": 0.8, "samples": 120, "beats_naive_baseline": True},
        "evidence": {"grade": "A"},
        "training": {"training_window": "1y", "timeframe": "1D"},
    }


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("Timed out waiting for background forecast state.")


def _wait_for_job(client: TestClient, job_id: str, expected_status: str) -> dict:
    def poll() -> dict | None:
        response = client.get(f"/api/v1/forecast-jobs/{job_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        return body if body["status"] == expected_status else None

    return _wait_for(poll, timeout=5.0, interval=0.1)


class _HistoryProvider:
    name = "demo"
    supports_live_stream = False
    supports_depth = False

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return True

    def health(self) -> dict:
        return {"provider": self.name, "configured": True}

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        with self._lock:
            self.calls += 1
        time.sleep(0.05)
        frame = _frame()
        frame.attrs.update({"source": "fake", "symbol": symbol, "timeframe": timeframe})
        return frame


def test_history_cache_normalizes_and_single_flights_concurrent_misses(monkeypatch, tmp_path):
    provider = _HistoryProvider()
    manager = ProviderManager()
    manager.providers = {"demo": provider}
    manager.order = ["demo"]
    manager.provider_mode = ProviderMode.OFFLINE_DEMO
    manager.ttl_history = 60
    monkeypatch.setattr("services.market_data.manager.CACHE_DIR", tmp_path)
    monkeypatch.setattr("services.market_data.manager.REDIS_HOT_CACHE.get_history", lambda _key: None)
    monkeypatch.setattr("services.market_data.manager.REDIS_HOT_CACHE.set_history", lambda *_args: None)

    requests = [("reliance.ns", "1d", "1Y"), ("RELIANCE", "1D", "1y")] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda args: manager.get_history(*args), requests))

    assert provider.calls == 1
    diagnostics = manager.cache_diagnostics()
    assert diagnostics["history_coalesced"] + diagnostics["history_memory_hits"] >= 1
    results[0].iloc[0, 0] = -1
    assert results[1].iloc[0, 0] != -1
    manager.get_history("RELIANCE", "1D", "1y")
    assert provider.calls == 1
    assert manager.cache_diagnostics()["history_memory_hits"] >= 1


def test_content_addressed_forecast_cache_reuses_and_coalesces():
    service = ForecastExecutionService(ttl_seconds=60, max_entries=4)
    frame = _frame()
    calls = 0
    lock = threading.Lock()

    def forecaster(_symbol, _data):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _forecast_result()

    def execute(data=frame):
        return service.execute(
            symbol="RELIANCE",
            timeframe="1D",
            window="1y",
            confidence=0.8,
            history_loader=lambda: ("RELIANCE", data),
            forecaster=forecaster,
            engine_version="test-v1",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _item: execute(), range(6)))
    assert calls == 1
    assert {outcome.trace["cache"] for outcome in outcomes} <= {"miss", "coalesced", "hit"}
    assert service.diagnostics()["coalesced"] >= 1

    cached = execute()
    assert cached.trace["cache"] == "hit"
    changed = frame.copy()
    changed.iloc[-1, changed.columns.get_loc("Close")] += 1
    execute(changed)
    assert calls == 2


def test_job_manager_coalesces_lifecycle_cancellation_and_quota():
    manager = ForecastJobManager(max_workers=1, max_queue=2, per_user_quota=2, max_retained=10)
    gate = threading.Event()

    def runner(request):
        gate.wait(2)
        return {"symbol": request["symbol"]}, {"trace_id": "safe", "timings_ms": {"total": 1}}

    request = {"symbol": "RELIANCE", "timeframe": "1D", "training_window": "1y", "confidence": 0.8}
    first, coalesced = manager.submit(owner_id=7, request=request, runner=runner, error_handler=lambda _exc: {})
    duplicate, was_coalesced = manager.submit(owner_id=7, request=request, runner=runner, error_handler=lambda _exc: {})
    assert coalesced is False
    assert was_coalesced is True
    assert duplicate["job_id"] == first["job_id"]
    _wait_for(lambda: manager.get(first["job_id"], owner_id=7)["status"] == "running")

    second_request = {**request, "confidence": 0.81}
    second, _ = manager.submit(owner_id=7, request=second_request, runner=runner, error_handler=lambda _exc: {})
    with pytest.raises(ForecastJobQuotaExceeded):
        manager.submit(
            owner_id=7,
            request={**request, "confidence": 0.82},
            runner=runner,
            error_handler=lambda _exc: {},
        )
    cancelled = manager.cancel_or_delete(second["job_id"], owner_id=7)
    assert cancelled["status"] == "cancelled"
    gate.set()
    completed = _wait_for(
        lambda: (job if (job := manager.get(first["job_id"], owner_id=7))["status"] == "succeeded" else None)
    )
    assert completed["result"] == {"symbol": "RELIANCE"}
    assert completed["runtime"]["over_limit"] is False
    assert manager.cancel_or_delete(first["job_id"], owner_id=7)["status"] == "deleted"
    manager.shutdown()


def test_job_manager_enforces_global_worker_and_queue_bound():
    manager = ForecastJobManager(max_workers=1, max_queue=1, per_user_quota=5, max_retained=10)
    gate = threading.Event()

    def runner(request):
        gate.wait(2)
        return request, {"trace_id": "safe"}

    base = {"symbol": "RELIANCE", "timeframe": "1D", "training_window": "1y", "confidence": 0.8}
    manager.submit(owner_id=1, request=base, runner=runner, error_handler=lambda _exc: {})
    manager.submit(owner_id=2, request={**base, "confidence": 0.81}, runner=runner, error_handler=lambda _exc: {})
    with pytest.raises(ForecastJobQueueFull):
        manager.submit(
            owner_id=3,
            request={**base, "confidence": 0.82},
            runner=runner,
            error_handler=lambda _exc: {},
        )
    assert manager.diagnostics()["queue_rejections"] == 1
    gate.set()
    manager.shutdown()


@pytest.fixture
def forecast_client(temp_db, monkeypatch):
    api_main.FORECAST_EXECUTION.clear()
    api_main.FORECAST_JOBS.clear()
    monkeypatch.setattr(api_deps, "_history", lambda *_args: ("RELIANCE", _frame()))
    monkeypatch.setattr(api_deps, "forecast_range", lambda *_args, **_kwargs: _forecast_result())
    client = TestClient(api_main.app)
    client.headers.update({"Origin": ORIGIN})
    return client


def test_forecast_job_endpoint_supports_bounded_anonymous_research_and_preserves_public_contract(forecast_client):
    request = {"symbol": "RELIANCE", "training_window": "1y", "timeframe": "1D", "confidence": 0.8}
    submitted = forecast_client.post("/api/v1/forecast-jobs", json=request)
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    completed = _wait_for_job(forecast_client, job_id, "succeeded")
    assert completed["result"]["research_range"]["median_reference"] == 105.0
    assert "methods" not in completed["result"]
    assert set(completed["execution"]) == {"trace_id", "cache", "timings_ms"}

    synchronous = forecast_client.get("/api/v1/predict/RELIANCE?training_window=1y&timeframe=1D")
    assert synchronous.status_code == 200
    assert synchronous.json()["research_range"] == completed["result"]["research_range"]
    assert synchronous.json()["execution"]["cache"] == "hit"


def test_job_failure_and_admin_diagnostics_are_sanitized_and_authorized(temp_db, monkeypatch):
    secret = "provider-token-DO-NOT-LEAK"
    admin_email = "forecast.admin@example.com"
    monkeypatch.setenv(admin_registry.ADMIN_EMAILS_VAR, admin_email)
    api_main.FORECAST_EXECUTION.clear()
    api_main.FORECAST_JOBS.clear()
    monkeypatch.setattr(api_deps, "_history", lambda *_args: ("RELIANCE", _frame()))

    def explode(*_args, **_kwargs):
        raise RuntimeError(f"provider rejected {secret}")

    monkeypatch.setattr(api_deps, "forecast_range", explode)
    client = TestClient(api_main.app)
    client.headers.update({"Origin": ORIGIN})
    assert client.get("/api/v1/admin/diagnostics").status_code == 401
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Forecast Admin", "email": admin_email, "password": PASSWORD, "date_of_birth": "1985-06-15"},
    )
    assert response.status_code == 200
    admin_registry.bootstrap_admins()
    submitted = client.post("/api/v1/forecast-jobs", json={"symbol": "RELIANCE", "training_window": "1y"})
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    failed = _wait_for_job(client, job_id, "failed")
    assert failed["error"]["code"] == "forecast_unavailable"
    assert failed["error"]["support_id"]
    assert secret not in json.dumps(failed)

    diagnostics = client.get("/api/v1/admin/diagnostics")
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert set(body) == {"jobs", "cache", "providers"}
    assert set(body["cache"]) == {"forecast_results", "market_history"}
    assert "request" not in json.dumps(body).lower()
    assert secret not in diagnostics.text
    for path in ("jobs", "cache", "providers", "forecasts"):
        response = client.get(f"/api/v1/admin/diagnostics/{path}")
        assert response.status_code == 200
        assert secret not in response.text


def test_quote_websocket_rejects_unapproved_browser_origin(temp_db):
    client = TestClient(api_main.app)
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect(
            "/ws/quotes/RELIANCE?timeframe=1m",
            headers={"Origin": "https://evil.example"},
        ):
            pass
    assert denied.value.code == 1008
