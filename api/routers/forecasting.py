"""Router for the forecasting domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/predict/{symbol}")
def prediction_endpoint(
    symbol: str,
    training_window: str = Query("1y"),
    timeframe: str | None = Query(default=None),
    confidence: float = Query(0.80, gt=0.5, lt=0.99),
    persist: bool = Query(False),
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    # Forecasting itself is public. Persistence is available through a separate
    # authenticated history route to avoid implicit authentication side-effects.
    # The session is read only to decide whether the administrator detail block
    # may be attached; a signed-out caller gets the public payload.
    training_window = training_window.lower()
    if training_window not in TRAINING_WINDOWS:
        raise HTTPException(status_code=422, detail=f"training_window must be one of {sorted(TRAINING_WINDOWS)}")
    resolved_tf = timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]
    try:
        outcome = _execute_forecast(
            symbol,
            resolved_tf,
            training_window,
            confidence,
            horizons=DEFAULT_HORIZONS,
            explain=_is_admin(user),
        )
    except HTTPException:
        raise
    except InsufficientDataError as exc:
        # Too little history for the per-symbol engine. Offer the clearly
        # labelled pooled cross-instrument range when that flagged path is
        # enabled; otherwise keep the honest insufficient_history refusal.
        fallback = _low_history_fallback(symbol, resolved_tf, training_window, confidence, user)
        if fallback is not None:
            return _serializable(fallback)
        raise _forecast_error(exc) from exc
    except Exception as exc:
        raise _forecast_error(exc) from exc
    payload = present_forecast(outcome.result, is_admin=_is_admin(user), provenance=_provenance(outcome.frame), expected_move=_expected_move_for_symbol(symbol))
    if persist:
        payload["persistence_note"] = "Use POST /api/v1/predictions/{symbol}/save while authenticated to save this forecast."
    payload["context"] = outcome.frame.attrs.get("context")
    payload["execution"] = outcome.trace
    return _serializable(payload)


@router.post("/api/v1/forecast-jobs", status_code=202)
def create_forecast_job(
    payload: ForecastJobPayload,
    request: Request,
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    training_window = payload.training_window.strip().lower()
    if training_window not in TRAINING_WINDOWS:
        raise HTTPException(
            status_code=422,
            detail={"code": "forecast_input_rejected", "message": "The training window is not supported."},
        )
    timeframe = payload.timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]
    if timeframe not in TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail={"code": "forecast_input_rejected", "message": "The timeframe is not supported."},
        )
    try:
        normalized = MANAGER.normalize_symbol(payload.symbol)
        job, coalesced = FORECAST_JOBS.submit(
            owner_id=_forecast_job_owner(request, user),
            request={
                "symbol": normalized,
                "timeframe": timeframe,
                "training_window": training_window,
                "confidence": payload.confidence,
            },
            runner=lambda job_request: _run_forecast_job(job_request, is_admin=_is_admin(user)),
            error_handler=_job_error_payload,
        )
    except ForecastJobQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": exc.code, "message": "Too many forecast jobs are already active.", "retryable": True},
        ) from exc
    except ForecastJobQueueFull as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "Forecast capacity is temporarily full.", "retryable": True},
        ) from exc
    except (InstrumentNotFoundError, ValueError) as exc:
        raise _forecast_error(exc) from exc
    return _serializable({**job, "coalesced": coalesced})


@router.get("/api/v1/forecast-jobs/{job_id}")
def get_forecast_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    try:
        return _serializable(FORECAST_JOBS.get(job_id, owner_id=_forecast_job_owner(request, user)))
    except ForecastJobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": "Forecast job not found."},
        ) from exc


@router.delete("/api/v1/forecast-jobs/{job_id}")
def delete_forecast_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    try:
        return _serializable(FORECAST_JOBS.cancel_or_delete(job_id, owner_id=_forecast_job_owner(request, user)))
    except ForecastJobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": "Forecast job not found."},
        ) from exc


@router.post("/api/v1/predictions/{symbol}/save")
def save_prediction_endpoint(
    symbol: str,
    training_window: str = Query("1y"),
    timeframe: str | None = Query(default=None),
    confidence: float = Query(0.80, gt=0.5, lt=0.99),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    training_window = training_window.lower()
    if training_window not in TRAINING_WINDOWS:
        raise HTTPException(status_code=422, detail=f"training_window must be one of {sorted(TRAINING_WINDOWS)}")
    resolved_tf = timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]
    try:
        outcome = _execute_forecast(
            symbol,
            resolved_tf,
            training_window,
            confidence,
            horizons=DEFAULT_HORIZONS,
            explain=_is_admin(user),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _forecast_error(exc) from exc
    normalized, frame, result = outcome.symbol, outcome.frame, outcome.result
    context = dict(frame.attrs.get("context") or {})
    provenance = _provenance(frame)
    context.setdefault("provider", frame.attrs.get("provider") or provenance["source"])
    context.setdefault("as_of", provenance["as_of"])
    context.setdefault("is_stale", provenance["is_stale"])
    result["context"] = context
    # The stored row keeps the internal result so calibration can be audited
    # later; the response returns only what this caller is allowed to read.
    # One immutable ledger row is written per computed horizon so empirical
    # coverage can be reported honestly per horizon (1/3/5/10 sessions).
    primary_id = save_range_forecast(user["id"], normalized, result)
    primary_sessions = int((result.get("horizon") or {}).get("sessions") or 1)
    prediction_ids: dict[str, int] = {str(primary_sessions): primary_id}
    multi = result.get("multi_horizon") or {}
    for entry in multi.get("horizons", []):
        horizon = int(entry["sessions"])
        if horizon == primary_sessions:
            continue
        horizon_payload = dict(result)
        horizon_payload["horizon"] = {
            "bars": horizon,
            "sessions": horizon,
            "timeframe": (result.get("horizon") or {}).get("timeframe"),
            "label": entry["label"],
        }
        horizon_payload["forecast"] = entry["forecast"]
        horizon_payload["target_timestamp"] = entry["target_timestamp"]
        horizon_payload["forecast_status"] = entry["forecast_status"]
        horizon_payload["low_utility"] = entry["low_utility"]
        horizon_payload["abstention_reason"] = entry["abstention_reason"]
        prediction_ids[str(horizon)] = save_range_forecast(user["id"], normalized, horizon_payload)
    payload = present_forecast(result, is_admin=_is_admin(user), provenance=_provenance(frame), expected_move=_expected_move_for_symbol(normalized))
    payload["context"] = frame.attrs.get("context")
    payload["execution"] = outcome.trace
    return _serializable({"prediction_id": primary_id, "prediction_ids": prediction_ids, **payload})


@router.get("/api/v1/predictions/history")
def prediction_history_endpoint(limit: int = Query(100, ge=1, le=1000), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": _serializable(get_prediction_details(user["id"], limit=limit))}


@router.post("/api/v1/predictions/{prediction_id}/settle")
def settle_prediction_endpoint(prediction_id: int, payload: ForecastSettlementPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    del payload
    conn = get_connection()
    owned = conn.execute("SELECT id FROM prediction_history WHERE id=? AND user_id=?", (prediction_id, user["id"])).fetchone()
    conn.close()
    if not owned:
        raise HTTPException(status_code=404, detail="Forecast not found.")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "automatic_settlement_only",
            "message": "Official forecast outcomes are settled automatically from authoritative market data; user-supplied prices are not accepted.",
        },
    )


@router.get("/api/v1/predictions/calibration")
def prediction_calibration_endpoint(limit: int = Query(500, ge=1, le=5000), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    rows = get_settled_range_forecasts(user["id"], limit=limit)
    quality = group_interval_quality(rows)
    by_horizon = group_by_horizon(rows)
    counts = get_forecast_outcome_counts(user["id"])
    quality["overall"].update({
        "coverage_numerator": sum(int(row["coverage_hit"]) for row in rows),
        "calibration_denominator": len(rows),
    })
    for group in quality["by_window_timeframe"]:
        grouped_rows = [
            row for row in rows
            if str(row.get("training_window") or "unknown") == group["training_window"]
            and str(row.get("timeframe") or "unknown") == group["timeframe"]
        ]
        group.update({
            "coverage_numerator": sum(int(row["coverage_hit"]) for row in grouped_rows),
            "calibration_denominator": len(grouped_rows),
        })
    for group in by_horizon:
        grouped_rows = [
            row for row in rows
            if str(row.get("horizon") or row.get("horizon_sessions") or "1") == group["horizon"]
        ]
        group.update({
            "coverage_numerator": sum(int(row["coverage_hit"]) for row in grouped_rows),
            "calibration_denominator": len(grouped_rows),
        })
    quality["by_horizon"] = by_horizon
    return _serializable({**quality, "track_record": counts})


@router.get("/api/v1/predictions/quality")
def prediction_quality_endpoint(
    limit: int = Query(3000, ge=100, le=10000),
    symbol: str | None = Query(default=None),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Admin-only model-health insight: drift + auto-adaptation state.

    Deliberately aggregates authoritative settled outcomes across all users,
    because model quality belongs to the model, not to one caller. The payload
    includes the rolling-coverage/MASE/directional dashboard, the persisted
    health records, and when each symbol's model was last refreshed (scheduled
    or drift-triggered auto-retrain).
    """
    rows = get_settled_rows_for_quality(limit=limit)
    if symbol:
        needle = str(symbol).strip().upper()
        rows = [row for row in rows if str(row.get("symbol") or "").strip().upper() == needle]
    dashboard = model_quality_dashboard(rows)
    refreshed = _last_refreshed_map()
    if symbol and needle in refreshed:
        refreshed = {needle: refreshed[needle]}
    return _serializable({
        "dashboard": dashboard,
        "health_records": latest_model_health_by_group(limit=500),
        "last_refreshed": refreshed,
        "scope": {"all_users_authoritative_settlements": True, "symbol_filter": symbol},
        "disclaimer": "Model health reflects settled historical range outcomes only; it is not a forecast.",
    })


@router.get("/api/v1/scorecard")
def public_scorecard_endpoint(
    tier: str | None = Query(default=None, description="Filter by tier: T0, T1, T2, T3, T4"),
    horizon: int | None = Query(default=None, description="Filter by horizon in sessions"),
    limit_symbols: int = Query(500, ge=10, le=5000, description="Max symbols to include"),
) -> dict[str, Any]:
    """Public rolling scorecard for model performance across all tiers.

    Provides transparent, real-time track record for the whole universe:
    - Coverage, interval score (Winkler), MASE by tier
    - Conditional coverage in high-volatility regimes
    - Promotion gate status per tier
    - Auto-widened/retired model counts

    This builds trust and keeps the team honest.
    """
    from services.scorecard import build_public_scorecard

    scorecard = build_public_scorecard(
        tier_filter=tier,
        horizon_filter=horizon,
        limit_symbols=limit_symbols,
    )
    return _serializable(scorecard)


@router.get("/api/v1/compare")
def compare_endpoint(
    symbols: str = Query(..., description="Comma-separated 2-4 NSE/BSE symbols"),
    training_window: str = Query("1y"),
    timeframe: str | None = Query(default=None),
    confidence: float = Query(0.80, ge=0.60, le=0.95),
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    requested = [item.strip() for item in symbols.split(",") if item.strip()]
    try:
        raw_items = compare_symbols(
            requested,
            training_window=training_window,
            timeframe=timeframe,
            confidence_level=confidence,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail="Market data is temporarily unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    admin = _is_admin(user)
    items = [present_compare_item(item, is_admin=admin) for item in raw_items]
    return _serializable({"items": items, "count": len(items), "model_label": PUBLIC_MODEL_LABEL})


@router.get("/api/v1/risk/{symbol}")
def risk_endpoint(symbol: str, timeframe: str = Query("1D"), window: str = Query("5y"), horizon_days: int = Query(30, ge=1, le=756), simulations: int = Query(2000, ge=100, le=100000)) -> dict[str, Any]:
    normalized, frame = _history(symbol, timeframe, window)
    returns = price_returns(frame["Close"])
    return _serializable({"symbol": normalized, "historical_metrics": calculate_performance_metrics(frame["Close"]), "monte_carlo": monte_carlo_projection(float(frame["Close"].iloc[-1]), returns, horizon_days=horizon_days, simulations=simulations)})


@router.post("/api/v1/risk/position-size")
def position_size_endpoint(payload: PositionSizePayload) -> dict[str, Any]:
    try:
        return calculate_position_size(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/reports/forecast/{symbol}.pdf")
def forecast_report_endpoint(
    symbol: str,
    training_window: str = Query("1y"),
    timeframe: str | None = Query(default=None),
    confidence: float = Query(0.80, ge=0.60, le=0.95),
    user: dict[str, Any] | None = Depends(optional_user),
) -> Response:
    training_window = training_window.lower()
    if training_window not in TRAINING_WINDOWS:
        raise HTTPException(status_code=422, detail=f"training_window must be one of {sorted(TRAINING_WINDOWS)}")
    resolved_tf = timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]
    try:
        outcome = _execute_forecast(symbol, resolved_tf, training_window, confidence)
        # A downloaded PDF leaves the application, so model internals are removed
        # unless the requester is a configured administrator.
        content = generate_forecast_pdf(
            outcome.result if _is_admin(user) else public_report_payload(outcome.result)
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _forecast_error(exc) from exc
    safe_symbol = outcome.symbol.replace(" ", "-")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=stockpilot-{safe_symbol}-forecast.pdf"},
    )
