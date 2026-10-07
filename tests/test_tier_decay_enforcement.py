import copy
import json

import pytest

from forecasting import live_decay
from forecasting import model_promotion as promotion
from test_promotion_registry_security import evidence


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_AUDIT_SECRET", "decay-test-signing-material-only-32-bytes")
    monkeypatch.setenv("STOCKPILOT_PROMOTION_MANIFEST_PATH", str(tmp_path / "manifest.json"))
    promotion.decide_promotion(evidence(), candidate_id="reviewed", artifact_hash="artifact")


def outcomes(start, hit=False):
    artifact = promotion.active_promotion_receipt()["artifact_hash"]
    return [{"id": n, "coverage_hit": int(hit), "confidence_level": .8,
             "symbol": "TEST", "target_timestamp": str(n), "timeframe": "1D",
             "horizon_sessions": 1, "payload_json": json.dumps({"promotion_artifact": artifact, "data_sufficiency": {"tier": "T3"}})}
            for n in range(start, start + 20)]


def payload():
    forecast = {"low": 90., "median": 100., "high": 110., "confidence_level": .8}
    return {"forecast": forecast, "data_sufficiency": {"tier": "T3"},
            "horizon": {"sessions": 1}, "validation": {"coverage": .8, "samples": 100,
                "empirical_coverage": .8, "nominal_coverage": .8, "beats_naive_baseline": True},
            "multi_horizon": {"horizons": [{"sessions": 1, "forecast": copy.copy(forecast)}]}}


def test_only_distinct_sustained_windows_trigger_enforced_widening(registry):
    for _ in range(4):
        live_decay.enforce_tier_decay(outcomes(1))
    assert live_decay.apply_tier_controls(payload())["forecast"]["low"] == 90
    live_decay.enforce_tier_decay(outcomes(21))
    live_decay.enforce_tier_decay(outcomes(41))
    result = live_decay.apply_tier_controls(payload())
    assert result["forecast"]["low"] < 90
    assert result["forecast"]["high"] > 110
    assert result["multi_horizon"]["horizons"][0]["forecast"] == result["forecast"]
    assert result["validation"]["coverage"] == .8
    assert result["live_control"]["action"] == "widen"
    from services.forecast_presentation import public_forecast
    public = public_forecast(result)
    assert public["confidence"]["level"] == "low"
    assert "not yet verified" in public["confidence"]["summary"]
    assert public["research_range"]["low"] == result["forecast"]["low"]


def test_completed_result_is_rechecked_at_publication(registry):
    from services.forecast_presentation import public_forecast
    completed = payload()
    live_decay.set_tier_pause("T3", paused=True, actor="operator", reason="Pause after completed job produced")
    assert public_forecast(completed)["research_range"] is None


def test_completed_comparison_is_rechecked_at_publication(registry):
    from services.forecast_presentation import present_compare_item
    completed = payload()
    live_decay.set_tier_pause("T3", paused=True, actor="operator", reason="Pause before cached comparison publication")
    assert present_compare_item(completed)["forecast"] is None


def test_widening_is_idempotent_and_presentation_preserves_cached_result(registry):
    for start in (1, 21, 41):
        live_decay.enforce_tier_decay(outcomes(start))
    result = live_decay.apply_tier_controls(payload())
    once = copy.deepcopy(result)
    assert live_decay.apply_tier_controls(result)["forecast"] == once["forecast"]
    from services.forecast_presentation import public_forecast
    assert public_forecast(result)["research_range"]["low"] == once["forecast"]["low"]
    assert result == once


def test_pause_abstains_on_primary_and_every_horizon(registry):
    live_decay.set_tier_pause("T3", paused=True, actor="operator", reason="Pause for operational model review")
    result = live_decay.apply_tier_controls(payload())
    assert result["abstained"] and result["forecast"] is None
    from services.forecast_presentation import public_forecast
    public = public_forecast(result)
    assert public["abstained"] and public["research_range"] is None
    assert result["multi_horizon"]["horizons"][0]["forecast"] is None
    live_decay.set_tier_pause("T3", paused=False, actor="operator", reason="Resume after operational model review")
    assert live_decay.apply_tier_controls(payload())["forecast"] is not None


def test_unsigned_control_tampering_cannot_publish(registry):
    live_decay.set_tier_pause("T3", paused=True, actor="operator", reason="Pause for operational model review")
    from forecasting.promotion_store import connect_registry
    conn = connect_registry(promotion.manifest_path())
    try:
        conn.execute("UPDATE model_controls SET payload_json='{}'")
        conn.commit()
    finally:
        conn.close()
    result = live_decay.apply_tier_controls(payload())
    assert result["forecast"] is None
    assert result["abstained"]


def test_missing_backtest_evidence_does_not_invent_decay(registry, monkeypatch):
    rows = outcomes(1)
    monkeypatch.setattr(live_decay, "active_promotion_receipt", lambda: None)
    assert live_decay.enforce_tier_decay(rows) == []


def test_control_change_invalidates_cached_forecasts(registry):
    import pandas as pd
    from services.forecast_execution import ForecastExecutionService
    frame = pd.DataFrame({"Close": [100.]})
    service = ForecastExecutionService()
    calls = []
    def predict(symbol, data):
        calls.append(symbol)
        return live_decay.apply_tier_controls(payload())
    arguments = dict(symbol="TEST", timeframe="1D", window="1y", confidence=.8,
                     history_loader=lambda: ("TEST", frame), forecaster=predict)
    assert service.execute(**arguments).result["forecast"] is not None
    live_decay.set_tier_pause("T3", paused=True, actor="operator", reason="Pause for operational model review")
    assert service.execute(**arguments).result["forecast"] is None
    assert len(calls) == 2
