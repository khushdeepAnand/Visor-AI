"""Router for the derivatives domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from derivatives.futures_engine import analyze_futures_term_structure
from derivatives.iv_stats import record_atm_iv
from services.expected_move import atm_iv_from_chain


router = APIRouter()


class FuturesTermContractPayload(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    days_to_expiry: int = Field(ge=0, le=730)
    futures_price: float = Field(gt=0)
    open_interest: float | None = Field(default=None, ge=0)


class FuturesTermStructurePayload(BaseModel):
    spot_price: float = Field(gt=0)
    contracts: list[FuturesTermContractPayload] = Field(min_length=2, max_length=12)
    annual_risk_free_rate: float = Field(default=0.065, ge=-5, le=5)
    annual_carry_yield: float = Field(default=0.0, ge=-5, le=5)
    as_of: str | None = Field(default=None, max_length=80)



@router.post("/api/v1/options/backtest")
def multi_leg_backtest_endpoint(payload: MultiLegBacktestPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Model-priced multi-leg cycles. No stored option chain history exists."""
    _require_feature(MULTI_LEG_FLAG)
    symbol = str(payload.underlying or "").strip().upper()
    frame = _history_loader(payload.timeframe, payload.window)(symbol)
    try:
        result = backtest_multi_leg(
            legs=[leg.model_dump() for leg in payload.legs],
            frame=frame,
            underlying=symbol,
            days_to_expiry=payload.days_to_expiry,
            entry_every_sessions=payload.entry_every_sessions,
            volatility=payload.volatility,
            risk_free_rate=payload.risk_free_rate,
            dividend_yield=payload.dividend_yield,
            costs_per_cycle=payload.costs_per_cycle,
        )
    except BacktestError as exc:
        raise _feature_error(exc) from exc
    result["request"] = {"timeframe": payload.timeframe, "window": payload.window}
    return _serializable(result)


@router.post("/api/v1/derivatives/options/greeks")
def option_greeks_endpoint(payload: OptionGreeksPayload) -> dict[str, Any]:
    values = payload.model_dump()
    market_price = values.pop("market_price", None)
    underlying_type = values.pop("underlying_type")
    try:
        if underlying_type == "stock":
            result = binomial_greeks(**values, american=True)
            result["pricing_model"] = "Cox-Ross-Rubinstein binomial (American exercise)"
        else:
            result = black_scholes(**values)
            result["pricing_model"] = "Black-Scholes-Merton (European exercise)"
        if market_price is not None:
            result["implied_volatility"] = implied_volatility(market_price=market_price, **{k:v for k,v in values.items() if k != "volatility"})
        result["disclaimer"] = "Analytical model output; not an options trade recommendation."
        return _serializable(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/derivatives/options/binomial")
def binomial_endpoint(payload: OptionGreeksPayload) -> dict[str, Any]:
    values = payload.model_dump(exclude={"market_price", "underlying_type"})
    try:
        return _serializable({"pricing": binomial_tree(**values, american=payload.underlying_type == "stock"), "greeks": binomial_greeks(**values, american=payload.underlying_type == "stock")})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/derivatives/options/scenarios")
def option_scenarios_endpoint(payload: OptionGreeksPayload) -> dict[str, Any]:
    """Bounded scenario ladder: vary spot ±10 %, IV ±5 pp, time ±25 %.

    Returns 3 labelled scenarios (Bear / Base / Bull) with descriptive notes.
    No model internals (d1, d2, pricing_model, steps) are exposed.
    """
    values = payload.model_dump()
    underlying_type = values.pop("underlying_type", "index")
    market_price = values.pop("market_price", None)
    spot = float(values.get("spot", 0))
    iv = float(values.get("volatility", 0.2))
    days = int(values.get("days_to_expiry", 30))
    is_american = underlying_type == "stock"
    exercise_label = "American exercise estimate" if is_american else "European exercise estimate"

    scenarios: list[dict[str, Any]] = []
    for label, spot_mult, iv_delta, day_mult in [
        ("Bear", 0.90, -0.05, 1.25),
        ("Base", 1.00, 0.00, 1.00),
        ("Bull", 1.10, 0.05, 0.75),
    ]:
        try:
            s = dict(values)
            s["spot"] = round(spot * spot_mult, 4)
            s["volatility"] = max(iv + iv_delta, 0.01)
            s["days_to_expiry"] = max(int(days * day_mult), 1)
            if is_american:
                greeks = binomial_greeks(**s, american=True)
            else:
                greeks = black_scholes(**s)
            scenarios.append({
                "label": label,
                "reference_price": greeks.get("theoretical_price"),
                "greeks": {
                    "delta": greeks.get("delta"),
                    "gamma": greeks.get("gamma"),
                    "theta_per_day": greeks.get("theta_per_day"),
                    "vega_per_vol_point": greeks.get("vega_per_vol_point"),
                },
                "note": f"Spot {round(spot_mult*100)}%, IV shift {iv_delta:+.0%}, DTE ×{day_mult:.2f}",
            })
        except (ValueError, TypeError):
            scenarios.append({"label": label, "reference_price": None, "greeks": {"delta": None, "gamma": None, "theta_per_day": None, "vega_per_vol_point": None}, "note": "Calculation unavailable for this scenario."})

    return _serializable({
        "model_label": "Analytical scenario ladder",
        "exercise_type": exercise_label,
        "spot": spot,
        "strike": values.get("strike"),
        "days_to_expiry": days,
        "volatility": iv,
        "risk_free_rate": values.get("risk_free_rate"),
        "scenarios": scenarios,
        "disclaimer": "Descriptive scenario reference only. Not an options trade recommendation.",
    })


@router.post("/api/v1/derivatives/options/chain")
def option_chain_endpoint(payload: OptionChainPayload) -> dict[str, Any]:
    try:
        return _serializable(analyze_option_chain([row.model_dump() for row in payload.rows], spot_price=payload.spot_price))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/derivatives/options/contracts/{underlying}")
def option_contracts_endpoint(underlying: str, expiry: str | None = Query(default=None)) -> dict[str, Any]:
    rows = LIVE_DERIVATIVES.contracts(underlying, expiry)
    return {"underlying": underlying.upper(), "expiry": expiry, "count": len(rows), "rows": _serializable(rows), "source": "Upstox instrument master", "context": LIVE_DERIVATIVES._context(underlying, provider="instrument_master", timeframe="contracts", as_of=None, is_live=False, is_stale=False)}


@router.get("/api/v1/derivatives/options/live-chain/{underlying}")
def live_option_chain_endpoint(underlying: str, expiry: str = Query(...)) -> dict[str, Any]:
    result = LIVE_DERIVATIVES.live_chain(underlying, expiry)
    result.setdefault("context", LIVE_DERIVATIVES._context(underlying, provider=str(result.get("source") or "unavailable"), timeframe="option_chain", as_of=result.get("fetched_at"), is_live=bool(result.get("is_live", False)), is_stale=bool(result.get("is_stale", True)), fallback_reason=result.get("message")))
    # Best-effort IV history capture: feeds iv-rank/percentile. Never blocks
    # the chain response (record_atm_iv itself swallows failures).
    try:
        iv_block = atm_iv_from_chain(result)
        if iv_block.get("iv"):
            record_atm_iv(
                underlying,
                float(iv_block["iv"]),
                spot=iv_block.get("spot_price"),
                source=str(result.get("source") or "live_chain"),
            )
    except Exception:
        pass
    return _serializable(result)


@router.post("/api/v1/derivatives/margin")
def live_margin_endpoint(payload: LiveMarginPayload) -> dict[str, Any]:
    result = LIVE_DERIVATIVES.margin(MarginRequest(**payload.model_dump()))
    result.setdefault("context", LIVE_DERIVATIVES._context(payload.symbol, provider=str(result.get("source") or "unavailable"), timeframe="margin", as_of=result.get("fetched_at"), is_live=not bool(result.get("approximate_margin", True)), is_stale=bool(result.get("is_stale", True)), fallback_reason=result.get("message")))
    return _serializable(result)


@router.get("/api/v1/derivatives/expiries/{underlying}")
def expiry_endpoint(underlying: str, exchange: str = Query("NSE")) -> dict[str, Any]:
    result = get_expiries(underlying)
    result["context"] = LIVE_DERIVATIVES._context(underlying, provider="instrument_master", timeframe="expiries", as_of=None, is_live=False, is_stale=False)
    return _serializable(result)


@router.get("/api/v1/derivatives/expected-move/{symbol}")
def expected_move_endpoint(symbol: str, expiry: str | None = Query(default=None)) -> dict[str, Any]:
    """ATM-implied expected-move snapshot (best effort, no model range)."""
    result = expected_move_snapshot(
        symbol,
        expiry=expiry,
        chain_provider=lambda underlying, exp: LIVE_DERIVATIVES.live_chain(underlying, exp),
        expiry_provider=lambda underlying: LIVE_DERIVATIVES.expiries(underlying),
    )
    if result.get("available"):
        result.setdefault(
            "context",
            LIVE_DERIVATIVES._context(
                symbol,
                provider=str(result.get("source") or "unavailable"),
                timeframe="expected_move",
                as_of=result.get("fetched_at"),
                is_live=bool(result.get("is_live")),
                is_stale=False,
            ),
        )
    return _serializable(result)


@router.post("/api/v1/derivatives/futures/analyse")
def futures_analysis_endpoint(payload: FuturesPayload) -> dict[str, Any]:
    try:
        values = payload.model_dump()
        realized = values.pop("realized_volatility", None)
        implied = values.pop("implied_volatility", None)
        result = analyze_futures_contract(**values)
        if realized is not None:
            result["expiry_bounded_scenario"] = expiry_bounded_scenario_interval(
                reference_price=payload.futures_price,
                days_to_expiry=payload.days_to_expiry,
                realized_volatility=realized,
                implied_volatility=implied,
            )
        return _serializable(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/derivatives/futures/term-structure")
def futures_term_structure_endpoint(
    payload: FuturesTermStructurePayload,
) -> dict[str, Any]:
    try:
        result = analyze_futures_term_structure(
            spot_price=payload.spot_price,
            contracts=[contract.model_dump() for contract in payload.contracts],
            annual_risk_free_rate=payload.annual_risk_free_rate,
            annual_carry_yield=payload.annual_carry_yield,
            as_of=payload.as_of,
        )
        return _serializable(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

