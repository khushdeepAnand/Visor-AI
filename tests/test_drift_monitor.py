"""Unit + DB round-trip tests for drift monitoring and auto-adaptation (Phase B item 9)."""
from __future__ import annotations

from typing import Any

import pandas as pd

import api.scheduler as scheduler_mod
import database
from forecasting.drift_monitor import (
    compute_group_quality,
    detect_drift,
    quality_dashboard,
)


def _row(
    i: int,
    *,
    symbol: str = "RELIANCE",
    timeframe: str = "1D",
    horizon: int = 1,
    coverage: int = 1,
    median_offset: float = 0.5,
    actual: float | None = None,
    confidence: float = 0.8,
) -> dict[str, Any]:
    actual = 100.0 + float(i) if actual is None else actual
    return {
        "id": i,
        "symbol": symbol,
        "timeframe": timeframe,
        "horizon": horizon,
        "forecast_low": 98.0 + float(i),
        "forecast_high": 102.0 + float(i),
        "forecast_median": 100.0 + float(i) + median_offset,
        "confidence_level": confidence,
        "actual_price": actual,
        "coverage_hit": coverage,
        "winkler_score": 1.0,
        "target_timestamp": f"2026-02-{1 + i:02d}T00:00:00",
        "origin_timestamp": f"2026-02-{1 + i:02d}T00:00:00",
        "created_at": f"2026-02-{1 + i:02d}T00:00:00",
    }


def test_group_quality_computes_coverage_mase_direction() -> None:
    rows = [_row(i) for i in range(1, 13)]
    quality = compute_group_quality(rows)
    assert quality["settled_samples"] == 12
    assert quality["rolling_coverage"] == 1.0
    assert quality["nominal_coverage"] == 0.8
    assert quality["coverage_gap"] == 0.2
    assert quality["mase"] is not None and 0.5 <= quality["mase"] <= 0.6
    assert quality["directional_accuracy"] == 1.0
    assert quality["naive_directional_accuracy"] is not None
    assert quality["state"] == "stable"
    drift = detect_drift(quality)
    assert drift["drift_detected"] is False


def test_drift_detected_on_coverage_collapse() -> None:
    rows = [_row(i, coverage=1) for i in range(1, 7)] + [_row(i, coverage=0) for i in range(7, 13)]
    quality = compute_group_quality(rows, window=12)
    assert quality["rolling_coverage"] <= (6 / 12)
    drift = detect_drift(quality)
    assert drift["drift_detected"] is True
    assert any("coverage" in reason for reason in drift["drift_reasons"])
    assert drift["severity"] == "high"


def test_drift_detected_on_mase_at_or_above_one() -> None:
    rows = [_row(i, median_offset=0.5, actual=100.0 + 4.0 * i, coverage=1) for i in range(1, 13)]
    quality = compute_group_quality(rows)
    assert quality["mase"] is not None and quality["mase"] > 1.0
    drift = detect_drift(quality)
    assert drift["drift_detected"] is True
    assert any("MASE" in reason for reason in drift["drift_reasons"])


def test_insufficient_evidence_never_calls_drift() -> None:
    rows = [_row(i, coverage=0) for i in range(1, 4)]
    quality = compute_group_quality(rows)
    assert quality["settled_samples"] == 3
    assert quality["state"] == "insufficient_data"
    drift = detect_drift(quality)
    assert drift["drift_detected"] is False
    assert drift["drift_reasons"] == []


def test_dashboard_groups_by_symbol_timeframe_horizon() -> None:
    rows: list[dict[str, Any]] = []
    for i in range(1, 9):
        rows.append(_row(i, symbol="RELIANCE", timeframe="1D", horizon=1))
        rows.append(_row(i, symbol="TCS", timeframe="1D", horizon=1))
        rows.append(_row(i, symbol="RELIANCE", timeframe="1D", horizon=3))
    dashboard = quality_dashboard(rows)
    assert dashboard["group_count"] == 3
    keys = {(g["symbol"], g["timeframe"], g["horizon"]) for g in dashboard["groups"]}
    assert keys == {("RELIANCE", "1D", 1), ("TCS", "1D", 1), ("RELIANCE", "1D", 3)}
    assert dashboard["policy"]["naive_baseline"].startswith("persistence")


def _user() -> int:
    conn = database.get_connection()
    cur = conn.execute(
        "INSERT INTO users(name,email,password) VALUES(?,?,?)",
        ("Drift User", "drift@example.com", "hash"),
    )
    uid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return uid


def _payload(day: int) -> dict[str, Any]:
    return {
        "forecast": {"low": 98.0, "median": 101.0, "high": 104.0, "confidence_level": 0.8},
        "horizon": {"bars": 1, "sessions": 1, "timeframe": "1D", "label": "next session"},
        "target_timestamp": f"2026-01-{1 + day:02d}T00:00:00",
        "training": {"training_window": "1y", "timeframe": "1D"},
        "forecast_status": "model_supported",
    }


def _settle(pid: int, actual: float) -> None:
    database.settle_range_forecast_automatically(
        pid,
        actual,
        provider="test-upstox",
        data_timestamp="2026-01-20T09:15:00+05:30",
        evidence={"source": "test"},
    )


def test_ledger_to_dashboard_round_trip(temp_db) -> None:
    uid = _user()
    pids: list[int] = []
    for day in range(1, 9):
        pid = database.save_range_forecast(uid, "RELIANCE", _payload(day))
        pids.append(pid)
        _settle(pid, 101.0 + 0.1 * (day % 3))  # near median, inside [98, 104]
    rows = database.get_settled_rows_for_quality()
    assert len(rows) >= 8
    dashboard = quality_dashboard(rows)
    group = next(g for g in dashboard["groups"] if g["symbol"] == "RELIANCE" and g["horizon"] == 1)
    assert group["settled_samples"] >= 8
    assert group["rolling_coverage"] == 1.0
    assert group["mase"] is not None and group["mase"] <= 1.0


def test_record_and_read_model_health(temp_db) -> None:
    health_id = database.record_model_health(
        symbol="RELIANCE",
        timeframe="1D",
        horizon=1,
        settled_samples=12,
        rolling_coverage=0.65,
        nominal_coverage=0.8,
        coverage_gap=-0.15,
        mase=1.2,
        drift_detected=True,
        drift_reasons=["rolling coverage fell below nominal by more than the tolerance"],
        severity="high",
    )
    assert health_id >= 1
    grouped = database.latest_model_health_by_group()
    row = next(item for item in grouped if item["id"] == health_id)
    assert row["drift_detected"] == 1
    assert row["severity"] == "high"
    assert "coverage" in row["drift_reasons_json"]


class _FakeManager:
    def get_history(self, symbol, timeframe="1D", window="1y"):
        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0],
                "High": [102.0, 103.0, 104.0, 105.0],
                "Low": [99.0, 100.0, 101.0, 102.0],
                "Close": [101.0, 102.0, 103.0, 104.0],
                "Volume": [1000.0, 1000.0, 1000.0, 1000.0],
            },
            index=pd.date_range("2026-01-05", periods=4, freq="B"),
        )
        return frame


def test_drift_auto_retrain_job_records_action(tmp_path, monkeypatch, temp_db) -> None:
    drift_rows = [_row(i, coverage=0) for i in range(1, 13)]
    monkeypatch.setattr(database, "get_settled_rows_for_quality", lambda limit=3000: drift_rows)
    monkeypatch.setattr(scheduler_mod, "MANAGER", _FakeManager())
    monkeypatch.setattr(scheduler_mod, "forecast_range", lambda *args, **kwargs: {"symbol": "RELIANCE"})
    monkeypatch.setattr(scheduler_mod, "REFRESH_DIR", tmp_path)

    scheduler_mod.evaluate_model_drift_and_retrain()

    grouped = database.latest_model_health_by_group()
    drift_records = [row for row in grouped if row["drift_detected"] == 1]
    assert drift_records, "at least one drift health record should be written"
    assert any(row["action_taken"] == "auto_retrained" for row in drift_records)
    artifacts = list(tmp_path.glob("auto_retrain_*.json"))
    assert len(artifacts) >= 1