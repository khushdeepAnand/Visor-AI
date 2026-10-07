"""Router for the admin_operations domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from api.routers.admin import require_support_ops
from services.admin_registry import apply_account_actions


router = APIRouter()


class BulkAccountPayload(BaseModel):
    model_config = {"str_strip_whitespace": True}
    account_ids: list[int] = Field(min_length=1, max_length=100)
    action: Literal["suspend", "reinstate", "force_logout", "reset_lockout", "force_mfa_reset"]
    reason: str = Field(min_length=12, max_length=160)


@router.post("/api/v1/admin/accounts/bulk")
def bulk_account_endpoint(payload: BulkAccountPayload, admin: dict[str, Any] = Depends(require_admin),
                          step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token")) -> dict[str, Any]:
    ids = sorted(set(payload.account_ids))
    target = f"accounts:{payload.action}:" + ",".join(map(str, ids))
    _require_step_up(admin, "admin_change", target, step_up_token)
    try:
        return apply_account_actions(ids, payload.action, actor_id=int(admin["id"]), actor_email=str(admin["email"]), reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ComplianceReviewPayload(BaseModel):
    model_config = {"str_strip_whitespace": True}
    status: Literal["open", "in_progress", "complete", "blocked"]
    note: str = Field(min_length=12, max_length=500)


@router.get("/api/v1/admin/compliance")
def compliance_operations_endpoint(limit: int = Query(100, ge=1, le=200),
                                   user: dict[str, Any] = Depends(require_support_ops)) -> dict[str, Any]:
    from services.compliance import review_checklist, acknowledgment_directory, RESEARCH_ACKNOWLEDGMENT_VERSION
    return {"version": RESEARCH_ACKNOWLEDGMENT_VERSION, "checklist": review_checklist(), "accounts": acknowledgment_directory(limit)}


@router.put("/api/v1/admin/compliance/{item_id}")
def compliance_update_endpoint(item_id: str, payload: ComplianceReviewPayload,
                               user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    from services.compliance import update_review_item, review_checklist
    try:
        update_review_item(item_id, status=payload.status, note=payload.note, actor_id=int(user["id"]), actor_email=str(user["email"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"checklist": review_checklist()}


class QueueActionPayload(BaseModel):
    model_config = {"str_strip_whitespace": True}
    reason: str = Field(min_length=12, max_length=160)


@router.get("/api/v1/admin/queue")
def queue_operations_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    from services.task_queue import operation_snapshot
    return operation_snapshot()


@router.post("/api/v1/admin/queue/trigger/{name}", status_code=202)
def queue_trigger_endpoint(name: str, payload: QueueActionPayload, admin: dict[str, Any] = Depends(require_admin),
                           step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token")) -> dict[str, Any]:
    from services.task_queue import submit
    tasks = {"retention": "run_retention", "instruments": "refresh_instruments", "backups": "backup_databases", "decay": "enforce_decay"}
    if name not in tasks:
        raise HTTPException(status_code=422, detail="Unknown scheduled task")
    _require_step_up(admin, "admin_change", f"queue:trigger:{name}", step_up_token)
    result = submit(f"services.task_queue.tasks.{tasks[name]}")
    record_admin_action(actor_id=admin.get("id"), actor_email=admin.get("email"), action="queue_trigger",
                        target=name, outcome=str(result.get("mode")), detail={"reason": payload.reason})
    if result.get("mode") not in {"queued", "inline"}:
        raise HTTPException(status_code=503, detail="Job submission failed; inspect queue health")
    return {"mode": result["mode"], "task_id": result.get("task_id")}


@router.post("/api/v1/admin/queue/replay/{entry_id}")
def queue_replay_endpoint(entry_id: str, payload: QueueActionPayload, admin: dict[str, Any] = Depends(require_admin),
                          step_up_token: str | None = Header(default=None, alias="X-Step-Up-Token")) -> dict[str, Any]:
    from services.task_queue import replay_dead_letter
    _require_step_up(admin, "admin_change", f"queue:replay:{entry_id}", step_up_token)
    replayed = replay_dead_letter(entry_id)
    record_admin_action(actor_id=admin.get("id"), actor_email=admin.get("email"), action="queue_replay",
                        target=entry_id, outcome="ok" if replayed else "failed", detail={"reason": payload.reason})
    if not replayed:
        raise HTTPException(status_code=409, detail="Replay unavailable; the entry is missing or submission failed")
    return {"replayed": True}



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
