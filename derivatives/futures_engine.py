"""Non-predictive futures fair-value, basis, carry, OI, and term-structure analytics."""

from __future__ import annotations

import math
from typing import Any


ANALYSIS_LABEL = "Analytical futures fair-value/basis/carry snapshot (non-predictive)"


def classify_open_interest(price_change_pct: float, oi_change_pct: float, *, neutral_band: float = 0.05) -> str:
    """Classify the signs of a two-point price/open-interest comparison."""

    price_change_pct = float(price_change_pct)
    oi_change_pct = float(oi_change_pct)
    neutral_band = abs(float(neutral_band))
    if not all(math.isfinite(value) for value in (price_change_pct, oi_change_pct, neutral_band)):
        raise ValueError("Price change, OI change, and neutral band must be finite.")
    price_direction = 0 if abs(price_change_pct) <= neutral_band else (1 if price_change_pct > 0 else -1)
    oi_direction = 0 if abs(oi_change_pct) <= neutral_band else (1 if oi_change_pct > 0 else -1)

    if price_direction > 0 and oi_direction > 0:
        return "Long buildup"
    if price_direction < 0 and oi_direction > 0:
        return "Short buildup"
    if price_direction > 0 and oi_direction < 0:
        return "Short covering"
    if price_direction < 0 and oi_direction < 0:
        return "Long unwinding"
    return "Neutral / inconclusive"


def _fair_value(spot: float, years: float, rate: float, carry_yield: float) -> float:
    return spot * math.exp((rate - carry_yield) * years)


def _rounded(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def analyze_futures_contract(
    *,
    spot_price: float,
    futures_price: float,
    days_to_expiry: int,
    open_interest: float,
    previous_open_interest: float,
    previous_futures_price: float,
    annual_risk_free_rate: float = 0.065,
    annual_carry_yield: float = 0.0,
    contract_symbol: str | None = None,
    expiry: str | None = None,
    as_of: str | None = None,
    lot_size: float | None = None,
    volume: float | None = None,
    bid_price: float | None = None,
    ask_price: float | None = None,
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Calculate a non-predictive carry snapshot and one-factor stresses.

    Existing callers need only the original arguments. Optional metadata and quote
    inputs improve evidence and liquidity disclosures when a provider has them.
    Rates and yields use decimal notation; scenario shocks are not forecasts.
    """

    numeric_values = {
        "spot_price": spot_price,
        "futures_price": futures_price,
        "open_interest": open_interest,
        "previous_open_interest": previous_open_interest,
        "previous_futures_price": previous_futures_price,
        "annual_risk_free_rate": annual_risk_free_rate,
        "annual_carry_yield": annual_carry_yield,
        "slippage_bps": slippage_bps,
    }
    try:
        converted = {name: float(value) for name, value in numeric_values.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError("Futures analysis inputs must be numeric.") from exc
    if not all(math.isfinite(value) for value in converted.values()):
        raise ValueError("Futures analysis inputs must be finite.")
    if any(converted[name] <= 0 for name in (
        "spot_price", "futures_price", "open_interest", "previous_open_interest", "previous_futures_price"
    )):
        raise ValueError("Prices and open-interest values must be positive.")
    if not 0 <= int(days_to_expiry) <= 366:
        raise ValueError("Days to expiry must be between 0 and 366.")
    if abs(converted["annual_risk_free_rate"]) > 5 or abs(converted["annual_carry_yield"]) > 5:
        raise ValueError("Annual rates and carry yields must be between -500% and 500%.")
    if not 0 <= converted["slippage_bps"] <= 10_000:
        raise ValueError("Slippage must be between 0 and 10,000 basis points.")

    optional_numeric = {"lot_size": lot_size, "volume": volume, "bid_price": bid_price, "ask_price": ask_price}
    cleaned_optional: dict[str, float | None] = {}
    for name, value in optional_numeric.items():
        if value is None:
            cleaned_optional[name] = None
            continue
        try:
            clean = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric when supplied.") from exc
        if not math.isfinite(clean):
            raise ValueError(f"{name} must be finite when supplied.")
        cleaned_optional[name] = clean
    if cleaned_optional["lot_size"] is not None and cleaned_optional["lot_size"] <= 0:
        raise ValueError("lot_size must be positive when supplied.")
    if cleaned_optional["volume"] is not None and cleaned_optional["volume"] < 0:
        raise ValueError("volume cannot be negative.")
    for name in ("bid_price", "ask_price"):
        price = cleaned_optional[name]
        if price is not None and price <= 0:
            raise ValueError(f"{name} must be positive when supplied.")

    spot_price = converted["spot_price"]
    futures_price = converted["futures_price"]
    previous_futures_price = converted["previous_futures_price"]
    open_interest = converted["open_interest"]
    previous_open_interest = converted["previous_open_interest"]
    annual_risk_free_rate = converted["annual_risk_free_rate"]
    annual_carry_yield = converted["annual_carry_yield"]
    slippage_bps = converted["slippage_bps"]
    days = int(days_to_expiry)
    years = days / 365.0
    basis = futures_price - spot_price
    basis_pct = basis / spot_price * 100.0
    annualized_basis = basis_pct / years if years > 0 else None
    fair_value = _fair_value(spot_price, years, annual_risk_free_rate, annual_carry_yield)
    mispricing_pct = (futures_price / fair_value - 1.0) * 100.0
    price_change_pct = (futures_price / previous_futures_price - 1.0) * 100.0
    oi_change = open_interest - previous_open_interest
    oi_change_pct = oi_change / previous_open_interest * 100.0
    classification = classify_open_interest(price_change_pct, oi_change_pct)

    bid = cleaned_optional["bid_price"]
    ask = cleaned_optional["ask_price"]
    observed_volume = cleaned_optional["volume"]
    warnings: list[dict[str, Any]] = [{
        "code": "TWO_POINT_OI_EVIDENCE",
        "severity": "info",
        "message": "OI/price regime uses only current and previous snapshots; it is descriptive, not predictive.",
    }]
    spread_bps: float | None = None
    market_condition_valid: bool | None = True
    if bid is None or ask is None:
        market_condition_valid = None
        warnings.append({
            "code": "QUOTE_LIQUIDITY_UNAVAILABLE",
            "severity": "warning",
            "message": "Both bid and ask are required to assess spread and crossed-market conditions.",
        })
    elif bid > ask:
        market_condition_valid = False
        warnings.append({
            "code": "CROSSED_MARKET",
            "severity": "error",
            "message": "Bid exceeds ask; quote-dependent liquidity evidence is invalid.",
        })
    else:
        midpoint = (bid + ask) / 2.0
        spread_bps = (ask - bid) / midpoint * 10_000.0
        if spread_bps > 50:
            warnings.append({
                "code": "WIDE_SPREAD",
                "severity": "warning",
                "message": "Bid/ask spread exceeds 50 bps; executable value may differ materially from the model snapshot.",
            })
    if observed_volume is None:
        warnings.append({
            "code": "VOLUME_UNAVAILABLE",
            "severity": "warning",
            "message": "Volume was not supplied; open interest alone does not establish liquidity.",
        })
    elif observed_volume == 0:
        warnings.append({
            "code": "ZERO_VOLUME",
            "severity": "warning",
            "message": "Reported volume is zero; executable liquidity may be absent.",
        })
    if as_of is None:
        warnings.append({
            "code": "AS_OF_UNAVAILABLE",
            "severity": "warning",
            "message": "No market-data timestamp was supplied; freshness cannot be established.",
        })

    def fair_value_case(spot_shock_pct: float = 0.0, rate_shock: float = 0.0, carry_shock: float = 0.0) -> dict[str, Any]:
        stressed_spot = spot_price * (1.0 + spot_shock_pct / 100.0)
        stressed_rate = annual_risk_free_rate + rate_shock
        stressed_carry = annual_carry_yield + carry_shock
        stressed_value = _fair_value(stressed_spot, years, stressed_rate, stressed_carry)
        return {
            "spot_shock_pct": _rounded(spot_shock_pct, 4),
            "rate_shock_bps": _rounded(rate_shock * 10_000, 2),
            "carry_yield_shock_bps": _rounded(carry_shock * 10_000, 2),
            "stressed_spot": _rounded(stressed_spot, 4),
            "stressed_rate": _rounded(stressed_rate, 6),
            "stressed_carry_yield": _rounded(stressed_carry, 6),
            "analytical_fair_value": _rounded(stressed_value, 4),
            "market_minus_fair_value_pct": _rounded((futures_price / stressed_value - 1.0) * 100.0),
        }

    slippage_levels = sorted({max(0.0, slippage_bps - 10.0), slippage_bps, slippage_bps + 10.0, slippage_bps + 25.0})
    stress_scenarios = {
        "semantics": "One-factor analytical sensitivities around the input snapshot; these are not price targets or forecasts.",
        "spot": [fair_value_case(spot_shock_pct=shock) for shock in (-10.0, 0.0, 10.0)],
        "rate": [fair_value_case(rate_shock=shock) for shock in (-0.01, 0.0, 0.01)],
        "carry_yield": [fair_value_case(carry_shock=shock) for shock in (-0.01, 0.0, 0.01)],
        "slippage": [{
            "slippage_bps": _rounded(level, 2),
            "effective_buy_price": _rounded(futures_price * (1.0 + level / 10_000.0), 4),
            "effective_sell_price": _rounded(futures_price * (1.0 - level / 10_000.0), 4),
        } for level in slippage_levels],
    }

    return {
        "spot_price": round(spot_price, 4),
        "futures_price": round(futures_price, 4),
        "days_to_expiry": days,
        "basis": round(basis, 4),
        "basis_pct": round(basis_pct, 6),
        "annualized_basis_pct": round(annualized_basis, 6) if annualized_basis is not None else None,
        "theoretical_fair_value": round(fair_value, 4),
        "fair_value_mispricing_pct": round(mispricing_pct, 6),
        "open_interest": round(open_interest, 4),
        "change_in_open_interest": round(oi_change, 4),
        "change_in_open_interest_pct": round(oi_change_pct, 6),
        "futures_price_change_pct": round(price_change_pct, 6),
        "classification": classification,
        "interpretation": _interpretation(classification, mispricing_pct),
        "analysis_label": ANALYSIS_LABEL,
        "analysis_type": "analytical_fair_value_basis_carry",
        "is_predictive": False,
        "target_semantics": {
            "target": "cost-of-carry fair value at contract expiry",
            "horizon_days": days,
            "meaning": "Model-implied valuation under supplied spot, rate, and carry assumptions; not a future market-price target.",
        },
        "as_of": as_of,
        "as_of_semantics": "Timestamp of the supplied market snapshot; null means freshness is unknown.",
        "contract_metadata": {
            "contract_symbol": contract_symbol,
            "expiry": expiry,
            "days_to_expiry": days,
            "lot_size": cleaned_optional["lot_size"],
        },
        "carry_inputs": {
            "annual_risk_free_rate": round(annual_risk_free_rate, 8),
            "annual_carry_yield": round(annual_carry_yield, 8),
            "year_fraction": round(years, 8),
            "compounding": "continuous",
        },
        "oi_price_regime": {
            "label": classification,
            "price_change_pct": round(price_change_pct, 6),
            "open_interest_change_pct": round(oi_change_pct, 6),
            "evidence": "two consecutive input snapshots",
            "is_predictive": False,
        },
        "liquidity": {
            "market_condition_valid": market_condition_valid,
            "bid_price": bid,
            "ask_price": ask,
            "spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
            "volume": observed_volume,
            "assessment": "invalid" if market_condition_valid is False else (
                "observed" if spread_bps is not None and observed_volume is not None else "incomplete"
            ),
        },
        "warnings": warnings,
        "stress_scenarios": stress_scenarios,
        "data_status": "Analytical snapshot only; no future price prediction or historical validation is performed.",
    }


def analyze_futures_term_structure(
    *,
    spot_price: float,
    contracts: list[dict[str, Any]],
    annual_risk_free_rate: float = 0.065,
    annual_carry_yield: float = 0.0,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Compare quoted futures months and quantify the cost of rolling a long.

    Contract prices are observed inputs. Fair values are analytical
    cost-of-carry references and are deliberately kept separate from quoted
    calendar spreads. Positive roll cost means the deferred contract costs more
    than the contract being closed.
    """
    try:
        spot = float(spot_price)
        rate = float(annual_risk_free_rate)
        carry = float(annual_carry_yield)
    except (TypeError, ValueError) as exc:
        raise ValueError("Term-structure inputs must be numeric.") from exc
    if not all(math.isfinite(value) for value in (spot, rate, carry)):
        raise ValueError("Term-structure inputs must be finite.")
    if spot <= 0:
        raise ValueError("Spot price must be positive.")
    if abs(rate) > 5 or abs(carry) > 5:
        raise ValueError("Annual rates and carry yields must be between -500% and 500%.")
    if not 2 <= len(contracts) <= 12:
        raise ValueError("Term structure requires between 2 and 12 contracts.")

    cleaned: list[dict[str, Any]] = []
    for position, raw in enumerate(contracts):
        try:
            label = str(raw.get("label") or f"Contract {position + 1}").strip()
            days = int(raw["days_to_expiry"])
            price = float(raw["futures_price"])
            raw_oi = raw.get("open_interest")
            open_interest = float(raw_oi) if raw_oi is not None else None
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each contract requires a label, days to expiry, and futures price.") from exc
        if not label or len(label) > 40:
            raise ValueError("Contract labels must contain 1 to 40 characters.")
        if not 0 <= days <= 730:
            raise ValueError("Contract days to expiry must be between 0 and 730.")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Contract futures prices must be finite and positive.")
        if open_interest is not None and (not math.isfinite(open_interest) or open_interest < 0):
            raise ValueError("Contract open interest must be finite and non-negative when supplied.")
        cleaned.append({
            "label": label,
            "days_to_expiry": days,
            "futures_price": price,
            "open_interest": open_interest,
        })

    cleaned.sort(key=lambda item: int(item["days_to_expiry"]))
    expiry_days = [int(item["days_to_expiry"]) for item in cleaned]
    if len(set(expiry_days)) != len(expiry_days):
        raise ValueError("Contract days to expiry must be unique.")
    labels = [str(item["label"]).casefold() for item in cleaned]
    if len(set(labels)) != len(labels):
        raise ValueError("Contract labels must be unique.")

    total_oi = sum(float(item["open_interest"] or 0.0) for item in cleaned)
    points: list[dict[str, Any]] = []
    for item in cleaned:
        days = int(item["days_to_expiry"])
        price = float(item["futures_price"])
        fair_value = _fair_value(spot, days / 365.0, rate, carry)
        basis = price - spot
        points.append({
            **item,
            "futures_price": _rounded(price, 4),
            "open_interest": _rounded(float(item["open_interest"]), 4) if item["open_interest"] is not None else None,
            "open_interest_share_pct": _rounded(float(item["open_interest"] or 0.0) / total_oi * 100.0) if total_oi > 0 else None,
            "basis": _rounded(basis, 4),
            "basis_pct": _rounded(basis / spot * 100.0),
            "theoretical_fair_value": _rounded(fair_value, 4),
            "fair_value_difference_pct": _rounded((price / fair_value - 1.0) * 100.0),
        })

    rollovers: list[dict[str, Any]] = []
    for near, deferred in zip(points, points[1:]):
        near_price = float(near["futures_price"])
        deferred_price = float(deferred["futures_price"])
        day_gap = int(deferred["days_to_expiry"]) - int(near["days_to_expiry"])
        roll_points = deferred_price - near_price
        roll_pct = roll_points / near_price * 100.0
        rollovers.append({
            "from_label": near["label"],
            "to_label": deferred["label"],
            "day_gap": day_gap,
            "long_roll_cost_points": _rounded(roll_points, 4),
            "long_roll_cost_pct": _rounded(roll_pct),
            "annualized_roll_yield_pct": _rounded(roll_pct * 365.0 / day_gap) if day_gap > 0 else None,
            "state": "contango" if roll_points > 0 else "backwardation" if roll_points < 0 else "flat",
        })

    near_price = float(points[0]["futures_price"])
    far_price = float(points[-1]["futures_price"])
    slope_pct = (far_price / near_price - 1.0) * 100.0
    states = {item["state"] for item in rollovers}
    if states == {"contango"}:
        curve_state = "contango"
    elif states == {"backwardation"}:
        curve_state = "backwardation"
    elif states == {"flat"}:
        curve_state = "flat"
    else:
        curve_state = "mixed"

    warnings: list[dict[str, str]] = []
    if total_oi == 0:
        warnings.append({
            "code": "OPEN_INTEREST_UNAVAILABLE",
            "message": "Open interest was not supplied; rollover-liquidity concentration cannot be assessed.",
        })
    if as_of is None:
        warnings.append({
            "code": "AS_OF_UNAVAILABLE",
            "message": "No market-data timestamp was supplied; freshness cannot be established.",
        })

    return {
        "analysis_label": "Analytical futures term structure and rollover snapshot (non-predictive)",
        "analysis_type": "futures_term_structure",
        "is_predictive": False,
        "spot_price": _rounded(spot, 4),
        "curve_state": curve_state,
        "near_to_far_slope_pct": _rounded(slope_pct),
        "contracts": points,
        "rollovers": rollovers,
        "carry_inputs": {
            "annual_risk_free_rate": _rounded(rate, 8),
            "annual_carry_yield": _rounded(carry, 8),
            "compounding": "continuous",
        },
        "as_of": as_of,
        "warnings": warnings,
        "data_status": "User-supplied analytical snapshot; no live quote, future price prediction, or historical validation is implied.",
    }


def _interpretation(classification: str, mispricing_pct: float) -> str:
    direction = {
        "Long buildup": "The two supplied snapshots have rising price and open interest (conventionally labelled long buildup).",
        "Short buildup": "The two supplied snapshots have falling price and rising open interest (conventionally labelled short buildup).",
        "Short covering": "The two supplied snapshots have rising price and falling open interest (conventionally labelled short covering).",
        "Long unwinding": "The two supplied snapshots have falling price and open interest (conventionally labelled long unwinding).",
    }.get(classification, "The supplied price/open-interest changes are inside the neutral band.")
    valuation = " Market price is above analytical carry value." if mispricing_pct > 0.1 else (
        " Market price is below analytical carry value." if mispricing_pct < -0.1 else " Market price is close to analytical carry value."
    )
    return direction + valuation + " Neither label predicts the next price move."


def expiry_bounded_scenario_interval(
    *, reference_price: float, days_to_expiry: int, realized_volatility: float, implied_volatility: float | None = None,
) -> dict[str, Any]:
    """Expiry-limited volatility interval for derivative analysis, never a forecast."""
    reference = float(reference_price)
    days = int(days_to_expiry)
    realized = float(realized_volatility)
    implied = float(implied_volatility if implied_volatility is not None else realized)
    if reference <= 0 or days < 0 or not all(math.isfinite(value) and value >= 0 for value in (realized, implied)):
        raise ValueError("Scenario inputs must be finite, non-negative, and use a positive reference price.")
    horizon_days = min(max(days, 1), 365)
    half_width = reference * ((realized + implied) / 2.0) * math.sqrt(horizon_days / 365.0)
    return {
        "analysis_label": "Expiry-bounded volatility scenario (non-predictive)",
        "is_predictive": False,
        "reference_price": round(reference, 4),
        "horizon_days": horizon_days,
        "realized_volatility": round(realized, 6),
        "implied_volatility": round(implied, 6),
        "low": round(max(0.01, reference - half_width), 4),
        "high": round(reference + half_width, 4),
        "semantics": "Expiry-limited analytical sensitivity, not a future price prediction or recommendation.",
    }
