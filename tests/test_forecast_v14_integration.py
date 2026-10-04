from __future__ import annotations

import pandas as pd
import pytest

from forecasting.v14_integration import (
    collect_context,
    cqr_canary_status,
    read_aci_state,
    record_realized_outcome,
)


def _frame(days: int = 3) -> pd.DataFrame:
    frame = pd.DataFrame({"Close": range(100, 100 + days)}, index=pd.date_range("2026-01-01", periods=days, freq="D"))
    frame.attrs["provider"] = "test"
    return frame


def test_context_is_truncated_at_origin_and_discloses_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_ACI_STATE_PATH", str(tmp_path / "aci.json"))
    result = collect_context(symbol="RELIANCE", origin="2026-01-02", timeframe="1D", window="1y", market_data=_frame())
    assert result["inputs"]["market_index"].status == "available"
    assert result["inputs"]["market_index"].rows == 2
    assert result["inputs"]["india_vix"].status == "unavailable"
    assert result["lineage"]["market_index"]["as_of"].startswith("2026-01-02")


def test_aci_updates_only_after_authoritative_realized_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_ACI_STATE_PATH", str(tmp_path / "aci.json"))
    before = read_aci_state("RELIANCE", "1D", 1, 0.8)
    with pytest.raises(ValueError):
        record_realized_outcome(symbol="RELIANCE", timeframe="1D", horizon=1, confidence=.8, covered=True, outcome_id="x", realized_at="2026-01-01", official=False, automatic=True)
    after = read_aci_state("RELIANCE", "1D", 1, 0.8)
    assert after["step"] == before["step"] == 0
    updated = record_realized_outcome(symbol="RELIANCE", timeframe="1D", horizon=1, confidence=.8, covered=True, outcome_id="x", realized_at="2020-01-01", official=True, automatic=True)
    assert updated["updated"] is True and updated["step"] == 1
    assert record_realized_outcome(symbol="RELIANCE", timeframe="1D", horizon=1, confidence=.8, covered=True, outcome_id="x", realized_at="2020-01-01", official=True, automatic=True)["updated"] is False


def test_cqr_canary_fails_closed():
    assert cqr_canary_status()["enabled"] is False
    assert cqr_canary_status({"passed": True, "n_forecasts": 100})["enabled"] is False


def test_cqr_canary_rollout_is_configurable_and_deterministic(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_CQR_CANARY_PERCENT", "100")
    receipt = {
        "passed": True,
        "gate_version": "gate-1",
        "evaluated_at": "2026-01-01T00:00:00Z",
        "artifact_hash": "abc",
        "n_forecasts": 100,
        "coverage": 0.8,
        "winkler_improved": True,
    }
    first = cqr_canary_status(receipt, symbol="RELIANCE")
    second = cqr_canary_status(receipt, symbol="RELIANCE")
    assert first["enabled"] is True
    assert first["canary_bucket"] == second["canary_bucket"]


def test_context_degrades_one_provider_without_losing_index(monkeypatch):
    def broken_loader(symbol: str, timeframe: str, window: str):
        if symbol == "INDIA VIX":
            raise TimeoutError("provider timeout")
        return _frame(4)

    result = collect_context(
        symbol="RELIANCE",
        origin="2026-01-03",
        timeframe="1D",
        window="1y",
        history_loader=broken_loader,
    )
    assert result["inputs"]["market_index"].status == "available"
    assert result["inputs"]["india_vix"].status == "unavailable"
    assert result["inputs"]["india_vix"].reason in {"TimeoutError", "timeout"}
    assert result["metrics"]["degraded"] >= 1
