"""Router for the admin_operations domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/admin/operations")
def admin_operations_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """One read model for kill switches, feature flags and status banners."""
    del admin
    return _serializable({
        "operations": GUARDRAILS.operations_summary(),
        "step_up": STEP_UP.requirements(),
        "low_history_model": low_history_status(),
        "invalidatable_caches": list(INVALIDATABLE_CACHES),
    })


@router.post("/api/v1/admin/operations/kill-switches", status_code=201)
def admin_create_kill_switch_endpoint(
    payload: KillSwitchPayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    """Pause forecast publication for one scope. Requires step-up confirmation."""
    _require_step_up(
        admin, "forecast_kill_switch",
        f"{payload.scope}:{payload.target or 'all'}", step_up_token,
    )
    try:
        switch = GUARDRAILS.create_kill_switch(
            scope=payload.scope,
            target=payload.target,
            reason=payload.reason,
            expires_in_hours=payload.expires_in_hours,
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=422, detail={"code": "kill_switch_rejected", "message": str(exc)}) from exc
    return _serializable({"kill_switch": switch})


@router.get("/api/v1/admin/operations/kill-switches")
def admin_list_kill_switches_endpoint(
    include_inactive: bool = Query(False),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del admin
    return _serializable({"items": GUARDRAILS.list_kill_switches(include_inactive=include_inactive)})


@router.delete("/api/v1/admin/operations/kill-switches/{switch_id}")
def admin_revoke_kill_switch_endpoint(
    switch_id: int,
    payload: KillSwitchRevokePayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    """Resume forecasts for a paused scope. Requires step-up confirmation."""
    _require_step_up(admin, "forecast_kill_switch", f"revoke:{switch_id}", step_up_token)
    try:
        switch = GUARDRAILS.revoke_kill_switch(
            int(switch_id),
            reason=payload.reason,
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=404, detail={"code": "kill_switch_not_found", "message": str(exc)}) from exc
    return _serializable({"kill_switch": switch})


@router.put("/api/v1/admin/operations/flags/{name}")
def admin_feature_flag_endpoint(
    name: str,
    payload: FeatureFlagPayload,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Stage or roll back a flagged capability with an auditable reason."""
    try:
        flag = GUARDRAILS.set_feature_flag(
            name,
            enabled=payload.enabled,
            rollout_percent=payload.rollout_percent,
            reason=payload.reason,
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=422, detail={"code": "feature_flag_rejected", "message": str(exc)}) from exc
    return _serializable({"flag": flag})


@router.post("/api/v1/admin/operations/banners", status_code=201)
def admin_draft_banner_endpoint(
    payload: BannerDraftPayload,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Draft a status banner. A draft is never visible until it is published."""
    try:
        banner = GUARDRAILS.draft_banner(
            level=payload.level,
            headline=payload.headline,
            body=payload.body,
            starts_at=payload.starts_at,
            ends_in_hours=payload.ends_in_hours,
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=422, detail={"code": "banner_rejected", "message": str(exc)}) from exc
    return _serializable({"banner": banner})


@router.get("/api/v1/admin/operations/banners")
def admin_list_banners_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    del admin
    return _serializable({"items": GUARDRAILS.list_banners()})


@router.post("/api/v1/admin/operations/banners/{banner_id}/publish")
def admin_publish_banner_endpoint(
    banner_id: int,
    payload: BannerPublishPayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    """Publish a drafted banner after preview confirmation and step-up."""
    _require_step_up(admin, "status_banner_publish", f"banner:{banner_id}", step_up_token)
    try:
        banner = GUARDRAILS.publish_banner(
            int(banner_id),
            confirmed_preview=bool(payload.confirmed_preview),
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=422, detail={"code": "banner_publish_rejected", "message": str(exc)}) from exc
    return _serializable({"banner": banner})


@router.post("/api/v1/admin/operations/banners/{banner_id}/withdraw")
def admin_withdraw_banner_endpoint(
    banner_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Withdraw a published banner immediately. Withdrawal is never gated."""
    try:
        banner = GUARDRAILS.withdraw_banner(
            int(banner_id),
            actor_email=admin.get("email"),
            actor_id=admin.get("id"),
        )
    except GuardrailError as exc:
        raise HTTPException(status_code=404, detail={"code": "banner_not_found", "message": str(exc)}) from exc
    return _serializable({"banner": banner})


@router.post("/api/v1/admin/operations/cache/invalidate")
def admin_cache_invalidate_endpoint(
    payload: CacheInvalidationPayload,
    admin: dict[str, Any] = Depends(require_admin),
    step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token"),
) -> dict[str, Any]:
    """Invalidate a forecast cache. Requires step-up confirmation."""
    cache = payload.cache.strip().lower()
    if cache not in INVALIDATABLE_CACHES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "cache_unknown",
                "message": "That cache cannot be invalidated from this surface.",
                "allowed": list(INVALIDATABLE_CACHES),
            },
        )
    _require_step_up(admin, "cache_invalidation", cache, step_up_token)
    cleared: dict[str, bool] = {}
    if cache in {"forecast_cache", "all"}:
        FORECAST_EXECUTION.clear()
        cleared["forecast_cache"] = True
    if cache in {"forecast_jobs", "all"}:
        FORECAST_JOBS.clear()
        cleared["forecast_jobs"] = True
    record_admin_action(
        actor_id=admin.get("id"), actor_email=admin.get("email"),
        action="cache_invalidate", target=cache, outcome="ok",
        detail={"reason": payload.reason},
    )
    return _serializable({"cleared": cleared, "diagnostics": FORECAST_EXECUTION.diagnostics()})
