"""Router for the strategies domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/strategies/metadata")
def strategy_metadata_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Metrics, comparators and starter rules for the no-code builder."""
    _require_feature(STRATEGY_FLAG)
    return _serializable(
        {
            "metrics": describe_metrics(),
            "operators": describe_operators(),
            "starters": starter_strategies(),
            "execution": "paper_only",
        }
    )


@router.get("/api/v1/strategies")
def strategy_list_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature(STRATEGY_FLAG)
    return _serializable({"strategies": STRATEGIES.list(user["id"])})


@router.post("/api/v1/strategies", status_code=201)
def strategy_save_endpoint(payload: StrategyDefinitionPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature(STRATEGY_FLAG)
    try:
        saved = STRATEGIES.save(user["id"], compile_strategy(_strategy_body(payload)))
    except StrategyError as exc:
        raise _feature_error(exc) from exc
    return _serializable(saved)


@router.delete("/api/v1/strategies/{strategy_id}")
def strategy_delete_endpoint(strategy_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature(STRATEGY_FLAG)
    deleted = STRATEGIES.delete(user["id"], int(strategy_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return {"deleted": True}


@router.post("/api/v1/strategies/run")
def strategy_run_endpoint(payload: StrategyBacktestPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Evaluate a compiled rule set over stored history. Paper only."""
    _require_feature(STRATEGY_FLAG)
    try:
        strategy = compile_strategy(_strategy_body(payload))
        result = run_strategy(strategy, history_loader=_history_loader(payload.timeframe, payload.window))
    except StrategyError as exc:
        raise _feature_error(exc) from exc
    result["request"] = {"timeframe": payload.timeframe, "window": payload.window}
    return _serializable(result)


@router.post("/api/v1/strategies/backtest")
def strategy_backtest_endpoint(payload: StrategyBacktestPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Replay one symbol's signals with spread and slippage applied."""
    _require_feature(EQUITY_BACKTEST_FLAG)
    try:
        strategy = compile_strategy(_strategy_body(payload))
    except StrategyError as exc:
        raise _feature_error(exc) from exc
    symbol = str(payload.symbol or (strategy["symbols"][0] if strategy["symbols"] else "")).strip().upper()
    if not symbol:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "strategy_symbols_required",
                "message": "Supply a symbol to backtest.",
                "retryable": False,
            },
        )
    frame = _history_loader(payload.timeframe, payload.window)(symbol)
    try:
        result = backtest_strategy(
            strategy,
            frame,
            symbol=symbol,
            spread_bps=payload.spread_bps,
            slippage_bps=payload.slippage_bps,
        )
    except (StrategyError, BacktestError) as exc:
        raise _feature_error(exc) from exc
    result["request"] = {"symbol": symbol, "timeframe": payload.timeframe, "window": payload.window}
    return _serializable(result)


@router.get("/api/v1/forward-tests")
def forward_test_list_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature(FORWARD_TEST_FLAG)
    return _serializable({"forward_tests": FORWARD_TESTS.list_tests(user["id"]), "order_placement": "never"})


@router.post("/api/v1/forward-tests", status_code=201)
def forward_test_start_endpoint(payload: ForwardTestStartPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Start tracking a saved strategy forward. No order is ever placed."""
    _require_feature(FORWARD_TEST_FLAG)
    try:
        strategy = STRATEGIES.get(user["id"], int(payload.strategy_id))
        symbols = payload.symbols or list(strategy["definition"].get("symbols") or [])
        started = FORWARD_TESTS.start(
            user["id"],
            strategy_id=int(payload.strategy_id),
            name=payload.name or strategy["name"],
            symbols=symbols,
        )
    except (StrategyError, ForwardTestError) as exc:
        raise _feature_error(exc) from exc
    return _serializable(started)


@router.post("/api/v1/forward-tests/{test_id}/evaluate")
def forward_test_evaluate_endpoint(
    test_id: int,
    payload: ForwardTestEvaluatePayload,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Record any signals observed since the test started."""
    _require_feature(FORWARD_TEST_FLAG)
    try:
        test = FORWARD_TESTS.get(user["id"], int(test_id))
        strategy = STRATEGIES.get(user["id"], int(test["strategy_id"]))["definition"]
        result = FORWARD_TESTS.evaluate(
            user["id"],
            int(test_id),
            strategy=strategy,
            history_loader=_history_loader(payload.timeframe, payload.window),
        )
    except (StrategyError, ForwardTestError) as exc:
        raise _feature_error(exc) from exc
    return _serializable(result)


@router.post("/api/v1/forward-tests/{test_id}/stop")
def forward_test_stop_endpoint(test_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    _require_feature(FORWARD_TEST_FLAG)
    try:
        return _serializable(FORWARD_TESTS.stop(user["id"], int(test_id)))
    except ForwardTestError as exc:
        raise _feature_error(exc) from exc


@router.get("/api/v1/forward-tests/{test_id}/scorecard")
def forward_test_scorecard_endpoint(test_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Realised, closed-trade only scorecard for a forward test."""
    _require_feature(FORWARD_TEST_FLAG)
    try:
        return _serializable(FORWARD_TESTS.scorecard(user["id"], int(test_id)))
    except ForwardTestError as exc:
        raise _feature_error(exc) from exc
