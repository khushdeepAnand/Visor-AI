"""Tests for OpenTelemetry tracing setup (services/telemetry.py)."""
from __future__ import annotations

import pytest

from services import telemetry


@pytest.fixture(autouse=True)
def _reset_telemetry():
    telemetry.shutdown_telemetry()
    telemetry.clear_memory_spans()
    yield
    telemetry.shutdown_telemetry()
    telemetry.clear_memory_spans()


def test_disabled_by_default_returns_noop_tracer(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.enabled() is False
    assert telemetry.configure_telemetry() is None
    tracer = telemetry.get_tracer("test")
    # Must not raise in disabled mode.
    with tracer.start_span("noop-span") as span:
        span.set_attribute("k", "v")
    assert telemetry.memory_spans() == []


def test_get_tracer_always_usable(monkeypatch):
    monkeypatch.delenv("STOCKPILOT_OTEL_ENABLED", raising=False)
    tracer = telemetry.get_tracer("stockpilot.test")
    span = tracer.start_span("anything")
    span.set_attribute("a", 1)
    span.end()


def test_memory_exporter_captures_spans(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_OTEL_ENABLED", "true")
    monkeypatch.setenv("STOCKPILOT_OTEL_EXPORTER", "memory")
    provider = telemetry.configure_telemetry(force=True)
    assert provider is not None
    tracer = telemetry.get_tracer("stockpilot.test")
    with tracer.start_span("login-span") as span:
        span.set_attribute("user.id", 42)
    finished = telemetry.memory_spans()
    assert any(s.name == "login-span" for s in finished)


def test_configure_is_idempotent(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_OTEL_ENABLED", "true")
    monkeypatch.setenv("STOCKPILOT_OTEL_EXPORTER", "memory")
    first = telemetry.configure_telemetry(force=True)
    second = telemetry.configure_telemetry()
    assert first is second


def test_setup_telemetry_noop_when_disabled(monkeypatch, temp_db):
    from fastapi import FastAPI
    monkeypatch.delenv("STOCKPILOT_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = FastAPI()
    assert telemetry.setup_telemetry(app) is False


def test_setup_telemetry_instruments_app(monkeypatch, temp_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("STOCKPILOT_OTEL_ENABLED", "true")
    monkeypatch.setenv("STOCKPILOT_OTEL_EXPORTER", "memory")

    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    assert telemetry.setup_telemetry(app) is True
    assert getattr(app, "_stockpilot_otel", False) is True
    # Second call must be a no-op (idempotent).
    assert telemetry.setup_telemetry(app) is False

    client = TestClient(app)
    assert client.get("/ping").status_code == 200

    # A server span should have been recorded by the instrumentation.
    names = [s.name for s in telemetry.memory_spans()]
    assert any("ping" in n or "GET" in n for n in names), names


def test_service_name_configurable(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_OTEL_ENABLED", "true")
    monkeypatch.setenv("STOCKPILOT_OTEL_EXPORTER", "memory")
    monkeypatch.setenv("STOCKPILOT_OTEL_SERVICE_NAME", "stockpilot-custom")
    provider = telemetry.configure_telemetry(force=True)
    assert provider is not None
    from opentelemetry.sdk.resources import Resource
    attrs = provider.resource.attributes
    assert attrs.get("service.name") == "stockpilot-custom"
