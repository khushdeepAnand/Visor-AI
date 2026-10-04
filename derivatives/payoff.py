"""Multi-leg option strategy payoff arithmetic.

This module answers a closed-form question with no forecasting content: *given
these legs and these premiums, what is the profit or loss at expiry for each
underlying price?* Everything it returns is arithmetic on the supplied inputs.

What it deliberately does not do:

* it does not predict the underlying price, and it does not attach a probability
  to any point on the payoff curve;
* it does not compute exchange or broker margin, because that requires the
  broker's risk parameters and SPAN/ELM files this product does not hold;
* it does not recommend a strategy, rank strategies, or judge suitability.

When `volatility` and `days_to_expiry` are supplied, the optional mark-to-model
block reuses `derivatives.options_engine.black_scholes` so there is exactly one
pricing implementation in the codebase; that block is explicitly labelled as a
theoretical value under supplied assumptions, not a quote and not a forecast.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

import pandas as pd

__all__ = [
    "MAX_LEGS",
    "PAYOFF_LABEL",
    "PAYOFF_DISCLOSURES",
    "PayoffError",
    "build_payoff",
    "normalize_leg",
    "complete_strategy_status",
]

#: A retail strategy above this leg count is almost always an input error.
MAX_LEGS = 8

#: Points on the payoff curve. Odd count so the spot price sits on a grid point.
GRID_POINTS = 121

#: Default half-width of the price grid around spot, as a fraction.
DEFAULT_GRID_SPAN = 0.20

PAYOFF_LABEL = "Expiry payoff arithmetic on supplied legs and premiums"

PAYOFF_DISCLOSURES = (
    "Payoff is computed at expiry from the premiums and strikes you supplied. It is not a forecast and carries no probability.",
    "Margin, exchange charges, brokerage, STT, stamp duty, and GST are not included unless you supply them as a cost input.",
    "Early assignment, dividends, corporate actions, expiry-day settlement mechanics, and liquidity are not modelled.",
    "Simulated analysis only. StockPilot AI cannot place, modify, or cancel a broker order.",
)

OPTION_TYPES = {"call", "put"}
LINEAR_TYPES = {"future", "equity"}
SIDES = {"buy", "sell"}


class PayoffError(ValueError):
    """Raised for structurally invalid strategy input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _finite(value: Any, *, field: str, code: str, allow_zero: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PayoffError(code, f"{field} must be a number.") from None
    if not math.isfinite(number):
        raise PayoffError(code, f"{field} must be a finite number.")
    if not allow_zero and number == 0:
        raise PayoffError(code, f"{field} must not be zero.")
    return number


def normalize_leg(leg: Any, index: int) -> dict[str, Any]:
    """Validate one leg and return it in canonical form."""
    if not isinstance(leg, dict):
        raise PayoffError("payoff_leg_invalid", f"Leg {index + 1} must be an object.")
    kind = str(leg.get("type") or leg.get("instrument") or "").strip().lower()
    if kind in {"ce", "c"}:
        kind = "call"
    if kind in {"pe", "p"}:
        kind = "put"
    if kind in {"fut", "futures"}:
        kind = "future"
    if kind in {"stock", "cash", "spot"}:
        kind = "equity"
    if kind not in OPTION_TYPES | LINEAR_TYPES:
        raise PayoffError(
            "payoff_leg_type_unsupported",
            f"Leg {index + 1} type must be one of call, put, future, equity.",
        )

    side = str(leg.get("side") or leg.get("action") or "buy").strip().lower()
    if side in {"long", "b"}:
        side = "buy"
    if side in {"short", "s", "write"}:
        side = "sell"
    if side not in SIDES:
        raise PayoffError("payoff_leg_side_unsupported", f"Leg {index + 1} side must be buy or sell.")

    # ``contracts`` is the compact spelling used by the backtester and by
    # chain-shaped payloads.  Treat it as quantity only when the explicit
    # quantity is absent; this keeps the canonical quantity/lot_size contract
    # while avoiding silently dropping a caller's contract count.
    quantity_value = leg.get("quantity", leg.get("contracts", 1))
    quantity = _finite(quantity_value, field=f"Leg {index + 1} quantity", code="payoff_leg_quantity_invalid")
    if quantity <= 0:
        raise PayoffError("payoff_leg_quantity_invalid", f"Leg {index + 1} quantity must be positive.")
    lot_size = _finite(leg.get("lot_size", 1), field=f"Leg {index + 1} lot size", code="payoff_leg_lot_invalid")
    if lot_size <= 0:
        raise PayoffError("payoff_leg_lot_invalid", f"Leg {index + 1} lot size must be positive.")

    premium = _finite(leg.get("premium", leg.get("price", 0.0)), field=f"Leg {index + 1} premium", code="payoff_leg_premium_invalid")
    if premium < 0:
        raise PayoffError("payoff_leg_premium_invalid", f"Leg {index + 1} premium cannot be negative.")

    strike: float | None = None
    if kind in OPTION_TYPES:
        if leg.get("strike") is None:
            raise PayoffError("payoff_leg_strike_required", f"Leg {index + 1} requires a strike.")
        strike = _finite(leg.get("strike"), field=f"Leg {index + 1} strike", code="payoff_leg_strike_invalid")
        if strike <= 0:
            raise PayoffError("payoff_leg_strike_invalid", f"Leg {index + 1} strike must be positive.")
    else:
        # For a future or equity leg the "premium" is the entry price.
        if premium <= 0:
            raise PayoffError(
                "payoff_leg_premium_invalid",
                f"Leg {index + 1} is a {kind} leg, so its entry price must be positive.",
            )

    return {
        "index": index + 1,
        "type": kind,
        "side": side,
        "strike": strike,
        "premium": premium,
        "quantity": quantity,
        "lot_size": lot_size,
        "contracts": quantity * lot_size,
        "label": leg.get("label") or f"{side} {kind}{'' if strike is None else f' {strike:g}'}",
    }


def _leg_intrinsic(leg: dict[str, Any], price: float) -> float:
    """Per-unit settlement value of one leg at expiry for the given price."""
    if leg["type"] == "call":
        return max(0.0, price - float(leg["strike"]))
    if leg["type"] == "put":
        return max(0.0, float(leg["strike"]) - price)
    return price  # future / equity settle at the underlying price


def _leg_payoff(leg: dict[str, Any], price: float) -> float:
    """Signed profit or loss of one leg at expiry, including its entry cost."""
    settlement = _leg_intrinsic(leg, price)
    entry = float(leg["premium"])
    per_unit = (settlement - entry) if leg["side"] == "buy" else (entry - settlement)
    return per_unit * float(leg["contracts"])


def _net_premium(legs: Sequence[dict[str, Any]]) -> float:
    """Positive means a net debit paid; negative means a net credit received."""
    total = 0.0
    for leg in legs:
        signed = float(leg["premium"]) * float(leg["contracts"])
        total += signed if leg["side"] == "buy" else -signed
    return total


def _breakevens(points: Sequence[tuple[float, float]]) -> list[float]:
    """Linear-interpolated zero crossings of the payoff curve."""
    crossings: list[float] = []
    for (price_a, value_a), (price_b, value_b) in zip(points, points[1:]):
        if value_a == 0.0:
            crossings.append(price_a)
            continue
        if (value_a < 0 < value_b) or (value_b < 0 < value_a):
            span = value_b - value_a
            if span == 0:
                continue
            crossings.append(price_a + (price_b - price_a) * (-value_a / span))
    if points and points[-1][1] == 0.0:
        crossings.append(points[-1][0])
    unique: list[float] = []
    for value in crossings:
        rounded = round(float(value), 2)
        if all(abs(rounded - existing) > 0.01 for existing in unique):
            unique.append(rounded)
    return sorted(unique)


def _tail_behaviour(legs: Sequence[dict[str, Any]], spot: float) -> dict[str, Any]:
    """Describe both tails analytically instead of reading the plotted window.

    Only the upside can be unbounded. An underlying price cannot fall below
    zero, so the downside extreme is always a finite number - but it is often
    far outside the plotted range, so it is evaluated exactly at a price of zero
    by the caller rather than inferred from the grid. Slopes are reported as the
    change in payoff per one point rise in the underlying, measured in each
    tail.
    """
    upside_slope = 0.0
    downside_slope = 0.0
    for leg in legs:
        direction = 1.0 if leg["side"] == "buy" else -1.0
        units = float(leg["contracts"]) * direction
        if leg["type"] == "call":
            # Deep out of the money on the downside; only the upside tail moves.
            upside_slope += units
        elif leg["type"] == "put":
            # A long put gains as the price falls, so its payoff falls as the
            # price rises: the near-zero slope is the negative of its units.
            downside_slope -= units
        else:
            # Futures and cash legs are linear in both directions.
            upside_slope += units
            downside_slope += units
    return {
        "upside_slope_per_point": round(upside_slope, 6),
        "downside_slope_per_point": round(downside_slope, 6),
        "profit_unbounded": bool(upside_slope > 1e-9),
        "loss_unbounded": bool(upside_slope < -1e-9),
        "downside_bounded_by_zero": True,
        "reference_spot": round(float(spot), 4),
    }


def build_payoff(
    *,
    legs: Iterable[Any],
    spot: float,
    underlying: str | None = None,
    grid_span: float = DEFAULT_GRID_SPAN,
    grid_points: int = GRID_POINTS,
    volatility: float | None = None,
    days_to_expiry: int | None = None,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    costs: float = 0.0,
) -> dict[str, Any]:
    """Return the expiry payoff profile for a multi-leg strategy.

    Args:
        legs: leg definitions (`type`, `side`, `strike`, `premium`, `quantity`,
            `lot_size`).
        spot: current underlying price, used to centre the price grid.
        grid_span: half-width of the grid as a fraction of spot.
        volatility: annualized volatility for the optional mark-to-model block.
        days_to_expiry: calendar days for the optional mark-to-model block.
        costs: total transaction costs to subtract from every payoff point.

    Raises:
        PayoffError: for empty, oversized, or structurally invalid input.
    """
    raw_legs = list(legs or [])
    if not raw_legs:
        raise PayoffError("payoff_legs_required", "Supply at least one leg.")
    if len(raw_legs) > MAX_LEGS:
        raise PayoffError("payoff_legs_too_many", f"A maximum of {MAX_LEGS} legs is supported.")

    normalized = [normalize_leg(leg, index) for index, leg in enumerate(raw_legs)]
    spot_value = _finite(spot, field="Spot price", code="payoff_spot_invalid")
    if spot_value <= 0:
        raise PayoffError("payoff_spot_invalid", "Spot price must be positive.")
    span = _finite(grid_span, field="Grid span", code="payoff_grid_invalid")
    if not 0.01 <= span <= 0.9:
        raise PayoffError("payoff_grid_invalid", "Grid span must be between 0.01 and 0.9 of spot.")
    points_count = int(grid_points)
    if not 21 <= points_count <= 401:
        raise PayoffError("payoff_grid_invalid", "Grid points must be between 21 and 401.")
    cost_total = _finite(costs, field="Costs", code="payoff_costs_invalid")
    if cost_total < 0:
        raise PayoffError("payoff_costs_invalid", "Costs cannot be negative.")

    low = spot_value * (1.0 - span)
    high = spot_value * (1.0 + span)
    step = (high - low) / (points_count - 1)
    curve: list[dict[str, Any]] = []
    raw_points: list[tuple[float, float]] = []
    for index in range(points_count):
        price = low + step * index
        total = sum(_leg_payoff(leg, price) for leg in normalized) - cost_total
        raw_points.append((price, total))
        curve.append({"price": round(price, 2), "payoff": round(total, 2)})

    tails = _tail_behaviour(normalized, spot_value)
    net_premium = _net_premium(normalized)
    payoff_at_spot = sum(_leg_payoff(leg, spot_value) for leg in normalized) - cost_total

    # The plotted window can hide the real extremes: a short put reaches its
    # worst case only at a price of zero, and a vertical spread reaches its best
    # case beyond its highest strike. Both are finite, so they are evaluated
    # exactly and flagged as outside the plotted range, rather than being read
    # off the grid and silently understated.
    boundary_points: list[tuple[float, float]] = list(raw_points)
    boundary_points.append((0.0, sum(_leg_payoff(leg, 0.0) for leg in normalized) - cost_total))
    if abs(float(tails["upside_slope_per_point"])) <= 1e-9:
        strikes = [float(leg["strike"]) for leg in normalized if leg.get("strike")]
        far_price = max([high, spot_value * 3.0, *[strike * 2.0 for strike in strikes]])
        boundary_points.append(
            (far_price, sum(_leg_payoff(leg, far_price) for leg in normalized) - cost_total)
        )

    best = max(boundary_points, key=lambda item: item[1])
    worst = min(boundary_points, key=lambda item: item[1])

    def _inside_plot(price: float) -> bool:
        return low - 1e-9 <= price <= high + 1e-9

    def _extreme_note(kind: str, unbounded: bool, point: tuple[float, float]) -> str:
        if unbounded:
            if kind == "profit":
                return "Profit increases without a modelled limit as the underlying rises."
            return (
                "Loss increases without a modelled limit as the underlying rises. A short call "
                "leg with no offsetting long leg is not risk-bounded."
            )
        if _inside_plot(point[0]):
            return f"Reached at {round(point[0], 2)}, inside the plotted range."
        if point[0] <= 0.0:
            return (
                "Reached only if the underlying falls to zero, far outside the plotted range. "
                "The downside is finite because a price cannot fall below zero, not because "
                "this structure is hedged."
            )
        return f"Reached at {round(point[0], 2)}, outside the plotted range."

    max_profit: dict[str, Any] = {
        "unbounded": tails["profit_unbounded"],
        "value": None if tails["profit_unbounded"] else round(best[1], 2),
        "at_price": None if tails["profit_unbounded"] else round(best[0], 2),
        "outside_plotted_range": bool(not tails["profit_unbounded"] and not _inside_plot(best[0])),
        "note": _extreme_note("profit", tails["profit_unbounded"], best),
    }
    max_loss: dict[str, Any] = {
        "unbounded": tails["loss_unbounded"],
        "value": None if tails["loss_unbounded"] else round(worst[1], 2),
        "at_price": None if tails["loss_unbounded"] else round(worst[0], 2),
        "outside_plotted_range": bool(not tails["loss_unbounded"] and not _inside_plot(worst[0])),
        "note": _extreme_note("loss", tails["loss_unbounded"], worst),
    }

    risk_reward = None
    if not tails["profit_unbounded"] and not tails["loss_unbounded"]:
        downside = abs(min(0.0, worst[1]))
        upside = max(0.0, best[1])
        if downside > 0:
            risk_reward = round(upside / downside, 3)

    valuation: dict[str, Any] = {"state": "not_requested"}
    if volatility is not None and days_to_expiry is not None:
        try:
            from derivatives.options_engine import black_scholes
        except Exception:  # pragma: no cover - import guard for flat layouts
            from options_engine import black_scholes  # type: ignore[import-not-found, no-redef]
        vol = _finite(volatility, field="Volatility", code="payoff_volatility_invalid")
        days = int(days_to_expiry)
        if vol <= 0:
            valuation = {"state": "unavailable", "reason": "volatility_must_be_positive"}
        elif days <= 0:
            valuation = {"state": "unavailable", "reason": "days_to_expiry_must_be_positive"}
        else:
            marks: list[dict[str, Any]] = []
            model_value = 0.0
            for leg in normalized:
                if leg["type"] not in OPTION_TYPES:
                    marks.append({"leg": leg["index"], "state": "not_applicable", "type": leg["type"]})
                    continue
                priced = black_scholes(
                    spot=spot_value,
                    strike=float(leg["strike"]),
                    days_to_expiry=days,
                    volatility=vol,
                    risk_free_rate=risk_free_rate,
                    dividend_yield=dividend_yield,
                    option_type=leg["type"],
                )
                theoretical = float(priced.get("theoretical_price") or 0.0)
                direction = 1.0 if leg["side"] == "buy" else -1.0
                model_value += direction * theoretical * float(leg["contracts"])
                marks.append(
                    {
                        "leg": leg["index"],
                        "state": "available",
                        "theoretical_price": theoretical,
                        "supplied_premium": leg["premium"],
                        "delta": priced.get("delta"),
                        "theta_per_day": priced.get("theta_per_day"),
                        "vega_per_vol_point": priced.get("vega_per_vol_point"),
                    }
                )
            valuation = {
                "state": "available",
                "model": "black_scholes",
                "label": "Theoretical value under the supplied volatility and rate. Not a quote and not a forecast.",
                "assumptions": {
                    "volatility": vol,
                    "days_to_expiry": days,
                    "risk_free_rate": risk_free_rate,
                    "dividend_yield": dividend_yield,
                },
                "net_model_value": round(model_value, 4),
                "legs": marks,
                "is_predictive": False,
            }

    return {
        "underlying": (str(underlying).strip().upper() if underlying else None),
        "analysis_label": PAYOFF_LABEL,
        "legs": normalized,
        "spot": round(spot_value, 4),
        "net_premium": round(net_premium, 2),
        "position": "debit" if net_premium > 0 else ("credit" if net_premium < 0 else "flat"),
        "costs_applied": round(cost_total, 2),
        "payoff_at_spot": round(payoff_at_spot, 2),
        "max_profit": max_profit,
        "max_loss": max_loss,
        "risk_reward_ratio": risk_reward,
        "breakevens": _breakevens(raw_points),
        "grid": {"low": round(low, 2), "high": round(high, 2), "points": points_count, "span": span},
        "curve": curve,
        "tails": tails,
        "valuation": valuation,
        "is_forecast": False,
        "is_recommendation": False,
        "margin": {
            "state": "unavailable",
            "reason": "broker_risk_parameters_not_held",
            "note": "Exchange span/exposure margin requires broker risk files this product does not hold.",
        },
        "disclosures": list(PAYOFF_DISCLOSURES),
    }


def complete_strategy_status(
    *, legs: Iterable[Any], spot: float, expiry: Any,
    live_marks: dict[Any, Any] | None = None,
    as_of: Any = None,
) -> dict[str, Any]:
    """Return payoff plus expiry countdown and optional mark-to-market P&L.

    ``live_marks`` is deliberately caller-supplied (leg index -> current
    premium).  Without marks the live block is ``unavailable``; entry
    premiums are never misrepresented as live prices.
    """
    profile = build_payoff(legs=legs, spot=spot)
    try:
        exp = pd.Timestamp(expiry).date()
    except Exception:
        exp = None
    now = pd.Timestamp(as_of).date() if as_of is not None else datetime.now(timezone.utc).date()
    dte = None if exp is None else max(0, (exp - now).days)
    marks = live_marks or {}
    normalized = profile["legs"]
    pnl = 0.0
    missing = []
    for leg in normalized:
        mark = marks.get(leg["index"], marks.get(str(leg["index"])))
        if mark is None:
            missing.append(leg["index"])
            continue
        try:
            mark_value = float(mark)
            if not math.isfinite(mark_value) or mark_value < 0:
                raise ValueError
        except (TypeError, ValueError):
            missing.append(leg["index"]); continue
        direction = 1.0 if leg["side"] == "buy" else -1.0
        pnl += direction * (mark_value - float(leg["premium"])) * float(leg["contracts"])
    live: dict[str, Any] = {"state": "unavailable", "reason": "live_marks_unavailable", "missing_legs": missing}
    if not missing and normalized:
        live = {"state": "available", "pnl": round(pnl, 2), "basis": "caller_supplied_live_marks"}
    return {"expiry": str(expiry), "days_to_expiry": dte, "payoff": profile,
            "breakevens": profile["breakevens"], "live_pnl": live,
            "disclosure": "Live P&L uses only caller-supplied marks; no quote is fabricated."}
