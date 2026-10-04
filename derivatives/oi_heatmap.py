"""Open-interest heatmap and expiry pin-risk analysis (descriptive arithmetic).

Turns an option chain's open-interest rows into:

* a per-strike heatmap (call/put OI, absolute and period-over-period change)
  optionally grouped across expiries;
* classic OI landmarks: **call wall** (highest call OI = resistance cluster),
  **put wall** (highest put OI = support cluster), biggest build-ups and
  unwinds;
* **pin risk**: how clustered open interest sits around the current spot near
  expiry, scored descriptively (high/moderate/low) from distance to the
  max-pain and max-OI strikes versus the underlying's typical move.

Everything here is arithmetic over the OI the caller supplies. It does not
forecast direction, does not assign a probability that the underlying pins,
and says so in its disclosures. Previous-period OI (for change columns) is
optional; when absent, changes are reported as null rather than guessed.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

__all__ = [
    "OI_HEATMAP_DISCLOSURES",
    "build_oi_heatmap",
]

OI_HEATMAP_DISCLOSURES = (
    "Open interest is a positioning snapshot, not a forecast or a probability of direction.",
    "Change columns appear only when previous-period open interest was supplied; otherwise they stay null.",
    "Pin risk is a descriptive clustering score from the data you supplied, not a probability that expiry pins.",
    "Call/put walls are the largest OI strikes, which act as reference support/resistance only if the data is current.",
)

OPTION_TYPES = {"call", "put", "ce", "pe", "c", "p"}
#: Pin distance (as % of spot) treated as "tight cluster" for a high score.
TIGHT_PIN_PCT = 1.0
#: Pin distance beyond this is considered loose (low score).
LOOSE_PIN_PCT = 3.0
#: Max pain / OI distance search horizon as % of spot around spot.
SEARCH_BAND_PCT = 30.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _norm_type(value: Any) -> str | None:
    kind = str(value or "").strip().lower()
    if kind in {"ce", "c"}:
        return "call"
    if kind in {"pe", "p"}:
        return "put"
    return kind if kind in {"call", "put"} else None


def _max_pain(strikes: dict[float, dict[str, float]]) -> float | None:
    """Strike where total option-writer payout at expiry is minimized."""
    if not strikes:
        return None
    best_strike, best_cost = None, None
    for settle in strikes:
        cost = 0.0
        for strike, side in strikes.items():
            if strike == settle:
                continue
            call_oi = side.get("call", 0.0)
            put_oi = side.get("put", 0.0)
            if strike < settle:
                cost += call_oi * (settle - strike)
            else:
                cost += put_oi * (strike - settle)
        if best_cost is None or cost < best_cost:
            best_strike, best_cost = settle, cost
    return best_strike


def _pin_score(distance_pct: float) -> str:
    if distance_pct <= TIGHT_PIN_PCT:
        return "high"
    if distance_pct <= LOOSE_PIN_PCT:
        return "moderate"
    return "low"


def build_oi_heatmap(
    rows: Iterable[Any],
    *,
    spot_price: float | None = None,
    expiry: str | None = None,
) -> dict[str, Any]:
    """Aggregate chain rows into a heatmap plus pin-risk block.

    Each row: ``{strike, option_type, open_interest, previous_open_interest?}``
    with optional ``expiry`` (enables multi-expiry columns).
    """
    items = list(rows or [])
    if not items:
        raise ValueError("Supply at least one open-interest row.")

    # strike -> {call/put -> [oi, prev_oi]}, plus per-expiry view
    grid: dict[float, dict[str, dict[str, float | None]]] = {}
    expiries: set[str] = set()
    usable = 0
    for row in items:
        if not isinstance(row, dict):
            continue
        strike = _finite(row.get("strike"))
        kind = _norm_type(row.get("option_type"))
        oi = _finite(row.get("open_interest"))
        if strike is None or strike <= 0 or kind is None or oi is None or oi < 0:
            continue
        prev = _finite(row.get("previous_open_interest"))
        cell = grid.setdefault(strike, {"call": {"oi": 0.0, "prev": None},
                                        "put": {"oi": 0.0, "prev": None}})
        side = cell[kind]
        side["oi"] = float(side["oi"] or 0.0) + oi
        if prev is not None:
            side["prev"] = float(side["prev"] or 0.0) + prev
        expiry_value = str(row.get("expiry") or "").strip()
        if expiry_value:
            expiries.add(expiry_value)
        usable += 1
    if not grid:
        raise ValueError("No usable rows: each row needs strike, option_type, and open_interest.")

    spot = _finite(spot_price)
    strikes = sorted(grid)

    # Per-strike heatmap entries.
    heat_rows: list[dict[str, Any]] = []
    call_wall = put_wall = None
    max_call_oi = max_put_oi = -1.0
    buildup: list[dict[str, Any]] = []
    unwind: list[dict[str, Any]] = []
    total_call = total_put = 0.0
    total_call_prev = total_put_prev = 0.0
    changes_available = False

    for strike in strikes:
        cell = grid[strike]
        call_oi = float(cell["call"]["oi"] or 0.0)
        put_oi = float(cell["put"]["oi"] or 0.0)
        call_prev, put_prev = cell["call"]["prev"], cell["put"]["prev"]
        call_change = None if call_prev is None else call_oi - float(call_prev)
        put_change = None if put_prev is None else put_oi - float(put_prev)
        if call_change is not None or put_change is not None:
            changes_available = True
        total_call += call_oi
        total_put += put_oi
        total_call_prev += float(call_prev or 0.0)
        total_put_prev += float(put_prev or 0.0)
        if call_oi > max_call_oi:
            max_call_oi, call_wall = call_oi, strike
        if put_oi > max_put_oi:
            max_put_oi, put_wall = put_oi, strike
        for kind, change, oi_now in (("call", call_change, call_oi), ("put", put_change, put_oi)):
            if change is None:
                continue
            entry = {"strike": round(strike, 2), "option_type": kind,
                     "open_interest": round(oi_now, 2), "change": round(change, 2)}
            if change > 0:
                buildup.append(entry)
            elif change < 0:
                unwind.append(entry)
        heat_rows.append({
            "strike": round(strike, 2),
            "call_oi": round(call_oi, 2),
            "put_oi": round(put_oi, 2),
            "call_oi_change": None if call_change is None else round(call_change, 2),
            "put_oi_change": None if put_change is None else round(put_change, 2),
            "net_oi": round(call_oi - put_oi, 2),
            "distance_from_spot_pct": None if spot is None or spot <= 0
            else round((strike - spot) / spot * 100.0, 4),
        })

    pcr = None if total_call <= 0 else round(total_put / total_call, 4)
    pcr_change = None
    if changes_available and total_call_prev > 0 and total_put_prev > 0:
        pcr_change = round((total_put / total_call) - (total_put_prev / total_call_prev), 4) \
            if total_call > 0 else None

    # Multi-expiry view (rows keyed by strike, one column per expiry).
    matrix: list[dict[str, Any]] = []
    if len(expiries) > 1:
        by_expiry: dict[tuple[float, str, str], dict[str, float | None]] = {}
        for row in items:
            if not isinstance(row, dict):
                continue
            strike = _finite(row.get("strike"))
            kind = _norm_type(row.get("option_type"))
            oi = _finite(row.get("open_interest"))
            exp = str(row.get("expiry") or "").strip()
            if strike is None or kind is None or oi is None or not exp:
                continue
            prev = _finite(row.get("previous_open_interest"))
            key = (strike, exp, kind)
            slot = by_expiry.setdefault(key, {"oi": 0.0, "prev": None})
            slot["oi"] = float(slot["oi"] or 0.0) + oi
            if prev is not None:
                slot["prev"] = float(slot["prev"] or 0.0) + prev
        for strike in sorted({key[0] for key in by_expiry}):
            expiry_entry: dict[str, Any] = {"strike": round(strike, 2)}
            for exp in sorted(expiries):
                column: dict[str, Any] = {}
                for kind in ("call", "put"):
                    expiry_slot = by_expiry.get((strike, exp, kind))
                    if expiry_slot is None:
                        continue
                    expiry_oi = float(expiry_slot["oi"] or 0.0)
                    change = None if expiry_slot["prev"] is None else expiry_oi - float(expiry_slot["prev"])
                    column[kind] = {"oi": round(expiry_oi, 2),
                                    "change": None if change is None else round(change, 2)}
                if column:
                    expiry_entry[exp] = column
            matrix.append(expiry_entry)

    buildup.sort(key=lambda item: item["change"], reverse=True)
    unwind.sort(key=lambda item: item["change"])
    support = resistance = None
    if spot is not None:
        below = [strike for strike in strikes if strike < spot]
        above = [strike for strike in strikes if strike > spot]
        support = max(below, key=lambda s: float(grid[s]["put"]["oi"] or 0.0)) if below else None
        resistance = min(above, key=lambda s: float(grid[s]["call"]["oi"] or 0.0)) if above else None

    pin: dict[str, Any] = {
        "level": None,
        "basis": "oi_clustering_descriptive",
        "spot": spot,
        "expiry": expiry,
    }
    if spot is not None and spot > 0:
        band = spot * SEARCH_BAND_PCT / 100.0
        candidates = [strike for strike in strikes if abs(strike - spot) <= band]
        if candidates:
            pain = _max_pain({strike: {"call": float(grid[strike]["call"]["oi"] or 0.0),
                                       "put": float(grid[strike]["put"]["oi"] or 0.0)}
                              for strike in candidates})
            max_oi_strike = max(candidates, key=lambda s: (grid[s]["call"]["oi"] or 0.0)
                                + (grid[s]["put"]["oi"] or 0.0))
            dists = [abs(pain - spot) / spot * 100.0 for pain in (pain,) if pain is not None]
            dists.append(abs(max_oi_strike - spot) / spot * 100.0)
            # The nearest cluster that matters for a pin is the tighter of
            # max-pain and max-OI distance; score the better one.
            distance_pct = min(dists)
            pin.update({
                "level": _pin_score(distance_pct),
                "max_pain": None if pain is None else round(pain, 2),
                "max_oi_strike": round(max_oi_strike, 2),
                "distance_pct": round(distance_pct, 4),
                "max_pain_distance_pct": None if pain is None
                else round(abs(pain - spot) / spot * 100.0, 4),
            })
            # Absolute strikes absent from the chain cannot host a pin.
            nearest_strike: float = min(candidates, key=lambda s: abs(s - float(spot)))
            pin["nearest_strike"] = round(nearest_strike, 2)
        else:
            pin["reason"] = "no_strikes_within_search_band"
    else:
        pin["reason"] = "spot_unavailable"

    return {
        "expiry": expiry,
        "spot_price": spot,
        "rows": heat_rows,
        "matrix": matrix if matrix else None,
        "expiries": sorted(expiries),
        "totals": {
            "call_oi": round(total_call, 2),
            "put_oi": round(total_put, 2),
            "put_call_ratio": pcr,
            "put_call_ratio_change": pcr_change,
            "change_basis": "previous_open_interest_supplied" if changes_available else None,
        },
        "landmarks": {
            "call_wall": None if call_wall is None else round(call_wall, 2),
            "put_wall": None if put_wall is None else round(put_wall, 2),
            "nearest_resistance": None if resistance is None else round(resistance, 2),
            "nearest_support": None if support is None else round(support, 2),
            "top_buildups": buildup[:5],
            "top_unwinds": unwind[:5],
        },
        "pin_risk": pin,
        "rows_count": usable,
        "is_forecast": False,
        "is_recommendation": False,
        "disclosures": list(OI_HEATMAP_DISCLOSURES),
    }
