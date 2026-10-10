"""Production promotion decision for StockPilot AI.

This module is the single place a candidate model can ship.  The promotion
gate is wired in here rather than left as a library function:

* :func:`decide_promotion` runs the real tier gates (``run_all_tier_gates``)
  and writes the production manifest **only** when every tier passes.  A
  failing gate blocks shipping; it does not merely produce a report.
* :func:`active_promotion_receipt` is the only supported reader of that
  manifest.  The live forecast path passes its output to
  ``forecast_range(cqr_promotion_receipt=...)``, so the CQR challenger can
  never shape published bounds without a fresh, passed gate.

The manifest fails closed: missing, stale, unsigned or un-passed manifests
resolve to ``None``, which ``cqr_canary_status`` reports as
``disabled_pending_real_promotion_gate``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from forecasting.promotion_gate import (
    PromotionGateConfig,
    PromotionGateResult,
    promotion_gate_summary,
    run_all_tier_gates,
)
from forecasting.promotion_store import append_decision, history, is_current, signing_key
from database import DATABASE_ERRORS

LOGGER = logging.getLogger(__name__)

#: Bump whenever the gate semantics or the receipt schema changes.
GATE_VERSION = "stockpilot-promotion-gate-v2"

#: Schema version of the persisted production manifest.
MANIFEST_SCHEMA_VERSION = 2

#: A manifest older than this is stale and must not enable anything.
#: Override with STOCKPILOT_PROMOTION_RECEIPT_MAX_AGE_DAYS.
MAX_RECEIPT_AGE_DAYS = int(os.getenv("STOCKPILOT_PROMOTION_RECEIPT_MAX_AGE_DAYS", "30"))

#: Feature flag recorded with every promotion so the canary width can be
#: rolled back per deployment without re-running the gate.
DEFAULT_CANARY_PCT = float(os.getenv("STOCKPILOT_CQR_CANARY_PCT", "0.1"))

_write_lock = threading.Lock()


def manifest_path() -> Path:
    """Resolved production-manifest path (env-overridable for tests/CI)."""
    configured = os.getenv("STOCKPILOT_PROMOTION_MANIFEST_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "cache" / "forecast_v14" / "promotion_manifest.json"


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Outcome of a promotion attempt.

    ``written`` is True only when the gate passed and the production
    manifest was replaced.  A blocked decision never touches the manifest,
    so whatever was shipping before keeps shipping unchanged.
    """

    candidate_id: str
    passed: bool
    written: bool
    reason: str
    gate_summary: dict[str, Any]
    receipt: dict[str, Any]
    manifest_path: str
    failed_tiers: tuple[str, ...] = field(default_factory=tuple)


def _artifact_digest(artifact_hash: str) -> str:
    """Normalise an operator-supplied artifact reference into a hex digest."""
    value = (artifact_hash or "").strip()
    if not value:
        return ""
    if all(c in "0123456789abcdefABCDEF" for c in value) and len(value) in (32, 40, 64):
        return value.lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_receipt(
    gate_results: Mapping[str, PromotionGateResult],
    *,
    candidate_id: str,
    artifact_hash: str,
    evaluated_at: datetime,
    n_forecasts: int,
    coverage: float,
    target_coverage: float,
    canary_pct: float | None = None,
) -> dict[str, Any]:
    """Build the receipt consumed by ``cqr_canary_status``.

    Every required field is present even for a failed gate; ``passed`` is
    what ``cqr_canary_status`` fails closed on.
    """
    summary = promotion_gate_summary(dict(gate_results))
    winkler_improved = all(bool(result.checks.get("winkler", False)) for result in gate_results.values())
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "passed": bool(summary["overall_passed"]),
        "gate_version": GATE_VERSION,
        "evaluated_at": evaluated_at.isoformat(),
        "artifact_hash": _artifact_digest(artifact_hash),
        "candidate_id": candidate_id,
        "n_forecasts": int(n_forecasts),
        "coverage": round(float(coverage), 6),
        "target_coverage": round(float(target_coverage), 6),
        "winkler_improved": bool(winkler_improved),
        "canary_pct": float(canary_pct if canary_pct is not None else DEFAULT_CANARY_PCT),
        "tiers_total": int(summary["tiers_total"]),
        "tiers_passed": int(summary["tiers_passed"]),
        "tier_results": summary["tier_results"],
    }


def decide_promotion(
    tier_results: Mapping[str, Any],
    *,
    candidate_id: str,
    artifact_hash: str,
    config: PromotionGateConfig | None = None,
    target_coverage: float = 0.80,
    canary_pct: float | None = None,
    manifest: Path | None = None,
    now: datetime | None = None,
) -> PromotionDecision:
    """Run the gate and ship only if every tier passes.

    This is the shipping boundary: on failure the manifest is left untouched
    and ``written`` stays False, so callers (CI, deploy scripts, operators)
    can block the release on the return value.
    """
    if not tier_results:
        raise ValueError("decide_promotion requires at least one tier evaluation result")

    signing_key()
    cfg = config or PromotionGateConfig()
    floor = PromotionGateConfig()
    policy_values = (cfg.min_forecasts_per_tier, cfg.min_forecasts_overall, cfg.max_mase,
                      cfg.coverage_tolerance, cfg.dm_significance, cfg.conditional_coverage_tolerance,
                      cfg.target_coverage, cfg.min_mase_improvement, cfg.min_winkler_improvement,
                      cfg.winkler_significance)
    if (not all(math.isfinite(value) for value in policy_values)
            or cfg.target_coverage != floor.target_coverage or target_coverage != cfg.target_coverage
            or not 0 <= cfg.coverage_tolerance <= floor.coverage_tolerance
            or not 0 < cfg.dm_significance <= floor.dm_significance
            or not 0 < cfg.winkler_significance <= floor.winkler_significance
            or not 0 <= cfg.conditional_coverage_tolerance <= floor.conditional_coverage_tolerance
            or not 0 <= cfg.min_mase_improvement < 1
            or not 0 <= cfg.min_winkler_improvement < 1
            or not 0 <= cfg.max_mase <= floor.max_mase
            or (cfg.max_pinball_loss is not None and
                (not math.isfinite(cfg.max_pinball_loss) or cfg.max_pinball_loss < 0))
            or cfg.min_forecasts_per_tier < floor.min_forecasts_per_tier
            or cfg.min_forecasts_overall < floor.min_forecasts_overall
            or cfg.max_mase > floor.max_mase
            or cfg.coverage_tolerance > floor.coverage_tolerance
            or cfg.dm_significance > floor.dm_significance
            or cfg.conditional_coverage_tolerance > floor.conditional_coverage_tolerance):
        raise ValueError("Production promotion policy cannot weaken the mandatory gate")
    rollout = canary_pct if canary_pct is not None else DEFAULT_CANARY_PCT
    if not math.isfinite(rollout) or not 0 <= rollout <= 1:
        raise ValueError("Canary percentage must be a finite fraction from zero to one")

    evaluated_at = now or datetime.now(timezone.utc)
    gate_results = run_all_tier_gates(dict(tier_results), cfg)
    summary = promotion_gate_summary(gate_results)
    passed = bool(summary["overall_passed"])
    failed_tiers = tuple(sorted(t for t, r in gate_results.items() if not r.passed))

    counts = [max(int(getattr(r, "n_forecasts", 0) or 0), 0) for t, r in tier_results.items() if t != "overall"]
    n_forecasts = int(sum(counts))
    coverages = [float(getattr(r, "coverage", 0.0) or 0.0) for t, r in tier_results.items() if t != "overall"]
    weights = counts or [0] * len(coverages)
    total_weight = float(sum(weights))
    coverage = float(sum(c * w for c, w in zip(coverages, weights)) / total_weight) if total_weight > 0 else 0.0
    if n_forecasts < cfg.min_forecasts_overall:
        passed = False
        summary["overall_passed"] = False
        summary["aggregate_minimum_passed"] = False
        failed_tiers = (*failed_tiers, "aggregate_minimum")

    receipt = build_receipt(
        gate_results,
        candidate_id=candidate_id,
        artifact_hash=artifact_hash,
        evaluated_at=evaluated_at,
        n_forecasts=n_forecasts,
        coverage=coverage,
        target_coverage=target_coverage,
        canary_pct=canary_pct,
    )
    receipt["passed"] = passed
    receipt["tier_validation"] = {
        tier: {"coverage": float(getattr(result, "coverage", 0)),
               "target_coverage": cfg.target_coverage,
               "n_forecasts": int(getattr(result, "n_forecasts", 0))}
        for tier, result in tier_results.items() if tier != "overall"
    }

    target = manifest or manifest_path()
    payload = {"schema_version": MANIFEST_SCHEMA_VERSION, "receipt": receipt,
               "gate_summary": summary, "promoted_at": evaluated_at.isoformat()}
    if not passed:
        append_decision(target, "reject", payload, actor="promotion-gate", reason="Promotion gate failed")
        failed_reason = ", ".join(failed_tiers) or "gate failed"
        LOGGER.error(
            "Promotion blocked for candidate %s: failed tiers %s",
            candidate_id,
            failed_reason,
            extra={"candidate_id": candidate_id, "failed_tiers": list(failed_tiers)},
        )
        return PromotionDecision(
            candidate_id=candidate_id,
            passed=False,
            written=False,
            reason=f"Promotion gate failed for tiers: {failed_reason}",
            gate_summary=summary,
            receipt=receipt,
            manifest_path=str(target),
            failed_tiers=failed_tiers,
        )

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "receipt": receipt,
        "gate_summary": summary,
        "promoted_at": evaluated_at.isoformat(),
    }
    payload = append_decision(target, "promote", payload, actor="promotion-gate", reason="All mandatory tier gates passed",
                              export=lambda signed: _atomic_write(target, signed))
    LOGGER.info(
        "Promoted candidate %s to production manifest %s (tiers passed %s/%s)",
        candidate_id,
        target,
        summary["tiers_passed"],
        summary["tiers_total"],
        extra={"candidate_id": candidate_id},
    )
    return PromotionDecision(
        candidate_id=candidate_id,
        passed=True,
        written=True,
        reason=f"Promoted {candidate_id}: all {summary['tiers_total']} tier gates passed",
        gate_summary=summary,
        receipt=receipt,
        manifest_path=str(target),
        failed_tiers=(),
    )


def _atomic_write(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
    with _write_lock:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, target)


def load_manifest(manifest: Path | None = None) -> dict[str, Any] | None:
    """Read the production manifest, or None if absent/corrupt."""
    target = manifest or manifest_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        LOGGER.error("Promotion manifest unreadable at %s (%s: %s)", target, type(exc).__name__, exc)
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        LOGGER.error("Promotion manifest at %s has an unsupported schema", target)
        return None
    try:
        if not is_current(target, raw):
            return None
    except (*DATABASE_ERRORS, OSError, RuntimeError, ValueError, TypeError):
        LOGGER.error("Promotion signature or audit integrity could not be verified")
        return None
    return raw


def active_promotion_receipt(manifest: Path | None = None) -> dict[str, Any] | None:
    """Return the live promotion receipt, or None when shipping is not allowed.

    Fails closed on: missing manifest, schema mismatch, ``passed`` not True,
    missing/empty artifact hash, or a receipt older than
    ``MAX_RECEIPT_AGE_DAYS``.  This is the function production call sites
    use; nothing else should hand a receipt to ``forecast_range``.
    """
    raw = load_manifest(manifest)
    if not raw:
        return None
    receipt = raw.get("receipt")
    if not isinstance(receipt, dict):
        LOGGER.error("Promotion manifest carries no receipt block")
        return None
    if receipt.get("passed") is not True:
        LOGGER.error("Promotion receipt is not passed; CQR remains disabled")
        return None
    if not str(receipt.get("artifact_hash") or "").strip():
        LOGGER.error("Promotion receipt has no artifact hash; refusing to activate")
        return None
    if str(receipt.get("gate_version")) != GATE_VERSION:
        LOGGER.error(
            "Promotion receipt gate version %s does not match %s",
            receipt.get("gate_version"),
            GATE_VERSION,
        )
        return None
    try:
        evaluated_at = datetime.fromisoformat(str(receipt["evaluated_at"]))
    except (KeyError, TypeError, ValueError):
        LOGGER.error("Promotion receipt has no parseable evaluated_at timestamp")
        return None
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - evaluated_at
    if age < timedelta(0):
        LOGGER.error("Promotion receipt is future-dated; refusing activation")
        return None
    if age > timedelta(days=MAX_RECEIPT_AGE_DAYS):
        LOGGER.error(
            "Promotion receipt is stale (%.1f days > %d day limit); CQR disabled until re-promoted",
            age.days,
            MAX_RECEIPT_AGE_DAYS,
        )
        return None
    return dict(receipt)


def manifest_status(manifest: Path | None = None) -> dict[str, Any]:
    """Observability snapshot of the promotion state (safe to expose)."""
    target = manifest or manifest_path()
    raw = load_manifest(target)
    receipt = raw.get("receipt") if raw else None
    active = active_promotion_receipt(target)
    return {
        "manifest_path": str(target),
        "manifest_exists": target.exists(),
        "schema_version": raw.get("schema_version") if raw else None,
        "candidate_id": (receipt or {}).get("candidate_id"),
        "promoted_at": raw.get("promoted_at") if raw else None,
        "gate_version": (receipt or {}).get("gate_version"),
        "receipt_passed": bool((receipt or {}).get("passed")),
        "active": active is not None,
        "inactive_reason": None if active is not None else _inactive_reason(raw),
        "canary_pct": (active or receipt or {}).get("canary_pct"),
        "max_receipt_age_days": MAX_RECEIPT_AGE_DAYS,
    }


def _inactive_reason(raw: dict[str, Any] | None) -> str:
    if not raw:
        return "manifest_missing_or_unreadable"
    receipt = raw.get("receipt")
    if not isinstance(receipt, dict):
        return "receipt_missing"
    if receipt.get("passed") is not True:
        return "receipt_not_passed"
    if not str(receipt.get("artifact_hash") or "").strip():
        return "artifact_hash_missing"
    if str(receipt.get("gate_version")) != GATE_VERSION:
        return "gate_version_mismatch"
    try:
        evaluated_at = datetime.fromisoformat(str(receipt["evaluated_at"]))
    except (KeyError, TypeError, ValueError):
        return "evaluated_at_unparseable"
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    if evaluated_at > datetime.now(timezone.utc):
        return "receipt_future_dated"
    if datetime.now(timezone.utc) - evaluated_at > timedelta(days=MAX_RECEIPT_AGE_DAYS):
        return "receipt_stale"
    return "unknown"


def promotion_history(manifest: Path | None = None) -> list[dict[str, Any]]:
    """Return verified append-only history; integrity errors fail closed."""
    return history(manifest or manifest_path())


def rollback_promotion(candidate_id: str, *, reason: str, actor: str, manifest: Path | None = None) -> dict[str, Any]:
    """Reactivate a prior gated version without renewing its evidence timestamp."""
    if len(reason.strip()) < 12 or not actor.strip():
        raise ValueError("Rollback requires an actor and a justification of at least 12 characters")
    target = manifest or manifest_path()
    events = promotion_history(target)
    candidates = [event["manifest"] for event in events
                  if event["action"] == "promote" and event["manifest"]["receipt"]["candidate_id"] == candidate_id]
    if not candidates:
        raise ValueError("No previously gated candidate with this ID")
    selected = candidates[-1]
    if any(event["action"] == "revoke" and
           event["manifest"]["receipt"].get("artifact_hash") == selected["receipt"].get("artifact_hash")
           for event in events):
        raise ValueError("Prior artifact was revoked; re-evaluation required")
    receipt = selected["receipt"]
    evaluated = datetime.fromisoformat(receipt["evaluated_at"])
    age = datetime.now(timezone.utc) - evaluated
    if (receipt.get("passed") is not True or receipt.get("gate_version") != GATE_VERSION
            or not receipt.get("artifact_hash") or not timedelta(0) <= age <= timedelta(days=MAX_RECEIPT_AGE_DAYS)):
        raise ValueError("Prior candidate has invalid or expired gate evidence; re-evaluation required")
    selected = append_decision(target, "rollback", selected, actor=actor, reason=reason,
                               export=lambda signed: _atomic_write(target, signed))
    return dict(receipt)


def revoke_promotion(*, reason: str, actor: str, manifest: Path | None = None) -> None:
    if len(reason.strip()) < 12 or not actor.strip():
        raise ValueError("Revocation requires an actor and a justification of at least 12 characters")
    target = manifest or manifest_path()
    previous = load_manifest(target)
    if previous is None:
        if target.exists():
            raise RuntimeError("Cannot revoke an unverified manifest; inspect audit integrity")
        return
    append_decision(target, "revoke", previous, actor=actor, reason=reason,
                    export=lambda signed: target.unlink(missing_ok=True))


__all__: Sequence[str] = (
    "GATE_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_RECEIPT_AGE_DAYS",
    "DEFAULT_CANARY_PCT",
    "PromotionDecision",
    "manifest_path",
    "build_receipt",
    "decide_promotion",
    "load_manifest",
    "active_promotion_receipt",
    "manifest_status",
    "promotion_history",
    "rollback_promotion",
    "revoke_promotion",
)
