"""Integration tests for per-step v13 enhancement isolation.

Each enhancement step in ``_v13_enhance_forecast`` must fail independently:
a broken subsystem degrades only itself, reports itself under
``enhancement_status``, increments its own metric counter, and never
discards the core forecast or the sibling enhancements.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

import forecasting.interval_forecast as iff
from forecasting.interval_forecast import (
    get_enhancement_metrics,
    reset_enhancement_metrics,
)

EXPECTED_STEPS = {
    "tier_router",
    "volatility",
    "regime",
    "cqr",
    "aci",
    "mondrian",
    "ipo_peer",
    "circuit_clip",
    "fan_chart",
    "width_floor",
}


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = 320
    idx = pd.bdate_range("2024-01-01", periods=rows)
    phase = np.arange(rows) * 2 * np.pi / 8
    close = 150 + 15 * np.sin(phase) + rng.normal(0, 0.1, rows)
    open_ = close + rng.normal(0, 0.05, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.3,
            "Low": np.minimum(open_, close) - 0.3,
            "Close": close,
            "Volume": 1_000_000 + np.sin(phase + 1) * 100_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_enhancement_metrics()
    yield
    reset_enhancement_metrics()


def _boom(*_args, **_kwargs):
    raise RuntimeError("injected step failure")


def test_healthy_forecast_reports_every_step_ok(frame):
    payload = iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")
    status = payload["enhancement_status"]
    assert status["status"] == "ok"
    assert status["degraded_steps"] == []
    assert set(status["steps"]) == EXPECTED_STEPS
    assert all(v == "ok" for v in status["steps"].values())
    assert status["duration_ms"] >= 0.0
    assert get_enhancement_metrics()["total_calls"] == 1
    assert get_enhancement_metrics()["partial_degradations"] == 0


def test_regime_failure_degrades_only_regime(frame, monkeypatch, caplog):
    monkeypatch.setattr(iff, "detect_regime", _boom)
    with caplog.at_level(logging.WARNING, logger="forecasting.interval_forecast"):
        payload = iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")

    status = payload["enhancement_status"]
    assert status["status"] == "degraded"
    assert status["degraded_steps"] == ["regime"]
    assert status["steps"]["regime"] == "failed:RuntimeError"
    # Sibling steps survive.
    for step in EXPECTED_STEPS - {"regime"}:
        assert status["steps"][step] == "ok", step

    # The core contract is untouched and the sibling outputs are present.
    assert payload["forecast"]["low"] < payload["forecast"]["median"] < payload["forecast"]["high"]
    assert "tier" in payload
    assert "volatility_forecast" in payload
    assert "fan_chart" in payload
    assert "market_regime" not in payload

    metrics = get_enhancement_metrics()
    assert metrics["regime_failures"] == 1
    assert metrics["partial_degradations"] == 1
    assert any("'regime' failed" in rec.getMessage() for rec in caplog.records)


def test_tier_router_failure_falls_back_to_neutral_tier(frame, monkeypatch):
    monkeypatch.setattr(iff, "assign_tier", _boom)
    payload = iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")

    status = payload["enhancement_status"]
    assert status["degraded_steps"] == ["tier_router"]
    # A well-formed fallback tier is published, so Mondrian and shrinkage
    # still receive usable inputs instead of crashing on missing state.
    assert payload["tier"]["tier"] == "T2"
    assert "fallback" in payload["tier"]["reason"]
    assert status["steps"]["mondrian"] == "ok"
    assert "mondrian_groups" in payload
    assert payload["forecast"]["low"] < payload["forecast"]["high"]


def test_circuit_clip_failure_is_flagged_critical(frame, monkeypatch, caplog):
    monkeypatch.setattr(iff, "apply_circuit_limits", _boom)
    with caplog.at_level(logging.ERROR, logger="forecasting.interval_forecast"):
        payload = iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")

    status = payload["enhancement_status"]
    assert status["degraded_steps"] == ["circuit_clip"]
    assert status["steps"]["circuit_clip"] == "failed:RuntimeError"
    # Critical steps log at ERROR so alerting can pick them up.
    assert any(rec.levelno >= logging.ERROR for rec in caplog.records)
    # Bounds are published unclipped and explicitly not marked as clipped.
    assert "circuit_clip" not in payload["forecast"]
    assert payload["forecast"]["low"] < payload["forecast"]["high"]


def test_aci_and_fan_chart_failures_are_isolated(frame, monkeypatch):
    monkeypatch.setattr(iff, "read_aci_state", _boom)
    payload = iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")
    status = payload["enhancement_status"]
    assert status["degraded_steps"] == ["aci"]
    # The call-site read degrades to a placeholder instead of raising.
    assert payload["aci_state"]["status"] == "unavailable"
    assert payload["aci_state"]["reason"] == "RuntimeError"
    assert status["steps"]["cqr"] == "ok"
    assert status["steps"]["width_floor"] == "ok"


def test_metrics_reset_between_runs(frame):
    iff.forecast_range("RELIANCE", frame, confidence_level=0.80, training_window="1y")
    assert get_enhancement_metrics()["total_calls"] == 1
    reset_enhancement_metrics()
    assert get_enhancement_metrics()["total_calls"] == 0
    assert get_enhancement_metrics()["regime_failures"] == 0
