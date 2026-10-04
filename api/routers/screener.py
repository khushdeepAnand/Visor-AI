"""Router for the screener domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/screener/fields")
def screener_fields_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Screenable fields, with the evidence each metric is computed from."""
    return _serializable({"fields": describe_fields()})


@router.post("/api/v1/screener/run")
def screener_run_endpoint(payload: ScreenerRunPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Evaluate user-defined technical filters over the requested symbols."""
    universe, source = _resolve_universe(payload.symbols, payload.universe, user)
    if not universe:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "screener_symbols_required",
                "message": "Supply symbols, or add symbols to your watchlist and screen that.",
                "retryable": False,
            },
        )
    filters = [
        {"field": rule.field, "op": rule.op, "value": rule.value, "low": rule.low, "high": rule.high}
        for rule in payload.filters
    ]
    try:
        result = run_screen(
            symbols=universe,
            history_loader=_history_loader(payload.timeframe, payload.window),
            filters=filters,
            sort_by=payload.sort_by,
            descending=payload.descending,
            limit=payload.limit,
        )
    except ScreenerError as exc:
        raise _feature_error(exc) from exc
    result["request"] = {"symbol_source": source, "timeframe": payload.timeframe, "window": payload.window}
    return _serializable(result)


@router.post("/api/v1/screener/saved", status_code=201)
def screener_save_endpoint(payload: SavedScreenPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    filters = [
        {"field": rule.field, "op": rule.op, "value": rule.value, "low": rule.low, "high": rule.high}
        for rule in payload.filters
    ]
    try:
        saved = SCREENS.save_screen(
            user_id=user["id"],
            name=payload.name,
            filters=filters,
            sort_by=payload.sort_by,
            descending=payload.descending,
            symbols=payload.symbols,
        )
    except ScreenerError as exc:
        raise _feature_error(exc) from exc
    return _serializable(saved)


@router.get("/api/v1/screener/saved")
def screener_saved_list_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        screens = SCREENS.list_screens(user_id=user["id"])
    except ScreenerError as exc:
        raise _feature_error(exc, status_code=503) from exc
    return _serializable({"screens": screens})


@router.delete("/api/v1/screener/saved/{screen_id}")
def screener_saved_delete_endpoint(screen_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        return _serializable(SCREENS.delete_screen(user_id=user["id"], screen_id=screen_id))
    except ScreenerError as exc:
        raise _feature_error(exc, status_code=404) from exc


@router.post("/api/v1/screener/saved/{screen_id}/run")
def screener_saved_run_endpoint(
    screen_id: int,
    timeframe: str = Query("1D"),
    window: str = Query("1y"),
    limit: int = Query(50),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Run a saved screen against its pinned symbols, or your watchlist."""
    try:
        screen = SCREENS.get_screen(user_id=user["id"], screen_id=screen_id)
    except ScreenerError as exc:
        raise _feature_error(exc, status_code=404) from exc
    universe = [str(item).strip().upper() for item in (screen.get("symbols") or [])]
    source = "saved_screen"
    if not universe:
        universe = _watchlist_symbols(user)
        source = "watchlist"
    if not universe:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "screener_symbols_required",
                "message": "This saved screen has no pinned symbols and your watchlist is empty.",
                "retryable": False,
            },
        )
    try:
        result = run_screen(
            symbols=universe,
            history_loader=_history_loader(timeframe, window),
            filters=screen["filters"],
            sort_by=(screen.get("sort") or {}).get("field"),
            descending=bool((screen.get("sort") or {}).get("descending", True)),
            limit=limit,
        )
    except ScreenerError as exc:
        raise _feature_error(exc) from exc
    result["request"] = {
        "symbol_source": source,
        "timeframe": timeframe,
        "window": window,
        "saved_screen": {"id": screen["id"], "name": screen["name"]},
    }
    return _serializable(result)
