import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from forecasting import model_promotion as promotion
from forecasting.promotion_gate import PromotionGateConfig


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_AUDIT_SECRET", "promotion-test-signing-material-only-32-bytes")
    path = tmp_path / "manifest.json"
    monkeypatch.setenv("STOCKPILOT_PROMOTION_MANIFEST_PATH", str(path))
    return path


def evidence(coverage=.8):
    return {"T3": SimpleNamespace(coverage=coverage, target_coverage=.8, mase=.7,
        winkler_score=5., baseline_winkler=7., n_forecasts=200,
        diebold_mariano=SimpleNamespace(reject_null=True, p_value=.01, dm_statistic=-3.),
        conditional_coverage={"overall": coverage}, pinball_losses=[])}


def test_tampered_manifest_cannot_activate(registry):
    promotion.decide_promotion(evidence(), candidate_id="reviewed", artifact_hash="artifact")
    payload = json.loads(registry.read_text())
    payload["receipt"]["candidate_id"] = "unreviewed"
    registry.write_text(json.dumps(payload))
    assert promotion.active_promotion_receipt() is None


def test_signing_key_is_required_before_publication(registry, monkeypatch):
    monkeypatch.delenv("STOCKPILOT_AUDIT_SECRET")
    with pytest.raises(RuntimeError, match="STOCKPILOT_AUDIT_SECRET"):
        promotion.decide_promotion(evidence(), candidate_id="candidate", artifact_hash="artifact")
    assert not registry.exists()


def test_weakened_gate_cannot_be_used_for_publication(registry):
    with pytest.raises(ValueError, match="weaken"):
        promotion.decide_promotion(evidence(), candidate_id="candidate", artifact_hash="artifact",
            config=PromotionGateConfig(min_forecasts_per_tier=1))


@pytest.mark.parametrize("changes", [
    {"target_coverage": .5}, {"coverage_tolerance": -1},
    {"min_mase_improvement": -1}, {"min_winkler_improvement": -1},
    {"winkler_significance": .9}, {"dm_significance": -1},
    {"max_pinball_loss": float("nan")},
])
def test_all_production_policy_fields_are_validated(registry, changes):
    with pytest.raises(ValueError):
        promotion.decide_promotion(evidence(), candidate_id="candidate", artifact_hash="artifact",
                                  config=PromotionGateConfig(**changes))


def test_aggregate_minimum_cannot_be_bypassed_by_omitting_overall(registry):
    rows = evidence()
    rows["T3"].n_forecasts = 30
    decision = promotion.decide_promotion(rows, candidate_id="thin", artifact_hash="artifact")
    assert not decision.passed
    assert promotion.active_promotion_receipt() is None


def test_configured_improvement_is_enforced(registry):
    decision = promotion.decide_promotion(evidence(), candidate_id="candidate", artifact_hash="artifact",
        config=PromotionGateConfig(min_winkler_improvement=.5))
    assert not decision.passed


def test_rollback_cannot_reactivate_revoked_candidate(registry):
    promotion.decide_promotion(evidence(), candidate_id="unsafe", artifact_hash="artifact")
    promotion.revoke_promotion(reason="Artifact found unsafe during review", actor="operator")
    with pytest.raises(ValueError, match="revoked"):
        promotion.rollback_promotion("unsafe", reason="Attempt to restore prior candidate", actor="operator")


def test_failed_export_preserves_prior_active_decision(registry, monkeypatch):
    promotion.decide_promotion(evidence(), candidate_id="first", artifact_hash="first")
    original = promotion._atomic_write
    def fail(*args):
        raise OSError("Export failed")
    monkeypatch.setattr(promotion, "_atomic_write", fail)
    with pytest.raises(OSError, match="Export failed"):
        promotion.decide_promotion(evidence(), candidate_id="second", artifact_hash="second")
    monkeypatch.setattr(promotion, "_atomic_write", original)
    assert promotion.active_promotion_receipt()["candidate_id"] == "first"
    assert len(promotion.promotion_history()) == 1


def test_future_receipt_cannot_activate(registry):
    promotion.decide_promotion(evidence(), candidate_id="candidate", artifact_hash="artifact",
        now=datetime.now(timezone.utc) + timedelta(days=1))
    assert promotion.active_promotion_receipt() is None


def test_history_logs_rejection_and_rollback_without_rewriting_evidence(registry):
    promotion.decide_promotion(evidence(), candidate_id="first", artifact_hash="first-artifact")
    evaluated_at = promotion.active_promotion_receipt()["evaluated_at"]
    promotion.decide_promotion(evidence(.2), candidate_id="bad", artifact_hash="bad-artifact")
    promotion.decide_promotion(evidence(), candidate_id="second", artifact_hash="second-artifact")
    promotion.rollback_promotion("first", reason="Rollback after operator review", actor="model-ops")
    receipt = promotion.active_promotion_receipt()
    assert receipt["candidate_id"] == "first" and receipt["evaluated_at"] == evaluated_at
    history = promotion.promotion_history()
    assert [event["action"] for event in history] == ["promote", "reject", "promote", "rollback"]


def test_registry_audit_rows_cannot_be_updated_or_deleted(registry):
    from forecasting.promotion_store import connect_registry
    from database import DATABASE_ERRORS
    promotion.decide_promotion(evidence(), candidate_id="first", artifact_hash="artifact")
    connection = connect_registry(registry)
    try:
        for command in ("DELETE FROM model_decisions", "UPDATE model_decisions SET action='promote'"):
            with pytest.raises(DATABASE_ERRORS, match="append-only"):
                connection.execute(command)
            connection.rollback()
    finally:
        connection.close()


def test_revoked_manifest_cannot_be_replayed(registry):
    promotion.decide_promotion(evidence(), candidate_id="first", artifact_hash="artifact")
    old = registry.read_text()
    promotion.revoke_promotion(reason="Revoke after review and drift", actor="model-ops")
    registry.write_text(old)
    assert promotion.active_promotion_receipt() is None


def test_rollback_cannot_refresh_expired_evidence(registry):
    promotion.decide_promotion(evidence(), candidate_id="old", artifact_hash="artifact",
        now=datetime.now(timezone.utc) - timedelta(days=60))
    with pytest.raises(ValueError, match="expired"):
        promotion.rollback_promotion("old", reason="Rollback after operator review", actor="model-ops")


def test_audit_tampering_fails_closed_even_if_triggers_are_removed(registry):
    from forecasting.promotion_store import connect_registry
    promotion.decide_promotion(evidence(), candidate_id="first", artifact_hash="artifact")
    connection = connect_registry(registry)
    try:
        connection.execute("DROP TRIGGER model_decisions_no_update")
        connection.execute("UPDATE model_decisions SET action='rollback'")
        connection.commit()
    finally:
        connection.close()
    assert promotion.active_promotion_receipt() is None
    with pytest.raises(RuntimeError, match="integrity"):
        promotion.promotion_history()
