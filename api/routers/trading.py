"""Router for the trading domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/watchlist")
def watchlist_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    items = []
    for row in get_watchlist(user["id"]):
        item = {"id": row[0], "symbol": row[1], "added_at": row[2]}
        try:
            item["quote"] = MANAGER.get_quote(row[1]).to_dict()
        except Exception:
            item["quote"] = None
        items.append(item)
    return _serializable({"items": items})


@router.post("/api/v1/watchlist", status_code=201)
def add_watchlist_endpoint(payload: WatchlistPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    symbol = MANAGER.normalize_symbol(payload.symbol)
    added = add_to_watchlist(user["id"], symbol)
    return {"added": added, "symbol": symbol}


@router.delete("/api/v1/watchlist/{watchlist_id}")
def remove_watchlist_endpoint(watchlist_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not remove_from_watchlist(watchlist_id, user["id"]):
        raise HTTPException(status_code=404, detail="Watchlist entry not found.")
    return {"deleted": True}


@router.get("/api/v1/portfolio")
def portfolio_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    holdings = []
    total_cost = total_value = 0.0
    for row in get_portfolio(user["id"]):
        stock_id, symbol, company, shares, buy_price, buy_date = row
        shares = float(shares); buy_price = float(buy_price)
        try:
            mark = float(MANAGER.get_quote(symbol).price)
        except Exception:
            mark = buy_price
        cost = shares * buy_price; value = shares * mark
        total_cost += cost; total_value += value
        holdings.append({"id":stock_id,"symbol":symbol,"company":company,"shares":shares,"buy_price":buy_price,"buy_date":buy_date,"mark_price":mark,"market_value":value,"pnl":value-cost,"pnl_pct":((mark/buy_price)-1)*100 if buy_price else 0})
    return _serializable({"holdings": holdings, "summary": {"cost": total_cost, "market_value": total_value, "pnl": total_value-total_cost, "pnl_pct": ((total_value/total_cost)-1)*100 if total_cost else 0}, "transactions": get_transactions(user["id"], 100)})


@router.get("/api/v1/reports/portfolio.pdf")
def portfolio_report_endpoint(user: dict[str, Any] = Depends(current_user)) -> Response:
    snapshot = portfolio_endpoint(user)
    holdings = snapshot.get("holdings") or []
    if not holdings:
        raise HTTPException(status_code=422, detail="Add at least one holding before exporting a portfolio PDF.")
    frame = pd.DataFrame([
        {
            "Symbol": item["symbol"],
            "Company": item.get("company") or item["symbol"],
            "Shares": item["shares"],
            "Buy Price": item["buy_price"],
            "Current Price": item["mark_price"],
            "Investment": float(item["shares"]) * float(item["buy_price"]),
            "Current Value": item["market_value"],
            "Profit": item["pnl"],
            "Return %": item["pnl_pct"],
        }
        for item in holdings
    ])
    content = generate_portfolio_pdf(frame, owner_name=str(user.get("name") or "StockPilot AI User"))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=stockpilot-portfolio.pdf"},
    )


@router.post("/api/v1/portfolio", status_code=201)
def portfolio_buy_endpoint(payload: PortfolioBuyPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    symbol = MANAGER.normalize_symbol(payload.symbol)
    buy_stock(user["id"], symbol, payload.company or symbol, payload.shares, payload.buy_price)
    return {"created": True, "symbol": symbol}


@router.put("/api/v1/portfolio/{holding_id}")
def portfolio_update_endpoint(holding_id: int, payload: PortfolioUpdatePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not update_stock(holding_id, user["id"], payload.shares, payload.buy_price):
        raise HTTPException(status_code=404, detail="Holding not found.")
    return {"updated": True}


@router.post("/api/v1/portfolio/{holding_id}/sell")
def portfolio_sell_endpoint(holding_id: int, payload: PortfolioSellPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not sell_stock(holding_id, user["id"], payload.shares, payload.sell_price):
        raise HTTPException(status_code=404, detail="Holding not found.")
    return {"sold": True}


@router.delete("/api/v1/portfolio/{holding_id}")
def portfolio_delete_endpoint(holding_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not delete_stock(holding_id, user["id"]):
        raise HTTPException(status_code=404, detail="Holding not found.")
    return {"deleted": True}


@router.get("/api/v1/alerts")
def alerts_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": list_price_alerts(user["id"])}


@router.post("/api/v1/alerts", status_code=201)
def alerts_create_endpoint(payload: AlertPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        alert_id = create_price_alert(
            user["id"], payload.symbol, payload.condition, payload.threshold,
            training_window=payload.training_window, confidence_level=payload.confidence_level,
            email_enabled=payload.email_enabled,
        )
        return {"id": alert_id}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/api/v1/alerts/{alert_id}")
def alerts_toggle_endpoint(alert_id: int, payload: AlertTogglePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not set_alert_active(user["id"], alert_id, payload.active):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"updated": True}


@router.delete("/api/v1/alerts/{alert_id}")
def alerts_delete_endpoint(alert_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not delete_price_alert(user["id"], alert_id):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"deleted": True}


@router.post("/api/v1/alerts/evaluate")
def alerts_evaluate_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    prices: dict[str, float] = {}
    ranges: dict[str, dict[str, Any]] = {}
    # Alerts that could not be evaluated are reported instead of silently
    # dropped, so a user never reads an empty result as "nothing triggered".
    skipped: list[dict[str, str]] = []
    for alert in list_price_alerts(user["id"], active_only=True):
        symbol = alert["symbol"]
        try:
            prices[symbol] = MANAGER.get_quote(symbol).price
        except Exception as exc:
            support_id = _support_id()
            _log_sanitized("alert_quote", support_id, exc)
            skipped.append({"symbol": symbol, "reason": "quote_unavailable", "support_id": support_id})
        if alert["condition"] not in FORECAST_CONDITIONS or symbol in ranges:
            continue
        try:
            window = str(alert.get("training_window") or "1mo")
            timeframe = WINDOW_TIMEFRAME_DEFAULTS[window]
            outcome = _execute_forecast(
                symbol,
                timeframe,
                window,
                float(alert.get("confidence_level") or 0.80),
            )
            status = str(outcome.result.get("forecast_status") or "abstained")
            if status in BLOCKED_FORECAST_STATUSES:
                skipped.append({"symbol": symbol, "reason": status, "support_id": _support_id()})
            else:
                ranges[symbol] = outcome.result["forecast"]
        except Exception as exc:
            support_id = _support_id()
            _log_sanitized("alert_forecast", support_id, exc)
            reason = "range_unavailable"
            detail: dict[str, Any] = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
            if isinstance(exc, UnsupportedHistoryRangeError) or detail.get("code") == "history_range_unsupported":
                reason = "history_range_unsupported"
            elif isinstance(exc, InsufficientDataError):
                reason = "insufficient_history"
            skipped.append({"symbol": symbol, "reason": reason, "support_id": support_id})
    triggered = evaluate_price_alerts(user["id"], prices, ranges)
    return {
        "triggered": _serializable(triggered),
        "deliveries": deliver_triggered_alerts(user["id"], str(user.get("email") or ""), triggered),
        "skipped": skipped,
    }


@router.get("/api/v1/paper/account")
def paper_account_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return _serializable(_paper_account_snapshot(user["id"]))


@router.post("/api/v1/paper/orders", status_code=201)
def paper_order_endpoint(payload: PaperOrderPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    values = payload.model_dump()
    try:
        result = place_paper_order(user_id=user["id"], **values)
        return _serializable(result)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail="Market data is temporarily unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/paper/orders/process")
def paper_process_endpoint(symbol: str | None = Query(default=None), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        return _serializable({"filled": process_open_orders(user["id"], symbol=symbol)})
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Paper orders could not be processed.") from exc


@router.delete("/api/v1/paper/orders/{order_id}")
def paper_cancel_endpoint(order_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not cancel_paper_order(user["id"], order_id):
        raise HTTPException(status_code=404, detail="Open paper order not found.")
    return {"cancelled": True}


@router.get("/api/v1/paper/journal")
def paper_journal_endpoint(limit: int = Query(200, ge=1, le=1000), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": paper_journal(user["id"], limit)}


@router.get("/api/v1/paper/badges")
def paper_badges_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": paper_badges(user["id"])}


@router.put("/api/v1/paper/leaderboard/visibility")
def paper_leaderboard_visibility_endpoint(payload: LeaderboardPrivacyPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not set_leaderboard_opt_in(user["id"], payload.enabled):
        raise HTTPException(status_code=404, detail="Paper account not found.")
    return {"leaderboard_opt_in": payload.enabled}


@router.get("/api/v1/paper/leaderboard")
def paper_leaderboard_endpoint(limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    return {"items": paper_leaderboard(limit), "privacy": "Only users who explicitly opt in are listed.", "valuation_note": "Positions use persisted cost when a public mark cannot be resolved."}


@router.get("/api/v1/paper/challenges")
def paper_challenges_endpoint() -> dict[str, Any]:
    return {"items": weekly_challenges()}


@router.get("/api/v1/paper/challenges/historical")
def historical_challenges_endpoint() -> dict[str, Any]:
    """Real, sourced NIFTY 50 replay scenarios. Outcomes stay hidden until reveal."""
    return {"items": historical_replay_scenarios()}


@router.get("/api/v1/paper/challenges/historical/mine")
def historical_challenges_mine_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": historical_replay_attempts(user["id"])}


@router.post("/api/v1/paper/challenges/historical/{key}/reveal")
def historical_challenge_reveal_endpoint(
    key: str,
    payload: HistoricalChallengeChoicePayload,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    try:
        return historical_replay_record_attempt(user["id"], key, payload.choice)
    except ScenarioNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown historical scenario: {key}")
    except InvalidChoiceError:
        raise HTTPException(status_code=422, detail=f"Invalid choice for scenario {key}: {payload.choice}")
