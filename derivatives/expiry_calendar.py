"""Resolve NSE/BSE derivative expiries from the live/bundled instrument master.

Expiry weekdays are intentionally not hardcoded because exchange contract rules
change.  The Upstox BOD master is the source of truth at runtime.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.market_data.instruments import CATALOGUE


def get_expiries(underlying_symbol: str, *, limit: int = 16) -> dict[str, Any]:
    symbol = str(underlying_symbol).strip().upper().replace(".NS", "").replace(".BO", "")
    rows = []
    for item in CATALOGUE.load():
        if item.segment not in {"NSE_FO", "BSE_FO"}:
            continue
        if (item.underlying_symbol or "").upper() != symbol:
            continue
        if not item.expiry:
            continue
        rows.append(item)
    expiries = sorted({str(item.expiry) for item in rows})[: max(1, min(limit, 50))]
    return {
        "underlying_symbol": symbol,
        "expiries": expiries,
        "source": "daily instrument master",
        "available": bool(expiries),
        "note": None if expiries else "Refresh the Upstox NSE/BSE instrument master to load live F&O expiries.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
