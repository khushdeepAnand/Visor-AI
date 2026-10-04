"""Router for the market domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/market/status")
def market_status_endpoint() -> dict[str, Any]:
    return market_status()


@router.get("/api/v1/market/stream-health")
def market_stream_health_endpoint() -> dict[str, Any]:
    status = LIVE_QUOTE_HUB.health()
    return {
        "native_stream_active": bool(status.get("native_stream_active")),
        "rest_fallback": bool(status.get("rest_fallback", True)),
    }


@router.get("/api/v1/market/providers/health")
def market_provider_health_endpoint() -> dict[str, Any]:
    """Public, secret-free provider availability and current failover order."""
    return _serializable(MANAGER.health())


@router.post("/api/v1/market/calendar/refresh/{year}")
def refresh_calendar_endpoint(year: int, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Pull a year's NSE holiday/special-session calendar from a configured source.

    Requires STOCKPILOT_NSE_CALENDAR_URL (or a source_url override) to be set;
    the bundled/cached calendar keeps serving market_status() safely if this
    is never called or fails.
    """
    try:
        result = refresh_calendar(year)
        return _serializable({"requested_by": user["id"], "year": year, **result})
    except CalendarRefreshError as exc:
        raise HTTPException(status_code=503, detail="Calendar refresh is unavailable.") from exc


@router.get("/api/v1/market/search")
def market_search(q: str = Query("", max_length=80), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return {"results": [item.to_dict() for item in MANAGER.search(q, limit)]}


@router.get("/api/v1/news/{symbol}")
def news_endpoint(symbol: str, limit: int = Query(10, ge=1, le=25)) -> dict[str, Any]:
    try:
        return _serializable(get_news(symbol, limit))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/market/instruments/refresh")
def refresh_instruments(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        result = CATALOGUE.refresh_from_upstox()
        return {"requested_by": user["id"], **result}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Instrument refresh is unavailable.") from exc


@router.get("/api/v1/market/quote/{symbol}")
def quote_endpoint(symbol: str, timeframe: str = "quote") -> dict[str, Any]:
    try:
        if timeframe != "quote" and timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe. Choose one of {sorted(TIMEFRAMES)}")
        quote = MANAGER.get_quote(symbol) if timeframe == "quote" else MANAGER.get_quote(symbol, timeframe=timeframe)
        return _serializable(quote.to_dict())
    except Exception as exc:
        _raise_market_error(exc)
        raise AssertionError("unreachable")


@router.get("/api/v1/market/history/{symbol}")
def history_endpoint(
    symbol: str,
    timeframe: str = Query("1D"),
    window: str = Query("1y"),
    limit: int = Query(5000, ge=20, le=100000),
) -> dict[str, Any]:
    normalized, frame = _history(symbol, timeframe, window)
    return _serializable({
        "symbol": normalized,
        "timeframe": timeframe,
        "window": window,
        "source": frame.attrs.get("source", frame.attrs.get("provider", "unknown")),
        "provider": frame.attrs.get("provider"),
        "is_stale": bool(frame.attrs.get("is_stale", False)),
        "context": frame.attrs.get("context"),
        "rows": len(frame),
        "candles": _frame_records(frame, limit),
    })


@router.get("/api/v1/market/history-support")
def history_support_endpoint() -> dict[str, Any]:
    """Publish which timeframe/window pairs the data provider can actually serve.

    The client uses this to disable impossible combinations up front instead of
    letting a user select one and receive an error.
    """
    return {"windows": sorted(WINDOW_DAYS, key=lambda name: WINDOW_DAYS[name]), "timeframes": history_support_matrix()}
