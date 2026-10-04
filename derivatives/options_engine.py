"""Non-predictive options valuation scenarios and chain-quality analytics."""

from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd

SQRT_2PI = math.sqrt(2.0 * math.pi)
OPTION_VALUATION_LABEL = "Analytical option model valuation (non-predictive)"
OPTION_SCENARIO_LABEL = "Analytical option scenario ladder (non-predictive)"


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / SQRT_2PI


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _validate_option_inputs(spot: float, strike: float, time_years: float, volatility: float) -> None:
    if not all(math.isfinite(value) for value in (spot, strike, time_years, volatility)):
        raise ValueError("Spot, strike, time, and volatility must be finite.")
    if spot <= 0 or strike <= 0:
        raise ValueError("Spot and strike prices must be positive.")
    if time_years <= 0:
        raise ValueError("Time to expiry must be positive.")
    if volatility <= 0 or volatility > 5:
        raise ValueError("Volatility must be greater than 0 and no more than 500%.")


def black_scholes(
    *,
    spot: float,
    strike: float,
    days_to_expiry: int,
    volatility: float,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    option_type: str = "call",
) -> dict[str, Any]:
    """Return Black-Scholes price and primary Greeks.

    Volatility and rates use decimal notation, e.g. 0.20 for 20%.
    """

    option_type = str(option_type).strip().lower()
    if option_type not in {"call", "put"}:
        raise ValueError("Option type must be 'call' or 'put'.")
    time_years = int(days_to_expiry) / 365.0
    spot = float(spot)
    strike = float(strike)
    volatility = float(volatility)
    risk_free_rate = float(risk_free_rate)
    dividend_yield = float(dividend_yield)
    _validate_option_inputs(spot, strike, time_years, volatility)
    if not math.isfinite(risk_free_rate) or not math.isfinite(dividend_yield):
        raise ValueError("Risk-free rate and dividend yield must be finite.")

    sqrt_t = math.sqrt(time_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discount_r = math.exp(-risk_free_rate * time_years)
    discount_q = math.exp(-dividend_yield * time_years)

    if option_type == "call":
        price = spot * discount_q * _normal_cdf(d1) - strike * discount_r * _normal_cdf(d2)
        delta = discount_q * _normal_cdf(d1)
        theta = (
            -(spot * discount_q * _normal_pdf(d1) * volatility) / (2 * sqrt_t)
            - risk_free_rate * strike * discount_r * _normal_cdf(d2)
            + dividend_yield * spot * discount_q * _normal_cdf(d1)
        ) / 365.0
        rho = strike * time_years * discount_r * _normal_cdf(d2) / 100.0
    else:
        price = strike * discount_r * _normal_cdf(-d2) - spot * discount_q * _normal_cdf(-d1)
        delta = discount_q * (_normal_cdf(d1) - 1.0)
        theta = (
            -(spot * discount_q * _normal_pdf(d1) * volatility) / (2 * sqrt_t)
            + risk_free_rate * strike * discount_r * _normal_cdf(-d2)
            - dividend_yield * spot * discount_q * _normal_cdf(-d1)
        ) / 365.0
        rho = -strike * time_years * discount_r * _normal_cdf(-d2) / 100.0

    gamma = discount_q * _normal_pdf(d1) / (spot * volatility * sqrt_t)
    vega = spot * discount_q * _normal_pdf(d1) * sqrt_t / 100.0
    intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)

    return {
        "option_type": option_type,
        "theoretical_price": round(price, 6),
        "intrinsic_value": round(intrinsic, 6),
        "time_value": round(max(0.0, price - intrinsic), 6),
        "delta": round(delta, 8),
        "gamma": round(gamma, 8),
        "theta_per_day": round(theta, 8),
        "vega_per_vol_point": round(vega, 8),
        "rho_per_rate_point": round(rho, 8),
        "d1": round(d1, 8),
        "d2": round(d2, 8),
        "analysis_label": OPTION_VALUATION_LABEL,
        "is_predictive": False,
        "target_semantics": "Theoretical premium under the supplied assumptions, not a future market-premium target.",
        "as_of_semantics": "No quote timestamp is used by this model calculation.",
        "historical_premium_validation_performed": False,
    }


def implied_volatility(
    *,
    market_price: float,
    spot: float,
    strike: float,
    days_to_expiry: int,
    option_type: str = "call",
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    tolerance: float = 1e-6,
    max_iterations: int = 120,
) -> float:
    """Solve IV from one supplied premium; this performs no historical validation."""

    market_price = float(market_price)
    if not math.isfinite(market_price) or market_price <= 0:
        raise ValueError("Market option price must be positive and finite.")
    low, high = 0.0001, 5.0
    low_price = float(black_scholes(
        spot=spot, strike=strike, days_to_expiry=days_to_expiry, volatility=low,
        risk_free_rate=risk_free_rate, dividend_yield=dividend_yield, option_type=option_type,
    )["theoretical_price"])
    high_price = float(black_scholes(
        spot=spot, strike=strike, days_to_expiry=days_to_expiry, volatility=high,
        risk_free_rate=risk_free_rate, dividend_yield=dividend_yield, option_type=option_type,
    )["theoretical_price"])
    if market_price < low_price - tolerance or market_price > high_price + tolerance:
        raise ValueError("Market price is outside the solvable Black-Scholes range.")

    for _ in range(int(max_iterations)):
        mid = (low + high) / 2.0
        price = float(black_scholes(
            spot=spot, strike=strike, days_to_expiry=days_to_expiry, volatility=mid,
            risk_free_rate=risk_free_rate, dividend_yield=dividend_yield, option_type=option_type,
        )["theoretical_price"])
        if abs(price - market_price) <= tolerance:
            return round(mid, 8)
        if price < market_price:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0, 8)


def analyze_option_scenarios(
    *,
    spot: float,
    strike: float,
    days_to_expiry: int,
    volatility: float,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    option_type: str = "call",
    market_price: float | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Return one-factor Black-Scholes sensitivities, explicitly not forecasts."""

    base_inputs: dict[str, Any] = {
        "spot": spot,
        "strike": strike,
        "days_to_expiry": days_to_expiry,
        "volatility": volatility,
        "risk_free_rate": risk_free_rate,
        "dividend_yield": dividend_yield,
        "option_type": option_type,
    }
    base = black_scholes(**base_inputs)
    base_price = float(base["theoretical_price"])
    if as_of is not None and pd.isna(pd.to_datetime(as_of, errors="coerce", utc=True)):
        raise ValueError("as_of must be a valid timestamp when supplied.")

    if market_price is not None:
        try:
            market_price = float(market_price)
        except (TypeError, ValueError) as exc:
            raise ValueError("Market option price must be numeric when supplied.") from exc
        if not math.isfinite(market_price) or market_price <= 0:
            raise ValueError("Market option price must be positive and finite when supplied.")

    def price_case(**updates: Any) -> float:
        return float(black_scholes(**{**base_inputs, **updates})["theoretical_price"])

    spot_ladder = []
    for label, shock in (("Spot -10%", -0.10), ("Base", 0.0), ("Spot +10%", 0.10)):
        stressed_spot = float(spot) * (1.0 + shock)
        spot_ladder.append({
            "label": label,
            "spot_shock_pct": round(shock * 100.0, 4),
            "stressed_spot": round(stressed_spot, 4),
            "analytical_premium": round(price_case(spot=stressed_spot), 6),
        })

    volatility_ladder = []
    for shock in (-0.05, 0.0, 0.05):
        stressed_volatility = max(0.0001, float(volatility) + shock)
        volatility_ladder.append({
            "volatility_shock_points": round(shock * 100.0, 4),
            "stressed_volatility": round(stressed_volatility, 6),
            "analytical_premium": round(price_case(volatility=stressed_volatility), 6),
        })

    rate_ladder = []
    for shock in (-0.01, 0.0, 0.01):
        stressed_rate = float(risk_free_rate) + shock
        rate_ladder.append({
            "rate_shock_bps": round(shock * 10_000.0, 2),
            "stressed_rate": round(stressed_rate, 6),
            "analytical_premium": round(price_case(risk_free_rate=stressed_rate), 6),
        })

    slippage_ladder = []
    for level in (0.0, 10.0, 25.0):
        slippage_ladder.append({
            "slippage_bps": level,
            "effective_buy_premium": round(base_price * (1.0 + level / 10_000.0), 6),
            "effective_sell_premium": round(base_price * (1.0 - level / 10_000.0), 6),
        })

    snapshot_comparison = None
    if market_price is not None:
        snapshot_comparison = {
            "supplied_market_price": round(market_price, 6),
            "model_minus_market": round(base_price - market_price, 6),
            "semantics": "Contemporaneous input comparison only; no historical premium validation was performed.",
        }

    return {
        "analysis_label": OPTION_SCENARIO_LABEL,
        "analysis_type": "analytical_option_sensitivities",
        "is_predictive": False,
        "option_type": str(option_type).strip().lower(),
        "base_theoretical_premium": round(base_price, 6),
        "target_semantics": "Scenario theoretical premiums under stated one-factor shocks; not expected or target future premiums.",
        "as_of": as_of,
        "as_of_semantics": "Timestamp of supplied inputs; null means freshness is unknown.",
        "scenarios": {
            "semantics": "Each ladder changes one input and holds the other model inputs constant.",
            "spot": spot_ladder,
            "volatility": volatility_ladder,
            "rate": rate_ladder,
            "slippage": slippage_ladder,
        },
        "snapshot_comparison": snapshot_comparison,
        "historical_premium_validation": {
            "performed": False,
            "reason": "No historical option-premium series is accepted or tested by this function.",
        },
        "disclaimer": "Analytical scenario reference only; not a prediction or options trade recommendation.",
    }


def _normalise_chain(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    required = {"strike", "option_type", "open_interest"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("Option chain is missing: " + ", ".join(sorted(missing)))
    frame = frame.copy()
    frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    frame["open_interest"] = pd.to_numeric(frame["open_interest"], errors="coerce")
    frame["option_type"] = frame["option_type"].astype(str).str.strip().str.lower()
    frame = frame.dropna(subset=["strike", "open_interest"])
    frame = frame[(frame["strike"] > 0) & (frame["open_interest"] >= 0)]
    frame = frame[frame["option_type"].isin(["call", "put"])]
    if frame.empty:
        raise ValueError("Option chain contains no valid call/put rows.")
    return frame


def _available_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def validate_option_chain(
    rows: Iterable[dict[str, Any]] | pd.DataFrame,
    *,
    as_of: str | None = None,
    stale_after_seconds: float = 900.0,
    wide_spread_pct: float = 20.0,
) -> dict[str, Any]:
    """Report chain evidence and quote defects without treating quotes as history."""

    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    required = {"strike", "option_type", "open_interest"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("Option chain is missing: " + ", ".join(sorted(missing)))
    try:
        stale_after_seconds = float(stale_after_seconds)
        wide_spread_pct = float(wide_spread_pct)
    except (TypeError, ValueError) as exc:
        raise ValueError("Staleness and spread thresholds must be numeric.") from exc
    if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive and finite.")
    if not math.isfinite(wide_spread_pct) or wide_spread_pct <= 0:
        raise ValueError("wide_spread_pct must be positive and finite.")
    reference_time = pd.to_datetime(as_of, errors="coerce", utc=True) if as_of is not None else pd.Timestamp.now(tz="UTC")
    if pd.isna(reference_time):
        raise ValueError("as_of must be a valid timestamp when supplied.")

    warnings: list[dict[str, Any]] = []

    def warn(code: str, severity: str, message: str, count: int | None = None) -> None:
        warning: dict[str, Any] = {"code": code, "severity": severity, "message": message}
        if count is not None:
            warning["row_count"] = int(count)
        warnings.append(warning)

    strikes = pd.to_numeric(frame["strike"], errors="coerce")
    oi = pd.to_numeric(frame["open_interest"], errors="coerce")
    option_types = frame["option_type"].astype(str).str.strip().str.lower()
    invalid_strikes = int((strikes.isna() | (strikes <= 0)).sum())
    invalid_oi = int((oi.isna() | (oi < 0)).sum())
    invalid_types = int((~option_types.isin(["call", "put"])).sum())
    if invalid_strikes:
        warn("INVALID_STRIKE", "error", "Rows with non-positive or non-numeric strikes are excluded.", invalid_strikes)
    if invalid_oi:
        warn("INVALID_OPEN_INTEREST", "error", "Rows with negative or non-numeric open interest are excluded.", invalid_oi)
    if invalid_types:
        warn("INVALID_OPTION_TYPE", "error", "Rows not labelled call or put are excluded.", invalid_types)

    valid_base = strikes.notna() & (strikes > 0) & oi.notna() & (oi >= 0) & option_types.isin(["call", "put"])
    zero_oi = int((valid_base & (oi == 0)).sum())
    if zero_oi:
        warn("ZERO_OPEN_INTEREST", "warning", "Rows with zero OI provide no positioning weight and may be illiquid.", zero_oi)
    if bool(valid_base.any()) and float(oi[valid_base].sum()) == 0:
        warn("INSUFFICIENT_OI_EVIDENCE", "error", "All valid rows have zero OI; PCR, walls, and max pain lack OI evidence.")

    bid_column = _available_column(frame, ("bid", "bid_price", "best_bid"))
    ask_column = _available_column(frame, ("ask", "ask_price", "best_ask"))
    spread_assessed_rows = 0
    crossed_rows = 0
    wide_spread_rows = 0
    invalid_quote_rows = 0
    if bid_column is None or ask_column is None:
        warn("SPREAD_UNAVAILABLE", "warning", "Bid and ask columns are required to validate crossed or wide markets.")
    else:
        bids = pd.to_numeric(frame[bid_column], errors="coerce")
        asks = pd.to_numeric(frame[ask_column], errors="coerce")
        supplied_quotes = frame[bid_column].notna() | frame[ask_column].notna()
        invalid_quotes = supplied_quotes & (bids.isna() | asks.isna() | (bids < 0) | (asks <= 0))
        invalid_quote_rows = int(invalid_quotes.sum())
        if invalid_quote_rows:
            warn("INVALID_QUOTE", "error", "Supplied bid/ask pairs must be numeric, with bid non-negative and ask positive.", invalid_quote_rows)
        assessable = valid_base & bids.notna() & asks.notna() & (bids >= 0) & (asks > 0)
        spread_assessed_rows = int(assessable.sum())
        crossed = assessable & (bids > asks)
        crossed_rows = int(crossed.sum())
        if crossed_rows:
            warn("CROSSED_MARKET", "error", "Bid exceeds ask; affected quote evidence is invalid.", crossed_rows)
        uncrossed = assessable & ~crossed
        midpoint = (bids + asks) / 2.0
        spread_pct = (asks - bids) / midpoint * 100.0
        wide = uncrossed & (spread_pct > wide_spread_pct)
        wide_spread_rows = int(wide.sum())
        if wide_spread_rows:
            warn(
                "WIDE_SPREAD",
                "warning",
                f"Relative bid/ask spread exceeds {wide_spread_pct:g}% for affected rows.",
                wide_spread_rows,
            )

        last_column = _available_column(frame, ("last_price", "ltp", "market_price"))
        if last_column is not None:
            lasts = pd.to_numeric(frame[last_column], errors="coerce")
            outside = uncrossed & lasts.notna() & ((lasts < bids) | (lasts > asks))
            outside_count = int(outside.sum())
            if outside_count:
                warn("LAST_OUTSIDE_MARKET", "warning", "Last price is outside the supplied bid/ask interval.", outside_count)

    volume_column = _available_column(frame, ("volume", "traded_volume"))
    if volume_column is None:
        warn("VOLUME_UNAVAILABLE", "warning", "Volume is unavailable; OI alone does not establish executable liquidity.")
    else:
        volume = pd.to_numeric(frame[volume_column], errors="coerce")
        invalid_volume = int((frame[volume_column].notna() & (volume.isna() | (volume < 0))).sum())
        if invalid_volume:
            warn("INVALID_VOLUME", "error", "Supplied volume must be numeric and non-negative.", invalid_volume)
        no_activity = int((valid_base & (oi == 0) & (volume.fillna(0) == 0)).sum())
        if no_activity:
            warn("NO_REPORTED_ACTIVITY", "warning", "Rows report neither open interest nor volume.", no_activity)

    timestamp_column = _available_column(frame, ("quote_timestamp", "timestamp", "as_of", "fetched_at"))
    stale_rows = 0
    future_rows = 0
    latest_quote_at: str | None = None
    freshness_assessed_rows = 0
    if timestamp_column is None:
        warn("QUOTE_TIME_UNAVAILABLE", "warning", "Quote timestamps are absent; freshness and staleness cannot be assessed.")
    else:
        timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce", utc=True)
        invalid_timestamps = int((frame[timestamp_column].notna() & timestamps.isna()).sum())
        if invalid_timestamps:
            warn("INVALID_QUOTE_TIME", "error", "Supplied quote timestamps could not be parsed.", invalid_timestamps)
        valid_timestamps = timestamps.notna()
        freshness_assessed_rows = int(valid_timestamps.sum())
        if freshness_assessed_rows:
            age_seconds = (reference_time - timestamps).dt.total_seconds()
            stale_rows = int((valid_timestamps & (age_seconds > stale_after_seconds)).sum())
            future_rows = int((valid_timestamps & (age_seconds < -60)).sum())
            latest_quote_at = timestamps[valid_timestamps].max().isoformat()
            if stale_rows:
                warn("STALE_QUOTE", "warning", f"Quote age exceeds {stale_after_seconds:g} seconds.", stale_rows)
            if future_rows:
                warn("FUTURE_QUOTE_TIME", "error", "Quote timestamp is more than 60 seconds after the analysis as-of time.", future_rows)

    error_codes = {warning["code"] for warning in warnings if warning["severity"] == "error"}
    market_errors = error_codes.intersection({"INVALID_QUOTE", "CROSSED_MARKET", "INVALID_QUOTE_TIME", "FUTURE_QUOTE_TIME"})
    market_data_valid: bool | None = not bool(market_errors)
    if not spread_assessed_rows and not freshness_assessed_rows and not market_errors:
        market_data_valid = None
    valid_rows = int(valid_base.sum())
    valid_types = set(option_types[valid_base].tolist())
    oi_data_valid = valid_rows > 0 and {"call", "put"}.issubset(valid_types) and not bool(
        error_codes.intersection({"INVALID_STRIKE", "INVALID_OPEN_INTEREST", "INVALID_OPTION_TYPE", "INSUFFICIENT_OI_EVIDENCE"})
    )
    evidence_quality = "low" if error_codes or stale_rows else (
        "limited" if any(warning["severity"] == "warning" for warning in warnings) else "moderate"
    )
    return {
        "input_row_count": int(len(frame)),
        "valid_oi_row_count": valid_rows,
        "excluded_row_count": int(len(frame) - valid_rows),
        "market_data_valid": market_data_valid,
        "oi_data_valid": oi_data_valid,
        "is_stale": bool(stale_rows) if freshness_assessed_rows else None,
        "freshness_assessed_row_count": freshness_assessed_rows,
        "spread_assessed_row_count": spread_assessed_rows,
        "crossed_row_count": crossed_rows,
        "wide_spread_row_count": wide_spread_rows,
        "invalid_quote_row_count": invalid_quote_rows,
        "latest_quote_at": latest_quote_at,
        "evidence_quality": evidence_quality,
        "warnings": warnings,
        "historical_premium_validation_performed": False,
    }


def calculate_max_pain(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> dict[str, Any]:
    frame = _normalise_chain(rows)
    strikes = sorted(float(value) for value in frame["strike"].unique())
    pain_by_settlement: dict[float, float] = {}
    for settlement in strikes:
        call_pain = ((settlement - frame.loc[frame["option_type"] == "call", "strike"]).clip(lower=0)
                     * frame.loc[frame["option_type"] == "call", "open_interest"]).sum()
        put_pain = ((frame.loc[frame["option_type"] == "put", "strike"] - settlement).clip(lower=0)
                    * frame.loc[frame["option_type"] == "put", "open_interest"]).sum()
        pain_by_settlement[settlement] = float(call_pain + put_pain)
    max_pain = min(
        pain_by_settlement,
        key=lambda settlement: pain_by_settlement[settlement],
    )
    return {
        "max_pain_strike": round(float(max_pain), 4),
        "minimum_writer_payout": round(float(pain_by_settlement[max_pain]), 4),
        "pain_curve": [
            {"strike": round(strike, 4), "writer_payout": round(pain_by_settlement[strike], 4)}
            for strike in strikes
        ],
    }


def analyze_option_chain(
    rows: Iterable[dict[str, Any]] | pd.DataFrame,
    *,
    spot_price: float | None = None,
    as_of: str | None = None,
    stale_after_seconds: float = 900.0,
    wide_spread_pct: float = 20.0,
) -> dict[str, Any]:
    """Summarize a chain snapshot with explicit evidence and quote warnings."""

    raw_frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    validation = validate_option_chain(
        raw_frame,
        as_of=as_of,
        stale_after_seconds=stale_after_seconds,
        wide_spread_pct=wide_spread_pct,
    )
    frame = _normalise_chain(raw_frame)
    if frame.loc[frame["option_type"] == "call"].empty or frame.loc[frame["option_type"] == "put"].empty:
        raise ValueError("Option chain must contain at least one valid call and one valid put row.")
    call_oi = float(frame.loc[frame["option_type"] == "call", "open_interest"].sum())
    put_oi = float(frame.loc[frame["option_type"] == "put", "open_interest"].sum())
    pcr = put_oi / call_oi if call_oi > 0 else 0.0
    max_pain = calculate_max_pain(frame)
    call_wall_row = frame[frame["option_type"] == "call"].sort_values("open_interest", ascending=False).iloc[0]
    put_wall_row = frame[frame["option_type"] == "put"].sort_values("open_interest", ascending=False).iloc[0]

    if spot_price is not None:
        try:
            spot_price = float(spot_price)
        except (TypeError, ValueError) as exc:
            raise ValueError("spot_price must be numeric when supplied.") from exc
        if not math.isfinite(spot_price) or spot_price <= 0:
            raise ValueError("spot_price must be positive and finite when supplied.")

    # This is a transparent snapshot descriptor, not a trading-success probability.
    score = 0.0 if call_oi == 0 or put_oi == 0 else max(-1.0, min(1.0, (pcr - 1.0) / 1.5))
    if spot_price is not None:
        distance = (float(max_pain["max_pain_strike"]) / spot_price - 1.0)
        score += max(-0.25, min(0.25, distance * 2.0))
        score = max(-1.0, min(1.0, score))
    label = "Bullish structure" if score > 0.15 else ("Bearish structure" if score < -0.15 else "Balanced structure")

    if call_oi == 0 or put_oi == 0:
        validation["warnings"].append({
            "code": "ONE_SIDED_OI_EVIDENCE",
            "severity": "error",
            "message": "Call or put OI totals zero; PCR and directional structure are not evidential.",
        })
        validation["evidence_quality"] = "low"

    def unique_metadata(names: tuple[str, ...]) -> Any:
        column = _available_column(raw_frame, names)
        if column is None:
            return None
        values = [value for value in raw_frame[column].dropna().unique().tolist() if str(value).strip()]
        return values[0] if len(values) == 1 else values or None

    settlement_scenarios: list[dict[str, Any]] = []
    if spot_price is not None:
        for label_name, shock in (("Spot -5%", -0.05), ("Input spot", 0.0), ("Spot +5%", 0.05)):
            settlement = spot_price * (1.0 + shock)
            call_payout = ((settlement - frame.loc[frame["option_type"] == "call", "strike"]).clip(lower=0)
                           * frame.loc[frame["option_type"] == "call", "open_interest"]).sum()
            put_payout = ((frame.loc[frame["option_type"] == "put", "strike"] - settlement).clip(lower=0)
                          * frame.loc[frame["option_type"] == "put", "open_interest"]).sum()
            settlement_scenarios.append({
                "label": label_name,
                "spot_shock_pct": round(shock * 100.0, 4),
                "hypothetical_settlement": round(settlement, 4),
                "writer_payout": round(float(call_payout + put_payout), 4),
            })

    analysis_as_of = as_of or validation["latest_quote_at"]

    return {
        "call_open_interest": round(call_oi, 4),
        "put_open_interest": round(put_oi, 4),
        "put_call_ratio": round(pcr, 6),
        "call_wall": {"strike": round(float(call_wall_row["strike"]), 4), "open_interest": round(float(call_wall_row["open_interest"]), 4)},
        "put_wall": {"strike": round(float(put_wall_row["strike"]), 4), "open_interest": round(float(put_wall_row["open_interest"]), 4)},
        **max_pain,
        "structure_score": round(score, 6),
        "structure_label": label,
        "analysis_label": "Analytical option-chain structure snapshot (non-predictive)",
        "analysis_type": "analytical_option_chain_snapshot",
        "is_predictive": False,
        "target_semantics": "Hypothetical expiry-settlement OI payout and current OI structure; not a future spot or premium target.",
        "as_of": analysis_as_of,
        "as_of_semantics": "Supplied analysis timestamp, otherwise latest parseable quote timestamp; null means freshness is unknown.",
        "contract_metadata": {
            "underlying": unique_metadata(("underlying", "underlying_symbol", "symbol")),
            "expiry": unique_metadata(("expiry", "expiry_date")),
            "row_count": int(len(raw_frame)),
        },
        "validation": validation,
        "warnings": validation["warnings"],
        "settlement_scenarios": {
            "semantics": "Hypothetical one-factor settlement calculations using current OI; these are not forecasts.",
            "items": settlement_scenarios,
        },
        "historical_premium_validation": {
            "performed": False,
            "reason": "No historical premium series is present; OI and quote snapshot checks do not validate past premiums.",
        },
        "disclaimer": "The structure score summarizes snapshot OI; it is not a calibrated success probability, premium validation, prediction, or trade recommendation.",
    }


def binomial_tree(
    *,
    spot: float,
    strike: float,
    days_to_expiry: int,
    volatility: float,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    option_type: str = "call",
    american: bool = True,
    steps: int = 250,
) -> dict[str, Any]:
    """Cox-Ross-Rubinstein option pricer with optional early exercise.

    This is used for stock options where early-exercise behavior must be modeled.
    Index-option surfaces may continue to use Black-Scholes for the European case.
    """
    option_type = str(option_type).strip().lower()
    if option_type not in {"call", "put"}:
        raise ValueError("Option type must be 'call' or 'put'.")
    steps = int(steps)
    if steps < 20 or steps > 5000:
        raise ValueError("steps must be between 20 and 5000.")
    t = int(days_to_expiry) / 365.0
    spot = float(spot); strike = float(strike); volatility = float(volatility)
    risk_free_rate = float(risk_free_rate); dividend_yield = float(dividend_yield)
    _validate_option_inputs(spot, strike, t, volatility)
    if not math.isfinite(risk_free_rate) or not math.isfinite(dividend_yield):
        raise ValueError("Risk-free rate and dividend yield must be finite.")
    dt = t / steps
    u = math.exp(volatility * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp((risk_free_rate - dividend_yield) * dt)
    p = (growth - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError("Binomial risk-neutral probability is outside (0,1); check inputs/steps.")
    disc = math.exp(-risk_free_rate * dt)

    values = []
    for j in range(steps + 1):
        s = spot * (u ** j) * (d ** (steps - j))
        values.append(max(0.0, s - strike) if option_type == "call" else max(0.0, strike - s))
    for i in range(steps - 1, -1, -1):
        next_values = []
        for j in range(i + 1):
            continuation = disc * (p * values[j + 1] + (1 - p) * values[j])
            if american:
                s = spot * (u ** j) * (d ** (i - j))
                exercise = max(0.0, s - strike) if option_type == "call" else max(0.0, strike - s)
                next_values.append(max(continuation, exercise))
            else:
                next_values.append(continuation)
        values = next_values
    price = float(values[0])
    return {
        "option_type": option_type,
        "theoretical_price": round(price, 6),
        "american": bool(american),
        "steps": steps,
        "model": "Cox-Ross-Rubinstein binomial tree",
        "analysis_label": OPTION_VALUATION_LABEL,
        "is_predictive": False,
        "target_semantics": "Theoretical premium under the supplied assumptions, not a future market-premium target.",
        "as_of_semantics": "No quote timestamp is used by this model calculation.",
        "historical_premium_validation_performed": False,
    }


def binomial_greeks(**kwargs) -> dict[str, float | str | bool | int]:
    """Return binomial price and finite-difference Delta/Gamma/Theta/Vega/Rho."""
    base_kwargs = dict(kwargs)
    base = binomial_tree(**base_kwargs)
    spot = float(base_kwargs["spot"])
    vol = float(base_kwargs["volatility"])
    rate = float(base_kwargs.get("risk_free_rate", 0.065))
    ds = max(spot * 0.005, 0.05)
    dv = 0.01
    dr = 0.001

    def price(**updates):
        args = {**base_kwargs, **updates}
        return float(binomial_tree(**args)["theoretical_price"])

    up = price(spot=spot + ds)
    down = price(spot=max(0.01, spot - ds))
    delta = (up - down) / (2 * ds)
    gamma = (up - 2 * float(base["theoretical_price"]) + down) / (ds * ds)
    vega = (price(volatility=vol + dv) - price(volatility=max(0.0001, vol - dv))) / 2.0
    rho = (price(risk_free_rate=rate + dr) - price(risk_free_rate=rate - dr)) / (2 * dr) / 100.0
    days = int(base_kwargs["days_to_expiry"])
    theta = 0.0 if days <= 1 else price(days_to_expiry=days - 1) - float(base["theoretical_price"])
    return {
        **base,
        "delta": round(delta, 8),
        "gamma": round(gamma, 8),
        "theta_per_day": round(theta, 8),
        "vega_per_vol_point": round(vega, 8),
        "rho_per_rate_point": round(rho, 8),
    }
