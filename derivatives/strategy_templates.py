"""Options strategy templates: named multi-leg structures from plain params.

A template turns `{name, spot, params}` into concrete legs (type, side,
strike, quantity, premium) that the existing payoff, valuation, and margin
engines consume. It never recommends a strategy and never predicts a price:
the caller supplies (or explicitly accepts model-priced) premiums and owns the
decision.

Premium source, stated per leg:

* ``user``   - the caller supplied the premium (treated as a market observation).
* ``model``  - Black-Scholes under the caller's supplied volatility/rate, labelled
               as a theoretical value everywhere it surfaces.

Strikes are resolved from offsets around the ATM price and rounded to
``strike_step`` so produced strikes are realistic list strikes. Calendar
structures carry per-leg expiries; their combined curve marks the far leg to
model at the near expiry because expiry arithmetic alone cannot value a
deferred leg (that block is explicitly labelled mark-to-model).
"""

from __future__ import annotations

import math
from typing import Any

from derivatives.payoff import PayoffError, build_payoff, normalize_leg

#: Retail-usable template catalog. `params` documents each tunable input.
TEMPLATES: dict[str, dict[str, Any]] = {
    "straddle": {
        "label": "Long straddle",
        "direction": "long_volatility",
        "legs": 2,
        "description": "Buy ATM call and put. Profits from a large move either way; loses premium if the underlying pins.",
        "params": {"strike": "ATM strike (default: spot rounded to strike_step)"},
    },
    "strangle": {
        "label": "Long strangle",
        "direction": "long_volatility",
        "legs": 2,
        "description": "Buy OTM call above and OTM put below spot. Cheaper than a straddle; needs a bigger move.",
        "params": {"call_offset_pct": "default 5%", "put_offset_pct": "default 5%"},
    },
    "iron_condor": {
        "label": "Iron condor",
        "direction": "short_volatility",
        "legs": 4,
        "description": "Sell a put spread and a call spread around spot. Collects a credit; risk is capped on both sides.",
        "params": {"short_offset_pct": "default 5%", "wing_width_pct": "default 5%"},
    },
    "iron_butterfly": {
        "label": "Iron butterfly",
        "direction": "short_volatility",
        "legs": 4,
        "description": "Sell ATM call and put, buy wings. Maximum credit at the ATM strike; both tails protected.",
        "params": {"wing_width_pct": "default 10%"},
    },
    "bull_call_spread": {
        "label": "Bull call spread",
        "direction": "bullish",
        "legs": 2,
        "description": "Buy a call and sell a higher call. Defined debit and defined risk.",
        "params": {"lower_offset_pct": "default -5%", "wing_width_pct": "default 5%"},
    },
    "bear_put_spread": {
        "label": "Bear put spread",
        "direction": "bearish",
        "legs": 2,
        "description": "Buy a put and sell a lower put. Defined debit and defined risk.",
        "params": {"upper_offset_pct": "default 5%", "wing_width_pct": "default 5%"},
    },
    "bull_put_spread": {
        "label": "Bull put spread (credit)",
        "direction": "bullish",
        "legs": 2,
        "description": "Sell a higher put and buy a lower put. Collects a credit with capped downside.",
        "params": {"short_offset_pct": "default 5%", "wing_width_pct": "default 5%"},
    },
    "bear_call_spread": {
        "label": "Bear call spread (credit)",
        "direction": "bearish",
        "legs": 2,
        "description": "Sell a lower call and buy a higher call. Collects a credit with capped upside risk.",
        "params": {"short_offset_pct": "default 5%", "wing_width_pct": "default 5%"},
    },
    "call_ratio_backspread": {
        "label": "Call ratio backspread",
        "direction": "bullish_volatility",
        "legs": 2,
        "description": "Sell one lower call, buy `ratio` higher calls. Net credit/debit depends on strikes; profits from a strong rally.",
        "params": {"short_offset_pct": "default 2%", "wing_width_pct": "default 5%", "ratio": "default 2"},
    },
    "put_ratio_spread": {
        "label": "Put ratio spread",
        "direction": "bearish_income",
        "legs": 2,
        "description": "Buy one higher put, sell `ratio` lower puts. Collects premium on a moderate decline; tail risk if the fall is extreme.",
        "params": {"short_offset_pct": "default 8%", "wing_width_pct": "default 5%", "ratio": "default 2"},
    },
    "calendar_call": {
        "label": "Call calendar",
        "direction": "time_decay",
        "legs": 2,
        "description": "Sell a near-expiry call and buy the same strike further out. Benefits from time decay with vol risk.",
        "params": {"strike_offset_pct": "default 0%", "near_days": "default 30", "far_days": "default 90"},
    },
    "calendar_put": {
        "label": "Put calendar",
        "direction": "time_decay",
        "legs": 2,
        "description": "Sell a near-expiry put and buy the same strike further out.",
        "params": {"strike_offset_pct": "default 0%", "near_days": "default 30", "far_days": "default 90"},
    },
}

DEFAULTS: dict[str, dict[str, float]] = {
    "straddle": {},
    "strangle": {"call_offset_pct": 5.0, "put_offset_pct": 5.0},
    "iron_condor": {"short_offset_pct": 5.0, "wing_width_pct": 5.0},
    "iron_butterfly": {"wing_width_pct": 10.0},
    "bull_call_spread": {"lower_offset_pct": -5.0, "wing_width_pct": 5.0},
    "bear_put_spread": {"upper_offset_pct": 5.0, "wing_width_pct": 5.0},
    "bull_put_spread": {"short_offset_pct": 5.0, "wing_width_pct": 5.0},
    "bear_call_spread": {"short_offset_pct": 5.0, "wing_width_pct": 5.0},
    "call_ratio_backspread": {"short_offset_pct": 2.0, "wing_width_pct": 5.0, "ratio": 2.0},
    "put_ratio_spread": {"short_offset_pct": 8.0, "wing_width_pct": 5.0, "ratio": 2.0},
    "calendar_call": {"strike_offset_pct": 0.0, "near_days": 30.0, "far_days": 90.0},
    "calendar_put": {"strike_offset_pct": 0.0, "near_days": 30.0, "far_days": 90.0},
}


class TemplateError(ValueError):
    """Raised for structurally invalid template input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def list_templates() -> dict[str, Any]:
    """Catalog for the builder UI: names, copy, and tunable parameters."""
    return {
        "templates": [
            {"name": name, **meta} for name, meta in TEMPLATES.items()
        ],
        "max_legs": 4,
        "disclosures": [
            "Templates structure your inputs; they are not recommendations and carry no probability.",
            "Model-priced premiums are theoretical values under your supplied volatility, not quotes.",
            "Margin shown is a SPAN-style estimate, not the exchange's official requirement.",
        ],
    }


def _round_strike(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 2)
    return round(round(value / step) * step, 2)


def _pct(spot: float, pct: float) -> float:
    return spot * (float(pct) / 100.0)


def _price_leg(spot: float, strike: float, kind: str, days: int, vol: float,
               rate: float, div: float) -> float:
    from derivatives.options_engine import black_scholes
    priced = black_scholes(
        spot=spot, strike=strike, days_to_expiry=max(1, int(days)), volatility=vol,
        risk_free_rate=rate, dividend_yield=div, option_type=kind,
    )
    return float(priced.get("theoretical_price") or 0.0)


def _strike_step_param(spot: float, params: dict[str, float]) -> float:
    step = params.get("strike_step_pct")
    if step and float(step) > 0:
        return _pct(spot, float(step))
    return max(0.5, _pct(spot, 1.0))


def build_template(
    template: str,
    *,
    spot: float,
    params: dict[str, Any] | None = None,
    quantity: float = 1.0,
    lot_size: float = 1.0,
    volatility: float | None = None,
    days_to_expiry: int = 30,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    premiums: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Resolve a template into legs plus provenance for each premium.

    Args:
        template: key of :data:`TEMPLATES`.
        spot: current underlying price.
        params: template-specific strike offsets (percent of spot).
        quantity/lots: contracts per leg (``quantity * lot_size``).
        volatility/days_to_expiry: model inputs when premiums are not supplied.
        premiums: optional per-leg premiums keyed ``"1".."4"`` (1-based order).

    Returns:
        ``{"template", "legs", "premium_basis", "metadata"}`` where legs are in
        the canonical shape ``build_payoff`` consumes (with ``days_to_expiry``
        attached for calendar legs).
    """
    name = str(template or "").strip().lower()
    if name not in TEMPLATES:
        raise TemplateError("template_unknown", f"Unknown template '{template}'.")
    try:
        spot_value = float(spot)
    except (TypeError, ValueError):
        raise TemplateError("template_spot_invalid", "Spot price must be a number.") from None
    if not math.isfinite(spot_value) or spot_value <= 0:
        raise TemplateError("template_spot_invalid", "Spot price must be positive and finite.")
    qty = float(quantity)
    lot = float(lot_size)
    if qty <= 0 or lot <= 0:
        raise TemplateError("template_quantity_invalid", "Quantity and lot size must be positive.")

    merged = dict(DEFAULTS.get(name, {}))
    for key, value in (params or {}).items():
        try:
            merged[str(key)] = float(value)
        except (TypeError, ValueError):
            raise TemplateError("template_param_invalid", f"Parameter '{key}' must be numeric.") from None

    step = _strike_step_param(spot_value, merged)
    vol = float(volatility) if volatility is not None else None
    if vol is not None and (not math.isfinite(vol) or vol <= 0):
        raise TemplateError("template_volatility_invalid", "Volatility must be positive.")
    dte = int(days_to_expiry)
    prem = {str(k): float(v) for k, v in (premiums or {}).items()}

    def strike_at(offset_pct: float, base: float | None = None) -> float:
        return _round_strike((base if base is not None else spot_value) + _pct(spot_value, offset_pct), step)

    raw: list[dict[str, Any]] = []
    basis: list[str] = []

    def add(kind: str, side: str, strike: float, days: int | None = None) -> None:
        index = len(raw) + 1
        key = str(index)
        if key in prem:
            premium = prem[key]
            source = "user"
        elif vol is not None:
            premium = _price_leg(spot_value, strike, kind, days or dte, vol, risk_free_rate, dividend_yield)
            source = "model"
        else:
            raise TemplateError(
                "template_premium_required",
                f"Premium for leg {index} was not supplied and no volatility was provided to model-price it.",
            )
        leg: dict[str, Any] = {
            "type": kind, "side": side, "strike": round(float(strike), 2),
            "premium": round(float(premium), 4), "quantity": qty, "lot_size": lot,
            "label": f"{side} {kind} {strike:g}",
        }
        if days is not None:
            leg["days_to_expiry"] = int(days)
        raw.append(leg)
        basis.append(source)

    if name == "straddle":
        k = strike_at(0.0)
        add("call", "buy", k)
        add("put", "buy", k)
    elif name == "strangle":
        add("call", "buy", strike_at(merged.get("call_offset_pct", 5.0)))
        add("put", "buy", strike_at(-merged.get("put_offset_pct", 5.0)))
    elif name == "iron_condor":
        width = merged.get("wing_width_pct", 5.0)
        off = merged.get("short_offset_pct", 5.0)
        add("put", "sell", strike_at(-off))
        add("put", "buy", strike_at(-(off + width)))
        add("call", "sell", strike_at(off))
        add("call", "buy", strike_at(off + width))
    elif name == "iron_butterfly":
        width = merged.get("wing_width_pct", 10.0)
        k = strike_at(0.0)
        add("put", "sell", k)
        add("call", "sell", k)
        add("call", "buy", strike_at(width))
        add("put", "buy", strike_at(-width))
    elif name == "bull_call_spread":
        lower = strike_at(merged.get("lower_offset_pct", -5.0))
        add("call", "buy", lower)
        add("call", "sell", _round_strike(lower + _pct(spot_value, merged.get("wing_width_pct", 5.0)), step))
    elif name == "bear_put_spread":
        upper = strike_at(merged.get("upper_offset_pct", 5.0))
        add("put", "buy", upper)
        add("put", "sell", _round_strike(upper - _pct(spot_value, merged.get("wing_width_pct", 5.0)), step))
    elif name == "bull_put_spread":
        width = merged.get("wing_width_pct", 5.0)
        off = merged.get("short_offset_pct", 5.0)
        add("put", "sell", strike_at(-off))
        add("put", "buy", strike_at(-(off + width)))
    elif name == "bear_call_spread":
        width = merged.get("wing_width_pct", 5.0)
        off = merged.get("short_offset_pct", 5.0)
        add("call", "sell", strike_at(off))
        add("call", "buy", strike_at(off + width))
    elif name == "call_ratio_backspread":
        ratio = max(2.0, merged.get("ratio", 2.0))
        short_k = strike_at(merged.get("short_offset_pct", 2.0))
        long_k = _round_strike(short_k + _pct(spot_value, merged.get("wing_width_pct", 5.0)), step)
        add("call", "sell", short_k)
        # The ratio multiplies the long leg's quantity.
        index = len(raw) + 1
        key = str(index)
        long_qty = qty * ratio
        if key in prem:
            premium, source = prem[key], "user"
        elif vol is not None:
            premium = _price_leg(spot_value, long_k, "call", dte, vol, risk_free_rate, dividend_yield)
            source = "model"
        else:
            raise TemplateError("template_premium_required", "Premium for leg 2 was not supplied.")
        raw.append({"type": "call", "side": "buy", "strike": long_k, "premium": round(premium, 4),
                    "quantity": long_qty, "lot_size": lot, "label": f"buy call {long_k:g} x{ratio:g}"})
        basis.append(source)
    elif name == "put_ratio_spread":
        ratio = max(2.0, merged.get("ratio", 2.0))
        long_k = strike_at(-merged.get("short_offset_pct", 8.0) - merged.get("wing_width_pct", 5.0))
        short_k = strike_at(-merged.get("short_offset_pct", 8.0))
        add("put", "buy", long_k)
        index = len(raw) + 1
        key = str(index)
        short_qty = qty * ratio
        if key in prem:
            premium, source = prem[key], "user"
        elif vol is not None:
            premium = _price_leg(spot_value, short_k, "put", dte, vol, risk_free_rate, dividend_yield)
            source = "model"
        else:
            raise TemplateError("template_premium_required", "Premium for leg 2 was not supplied.")
        raw.append({"type": "put", "side": "sell", "strike": short_k, "premium": round(premium, 4),
                    "quantity": short_qty, "lot_size": lot, "label": f"sell put {short_k:g} x{ratio:g}"})
        basis.append(source)
    elif name in ("calendar_call", "calendar_put"):
        near = int(merged.get("near_days", 30))
        far = int(merged.get("far_days", 90))
        if near <= 0 or far <= near:
            raise TemplateError("template_calendar_dte_invalid", "Calendar needs 0 < near_days < far_days.")
        kind = "call" if name.endswith("call") else "put"
        k = strike_at(merged.get("strike_offset_pct", 0.0))
        add(kind, "sell", k, days=near)
        add(kind, "buy", k, days=far)

    normalized = [normalize_leg(leg, index) for index, leg in enumerate(raw)]
    for leg, source, original in zip(normalized, basis, raw):
        leg["premium_basis"] = source
        if "days_to_expiry" in original:
            leg["days_to_expiry"] = original["days_to_expiry"]

    return {
        "template": name,
        "label": TEMPLATES[name]["label"],
        "legs": normalized,
        "premium_basis": basis,
        "metadata": {
            "spot": round(spot_value, 4),
            "params": merged,
            "strike_step": round(step, 4),
            "quantity": qty,
            "lot_size": lot,
            "volatility": vol,
            "days_to_expiry": dte,
            "all_model_priced": all(item == "model" for item in basis),
            "all_user_supplied": all(item == "user" for item in basis),
        },
        "is_forecast": False,
        "is_recommendation": False,
    }


def calendar_payoff(
    built: dict[str, Any],
    *,
    volatility: float,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    grid_points: int = 121,
) -> dict[str, Any]:
    """Combined curve for calendar templates: near intrinsic + far mark-to-model.

    At the near expiry the short leg settles at intrinsic; the deferred leg is
    valued with Black-Scholes at ``far_days - near_days`` under the supplied
    volatility. This is explicitly a model value, not a quote.
    """
    from derivatives.options_engine import black_scholes

    legs = built["legs"]
    if len(legs) != 2:
        raise PayoffError("payoff_legs_required", "Calendar requires exactly two legs.")
    spot = float(built["metadata"]["spot"])
    near = int(legs[0].get("days_to_expiry") or built["metadata"]["days_to_expiry"])
    far = int(legs[1].get("days_to_expiry") or near * 3)
    remaining = max(1, far - near)

    lo, hi = spot * 0.80, spot * 1.20
    step = (hi - lo) / (grid_points - 1)
    curve: list[dict[str, float]] = []
    raw: list[tuple[float, float]] = []
    for index in range(grid_points):
        price = lo + step * index
        total = 0.0
        for leg in legs:
            strike = float(leg["strike"])
            contracts = float(leg["contracts"])
            direction = 1.0 if leg["side"] == "buy" else -1.0
            if leg is legs[0]:  # near leg settles at intrinsic
                settlement = max(0.0, price - strike) if leg["type"] == "call" else max(0.0, strike - price)
                entry = float(leg["premium"])
                total += ((settlement - entry) if leg["side"] == "buy" else (entry - settlement)) * contracts
            else:  # far leg marked to model at the near expiry
                priced = black_scholes(
                    spot=price, strike=strike, days_to_expiry=remaining, volatility=volatility,
                    risk_free_rate=risk_free_rate, dividend_yield=dividend_yield, option_type=leg["type"],
                )
                mark = float(priced.get("theoretical_price") or 0.0)
                entry = float(leg["premium"])
                total += direction * (mark - entry) * contracts
        raw.append((price, total))
        curve.append({"price": round(price, 2), "payoff": round(total, 2)})

    crossings: list[float] = []
    for (price_a, value_a), (price_b, value_b) in zip(raw, raw[1:]):
        if (value_a < 0 < value_b) or (value_b < 0 < value_a):
            span = value_b - value_a
            if span != 0:
                crossings.append(price_a + (price_b - price_a) * (-value_a / span))
    breakevens = sorted({round(value, 2) for value in crossings})
    at_spot_value = next(
        (value for price, value in raw if abs(price - spot) <= step / 2), curve[grid_points // 2]["payoff"]
    )
    return {
        "curve": curve,
        "breakevens": breakevens,
        "payoff_at_spot": round(at_spot_value, 2),
        "max_profit": round(max(value for _, value in raw), 2),
        "max_loss": round(min(value for _, value in raw), 2),
        "basis": "near_expiry_intrinsic_plus_far_mark_to_model",
        "label": (
            "Near leg settles at intrinsic; far leg is a Black-Scholes model value at the near expiry "
            "under the supplied volatility. Not a quote."
        ),
        "is_forecast": False,
    }


def default_payoff(built: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Single-expiry templates: delegate to the shared expiry payoff engine."""
    return build_payoff(legs=built["legs"], spot=built["metadata"]["spot"], **kwargs)
