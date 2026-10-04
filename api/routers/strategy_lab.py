"""Strategy lab: multi-leg templates, live P&L, OI heatmap, and IV statistics.

Split out of `derivatives.py` so every domain router stays inside the
500-line bound enforced by `tests/test_frontend_api_contract.py`.
"""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from derivatives.iv_stats import iv_statistics
from derivatives.oi_heatmap import build_oi_heatmap
from derivatives.span_margin import estimate_span_margin
from derivatives.strategy_templates import (
    TemplateError,
    build_template,
    calendar_payoff,
    default_payoff,
    list_templates,
)


router = APIRouter()


class StrategyBuildPayload(BaseModel):
    template: str = Field(min_length=1, max_length=40)
    spot: float = Field(gt=0, le=10_000_000)
    params: dict[str, float] = Field(default_factory=dict)
    quantity: float = Field(default=1.0, gt=0, le=10_000)
    lot_size: float = Field(default=1.0, gt=0, le=100_000)
    volatility: float | None = Field(default=None, gt=0, le=5)
    days_to_expiry: int = Field(default=30, ge=0, le=730)
    risk_free_rate: float = Field(default=0.065, ge=-5, le=5)
    dividend_yield: float = Field(default=0.0, ge=-5, le=5)
    premiums: dict[str, float] = Field(default_factory=dict)
    grid_span: float = Field(default=0.2, ge=0.01, le=0.9)
    costs: float = Field(default=0.0, ge=0, le=1_000_000)


class StrategyPnlLegPayload(BaseModel):
    type: Literal["call", "put"] = "call"
    side: Literal["buy", "sell"] = "buy"
    strike: float = Field(gt=0)
    premium: float = Field(ge=0)
    quantity: float = Field(default=1.0, gt=0, le=10_000)
    lot_size: float = Field(default=1.0, gt=0, le=100_000)
    days_to_expiry: int = Field(default=30, ge=0, le=730)
    mark_price: float | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=60)


class StrategyPnlPayload(BaseModel):
    legs: list[StrategyPnlLegPayload] = Field(min_length=1, max_length=8)
    spot_now: float = Field(gt=0, le=10_000_000)
    volatility_now: float | None = Field(default=None, gt=0, le=5)
    days_to_expiry_now: int = Field(default=0, ge=0, le=730)
    risk_free_rate: float = Field(default=0.065, ge=-5, le=5)
    dividend_yield: float = Field(default=0.0, ge=-5, le=5)


class OiHeatmapRowPayload(BaseModel):
    strike: float = Field(gt=0)
    option_type: Literal["call", "put", "ce", "pe", "c", "p"]
    open_interest: float = Field(ge=0, le=10_000_000_000)
    previous_open_interest: float | None = Field(default=None, ge=0, le=10_000_000_000)
    expiry: str | None = Field(default=None, max_length=40)


class OiHeatmapPayload(BaseModel):
    rows: list[OiHeatmapRowPayload] = Field(min_length=1, max_length=2000)
    spot_price: float | None = Field(default=None, gt=0)
    expiry: str | None = Field(default=None, max_length=40)


@router.get("/api/v1/derivatives/strategies/templates")
def strategy_templates_endpoint() -> dict[str, Any]:
    """Builder catalog: named multi-leg structures and their tunable inputs."""
    return _serializable(list_templates())


@router.post("/api/v1/derivatives/strategies/build")
def strategy_build_endpoint(payload: StrategyBuildPayload) -> dict[str, Any]:
    """Resolve a template into legs + expiry payoff + SPAN-style margin estimate."""
    try:
        built = build_template(
            payload.template,
            spot=payload.spot,
            params=payload.params,
            quantity=payload.quantity,
            lot_size=payload.lot_size,
            volatility=payload.volatility,
            days_to_expiry=payload.days_to_expiry,
            risk_free_rate=payload.risk_free_rate,
            dividend_yield=payload.dividend_yield,
            premiums={str(k): v for k, v in payload.premiums.items()},
        )
    except TemplateError as exc:
        raise _feature_error(exc) from exc

    is_calendar = built["template"].startswith("calendar_")
    payoff: dict[str, Any] | None = None
    payoff_unavailable: str | None = None
    try:
        if is_calendar:
            if payload.volatility is None:
                payoff_unavailable = "calendar_curve_requires_volatility_to_mark_the_deferred_leg"
            else:
                payoff = calendar_payoff(
                    built,
                    volatility=payload.volatility,
                    risk_free_rate=payload.risk_free_rate,
                    dividend_yield=payload.dividend_yield,
                )
        else:
            payoff = default_payoff(
                built,
                grid_span=payload.grid_span,
                volatility=payload.volatility,
                days_to_expiry=payload.days_to_expiry,
                risk_free_rate=payload.risk_free_rate,
                dividend_yield=payload.dividend_yield,
                costs=payload.costs,
            )
    except PayoffError as exc:
        raise _feature_error(exc) from exc

    margin: dict[str, Any] | None = None
    margin_unavailable: str | None = None
    if payload.volatility is None:
        margin_unavailable = "margin_scan_requires_volatility"
    else:
        try:
            margin = estimate_span_margin(
                built["legs"],
                spot=payload.spot,
                volatility=payload.volatility,
                days_to_expiry=payload.days_to_expiry,
                risk_free_rate=payload.risk_free_rate,
                dividend_yield=payload.dividend_yield,
            )
        except (ValueError, PayoffError) as exc:
            margin_unavailable = str(getattr(exc, "message", exc))

    return _serializable({
        **built,
        "payoff": payoff,
        "payoff_unavailable_reason": payoff_unavailable,
        "margin": margin,
        "margin_unavailable_reason": margin_unavailable,
        "disclosures": (
            payoff.get("disclosures", []) if payoff else []
        ) + (
            margin.get("disclosures", []) if margin else []
        ) + [
            "Template output structures your inputs only; it is not a recommendation.",
            "Model-priced premiums are theoretical values under your supplied volatility, not quotes.",
        ],
    })


@router.post("/api/v1/derivatives/strategies/pnl")
def strategy_pnl_endpoint(payload: StrategyPnlPayload) -> dict[str, Any]:
    """Revalue an open multi-leg book at current spot/IV/DTE.

    Per-leg marks: your ``mark_price`` (market observation) when supplied,
    otherwise a Black-Scholes model value under ``volatility_now``. Every leg
    states which basis was used; legs with neither are reported as null with a
    reason instead of being guessed.
    """
    spot_now = payload.spot_now
    legs_out: list[dict[str, Any]] = []
    total = 0.0
    total_basis = "all_legs_valued"
    for index, leg in enumerate(payload.legs, start=1):
        contracts = leg.quantity * leg.lot_size
        direction = 1.0 if leg.side == "buy" else -1.0
        entry = leg.premium
        mark: float | None
        basis: str
        reason: str | None = None
        if leg.mark_price is not None:
            mark = float(leg.mark_price)
            basis = "user_mark"
        elif payload.volatility_now is not None:
            try:
                priced = black_scholes(
                    spot=spot_now,
                    strike=leg.strike,
                    days_to_expiry=max(1, leg.days_to_expiry),
                    volatility=payload.volatility_now,
                    risk_free_rate=payload.risk_free_rate,
                    dividend_yield=payload.dividend_yield,
                    option_type=leg.type,
                )
                mark = float(priced.get("theoretical_price") or 0.0)
                basis = "model_mark"
            except (ValueError, TypeError) as exc:
                mark = None
                basis = "unavailable"
                reason = str(exc)
        else:
            mark = None
            basis = "unavailable"
            reason = "supply mark_price or volatility_now to value this leg"
        pnl = None if mark is None else round(direction * (mark - entry) * contracts, 2)
        if pnl is None:
            total_basis = "partial"
        else:
            total += pnl
        legs_out.append({
            "index": index,
            "label": leg.label or f"{leg.side} {leg.type} {leg.strike:g}",
            "type": leg.type,
            "side": leg.side,
            "strike": leg.strike,
            "entry_premium": entry,
            "current_mark": None if mark is None else round(mark, 4),
            "mark_basis": basis,
            "mark_unavailable_reason": reason,
            "pnl": pnl,
            "contracts": contracts,
        })
    return _serializable({
        "legs": legs_out,
        "total_pnl": round(total, 2),
        "total_pnl_basis": total_basis,
        "spot_now": spot_now,
        "volatility_now": payload.volatility_now,
        "days_to_expiry_now": payload.days_to_expiry_now,
        "is_forecast": False,
        "is_recommendation": False,
        "disclaimer": (
            "P&L on supplied inputs. User marks are your observations; model marks are theoretical "
            "values under the supplied volatility, not quotes. Not a forecast."
        ),
    })


@router.post("/api/v1/derivatives/oi-heatmap")
def oi_heatmap_endpoint(payload: OiHeatmapPayload) -> dict[str, Any]:
    """Per-strike OI heatmap, walls/build-ups, and descriptive pin risk."""
    try:
        result = build_oi_heatmap(
            [row.model_dump() for row in payload.rows],
            spot_price=payload.spot_price,
            expiry=payload.expiry,
        )
    except ValueError as exc:
        raise _feature_error(exc) from exc
    return _serializable(result)


@router.get("/api/v1/derivatives/iv-stats/{underlying}")
def iv_stats_endpoint(
    underlying: str,
    lookback_days: int = Query(default=365, ge=5, le=3650),
    include_rv_fallback: bool = Query(default=True),
) -> dict[str, Any]:
    """IV rank / IV percentile from recorded ATM IV observations.

    Falls back to a clearly-labelled realized-vol percentile when no market IV
    history exists yet; the two bases are never merged.
    """
    frame = None
    if include_rv_fallback:
        window = "1y" if lookback_days <= 365 else ("5y" if lookback_days <= 1825 else "10y")
        try:
            frame = _history_loader("1D", window)(underlying)
        except Exception:
            frame = None
    return _serializable(iv_statistics(underlying, lookback_days=lookback_days, price_history=frame))
