"""Router for the admin domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from services.admin_registry import effective_role, bootstrap_operational_roles, apply_account_actions
from forecasting.model_promotion import manifest_status, promotion_history, rollback_promotion
from forecasting.live_decay import tier_controls, set_tier_pause


router = APIRouter()


def require_model_ops(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if effective_role(user) not in {"admin", "model-ops"}:
        raise HTTPException(status_code=403, detail="Model operations role required")
    return user


def require_support_ops(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if effective_role(user) not in {"admin", "support-ops"}:
        raise HTTPException(status_code=403, detail="Support operations role required")
    return user


class ModelRollbackPayload(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=12, max_length=500)


class TierPausePayload(BaseModel):
    tier: Literal["T0", "T1", "T2", "T3", "T4"]
    paused: bool
    reason: str = Field(min_length=12, max_length=500)


@router.get("/api/v1/admin/model-operations")
def model_operations_endpoint(user: dict[str, Any] = Depends(require_model_ops)) -> dict[str, Any]:
    del user
    try:
        return {"status": manifest_status(), "history": promotion_history(), "controls": tier_controls()}
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Model registry integrity unavailable") from exc


@router.post("/api/v1/admin/model-operations/rollback")
def model_rollback_endpoint(payload: ModelRollbackPayload, user: dict[str, Any] = Depends(require_model_ops)) -> dict[str, Any]:
    try:
        return {"receipt": rollback_promotion(payload.candidate_id, actor=str(user["id"]), reason=payload.reason)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Model registry unavailable") from exc


@router.put("/api/v1/admin/model-operations/pause")
def tier_pause_endpoint(payload: TierPausePayload, user: dict[str, Any] = Depends(require_model_ops)) -> dict[str, Any]:
    try:
        set_tier_pause(payload.tier, paused=payload.paused, actor=str(user["id"]), reason=payload.reason)
        return {"controls": tier_controls()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Model controls unavailable") from exc


@router.get("/api/v1/admin/users")
def user_directory_endpoint(search: str = Query("", max_length=120), mfa: bool | None = None,
                            limit: int = Query(50, ge=1, le=200),
                            user: dict[str, Any] = Depends(require_support_ops),
                            min_age_days: int | None = Query(None, ge=0, le=36500),
                            anomalous: bool | None = None) -> dict[str, Any]:
    del user
    conn = get_connection()
    try:
        from services.login_anomaly import _ensure_tables
        _ensure_tables()
        from services.webauthn import _ensure_table
        _ensure_table()
        rows = conn.execute("""SELECT * FROM (
            SELECT u.id,u.name,u.email,u.created_at,u.account_status,
              (EXISTS(SELECT 1 FROM user_mfa m WHERE m.user_id=u.id AND m.enabled=1)
               OR EXISTS(SELECT 1 FROM webauthn_credentials w WHERE w.user_id=u.id)) AS mfa_enabled,u.role,
              EXISTS(SELECT 1 FROM login_anomalies a WHERE a.user_id=u.id AND a.acknowledged_at IS NULL) AS anomalous_login
            FROM users u) WHERE (LOWER(email) LIKE ? OR LOWER(name) LIKE ?)
            AND (? IS NULL OR mfa_enabled=?)
            AND (? IS NULL OR julianday('now')-julianday(created_at)>=?)
            AND (? IS NULL OR anomalous_login=?) ORDER BY id DESC LIMIT ?""",
            (f"%{search.lower()}%", f"%{search.lower()}%", mfa, mfa, min_age_days, min_age_days, anomalous, anomalous, limit)).fetchall()
        return {"items": [dict(zip(("id", "name", "email", "created_at", "account_status", "mfa_enabled", "role", "anomalous_login"), row)) for row in rows]}
    finally:
        conn.close()


@router.post("/api/v1/admin/operational-roles/bootstrap")
def operational_bootstrap_endpoint(user: dict[str, Any] = Depends(require_admin),
                                   step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token")) -> dict[str, Any]:
    _require_step_up(user, "admin_change", "operational-roles", step_up_token)
    result = bootstrap_operational_roles()
    record_admin_action(actor_id=user.get("id"), actor_email=user.get("email"), action="operational_roles_bootstrap")
    return result



@router.get("/api/v1/admin/instruments/reconcile")
def reconcile_instruments_endpoint(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Read-only drift report: expected bundled universe vs resolved master."""
    try:
        return _serializable({"requested_by": user["id"], **reconcile_instrument_master()})
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Instrument reconciliation is unavailable.") from exc


@router.post("/api/v1/admin/step-up", status_code=201)
def admin_step_up_endpoint(
    payload: StepUpChallengePayload,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Exchange an admin password for one short-lived, single-use action token."""
    action = payload.action.strip().lower()
    if action not in STEP_UP_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "step_up_action_unknown",
                "message": "That action does not use step-up confirmation.",
                "allowed": sorted(STEP_UP_ACTIONS),
            },
        )
    try:
        granted = STEP_UP.challenge(
            actor_email=str(admin.get("email") or ""),
            password=payload.password,
            action=action,
            target=payload.target,
            actor_id=admin.get("id"),
        )
    except StepUpConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "step_up_unavailable", "message": str(exc), "retryable": True},
        ) from exc
    except StepUpError as exc:
        raise HTTPException(status_code=401, detail={"code": "step_up_rejected", "message": str(exc)}) from exc
    return _serializable(granted)


@router.get("/api/v1/admin/security")
def admin_security_center_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Read-only live security posture scorecard. Never mutates application state."""
    del admin
    return _serializable(run_security_checks())


@router.get("/api/v1/admin/overview")
def admin_overview_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    database = database_health_check()
    diagnostics = _forecast_diagnostics()
    try:
        configured = configured_admin_emails()
        config_error = None
    except AdminConfigurationError as exc:
        configured, config_error = [], str(exc)
    return _serializable({
        "version": APP_VERSION,
        "database_status": str(database.get("status", "Unavailable")).lower(),
        "market": market_status(),
        "instrument_count": len(CATALOGUE.load()),
        "counts": aggregate_counts(),
        "errors": error_summary(),
        "administrators": {
            "max_admins": MAX_ADMINS,
            # Only the count and the domain-less local shape are needed here; the
            # allowlist itself is local configuration, so it is echoed back to a
            # verified administrator but never to anyone else.
            "configured": configured,
            "configuration_error": config_error,
        },
        "privileges": ADMIN_PRIVILEGES,
        "scheduler_enabled": _truthy("STOCKPILOT_ENABLE_SCHEDULER"),
        "settings": get_settings(),
        "jobs": diagnostics["jobs"],
        "cache": diagnostics["cache"],
        "data_quality": {
            "saved_forecasts": aggregate_counts().get("saved_forecasts", 0),
            "provider_readiness": diagnostics["providers"],
        },
        "requested_by": admin.get("email"),
    })


@router.get("/api/v1/admin/models")
def admin_models_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """The authoritative model inventory, including honest negative statuses."""
    del admin
    return _serializable({
        "registry": registry_summary(),
        "history_support": history_support_matrix(),
        "note": "A model is only 'production' when the live forecast path uses it on every request.",
    })


@router.get("/api/v1/admin/errors")
def admin_errors_endpoint(limit: int = Query(50, ge=1, le=200), admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Grouped failure counts. No exception text or traceback is ever returned."""
    del admin
    return _serializable({"summary": error_summary(), "groups": error_groups(limit)})


@router.get("/api/v1/admin/setup")
def admin_setup_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Why sign-in or market data is not working, named variable by variable.

    The most common failure report on this deployment is not a defect: it is an
    unset environment variable. This endpoint returns the exact variable names
    that are missing, what each one blocks, and the fix. No secret value is
    returned, only presence and derived metadata such as token expiry.
    """
    del admin
    report = run_setup_diagnostics()
    return _serializable({
        "setup": report,
        "providers": MANAGER.provider_readiness(),
        "database": database_health_check(),
        "version": APP_VERSION,
    })


@router.get("/api/v1/admin/calibration/methodology")
def admin_calibration_methodology_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """How intervals are calibrated, and what this system refuses to claim."""
    del admin
    return _serializable({
        "methodology": describe_calibration_methodology(),
        "supported_confidence_levels": list(CONFIDENCE_LEVELS),
    })


@router.get("/api/v1/admin/calibration")
def admin_calibration_endpoint(
    symbols: str = Query("", description="Comma-separated symbols to calibrate. Up to 25."),
    horizon: int = Query(5, ge=1, le=20),
    confidence: float = Query(0.80),
    timeframe: str = Query("1D"),
    window: str = Query("5y"),
    model: str = Query("damped_drift"),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Measured interval quality per symbol: coverage, error, and skill.

    This is the review surface for the claim the product makes about ranges.
    Every number here is produced walk-forward on the symbol's own history, so
    a symbol that cannot beat a random walk is visible as such instead of
    being averaged away.
    """
    del admin
    requested = [item.strip().upper() for item in str(symbols or "").split(",") if item.strip()]
    if not requested:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "calibration_symbols_required",
                "message": "Pass at least one symbol to calibrate.",
                "retryable": False,
            },
        )
    if len(requested) > 25:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "calibration_too_many_symbols",
                "message": "Calibrate at most 25 symbols per request.",
                "retryable": False,
            },
        )
    try:
        report = calibration_report(
            requested,
            history_loader=_history_loader(timeframe, window),
            horizon=int(horizon),
            confidence=float(confidence),
            model=str(model),
        )
    except CalibrationError as exc:
        raise _feature_error(exc) from exc
    return _serializable({"timeframe": timeframe, "window": window, **report})


@router.get("/api/v1/admin/review")
def admin_review_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """One consolidated operator view: configuration, data, flags, failures.

    Existing admin endpoints each answer one question. This one answers "what
    should I look at first", and deliberately leads with the configuration
    blockers because nothing downstream can work while one is outstanding.
    """
    del admin
    setup = run_setup_diagnostics()
    return _serializable({
        "version": APP_VERSION,
        "configuration": {
            "status": setup["status"],
            "action_required": setup["action_required"],
            "degraded": setup["degraded"],
            "blocked_capabilities": setup["blocked_capabilities"],
            "next_step": setup["next_step"],
        },
        "database": database_health_check(),
        "providers": MANAGER.provider_readiness(),
        "feature_flags": public_status_payload(),
        "counts": aggregate_counts(),
        "errors": error_summary(),
        "forecast_cache": FORECAST_EXECUTION.diagnostics(),
        "calibration": describe_calibration_methodology(),
        "note": (
            "Configuration blockers are listed first because every data surface fails while one is open. "
            "Feature flags default to off and are not enabled by this endpoint."
        ),
    })


@router.get("/api/v1/admin/diagnostics")
def admin_diagnostics_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _forecast_diagnostics()


@router.get("/api/v1/admin/diagnostics/forecasts")
def admin_forecast_diagnostics_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _forecast_diagnostics()


@router.get("/api/v1/admin/diagnostics/jobs")
def admin_job_diagnostics_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _serializable({"jobs": FORECAST_JOBS.diagnostics()})


@router.get("/api/v1/admin/diagnostics/cache")
def admin_cache_diagnostics_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _serializable({
        "cache": {
            "forecast_results": FORECAST_EXECUTION.diagnostics(),
            "market_history": MANAGER.cache_diagnostics(),
        }
    })


@router.get("/api/v1/admin/diagnostics/providers")
def admin_provider_diagnostics_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _serializable({"providers": MANAGER.provider_readiness()})


@router.post("/api/v1/admin/diagnostics/providers/test", status_code=202)
def admin_provider_test_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    MANAGER.start_readiness_probe()
    record_admin_action(
        actor_id=admin.get("id"), actor_email=admin.get("email"),
        action="provider_readiness_test", outcome="started",
    )
    return _serializable({"status": "started", "providers": MANAGER.provider_readiness()})


@router.put("/api/v1/admin/diagnostics/providers/order")
def admin_provider_order_endpoint(
    payload: ProviderOrderPayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    # Reordering providers changes which venue serves live prices, so it is
    # treated as a provider mode change and requires step-up confirmation.
    _require_step_up(admin, "provider_mode_change", "provider_order", step_up_token)
    try:
        order = MANAGER.set_order(payload.order)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "provider_order_invalid", "message": str(exc)}) from exc
    record_admin_action(
        actor_id=admin.get("id"), actor_email=admin.get("email"),
        action="provider_order_update", outcome="ok", detail={"order": ",".join(order)},
    )
    MANAGER.start_readiness_probe()
    return {"order": order, "effective_order": MANAGER.effective_order()}


@router.post("/api/v1/admin/account-lifecycle")
def admin_account_lifecycle_endpoint(
    payload: AccountLifecyclePayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    """Apply lifecycle controls without returning any user-owned content."""
    _require_step_up(
        admin, "admin_change",
        f"account:{payload.action}:{int(payload.account_id)}", step_up_token,
    )
    user_id = payload.account_id
    action = payload.action
    try:
        apply_account_actions([user_id], action, actor_id=int(admin["id"]), actor_email=str(admin["email"]), reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"updated": True, "action": action, "user_id": int(user_id)}


@router.get("/api/v1/admin/audit")
def admin_audit_endpoint(limit: int = Query(100, ge=1, le=1000), admin: dict[str, Any] = Depends(require_support_ops)) -> dict[str, Any]:
    del admin
    return _serializable({"items": admin_audit_events(limit)})


@router.get("/api/v1/admin/settings")
def admin_settings_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _serializable({"settings": describe_settings()})


@router.put("/api/v1/admin/settings")
def admin_settings_update_endpoint(payload: AdminSettingsPayload, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        effective = update_settings(payload.updates, actor_email=admin.get("email"))
    except ValueError as exc:
        record_admin_action(
            actor_id=admin.get("id"), actor_email=admin.get("email"),
            action="settings_update", outcome="rejected",
            detail={"keys": ",".join(sorted(payload.updates))},
        )
        raise HTTPException(status_code=422, detail={"code": "setting_out_of_bounds", "message": str(exc)}) from exc
    record_admin_action(
        actor_id=admin.get("id"), actor_email=admin.get("email"),
        action="settings_update", outcome="ok",
        detail={"keys": ",".join(sorted(payload.updates))},
    )
    return _serializable({"settings": effective})


@router.post("/api/v1/admin/maintenance/{action}")
def admin_maintenance_endpoint(
    action: str,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    if action in STEP_UP_MAINTENANCE_ACTIONS:
        # bootstrap_admins can change who administers the deployment.
        _require_step_up(admin, "admin_change", f"maintenance:{action}", step_up_token)
    if action not in SAFE_MAINTENANCE_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail={"code": "maintenance_action_unknown", "message": "That maintenance action is not available.",
                    "allowed": sorted(SAFE_MAINTENANCE_ACTIONS)},
        )
    outcome = "ok"
    result: dict[str, Any] = {}
    try:
        if action == "refresh_instruments":
            result = CATALOGUE.refresh_from_upstox()
        elif action == "refresh_calendar":
            result = refresh_calendar(datetime.now(timezone.utc).year)
        elif action == "clear_error_groups":
            reset_error_groups()
            result = {"cleared": True}
        elif action == "settle_forecast_outcomes":
            result = settle_due_forecasts()
        else:
            result = bootstrap_admins()
    except Exception as exc:
        support_id = _support_id()
        _log_sanitized(f"admin_maintenance:{action}", support_id, exc)
        record_admin_action(
            actor_id=admin.get("id"), actor_email=admin.get("email"),
            action=f"maintenance:{action}", outcome="failed", detail={"support_id": support_id},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "maintenance_failed", "message": "The maintenance action did not complete.",
                    "retryable": True, "support_id": support_id},
        ) from exc
    record_admin_action(
        actor_id=admin.get("id"), actor_email=admin.get("email"),
        action=f"maintenance:{action}", outcome=outcome,
    )
    return _serializable({"action": action, "result": result})
