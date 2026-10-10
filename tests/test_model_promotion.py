"""The promotion gate must block bad models from shipping.

Covers the wired decision path: ``decide_promotion`` runs the real gate,
writes the production manifest only on a full pass, and the live forecast
path reads its receipt via ``active_promotion_receipt``.  A deliberately bad
candidate (inverted quantiles) must be rejected end to end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from forecasting.evaluation_harness import evaluate_tier
from forecasting.model_promotion import (
    GATE_VERSION,
    active_promotion_receipt,
    decide_promotion,
    manifest_path,
    manifest_status,
)
from forecasting.v14_integration import cqr_canary_status

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def audit_signing_key(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_AUDIT_SECRET", "promotion-test-signing-material-only-32-bytes")


@pytest.fixture()
def manifest(tmp_path, monkeypatch):
    path = tmp_path / "promotion_manifest.json"
    monkeypatch.setenv("STOCKPILOT_PROMOTION_MANIFEST_PATH", str(path))
    return path


def _series(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic volatile walk plus per-row actuals (80% inside +/-2)."""
    rng = np.random.default_rng(7)
    median = 100.0 + np.cumsum(rng.normal(0.0, 2.0, n))
    actual = median + 0.1
    miss = slice(int(n * 0.8), n)
    signs = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    actual[miss] = median[miss] + 2.1 * signs[miss]  # just outside the band
    return median, actual


def _calibrated_forecasts(n: int = 200) -> tuple[list[dict], list[dict]]:
    """Well-calibrated candidate: 80% coverage, beats persistence on MAE/MSE."""
    median, actual = _series(n)
    forecasts = [
        {"symbol": "TEST", "actual": float(actual[i]), "median": float(median[i]), "low": float(median[i] - 2.0), "high": float(median[i] + 2.0)}
        for i in range(n)
    ]
    baselines = [
        {"symbol": "TEST", "actual": float(actual[i]), "median": float(actual[i - 1] if i else actual[0]), "low": float(actual[i - 1] if i else actual[0]) - 5, "high": float(actual[i - 1] if i else actual[0]) + 5}
        for i in range(n)
    ]
    return forecasts, baselines


def _inverted_forecasts(n: int = 200) -> tuple[list[dict], list[dict]]:
    """Deliberately broken candidate: quantile bounds are inverted."""
    median, actual = _series(n)
    forecasts = [
        {
            "symbol": "TEST",
            "actual": float(actual[i]),
            "median": float(median[i] + 20.0),  # point forecast far from truth
            "low": float(median[i] + 2.5),  # low > high: inverted interval
            "high": float(median[i] - 2.5),
        }
        for i in range(n)
    ]
    baselines = [
        {"symbol": "TEST", "actual": float(actual[i]), "median": float(actual[i - 1] if i else actual[0])}
        for i in range(n)
    ]
    return forecasts, baselines


def _evaluate(forecasts: list[dict], baselines: list[dict]) -> SimpleNamespace:
    result = evaluate_tier("T3", ["TEST"], forecasts, 0.80, baseline_forecasts=baselines)
    return SimpleNamespace(
        coverage=result.coverage,
        target_coverage=result.target_coverage,
        mase=result.mase,
        winkler_score=result.winkler_score,
        baseline_winkler=result.baseline_winkler,
        n_forecasts=result.n_forecasts,
        conditional_coverage=result.conditional_coverage,
        pinball_losses=result.pinball_losses,
        diebold_mariano=result.diebold_mariano,
    )


def test_inverted_quantile_candidate_is_rejected_by_gate(manifest):
    forecasts, baselines = _inverted_forecasts()
    tier_results = {"T3": _evaluate(forecasts, baselines)}

    decision = decide_promotion(tier_results, candidate_id="bad-inverted", artifact_hash="deadbeef-candidate")

    assert decision.passed is False
    assert decision.written is False
    assert "T3" in decision.failed_tiers
    assert not manifest.exists(), "a failing gate must not write the production manifest"
    assert active_promotion_receipt() is None
    # And the disabled receipt never activates CQR.
    assert cqr_canary_status(decision.receipt)["status"] == "disabled_pending_real_promotion_gate"


def test_calibrated_candidate_passes_gate_and_activates_receipt(manifest):
    forecasts, baselines = _calibrated_forecasts()
    tier_results = {"T3": _evaluate(forecasts, baselines)}

    decision = decide_promotion(tier_results, candidate_id="v14-good", artifact_hash="abc123-candidate")

    assert decision.passed is True
    assert decision.written is True
    assert manifest.exists()
    receipt = active_promotion_receipt()
    assert receipt is not None
    assert receipt["passed"] is True
    assert receipt["gate_version"] == GATE_VERSION
    assert receipt["n_forecasts"] >= 100
    assert receipt["artifact_hash"]
    status = cqr_canary_status(receipt)
    assert status["status"] == "control_arm"  # No symbol is assigned to a canary arm.
    assert status["enabled"] is True


def test_failed_promotion_never_clobbers_a_live_manifest(manifest):
    good, good_base = _calibrated_forecasts()
    first = decide_promotion({"T3": _evaluate(good, good_base)}, candidate_id="good-1", artifact_hash="hash-1")
    assert first.written is True
    before = manifest.read_text(encoding="utf-8")

    bad, bad_base = _inverted_forecasts()
    second = decide_promotion({"T3": _evaluate(bad, bad_base)}, candidate_id="bad-2", artifact_hash="hash-2")
    assert second.passed is False
    assert second.written is False
    assert manifest.read_text(encoding="utf-8") == before
    assert active_promotion_receipt()["candidate_id"] == "good-1"


def test_stale_receipt_fails_closed(manifest):
    forecasts, baselines = _calibrated_forecasts()
    stale_now = datetime.now(timezone.utc) - timedelta(days=60)
    decision = decide_promotion(
        {"T3": _evaluate(forecasts, baselines)},
        candidate_id="stale-1",
        artifact_hash="hash-stale",
        now=stale_now,
    )
    assert decision.written is True
    assert active_promotion_receipt() is None
    status = manifest_status()
    assert status["active"] is False
    assert status["inactive_reason"] == "receipt_stale"


def test_receipt_without_artifact_hash_fails_closed(manifest):
    forecasts, baselines = _calibrated_forecasts()
    decide_promotion({"T3": _evaluate(forecasts, baselines)}, candidate_id="no-hash", artifact_hash="  ")
    assert manifest.exists()
    # An empty artifact hash is stripped to "", so activation must refuse it.
    assert active_promotion_receipt() is None
    assert manifest_status()["inactive_reason"] == "artifact_hash_missing"


def test_forecast_path_consumes_the_promotion_receipt(manifest, monkeypatch):
    """Production wiring: forecast_range is handed the gate's live receipt."""
    import pandas as pd

    from forecasting.interval_forecast import forecast_range

    monkeypatch.setenv("STOCKPILOT_CQR_CANARY_PERCENT", "100")

    forecasts, baselines = _calibrated_forecasts()
    decide_promotion({"T3": _evaluate(forecasts, baselines)}, candidate_id="wire-1", artifact_hash="hash-wire")

    rng = np.random.default_rng(3)
    rows = 320
    idx = pd.bdate_range("2024-01-01", periods=rows)
    close = 150 + rng.normal(0, 1.0, rows).cumsum() * 0.1
    frame = pd.DataFrame(
        {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": np.full(rows, 1_000_000)},
        index=idx,
    )
    payload = forecast_range(
        "RELIANCE",
        frame,
        confidence_level=0.80,
        training_window="1y",
        cqr_promotion_receipt=active_promotion_receipt(),
    )
    assert payload["cqr"]["status"] == "promoted_canary"
    assert payload["cqr"]["enabled"] is True


def _cli(args: list[str], env_manifest: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["STOCKPILOT_PROMOTION_MANIFEST_PATH"] = str(env_manifest)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "promote_model.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=180,
    )


def test_cli_blocks_bad_candidate_and_promotes_good_one(tmp_path):
    manifest = tmp_path / "cli_manifest.json"
    eval_path = tmp_path / "eval.json"

    eval_path.write_text(json.dumps({"T3": {"coverage": 0.0, "mase": 5.0, "winkler_score": 50.0, "n_forecasts": 200}}), encoding="utf-8")
    blocked = _cli(["promote", "--eval-json", str(eval_path), "--candidate", "bad", "--artifact-hash", "h1"], manifest)
    assert blocked.returncode == 1, blocked.stdout + blocked.stderr
    assert "BLOCKED" in blocked.stderr
    assert not manifest.exists()

    check_before = _cli(["check"], manifest)
    assert check_before.returncode == 1

    eval_path.write_text(json.dumps({"T3": {"coverage": 0.80, "mase": 0.5, "winkler_score": 10.0, "baseline_winkler": 12.0, "diebold_mariano": {"reject_null": True, "p_value": 0.01, "dm_statistic": -3.0}, "n_forecasts": 200}}), encoding="utf-8")
    promoted = _cli(["promote", "--eval-json", str(eval_path), "--candidate", "good", "--artifact-hash", "h2"], manifest)
    assert promoted.returncode == 0, promoted.stdout + promoted.stderr
    assert manifest.exists()

    check_after = _cli(["check"], manifest)
    assert check_after.returncode == 0

    revoked = _cli(["revoke", "--reason", "rollback drill"], manifest)
    assert revoked.returncode == 0
    assert not manifest.exists()
    assert _cli(["check"], manifest).returncode == 1


def test_manifest_path_env_override(manifest):
    assert manifest_path() == manifest


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
