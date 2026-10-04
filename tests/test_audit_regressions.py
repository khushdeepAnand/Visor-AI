"""Regression proof for the October 2026 reliability audit."""
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import main
from forecasting.evaluation_harness import crps_ensemble, evaluate_tier, mase
from scripts.release_hygiene import REQUIRED_RUNTIME_FILES, collect_allowlisted


def test_readiness_refuses_unavailable_database(monkeypatch):
    monkeypatch.setattr(main, "database_health_check", lambda: {"status": "Unavailable"})
    assert TestClient(main.app).get("/api/v1/ready").status_code == 503


def test_crps_matches_known_distribution_and_single_member():
    assert crps_ensemble(np.array([1., 2., 3.]), np.array([[0., 2.], [1., 3.], [2., 4.]])) == pytest.approx(0.5)
    assert crps_ensemble(np.array([1., 3.]), np.array([[1.], [2.]])) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        crps_ensemble(np.array([1., 2.]), np.array([[1., 2.]]))


def test_baseline_skill_uses_aligned_errors_and_short_series_fails_closed():
    forecasts = [{"symbol": "TEST", "actual": actual, "median": actual - 1, "low": actual - 2, "high": actual + 2} for actual in (102., 104., 106.)]
    baseline = [{"actual": actual, "median": 100.} for actual in (102., 104., 106.)]
    assert evaluate_tier("T3", ["TEST"], forecasts, baseline_forecasts=baseline).mase == pytest.approx(0.25)
    assert mase(np.array([1.]), np.array([1.]), np.array([1.])) == float("inf")


def test_release_contains_build_and_migration_inputs():
    root = Path(__file__).resolve().parents[1]
    selected = {p.relative_to(root).as_posix() for p in collect_allowlisted(root)}
    assert REQUIRED_RUNTIME_FILES <= selected


def test_background_tasks_call_current_service_contracts(monkeypatch):
    from services import outcome_settlement, retention, task_queue
    monkeypatch.setattr(outcome_settlement, "settle_due_forecasts", lambda **kwargs: kwargs)
    assert task_queue.replay_settlement(42, 7)["settled"] == {"user_id": 42, "limit": 7}
    monkeypatch.setattr(retention, "run_retention_enforcement", lambda: {"completed": True})
    assert task_queue.run_retention() == {"completed": True}


def test_pending_forecast_selection_is_scoped_before_limit(temp_db):
    from database import get_connection, get_pending_range_forecasts, save_range_forecast
    connection = get_connection()
    ids = []
    for email in ("audit-a@example.test", "audit-b@example.test"):
        ids.append(connection.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)", ("Audit", email, "test-hash")).lastrowid)
    connection.commit()
    connection.close()
    payload = {
        "forecast": {"low": 100., "median": 105., "high": 110., "confidence_level": 0.8},
        "training": {"training_window": "1y", "timeframe": "1D", "rows": 300},
        "forecast_status": "available", "target_timestamp": "2026-10-05T10:00:00+00:00",
    }
    first = save_range_forecast(ids[0], "RELIANCE", payload)
    second = save_range_forecast(ids[1], "RELIANCE", payload)
    assert {row["id"] for row in get_pending_range_forecasts()} == {first, second}
    assert [row["id"] for row in get_pending_range_forecasts(limit=1, user_id=ids[1])] == [second]
