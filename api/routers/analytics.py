"""Router for the analytics domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/indicators/{symbol}")
def indicators_endpoint(symbol: str, timeframe: str = Query("1D"), window: str = Query("1y")) -> dict[str, Any]:
    normalized, frame = _history(symbol, timeframe, window)
    enriched = add_indicators(frame)
    latest = enriched.iloc[-1]
    columns = [c for c in enriched.columns if c not in {"Open", "High", "Low", "Close", "Volume"}]
    return _serializable({"symbol": normalized, "as_of": enriched.index[-1], "price": latest.get("Close"), "indicators": {c: latest.get(c) for c in columns}, "context": frame.attrs.get("context")})


@router.get("/api/v1/strategy/{symbol}")
def strategy_endpoint(symbol: str, timeframe: str = Query("1D"), window: str = Query("5y"), strategy: str = Query("ma"), transaction_cost_bps: float = Query(10.0, ge=0, le=500)) -> dict[str, Any]:
    normalized, frame = _history(symbol, timeframe, window)
    key = strategy.strip().lower()
    if key in {"ma", "moving_average", "moving-average"}:
        result = run_moving_average_strategy(frame, transaction_cost_bps=transaction_cost_bps)
    elif key in {"rsi", "mean_reversion", "mean-reversion"}:
        result = run_rsi_strategy(frame, transaction_cost_bps=transaction_cost_bps)
    else:
        raise HTTPException(status_code=422, detail="strategy must be 'ma' or 'rsi'.")
    return _serializable({"symbol": normalized, **result})


@router.get("/api/v1/patterns/{symbol}")
def patterns_endpoint(symbol: str, timeframe: str = Query("1D"), window: str = Query("1y")) -> dict[str, Any]:
    normalized, frame = _history(symbol, timeframe, window)
    try:
        return _serializable({"symbol": normalized, **detect_chart_patterns(frame)})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/brief")
def morning_brief_endpoint(
    symbols: str = Query("", description="Comma-separated symbols. Defaults to your watchlist."),
    timeframe: str = Query("1D"),
    window: str = Query("1y"),
    headlines: bool = Query(False, description="Include attributed headlines when a news source is configured."),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Descriptive pre-open brief over your own symbols. Never a forecast."""
    requested = [item.strip().upper() for item in str(symbols or "").split(",") if item.strip()]
    source = "request"
    if not requested:
        requested = _watchlist_symbols(user)
        source = "watchlist"
    if not requested:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "brief_symbols_required",
                "message": "Add symbols to your watchlist or pass ?symbols=RELIANCE,TCS.",
                "retryable": False,
            },
        )

    news_loader = None
    if headlines:
        from services.news import get_news as _brief_news

        news_loader = lambda symbol: _brief_news(symbol, limit=3)  # noqa: E731 - simple adapter

    try:
        payload = build_morning_brief(
            symbols=requested,
            history_loader=_history_loader(timeframe, window),
            market_status_loader=market_status,
            news_loader=news_loader,
        )
    except BriefUnavailable as exc:
        raise _feature_error(exc) from exc

    payload["request"] = {"symbol_source": source, "timeframe": timeframe, "window": window, "headlines": bool(headlines)}
    return _serializable(payload)


@router.post("/api/v1/options/payoff")
def option_payoff_endpoint(payload: OptionPayoffPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Expiry payoff arithmetic for a multi-leg option strategy. Not a forecast."""
    legs = [
        {
            "type": leg.type,
            "side": leg.side,
            "strike": leg.strike,
            "premium": leg.premium,
            "quantity": leg.quantity,
            "lot_size": leg.lot_size,
            "label": leg.label,
        }
        for leg in payload.legs
    ]
    try:
        result = build_payoff(
            legs=legs,
            spot=payload.spot,
            underlying=payload.underlying,
            grid_span=payload.grid_span,
            grid_points=payload.grid_points,
            volatility=payload.volatility,
            days_to_expiry=payload.days_to_expiry,
            risk_free_rate=payload.risk_free_rate,
            dividend_yield=payload.dividend_yield,
            costs=payload.costs,
        )
    except PayoffError as exc:
        raise _feature_error(exc) from exc
    return _serializable(result)
