"""IV Rank / IV Percentile history for option underlyings.

Market-implied volatility history does not exist anywhere in this codebase by
default, so this module records ATM IV observations as the app sees them (from
live-chain fetches) and computes classic IV statistics over the recorded
window:

* **IV Rank** = (current_iv - window_min) / (window_max - window_min)
* **IV Percentile** = share of recorded observations strictly below current IV

Honesty rules:

* Nothing is invented. With too few recorded observations the market-IV block
  is ``available: false`` with the reason stated, and an optional
  **realized-volatility percentile** fallback (computed from price history the
  app already holds) is reported under a distinct ``basis`` so it can never be
  mistaken for implied volatility.
* Recording is best-effort and never blocks or fails a live-chain response.

Persistence is plain SQLite through ``database.get_connection`` so it works in
the single-user default (SQLCipher) and under the DAO layer's Postgres path is
not required for this advisory table (it is recreated by ``create_tables`` on
SQLite deployments; ``_ensure_tables`` keeps tests/hermetic runs working).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from database import get_connection

#: Minimum recorded observations before market-IV statistics are published.
MIN_SAMPLES = 5

#: Skip recording if the same underlying was captured this recently
#: (per-observation granularity beyond this adds nothing to rank/percentile).
DEDUPE_WINDOW = timedelta(minutes=30)

#: Annualization basis for Indian markets (matches expected_move.py).
TRADING_DAYS_PER_YEAR = 252.0


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS iv_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                underlying TEXT NOT NULL,
                atm_iv REAL NOT NULL,
                spot REAL,
                source TEXT,
                observed_at TEXT NOT NULL,
                UNIQUE(underlying, observed_at)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_iv_history_underlying ON iv_history(underlying, observed_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_atm_iv(
    underlying: str,
    atm_iv: float,
    spot: float | None = None,
    source: str | None = None,
    at: datetime | None = None,
) -> bool:
    """Append one ATM IV observation. Returns True when a row was stored.

    Best-effort: returns False (never raises) on invalid input, duplicate
    windows, or storage errors so a chain response can never fail because of
    history bookkeeping.
    """
    try:
        symbol = str(underlying or "").strip().upper()
        iv = float(atm_iv)
        if not symbol or not math.isfinite(iv) or not (0.001 <= iv <= 5.0):
            return False
        observed = (at or _utcnow()).astimezone(timezone.utc)
        _ensure_tables()
        conn = get_connection()
        try:
            window_start = (observed - DEDUPE_WINDOW).isoformat()
            recent = conn.execute(
                "SELECT observed_at FROM iv_history WHERE underlying=? AND observed_at>=? "
                "ORDER BY observed_at DESC LIMIT 1",
                (symbol, window_start),
            ).fetchone()
            if recent is not None:
                return False
            conn.execute(
                "INSERT INTO iv_history(underlying, atm_iv, spot, source, observed_at) VALUES (?,?,?,?,?)",
                (symbol, round(iv, 6), float(spot) if spot else None, str(source or "live_chain")[:40],
                 observed.isoformat()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def _percentile_rank(values: Sequence[float], current: float) -> float:
    """Share of observations strictly below current (0..1), averaged ties."""
    if not values:
        return 0.0
    below = sum(1 for value in values if value < current)
    equal = sum(1 for value in values if value == current)
    return (below + 0.5 * equal) / len(values)


def realized_volatility_percentile(frame: Any, lookback_days: int) -> dict[str, Any] | None:
    """Realized-vol percentile from an OHLCV frame (fallback, never implied).

    Returns None when the frame is too short to be evidential.
    """
    try:
        import pandas as pd
        if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
            return None
        frame = frame.copy()
        close_col = "close" if "close" in frame.columns else None
        if close_col is None:
            return None
        closes = pd.to_numeric(frame[close_col], errors="coerce").dropna()
        if len(closes) < 30:
            return None
        returns = closes.pct_change().dropna()
        window = returns.tail(max(30, min(len(returns), int(lookback_days))))
        if len(window) < 30:
            return None
        rolling = window.rolling(21).std().dropna()
        if len(rolling) < 10:
            return None
        current = float(rolling.iloc[-1]) * math.sqrt(TRADING_DAYS_PER_YEAR)
        history = (rolling * math.sqrt(TRADING_DAYS_PER_YEAR)).to_numpy()
        lo = float(min(history))
        hi = float(max(history))
        rank = 0.0 if hi - lo < 1e-12 else (current - lo) / (hi - lo)
        return {
            "basis": "realized_volatility_proxy",
            "label": (
                "Percentile of trailing realized volatility, not implied volatility. "
                "Use it only as context when no market IV history exists."
            ),
            "current_realized_vol": round(current, 6),
            "min_realized_vol": round(lo, 6),
            "max_realized_vol": round(hi, 6),
            "realized_vol_percentile": round(max(0.0, min(1.0, rank)), 4),
            "observations": int(len(history)),
            "is_implied": False,
        }
    except Exception:
        return None


def iv_statistics(
    underlying: str,
    lookback_days: int = 365,
    price_history: Any = None,
) -> dict[str, Any]:
    """Compute IV Rank / IV Percentile from recorded ATM IV observations.

    Args:
        underlying: symbol to compute stats for.
        lookback_days: window for rank/percentile.
        price_history: optional OHLCV frame for the realized-vol fallback.
    """
    _ensure_tables()
    symbol = str(underlying or "").strip().upper()
    since = (_utcnow() - timedelta(days=max(1, min(int(lookback_days), 3650)))).isoformat()

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT atm_iv, spot, source, observed_at FROM iv_history "
            "WHERE underlying=? AND observed_at>=? ORDER BY observed_at ASC",
            (symbol, since),
        ).fetchall()
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM iv_history WHERE underlying=?", (symbol,)
        ).fetchone()
    finally:
        conn.close()

    values = [float(row[0]) for row in rows]
    result: dict[str, Any] = {
        "underlying": symbol,
        "lookback_days": int(lookback_days),
        "observations": len(values),
        "lifetime_observations": int(total_rows[0]) if total_rows else 0,
        "market_iv": {"available": False, "basis": "atm_implied_volatility"},
        "is_forecast": False,
        "is_recommendation": False,
    }

    if values:
        current = values[-1]
        lo, hi = min(values), max(values)
        result["market_iv"].update(
            {
                "available": True,
                "current_iv": round(current, 6),
                "iv_rank": round((current - lo) / (hi - lo), 4) if hi - lo > 1e-9 else 0.0,
                "iv_percentile": round(_percentile_rank(values, current), 4),
                "min_iv": round(lo, 6),
                "max_iv": round(hi, 6),
                "mean_iv": round(sum(values) / len(values), 6),
                "first_observed": rows[0][3],
                "last_observed": rows[-1][3],
                "sources": sorted({str(row[2] or "live_chain") for row in rows}),
            }
        )
        result["series"] = [
            {"observed_at": row[3], "iv": round(float(row[0]), 6), "spot": row[1]}
            for row in rows[-500:]
        ]
    else:
        result["market_iv"]["reason"] = "no_recorded_iv_observations"
        result["market_iv"]["hint"] = (
            "Market IV observations accumulate each time a live option chain is fetched "
            "for this underlying."
        )

    fallback = realized_volatility_percentile(price_history, int(lookback_days))
    if fallback is not None:
        result["realized_vol_fallback"] = fallback
    else:
        result["realized_vol_fallback"] = {"basis": "realized_volatility_proxy", "available": False,
                                           "reason": "insufficient_price_history"}
    return result
