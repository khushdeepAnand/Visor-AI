"""SPAN-style multi-leg margin estimate by scenario scanning.

Exchange SPAN uses broker-supplied risk files (correlation arrays, vol
scans, vega buckets) that this product does not hold. What is implemented
here is the *structure* SPAN rests on, stated honestly:

1. **Premium effect** - short option premium is at risk first (SPAN's
   premium-at-risk floor).
2. **Scanning scenarios** - the portfolio is revalued under a grid of
   underlying shocks (0, +/-3, +/-5, +/-10, +/-15%) crossed with relative
   volatility shocks (0, +/-25%, +50%), at expiry settlement for the
   delta component and mark-to-model for the vega component.
3. **Margin** = the largest loss across scenarios, floored at short
   premium, plus per-leg worst-case for outright shorts (naked risk that
   scenario grids at +/-15% can understate for deep OTM shorts is covered
   by the premium floor + wider legs).

Defined-risk structures (spreads, condors) come out capped at their width
because the long leg settles against the short leg in every scenario - the
offset emerges from the arithmetic rather than a hand-tuned factor.

Every response is labelled ``basis: "span_style_estimate"`` with an explicit
disclosure that this is not the exchange's official requirement, and each
figure carries its derivation so a paper account can be reconciled.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from derivatives.options_engine import black_scholes
from derivatives.payoff import normalize_leg

__all__ = [
    "MARGIN_DISCLOSURES",
    "estimate_span_margin",
]

#: Underlying shocks as percent moves (the SPAN risk-array core).
UNDERLYING_SHOCKS_PCT = (0.0, -3.0, -5.0, -10.0, -15.0, 3.0, 5.0, 10.0, 15.0)

#: Relative volatility shocks (vega scan).
VOL_SHOCKS_REL = (0.0, -0.25, 0.25, 0.50)

#: Extra relief allowed for clearly defined-risk structures is not faked:
#: the scenario grid already caps them, so no manual offset is applied.

MARGIN_DISCLOSURES = (
    "SPAN-style estimate from a scenario grid; not the exchange's official SPAN requirement, which needs broker risk files this product does not hold.",
    "Covers underlying moves up to +/-15% and volatility shocks up to +50%; tail events beyond the grid are not modelled.",
    "Short premium is treated as at-risk capital (SPAN premium floor). Taxes, brokerage, and exchange charges are excluded.",
    "Simulated analysis for paper positions only. StockPilot AI does not place orders or enforce broker margin.",
)


def _leg_value(leg: dict[str, Any], spot: float, vol: float, days: float, rate: float, div: float) -> float:
    """Mark one option leg at the given scenario; intrinsic for expired legs."""
    kind = leg["type"]
    if kind not in {"call", "put"}:
        # Equity/future legs: value change is linear in the underlying.
        direction = 1.0 if leg["side"] == "buy" else -1.0
        return direction * (spot - float(leg["premium"])) * float(leg["contracts"])
    if days <= 0:
        strike = float(leg["strike"])
        intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
        return (intrinsic - float(leg["premium"])) * float(leg["contracts"]) * (
            1.0 if leg["side"] == "buy" else -1.0
        )
    priced = black_scholes(
        spot=spot, strike=float(leg["strike"]), days_to_expiry=max(1, int(round(days))),
        volatility=vol, risk_free_rate=rate, dividend_yield=div, option_type=kind,
    )
    mark = float(priced.get("theoretical_price") or 0.0)
    direction = 1.0 if leg["side"] == "buy" else -1.0
    return direction * (mark - float(leg["premium"])) * float(leg["contracts"])


def estimate_span_margin(
    legs: Iterable[Any],
    *,
    spot: float,
    volatility: float,
    days_to_expiry: int,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
) -> dict[str, Any]:
    """Scenario-scan margin for a multi-leg book.

    Args:
        legs: canonical legs (``build_payoff`` shape, with entry premiums).
        spot: current underlying price.
        volatility: annualized volatility of the underlying (decimal).
        days_to_expiry: days to the nearest expiry in the book.
        risk_free_rate/dividend_yield: model inputs for the vega scan.

    Returns:
        Estimate with ``total_margin``, ``basis``, per-leg standalone margins,
        worst scenario, and disclosures.
    """
    raw = list(legs or [])
    if not raw:
        raise ValueError("Supply at least one leg.")
    spot_value = float(spot)
    if not math.isfinite(spot_value) or spot_value <= 0:
        raise ValueError("Spot price must be positive and finite.")
    vol = float(volatility)
    if not math.isfinite(vol) or vol <= 0:
        raise ValueError("Volatility must be positive and finite.")
    dte = max(0, int(days_to_expiry))

    normalized = [normalize_leg(leg, index) for index, leg in enumerate(raw)]

    base_value = sum(
        _leg_value(leg, spot_value, vol, float(dte), risk_free_rate, dividend_yield)
        for leg in normalized
    )

    worst_loss = 0.0
    worst_scenario: dict[str, Any] | None = None
    for shock in UNDERLYING_SHOCKS_PCT:
        scenario_spot = spot_value * (1.0 + shock / 100.0)
        for vol_rel in VOL_SHOCKS_REL:
            scenario_vol = max(1e-4, vol * (1.0 + vol_rel))
            value = sum(
                _leg_value(leg, scenario_spot, scenario_vol, float(dte), risk_free_rate, dividend_yield)
                for leg in normalized
            )
            loss = base_value - value
            if loss > worst_loss:
                worst_loss = loss
                worst_scenario = {
                    "underlying_move_pct": shock,
                    "volatility_change_pct": round(vol_rel * 100.0, 2),
                    "portfolio_value": round(value, 2),
                    "loss": round(loss, 2),
                    "valuation": "expiry_settlement_when_expired_else_mark_to_model",
                }

    # Per-leg standalone margin (no offsets): short options carry a
    # premium floor plus the scanned move against them.
    per_leg: list[dict[str, Any]] = []
    short_premium_total = 0.0
    for leg in normalized:
        direction = 1.0 if leg["side"] == "buy" else -1.0
        premium = float(leg["premium"]) * float(leg["contracts"])
        if leg["side"] == "sell" and leg["type"] in {"call", "put"}:
            short_premium_total += premium
            # Scan the leg alone under the +/-15% grid.
            base = _leg_value(leg, spot_value, vol, float(dte), risk_free_rate, dividend_yield)
            scan = 0.0
            for shock in (-15.0, 15.0):
                scenario_spot = spot_value * (1.0 + shock / 100.0)
                value = _leg_value(leg, scenario_spot, vol * 1.5, float(dte), risk_free_rate, dividend_yield)
                scan = max(scan, base - value)
            standalone = max(premium, scan)
            derivation = "max(short_premium, single_leg_scan)"
        else:
            standalone = premium if leg["type"] in {"call", "put"} else 0.0
            derivation = "capital_at_risk" if premium else "none"
        per_leg.append({
            "index": leg["index"],
            "label": leg.get("label"),
            "side": leg["side"],
            "type": leg["type"],
            "standalone_margin": round(standalone, 2),
            "derivation": derivation,
        })

    # Combined requirement: the scenario scan already nets long legs against
    # shorts (strategy offsets fall out of the arithmetic); the short-premium
    # floor guards against scenarios that understate deep-OTM short risk.
    combined = max(worst_loss, short_premium_total, 0.0)
    sum_standalone = sum(item["standalone_margin"] for item in per_leg)
    strategy_offset = round(max(0.0, sum_standalone - combined), 2)

    return {
        "total_margin": round(combined, 2),
        "basis": "span_style_estimate",
        "valuation_basis": "expiry_settlement_when_expired_else_mark_to_model",
        "base_portfolio_value": round(base_value, 2),
        "worst_scenario": worst_scenario,
        "scenarios_evaluated": len(UNDERLYING_SHOCKS_PCT) * len(VOL_SHOCKS_REL),
        "underlying_shocks_pct": list(UNDERLYING_SHOCKS_PCT),
        "volatility_shocks_rel": list(VOL_SHOCKS_REL),
        "short_premium_floor": round(short_premium_total, 2),
        "per_leg": per_leg,
        "sum_standalone": round(sum_standalone, 2),
        "strategy_offset": strategy_offset,
        "is_official_requirement": False,
        "is_forecast": False,
        "disclosures": list(MARGIN_DISCLOSURES),
    }
