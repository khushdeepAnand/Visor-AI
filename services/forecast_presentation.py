"""Turn a full internal forecast result into the two views the product allows.

Normal users receive a *non-prescriptive* research payload: an interval, ranges
derived only from validated output, scenario bands, uncertainty context and
provenance. They never receive a recommendation, an exact entry/target/stop
price, a quantity, a model class name, a diagnostic metric or a feature list.

Administrators receive the same public payload plus a detail block carrying the
internals. Nothing here invents a number: every published bound is either a
validated model output or the last traded price, so a zone is reported only when
it genuinely follows from data, and otherwise reported as unavailable with a
reason.
"""
from __future__ import annotations

from typing import Any

from services.model_registry import PUBLIC_MODEL_LABEL
from services.expected_move import crossover_vs_forecast

#: Largest gap between empirical and nominal interval coverage that still counts
#: as calibrated enough to publish derived zones. Beyond it the interval is still
#: shown, but zones are withheld rather than guessed.
COVERAGE_TOLERANCE = 0.10

#: Smallest validated out-of-sample sample count that may support a zone.
MINIMUM_VALIDATION_SAMPLES = 30
LOW_UTILITY_RANGE_PCT = 0.20

#: Wording used everywhere a range is presented. Deliberately neutral.
RESEARCH_TITLE = "Research range"

DISCLAIMER = (
    "Research support only, not financial advice or a recommendation. "
    "All trading here is simulated."
)

PUBLISHABLE_FORECAST_STATUSES = frozenset({"model_supported", "baseline_only", "low_evidence", "available"})
BLOCKED_FORECAST_STATUSES = frozenset({"abstained", "drift_blocked", "data_quality_blocked"})


def _pct(value: float, base: float) -> float:
    return round((value / base) * 100.0, 2) if base else 0.0


def _band(low: float, high: float, *, label: str, description: str) -> dict[str, Any]:
    lo, hi = (low, high) if low <= high else (high, low)
    return {
        "label": label,
        "low": round(lo, 2),
        "high": round(hi, 2),
        "description": description,
    }


def _width_words(width_pct: float) -> str:
    if width_pct < 2.0:
        return "narrow"
    if width_pct < 6.0:
        return "moderate"
    return "wide"


def _confidence(
    validation: dict[str, Any],
    drift: dict[str, Any],
    evidence_grade: str | None = None,
    *,
    width_pct: float = 0.0,
    trust: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plain-language confidence. No metric values cross into the public view."""
    coverage = float(validation.get("empirical_coverage") or 0.0)
    nominal = float(validation.get("nominal_coverage") or 0.0)
    samples = int(validation.get("samples") or 0)
    gap = abs(coverage - nominal)
    drifting = bool(drift.get("drift_detected"))
    checks = trust or {}
    trust_states = {str(item.get("status")) for item in checks.values() if isinstance(item, dict)}
    if "blocked" in trust_states or "stale" in trust_states:
        level, summary = "low", "A data, regime, or liquidity trust check is blocked or stale, so confidence is low."
    elif not bool(validation.get("beats_naive_baseline")):
        level, summary = "low", "The model did not establish skill beyond persistence; this is baseline-only evidence."
    elif evidence_grade in {"C", "none"} or samples < MINIMUM_VALIDATION_SAMPLES:
        level, summary = "low", "Evidence is limited by short history and a small unseen test sample; this range is not dependable."
    elif width_pct > LOW_UTILITY_RANGE_PCT * 100:
        level, summary = "low", "The calibrated range is too wide to be decision-useful even though it remains statistically honest."
    elif gap <= 0.05 and not drifting:
        level, summary = "high", "On unseen history the range held about as often as it claims to."
    elif gap <= COVERAGE_TOLERANCE and not drifting:
        level, summary = "moderate", "On unseen history the range mostly held, with some slippage."
    elif drifting:
        level, summary = "low", "Recent behaviour has shifted away from the checked period, so the range is less dependable."
    else:
        level, summary = "low", "On unseen history the range did not hold as often as it claims to."
    return {"level": level, "summary": summary}


def _zones_are_supported(
    validation: dict[str, Any],
    *,
    evidence_grade: str | None = None,
    low_utility: bool = False,
) -> tuple[bool, str | None]:
    """Decide whether validated evidence supports publishing derived zones."""
    if evidence_grade in {"C", "none"}:
        return False, "Zones are unavailable because this instrument has only limited historical evidence."
    if low_utility:
        return False, "Zones are unavailable because the forecast range is too wide to be decision-useful."
    samples = int(validation.get("samples") or 0)
    if samples < MINIMUM_VALIDATION_SAMPLES:
        return False, "Not enough unseen history has been checked to derive zones for this instrument."
    coverage = float(validation.get("empirical_coverage") or 0.0)
    nominal = float(validation.get("nominal_coverage") or 0.0)
    if abs(coverage - nominal) > COVERAGE_TOLERANCE:
        return False, "The range is not calibrated closely enough on unseen history to derive zones, so only the range itself is shown."
    if not bool(validation.get("beats_naive_baseline")):
        return False, "The model did not improve on a last-price baseline for this instrument, so no zones are derived."
    return True, None


def _public_horizons(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Range-only strip of the multi-horizon ladder for normal users.

    Public users never receive a diagnostic metric, so each horizon entry keeps
    only the range numbers, status, target and width — the same contract as the
    primary forecast. Horizons the history could not support are listed with
    their data-sufficiency reason instead of being guessed.
    """
    multi = result.get("multi_horizon") or {}
    entries: list[dict[str, Any]] = []
    for entry in multi.get("horizons", []):
        status = str(entry.get("forecast_status") or result.get("forecast_status") or "baseline_only")
        inner = entry.get("forecast") or {}
        horizon_entry = {
            "sessions": entry.get("sessions", 1),
            "label": entry.get("label", "next session"),
            "target_timestamp": entry.get("target_timestamp"),
            "forecast_status": status,
            "abstained": bool(entry.get("abstained") or status in BLOCKED_FORECAST_STATUSES),
            "abstention_reason": entry.get("abstention_reason"),
            "low_data": bool(entry.get("low_data")),
            "forecast": None if status in BLOCKED_FORECAST_STATUSES or not inner else {
                "low": round(float(inner.get("low") or 0.0), 2),
                "median_reference": round(float(inner.get("median") or 0.0), 2),
                "high": round(float(inner.get("high") or 0.0), 2),
                "currency": inner.get("currency", "INR"),
                "confidence_label": inner.get("label"),
                "confidence_level": inner.get("confidence_level"),
            },
            "low_utility": bool(entry.get("low_utility")),
            "width_pct": entry.get("width_pct"),
        }
        # v13: circuit clip per horizon
        if inner.get("circuit_clip"):
            horizon_entry["circuit_clip"] = inner["circuit_clip"]
        # v13: tier per horizon
        if "tier" in entry:
            horizon_entry["tier"] = entry["tier"]
        entries.append(horizon_entry)
    if not entries:
        forecast = dict(result.get("forecast") or {})
        status = str(result.get("forecast_status") or "baseline_only")
        entries.append({
            "sessions": 1,
            "label": "next session",
            "target_timestamp": result.get("target_timestamp"),
            "forecast_status": status,
            "abstained": bool(result.get("abstained") or status in BLOCKED_FORECAST_STATUSES),
            "forecast": None if status in BLOCKED_FORECAST_STATUSES or not forecast else {
                "low": round(float(forecast.get("low") or 0.0), 2),
                "median_reference": round(float(forecast.get("median") or 0.0), 2),
                "high": round(float(forecast.get("high") or 0.0), 2),
                "currency": forecast.get("currency", "INR"),
                "confidence_label": forecast.get("label"),
                "confidence_level": forecast.get("confidence_level"),
            },
            "low_utility": bool(result.get("low_utility")),
            "width_pct": None,
        })
    return entries


def _expected_move_public(
    snapshot: dict[str, Any] | None,
    model_low: float | None,
    model_high: float | None,
) -> dict[str, Any]:
    """The user-facing options-implied expected-move block.

    Market prices (IV-derived moves) are not model internals, so this block is
    public-safe. When no chain is available the block states that plainly
    instead of inventing an implied volatility.
    """
    if not snapshot or not snapshot.get("available"):
        return {
            "available": False,
            "reason": snapshot.get("reason") if snapshot else "not_computed",
            "message": snapshot.get("message") if snapshot else "No live option chain was available when this range was built.",
        }
    return {
        "available": True,
        "underlying": snapshot.get("underlying"),
        "expiry": snapshot.get("expiry"),
        "days_to_expiry": snapshot.get("days_to_expiry"),
        "source": snapshot.get("source"),
        "fetched_at": snapshot.get("fetched_at"),
        "spot_price": snapshot.get("spot_price"),
        "atm_iv": snapshot.get("atm_iv"),
        "expected_moves": snapshot.get("expected_moves", []),
        "model_crossover": crossover_vs_forecast(snapshot, model_low, model_high),
        "basis": snapshot.get("basis"),
        "disclosure": snapshot.get("disclosure"),
    }


def public_forecast(result: dict[str, Any], provenance: dict[str, Any] | None = None, expected_move: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the payload a normal user is allowed to see."""
    forecast = dict(result.get("forecast") or {})
    validation = dict(result.get("validation") or {})
    drift = dict(result.get("drift") or {})
    evidence = dict(result.get("evidence") or {})
    sufficiency = dict(result.get("data_sufficiency") or {})
    samples = int(validation.get("samples") or 0)
    evidence_grade = str(evidence.get("grade") or sufficiency.get("evidence_grade") or ("A" if samples >= 36 else "C" if samples >= 5 else "none"))
    low = float(forecast.get("low") or 0.0)
    high = float(forecast.get("high") or 0.0)
    median = float(forecast.get("median") or 0.0)
    reference_price = float(result.get("current_price") or 0.0)
    basis = reference_price or median or 1.0
    width_pct = _pct(high - low, basis)
    fallback_status = (
        "drift_blocked" if bool(drift.get("drift_detected"))
        else "abstained" if samples < 5
        else "low_evidence" if samples < MINIMUM_VALIDATION_SAMPLES
        else "model_supported" if bool(validation.get("beats_naive_baseline"))
        else "baseline_only"
    )
    forecast_status = str(result.get("forecast_status") or fallback_status)
    abstained = forecast_status in BLOCKED_FORECAST_STATUSES

    low_utility = bool(result["low_utility"]) if "low_utility" in result else width_pct > 20.0
    supported, reason = _zones_are_supported(
        validation,
        evidence_grade=evidence_grade,
        low_utility=low_utility,
    )
    if abstained:
        supported = False
        reason = str(result.get("abstention_reason") or "No numerical range was released because a trust gate was blocked.")
    observation_zone: dict[str, Any] | None = None
    risk_zone: dict[str, Any] | None = None
    if supported:
        if median > low:
            observation_zone = _band(
                low,
                median,
                label="Observation zone",
                description="Part of the range that sits below the central reference.",
            )
        if reference_price > low:
            risk_zone = _band(
                low,
                min(reference_price, high),
                label="Risk range",
                description="How far below the last traded price the range still extends.",
            )
        if observation_zone is None and risk_zone is None:
            reason = "The range does not extend below the last traded price, so no downside zone can be derived."

    scenarios: list[dict[str, Any]] = []
    if not abstained:
        inner_low = min(median, reference_price)
        inner_high = max(median, reference_price)
        scenarios = [
            _band(low, inner_low, label="Bear", description="Lower part of the range, below both the last price and the central reference."),
            _band(inner_low, inner_high, label="Base", description="Band between the last traded price and the central reference."),
            _band(inner_high, high, label="Bull", description="Upper part of the range, above both the last price and the central reference."),
        ]

    payload: dict[str, Any] = {
        "symbol": result.get("symbol"),
        "title": RESEARCH_TITLE,
        "model_label": PUBLIC_MODEL_LABEL,
        "research_range": None if abstained else {
            "low": round(low, 2),
            "median_reference": round(median, 2),
            "high": round(high, 2),
            "currency": forecast.get("currency", "INR"),
            "confidence_label": forecast.get("label"),
            "confidence_level": forecast.get("confidence_level"),
        },
        "reference_price": round(reference_price, 2),
        "target_timestamp": result.get("target_timestamp"),
        "horizon": result.get("horizon"),
        "horizons": _public_horizons(result),
        "horizon_consistency": (result.get("multi_horizon") or {}).get("consistency"),
        "unavailable_horizons": [
            {"sessions": entry.get("sessions"), "reason": entry.get("reason")}
            for entry in (result.get("multi_horizon") or {}).get("unavailable", [])
        ],
        "confidence": _confidence(validation, drift, evidence_grade, width_pct=width_pct, trust=result.get("trust")),
        "evidence": {
            "grade": evidence_grade,
            "summary": evidence.get("summary") or (
                "Limited unseen evidence is available."
                if int(validation.get("samples") or 0) < MINIMUM_VALIDATION_SAMPLES
                else "Evidence inputs were not recorded, so no evidence grade can be assigned."
            ),
        },
        "low_utility": low_utility,
        "forecast_status": forecast_status,
        "support_state": forecast_status,
        "model_supported": forecast_status == "model_supported",
        "baseline_only": forecast_status == "baseline_only",
        "low_evidence": forecast_status == "low_evidence",
        "abstained": abstained,
        "abstention_reason": result.get("abstention_reason"),
        "trust": result.get("trust") or {},
        "uncertainty": {
            "range_width": round(high - low, 2),
            "range_width_pct": width_pct,
            "band": _width_words(width_pct),
            "summary": f"The range spans {width_pct}% of the last traded price, which is {_width_words(width_pct)} for this instrument and window.",
        },
        "observation_zone": observation_zone,
        "risk_zone": risk_zone,
        "zones_unavailable_reason": None if (observation_zone or risk_zone) else reason,
        "scenarios": scenarios,
        "generated_at": result.get("generated_at"),
        "data_timestamp": result.get("data_timestamp"),
        "feature_timestamp": result.get("feature_timestamp"),
        "low_data": bool(result.get("low_data")),
        "low_data_branch": result.get("low_data_branch"),
        "expected_move": _expected_move_public(expected_move, low if not abstained else None, high if not abstained else None),
        "disclaimer": DISCLAIMER,
    }

    # v13: Data tier, evidence grade, and reason (honest disclosure)
    if "tier" in result:
        payload["tier"] = result["tier"]

    # v13: Volatility forecast (most reliable component)
    if "volatility_forecast" in result:
        payload["volatility_forecast"] = result["volatility_forecast"]
    if "volatility_scorecard" in result:
        payload["volatility_scorecard"] = result["volatility_scorecard"]
    if "range_estimators" in result:
        payload["range_estimators"] = result["range_estimators"]

    # v13: Market regime and model routing
    if "market_regime" in result:
        payload["market_regime"] = result["market_regime"]
    if "regime_model_weights" in result:
        payload["regime_model_weights"] = result["regime_model_weights"]

    # v13: Circuit-limit clipping info
    if "forecast" in payload and isinstance(payload["forecast"], dict) and "circuit_clip" in payload["forecast"]:
        payload["circuit_clip"] = payload["forecast"].pop("circuit_clip")

    # v13: Fan chart (distributional output)
    if "fan_chart" in result:
        payload["fan_chart"] = result["fan_chart"]

    # v13: Min width floor applied
    if result.get("min_width_floor_applied"):
        payload["min_width_floor_applied"] = True
        payload["min_half_width"] = result.get("min_half_width")

    # v13: IPO peer blend info
    if "ipo_peer_blend" in result:
        payload["ipo_peer_blend"] = result["ipo_peer_blend"]

    # v13: Hierarchical shrinkage
    if "hierarchical_shrinkage" in result:
        payload["hierarchical_shrinkage"] = result["hierarchical_shrinkage"]

    assessment = dict(result.get("assessment") or {})
    publishable = forecast_status in PUBLISHABLE_FORECAST_STATUSES
    payload["assessment"] = {
        "probability": assessment.get("probability") if publishable else None,
        "expected_return_pct": assessment.get("expected_return_pct") if publishable else None,
        "expected_volatility_pct": assessment.get("expected_volatility_pct") if publishable else None,
        "market_regime": assessment.get("market_regime"),
        "model_agreement": assessment.get("model_agreement") if publishable else None,
        "confidence_score": assessment.get("confidence_score"),
        "data_quality": assessment.get("data_quality"),
        "explanation": assessment.get("explanation"),
    }
    if provenance:
        payload["provenance"] = provenance
    return payload


def present_compare_item(item: dict[str, Any], *, is_admin: bool = False) -> dict[str, Any]:
    """Apply the same public contract to one comparison row.

    A comparison table is a normal-user surface, so it carries the range,
    plain-language confidence and uncertainty only. The diagnostic blocks are
    removed for everyone except an administrator.
    """
    forecast = dict(item.get("forecast") or {})
    validation = dict(item.get("validation") or {})
    drift = dict(item.get("drift") or {})
    evidence = dict(item.get("evidence") or {})
    evidence_grade = str(evidence.get("grade") or "none")
    low = float(forecast.get("low") or 0.0)
    high = float(forecast.get("high") or 0.0)
    basis = float((item.get("quote") or {}).get("price") or forecast.get("median") or 1.0)
    width_pct = _pct(high - low, basis)
    forecast_status = str(item.get("forecast_status") or (
        "drift_blocked" if bool(drift.get("drift_detected"))
        else "model_supported" if bool(validation.get("beats_naive_baseline"))
        else "baseline_only"
    ))
    public = {
        key: value
        for key, value in item.items()
        if key not in {"validation", "drift", "training", "data_sufficiency"}
    }
    public["forecast"] = None if forecast_status in BLOCKED_FORECAST_STATUSES else {
        "low": round(low, 2),
        "median_reference": round(float(forecast.get("median") or 0.0), 2),
        "high": round(high, 2),
        "currency": forecast.get("currency", "INR"),
        "confidence_label": forecast.get("label"),
        "confidence_level": forecast.get("confidence_level"),
    }
    public["confidence"] = _confidence(validation, drift, evidence_grade, width_pct=width_pct, trust=item.get("trust"))
    public["uncertainty"] = {"range_width_pct": width_pct, "band": _width_words(width_pct)}
    public["evidence"] = evidence or {
        "grade": "none",
        "summary": "Evidence inputs were not recorded, so no evidence grade can be assigned.",
    }
    public["low_utility"] = bool(item["low_utility"]) if "low_utility" in item else width_pct > 20.0
    public["forecast_status"] = forecast_status
    public["abstained"] = forecast_status in BLOCKED_FORECAST_STATUSES
    public["abstention_reason"] = item.get("abstention_reason")
    public["model_label"] = PUBLIC_MODEL_LABEL
    if is_admin:
        public["admin_detail"] = {
            "validation": item.get("validation"),
            "drift": item.get("drift"),
            "training": item.get("training"),
        }
    return public


def public_report_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Strip the diagnostic blocks before a result is rendered into a PDF.

    A downloaded report leaves the application, so it must never carry model
    internals. The renderer keeps working because it treats the blocks as
    optional and prints a dash when they are absent.
    """
    return {
        key: value
        for key, value in result.items()
        if key not in {"methods", "validation", "drift", "training", "data_sufficiency", "multi_horizon", "explainability"}
    }


def admin_detail(result: dict[str, Any]) -> dict[str, Any]:
    """The internals, for configured administrators only."""
    return {
        "methods": result.get("methods"),
        "validation": result.get("validation"),
        "drift": result.get("drift"),
        "training": result.get("training"),
        "multi_horizon": result.get("multi_horizon"),
        "explainability": result.get("explainability"),
        # v13 additions
        "tier": result.get("tier"),
        "volatility_forecast": result.get("volatility_forecast"),
        "volatility_scorecard": result.get("volatility_scorecard"),
        "range_estimators": result.get("range_estimators"),
        "market_regime": result.get("market_regime"),
        "regime_model_weights": result.get("regime_model_weights"),
        "cqr": result.get("cqr"),
        "aci_state": result.get("aci_state"),
        "mondrian_groups": result.get("mondrian_groups"),
        "ipo_peer_blend": result.get("ipo_peer_blend"),
        "hierarchical_shrinkage": result.get("hierarchical_shrinkage"),
        "fan_chart": result.get("fan_chart"),
        "min_width_floor_applied": result.get("min_width_floor_applied"),
        "circuit_clip": result.get("circuit_clip"),
    }


def present_forecast(
    result: dict[str, Any],
    *,
    is_admin: bool = False,
    provenance: dict[str, Any] | None = None,
    expected_move: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public payload always; the detail block only for an administrator."""
    payload = public_forecast(result, provenance, expected_move=expected_move)
    if is_admin:
        payload["admin_detail"] = admin_detail(result)
    return payload
