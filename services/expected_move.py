"""ATM implied-volatility expected-move crossover (Sensibull-style).

Where a live option chain is available, the ATM (at-the-money) implied
volatility is converted into the textbook one/two-sigma expected move for the
expiry horizon and shown alongside the model's calibrated range, with a
plain-language divergence flag.

Honesty contract:

- Nothing in this module invents an implied volatility. When no live chain row
  carries a usable IV, ``available`` is ``False`` and the reason is stated.
- The expected move uses sqrt-of-time scaling from an ATM IV:
  ``EM(sigma) = spot * IV * sqrt(horizon_days / 252)``. NSE derivatives are
  annualized on 252 trading days, matching the model's session-based horizons
  rather than calendar days.
- The crossover compares the *model's calibrated range* with the options
  market's ``±1σ``/``±2σ`` implied band only when both quantities are present.
  The flags are descriptive, not recommendations.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Sequence

#: Trading days used to annualize the implied volatility (India derivatives).
TRADING_DAYS_PER_YEAR = 252.0

#: Strikes further than this fraction from spot are ignored when picking ATM.
ATM_NEAREST_STRIKE_FRACTION = 0.05

#: Model range narrower than 80% of the options market's +/-1-sigma band.
CROSSOVER_NARROW_THRESHOLD = 0.80

#: Model range wider than the options market's +/-2-sigma band.
CROSSOVER_WIDE_THRESHOLD = 1.00

#: Valid implied-volatility range in annualized decimal terms.
MIN_ATMV_IV = 0.001
MAX_ATMV_IV = 5.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp_horizon_days(days: float | None, expiry_days: float) -> int:
    if days is None or not math.isfinite(float(days)) or float(days) <= 0:
        value = expiry_days if math.isfinite(expiry_days) and expiry_days > 0 else 1.0
    else:
        value = float(days)
    return min(max(int(round(value)), 1), 365)


def expected_move_bands(
    spot_price: float,
    atm_iv: float,
    days_to_expiry: float,
    *,
    horizon_days: float | None = None,
    sigma_levels: Sequence[float] = (1.0, 2.0),
    trading_days_per_year: float = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any] | None:
    """Return one/two-sigma implied move bands, or None for invalid inputs.

    ``days_to_expiry`` is the calendar distance to the option expiry, which is
    the horizon options are pricing. ``horizon_days`` may override the scaling
    horizon (e.g. to align with the model's own session count) but never exceeds
    the expiry itself; the options market prices up to expiry, so a longer
    horizon than the expiry is not implied by this chain.
    """
    spot = _finite(spot_price)
    iv = _finite(atm_iv)
    expiry = _finite(days_to_expiry)
    if spot is None or spot <= 0 or iv is None or not (MIN_ATMV_IV <= iv <= MAX_ATMV_IV):
        return None
    expiry_days = 1.0 if expiry is None else min(max(expiry, 1.0), 365.0)
    horizon = _clamp_horizon_days(horizon_days, expiry_days)
    if horizon > expiry_days:
        horizon = int(expiry_days)
    annual = max(float(trading_days_per_year), 1.0)
    scale = math.sqrt(horizon / annual)
    levels: list[dict[str, Any]] = []
    for sigma in sigma_levels:
        sigma = abs(float(sigma))
        move_pct = sigma * iv * scale * 100.0
        move_points = spot * sigma * iv * scale
        levels.append({
            "sigma": round(sigma, 2),
            "low": round(max(0.01, spot - move_points), 2),
            "high": round(spot + move_points, 2),
            "move_pct": round(move_pct, 4),
        })
    return {
        "basis": "atm_implied_volatility",
        "atm_iv": round(iv, 6),
        "horizon_days": horizon,
        "trading_days_per_year": round(annual, 4),
        "expected_moves": levels,
    }


def atm_iv_from_chain(
    chain: dict[str, Any] | None,
    spot_price: float | None = None,
) -> dict[str, Any]:
    """Infer an ATM implied volatility from a live option-chain payload.

    ``chain`` is the payload shape produced by
    ``services.market_data.derivatives_live.LiveDerivativesService.live_chain``
    (rows with ``strike``, ``underlying_spot`` and ``ce``/``pe`` sides carrying
    an ``iv`` field). The nearest strike to spot is chosen first; its call and
    put IVs are averaged; if that strike has no usable IV, the next-nearest
    strike is tried. No row outside +/-5% of spot is considered ATM.
    """
    if not chain or not isinstance(chain, dict):
        return {"iv": None, "reason": "chain_unavailable"}
    rows = chain.get("rows") or []
    if not rows:
        return {"iv": None, "reason": "chain_empty"}

    spot = _finite(spot_price)
    for row in rows:
        inferred = _finite(row.get("underlying_spot"))
        if inferred is not None and inferred > 0:
            spot = inferred
            break
    if spot is None or spot <= 0:
        return {"iv": None, "reason": "spot_unavailable"}

    candidates: list[tuple[float, list[float], float]] = []
    for row in rows:
        strike = _finite(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        if abs(strike - spot) / spot > ATM_NEAREST_STRIKE_FRACTION:
            continue
        ivs: list[float] = []
        sides = []
        for key in ("ce", "pe"):
            side = row.get(key)
            if not isinstance(side, dict):
                continue
            value = _finite(side.get("iv"))
            if value is not None and MIN_ATMV_IV <= value <= MAX_ATMV_IV:
                ivs.append(value)
                sides.append(str(key).upper())
        if ivs:
            candidates.append((abs(strike - spot), ivs, strike))
    if not candidates:
        return {"iv": None, "spot_price": round(spot, 4), "reason": "no_atm_iv_in_chain"}

    candidates.sort(key=lambda item: (item[0], item[2]))
    _, ivs, strike = candidates[0]
    return {
        "iv": round(sum(ivs) / len(ivs), 6),
        "spot_price": round(spot, 4),
        "strike": round(float(strike), 4),
        "sides": 2 if len(ivs) == 2 else 1,
        "count": len(ivs),
        "method": "average of usable CE/PE IV at the strike nearest spot (within +/-5%)",
        "reason": None,
    }


def model_crossover(
    model_low: float,
    model_high: float,
    bands: dict[str, Any] | None,
    *,
    narrow_threshold: float = CROSSOVER_NARROW_THRESHOLD,
    wide_threshold: float = CROSSOVER_WIDE_THRESHOLD,
) -> dict[str, Any] | None:
    """Compare the model's calibrated range with the options implied band."""
    if bands is None:
        return None
    levels = {float(level.get("sigma")): level for level in bands.get("expected_moves", [])}
    one = levels.get(1.0)
    two = levels.get(2.0)
    if one is None or two is None:
        return None
    low = _finite(model_low)
    high = _finite(model_high)
    if low is None or high is None or high <= low:
        return None
    model_width = high - low
    width_one = float(one["high"]) - float(one["low"])
    width_two = float(two["high"]) - float(two["low"])
    if width_one <= 0 or width_two <= 0:
        return None
    ratio = model_width / width_one
    if model_width < narrow_threshold * width_one:
        flag = "model_tighter_than_implied"
        message = (
            "The model range is narrower than the options market's +/-1-sigma expected move, "
            "so the model expects less movement over the horizon than option prices are implying."
        )
    elif model_width > wide_threshold * width_two:
        flag = "model_wider_than_implied"
        message = (
            "The model range is wider than the options market's +/-2-sigma expected-move band, "
            "so the model prices more short-session uncertainty than the options market."
        )
    else:
        flag = "aligned"
        message = (
            "The model range sits inside the options market's +/-1-sigma to +/-2-sigma implied band; "
            "the two uncertainty views are broadly consistent."
        )
    return {
        "flag": flag,
        "message": message,
        "model_range_width": round(model_width, 4),
        "implied_1sigma_width": round(width_one, 4),
        "implied_2sigma_width": round(width_two, 4),
        "model_vs_1sigma_ratio": round(ratio, 4),
    }


def _days_to_expiry(expiry: str | None, now: datetime | None = None) -> float | None:
    if not expiry:
        return None
    try:
        parsed = date.fromisoformat(str(expiry)[:10])
    except (TypeError, ValueError):
        return None
    effective_now = (now if now is not None else datetime.now(timezone.utc)).date()
    return float((parsed - effective_now).days)


def _pick_near_expiry(expiries: Iterable[str]) -> str | None:
    listed = [str(value) for value in expiries if value]
    if not listed:
        return None
    def _key(value: str) -> tuple[int, str]:
        try:
            return (0, date.fromisoformat(value[:10]).isoformat())
        except (TypeError, ValueError):
            return (1, value)
    return min(listed, key=_key)


def expected_move_snapshot(
    underlying: str,
    *,
    expiry: str | None = None,
    spot_price: float | None = None,
    chain_provider: Callable[[str, str], dict[str, Any]] | None = None,
    expiry_provider: Callable[[str], Sequence[str]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Best-effort ATM-implied expected-move snapshot for one underlying.

    ``chain_provider(underlying, expiry)`` must return the payload shape of
    ``LiveDerivativesService.live_chain``; ``expiry_provider(underlying)``
    returns the available expiry strings. Both are injected so this module is
    testable without any broker or network. Every failure path yields an honest
    ``available=False`` block with a reason instead of a guessed number.
    """
    clean = str(underlying or "").strip().upper()
    if not clean:
        return {"underlying": clean, "available": False, "reason": "underlying_required"}

    selected_expiry = expiry
    if not selected_expiry and expiry_provider is not None:
        try:
            selected_expiry = _pick_near_expiry(expiry_provider(clean))
        except Exception:  # provider boundary; the chain path will decide availability
            selected_expiry = None

    if not selected_expiry:
        return {
            "underlying": clean,
            "expiry": None,
            "available": False,
            "reason": "no_expiry_available",
            "message": "No option expiry could be resolved for this underlying.",
        }

    chain: dict[str, Any] = {}
    if chain_provider is not None:
        try:
            chain = chain_provider(clean, selected_expiry) or {}
        except Exception as error:
            chain = {"is_live": False, "is_stale": True, "message": str(error)[:200]}

    if not chain.get("is_live") or not chain.get("rows"):
        return {
            "underlying": clean,
            "expiry": selected_expiry,
            "available": False,
            "is_live": False,
            "source": chain.get("source") or "unavailable",
            "reason": "chain_unavailable",
            "message": chain.get("message") or "Live option-chain data is unavailable.",
        }

    atm = atm_iv_from_chain(chain, spot_price)
    if atm.get("iv") is None:
        return {
            "underlying": clean,
            "expiry": selected_expiry,
            "available": False,
            "is_live": True,
            "source": chain.get("source"),
            "reason": atm.get("reason") or "atm_iv_unavailable",
            "message": "The option chain carries no usable ATM implied volatility.",
        }

    days = _days_to_expiry(selected_expiry, now=now)
    bands = expected_move_bands(
        float(atm["spot_price"]),
        float(atm["iv"]),
        days if days is not None and days > 0 else 1.0,
        horizon_days=days if days is not None and days > 0 else None,
    )
    payload: dict[str, Any] = {
        "underlying": clean,
        "expiry": selected_expiry,
        "days_to_expiry": None if days is None else round(days, 1),
        "available": True,
        "is_live": True,
        "source": chain.get("source"),
        "fetched_at": chain.get("fetched_at"),
        "atm_iv": atm.get("iv"),
        "spot_price": atm.get("spot_price"),
        "atm_strike": atm.get("strike"),
        "expected_moves": (bands or {}).get("expected_moves", []),
        "basis": "atm_implied_volatility",
        "trading_days_per_year": TRADING_DAYS_PER_YEAR,
        "disclosure": (
            "Options-implied expected move from ATM implied volatility. "
            "It reflects option-market pricing, not a StockPilot model forecast."
        ),
    }
    return payload


def crossover_vs_forecast(snapshot: dict[str, Any] | None, model_low: float | None, model_high: float | None) -> dict[str, Any] | None:
    """Attach the divergence flag when both the snapshot and a model range exist."""
    if not snapshot or not snapshot.get("available"):
        return None
    bands = {
        "basis": snapshot.get("basis"),
        "atm_iv": snapshot.get("atm_iv"),
        "horizon_days": snapshot.get("days_to_expiry"),
        "expected_moves": snapshot.get("expected_moves", []),
    }
    low = _finite(model_low)
    high = _finite(model_high)
    if low is None or high is None:
        return None
    return model_crossover(low, high, bands)


__all__: Sequence[str] = (
    "ATM_NEAREST_STRIKE_FRACTION",
    "CROSSOVER_NARROW_THRESHOLD",
    "CROSSOVER_WIDE_THRESHOLD",
    "TRADING_DAYS_PER_YEAR",
    "atm_iv_from_chain",
    "crossover_vs_forecast",
    "expected_move_bands",
    "expected_move_snapshot",
    "model_crossover",
)
