"""Pre-open morning brief assembled only from evidence the product already has.

The brief answers one question honestly: *what actually happened, and how unusual
was it?* It therefore contains realized statistics (returns, volume against its own
baseline, realized range, distance from recent extremes), the session state from the
market calendar, and optionally attributed headlines with lexicon sentiment.

Deliberate omissions, so that this module cannot become a recommendation engine:

* no forecast, target price, or expected return is produced here;
* nothing is ranked as a buy, sell, or "top pick" -- movers are sorted by realized
  move only, and that is stated in the payload;
* a symbol with insufficient or stale history is excluded **with a reason** rather
  than silently dropped or padded with defaults.

All inputs are injected (`history_loader`, `news_loader`, `market_status_loader`),
which keeps the module unit-testable without network access, providers, or
credentials.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence, cast

import numpy as np
import pandas as pd

__all__ = [
    "BRIEF_BASIS",
    "BRIEF_DISCLOSURES",
    "MAX_SYMBOLS",
    "MIN_SESSIONS",
    "STALE_AFTER_SESSIONS",
    "BriefUnavailable",
    "build_morning_brief",
    "symbol_snapshot",
]

HistoryLoader = Callable[[str], pd.DataFrame]
NewsLoader = Callable[[str], dict[str, Any]]

#: Upper bound on symbols per brief. Keeps one brief bounded in provider calls.
MAX_SYMBOLS = 12

#: A 20-session baseline is the shortest window that makes "unusual volume" or
#: "distance from recent high" mean anything. Below this the symbol is excluded.
MIN_SESSIONS = 25

#: Calendar sessions after which the newest candle is reported as stale.
STALE_AFTER_SESSIONS = 5

BRIEF_BASIS = (
    "Realized daily open/high/low/close/volume history only. No model output, "
    "forecast, target price, or recommendation is included."
)

BRIEF_DISCLOSURES = (
    "Descriptive statistics on past sessions. Past behaviour does not establish future behaviour.",
    "Movers are ordered by realized percentage change, which is not a ranking of quality or suitability.",
    "Headline sentiment is a transparent lexicon score over headline text only, not an assessment of the company.",
    "Excluded symbols are listed with a reason instead of being filled with defaults.",
)


class BriefUnavailable(RuntimeError):
    """Raised when no symbol in the request could be described at all."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize_history(frame: Any) -> pd.DataFrame | None:
    """Return a date-sorted OHLCV frame with lowercase columns, or None."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    data = frame.copy()
    data.columns = [str(column).strip().lower() for column in data.columns]
    if "close" not in data.columns:
        return None
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce", utc=False)
        data = data.dropna(subset=["date"]).sort_values("date")
    elif isinstance(data.index, pd.DatetimeIndex):
        data = data.sort_index()
        data["date"] = pd.to_datetime(data.index, errors="coerce")
    else:
        data["date"] = pd.NaT
    for column in ("open", "high", "low", "close", "volume"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["close"])
    if data.empty:
        return None
    return cast(pd.DataFrame, data.reset_index(drop=True))


def _pct_change(current: float, previous: float) -> float | None:
    if previous is None or not math.isfinite(previous) or previous == 0:
        return None
    if current is None or not math.isfinite(current):
        return None
    return round((current - previous) / abs(previous) * 100.0, 2)


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _realized_range_pct(data: pd.DataFrame, window: int = 14) -> float | None:
    """Average high-low range over the last `window` sessions, in percent of close.

    This is a realized-volatility descriptor, not an ATR forecast: no smoothing
    model is fitted and no future value is implied.
    """
    if "high" not in data.columns or "low" not in data.columns:
        return None
    tail = data.tail(window)
    spans = (tail["high"] - tail["low"]).dropna()
    closes = tail["close"].dropna()
    if spans.empty or closes.empty:
        return None
    reference = float(closes.mean())
    if reference == 0:
        return None
    return _round(float(spans.mean()) / reference * 100.0)


def symbol_snapshot(symbol: str, frame: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Describe one symbol, or explain why it cannot be described.

    Returns either `{"symbol": ..., "included": True, ...}` or
    `{"symbol": ..., "included": False, "reason": <code>}`.
    """
    label = str(symbol or "").strip().upper()
    data = _normalize_history(frame)
    if data is None:
        return {"symbol": label, "included": False, "reason": "history_unavailable"}
    sessions = int(len(data))
    if sessions < MIN_SESSIONS:
        return {
            "symbol": label,
            "included": False,
            "reason": "insufficient_history",
            "sessions_available": sessions,
            "sessions_required": MIN_SESSIONS,
        }

    closes = data["close"].astype(float)
    last_close = float(closes.iloc[-1])
    as_of_value = data["date"].iloc[-1]
    as_of = None
    stale_sessions = None
    if pd.notna(as_of_value):
        as_of_ts = pd.Timestamp(as_of_value)
        as_of = as_of_ts.isoformat()
        reference_now = now or datetime.now(timezone.utc)
        try:
            naive_now = reference_now.replace(tzinfo=None)
            delta_days = (naive_now - as_of_ts.tz_localize(None).to_pydatetime()).days
            # Approximate calendar sessions as 5 per 7 days; used only for a flag.
            stale_sessions = max(0, int(delta_days * 5 / 7))
        except Exception:
            stale_sessions = None

    window20 = closes.tail(20)
    volume_series = data["volume"].astype(float) if "volume" in data.columns else None
    volume_ratio = None
    last_volume = None
    if volume_series is not None and volume_series.notna().sum() >= 20:
        last_volume = _round(volume_series.iloc[-1], 0)
        baseline = float(volume_series.tail(20).mean())
        if baseline > 0:
            volume_ratio = _round(float(volume_series.iloc[-1]) / baseline)

    high20 = float(window20.max())
    low20 = float(window20.min())

    snapshot: dict[str, Any] = {
        "symbol": label,
        "included": True,
        "as_of": as_of,
        "sessions_available": sessions,
        "last_close": _round(last_close),
        "change_pct_1d": _pct_change(last_close, float(closes.iloc[-2])) if sessions >= 2 else None,
        "change_pct_5d": _pct_change(last_close, float(closes.iloc[-6])) if sessions >= 6 else None,
        "change_pct_20d": _pct_change(last_close, float(closes.iloc[-21])) if sessions >= 21 else None,
        "volume": last_volume,
        "volume_vs_20d_average": volume_ratio,
        "realized_range_pct_14d": _realized_range_pct(data),
        "distance_from_20d_high_pct": _pct_change(last_close, high20),
        "distance_from_20d_low_pct": _pct_change(last_close, low20),
        "freshness": {
            "as_of": as_of,
            "approx_sessions_behind": stale_sessions,
            "stale": bool(stale_sessions is not None and stale_sessions > STALE_AFTER_SESSIONS),
        },
    }
    return snapshot


def _headline_block(
    symbols: Sequence[str],
    news_loader: NewsLoader | None,
    *,
    per_symbol_limit: int,
) -> dict[str, Any]:
    if news_loader is None:
        return {"state": "not_requested", "items": []}
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for symbol in symbols:
        try:
            payload = news_loader(symbol) or {}
        except Exception as exc:  # provider/network failures must not break the brief
            failures.append({"symbol": symbol, "reason": type(exc).__name__})
            continue
        status = str(payload.get("status") or "unavailable")
        if status not in {"available", "stale"}:
            failures.append({"symbol": symbol, "reason": str(payload.get("reason") or status)})
            continue
        for row in list(payload.get("items") or [])[:per_symbol_limit]:
            items.append(
                {
                    "symbol": symbol,
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "publisher": row.get("publisher"),
                    "published_at": row.get("published_at"),
                    "sentiment": row.get("sentiment"),
                    "is_stale": status == "stale",
                }
            )
    if not items:
        return {
            "state": "unavailable",
            "items": [],
            "failures": failures,
            "note": "No attributed headline source returned usable items. Nothing is inferred from the absence of news.",
        }
    scores = [
        float(row["sentiment"]["score"])
        for row in items
        if isinstance(row.get("sentiment"), dict) and isinstance(row["sentiment"].get("score"), (int, float))
    ]
    return {
        "state": "available",
        "items": items,
        "failures": failures,
        "headline_count": len(items),
        "mean_sentiment": _round(float(np.mean(scores)), 3) if scores else None,
        "sentiment_basis": "Headline text only, transparent lexicon scoring. Not a company assessment.",
    }


def build_morning_brief(
    *,
    symbols: Iterable[str],
    history_loader: HistoryLoader,
    market_status_loader: Callable[[], dict[str, Any]] | None = None,
    news_loader: NewsLoader | None = None,
    now: datetime | None = None,
    max_symbols: int = MAX_SYMBOLS,
    movers_limit: int = 5,
    headlines_per_symbol: int = 3,
) -> dict[str, Any]:
    """Assemble the morning brief payload.

    Raises:
        BriefUnavailable: when no requested symbol had describable history, so the
            caller can return an explicit error instead of an empty brief that
            looks like "nothing happened".
    """
    requested = [str(item).strip().upper() for item in symbols if str(item or "").strip()]
    deduped: list[str] = []
    for symbol in requested:
        if symbol not in deduped:
            deduped.append(symbol)
    if not deduped:
        raise BriefUnavailable("brief_symbols_required", "Provide at least one symbol for the brief.")
    truncated = deduped[: max(1, int(max_symbols))]

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for symbol in truncated:
        try:
            frame = history_loader(symbol)
        except Exception as exc:
            excluded.append({"symbol": symbol, "reason": "history_unavailable", "detail": type(exc).__name__})
            continue
        snapshot = symbol_snapshot(symbol, frame, now=now)
        if snapshot.get("included"):
            included.append(snapshot)
        else:
            excluded.append(snapshot)

    if not included:
        raise BriefUnavailable(
            "brief_history_unavailable",
            "No requested symbol had enough recent history to describe. "
            "The brief is withheld rather than published from partial data.",
        )

    with_move = [row for row in included if row.get("change_pct_1d") is not None]
    gainers = sorted(with_move, key=lambda row: row["change_pct_1d"], reverse=True)[:movers_limit]
    losers = sorted(with_move, key=lambda row: row["change_pct_1d"])[:movers_limit]
    with_volume = [row for row in included if row.get("volume_vs_20d_average") is not None]
    volume_leaders = sorted(with_volume, key=lambda row: row["volume_vs_20d_average"], reverse=True)[:movers_limit]
    widest_range = [row for row in included if row.get("realized_range_pct_14d") is not None]
    widest_range = sorted(widest_range, key=lambda row: row["realized_range_pct_14d"], reverse=True)[:movers_limit]

    session: dict[str, Any]
    if market_status_loader is None:
        session = {"state": "not_requested"}
    else:
        try:
            session = dict(market_status_loader() or {})
        except Exception as exc:
            session = {"state": "unavailable", "reason": type(exc).__name__}

    stale_symbols = [row["symbol"] for row in included if row.get("freshness", {}).get("stale")]

    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "session": session,
        "coverage": {
            "requested": len(deduped),
            "considered": len(truncated),
            "included": len(included),
            "excluded": excluded,
            "max_symbols": int(max_symbols),
            "truncated": len(deduped) > len(truncated),
        },
        "movers": {
            "basis": "Sorted by realized 1-session percentage change. Not a ranking of quality or suitability.",
            "gainers": gainers,
            "losers": losers,
            "volume_leaders": volume_leaders,
            "widest_realized_range": widest_range,
        },
        "symbols": included,
        "headlines": _headline_block(
            [row["symbol"] for row in included],
            news_loader,
            per_symbol_limit=max(1, int(headlines_per_symbol)),
        ),
        "evidence": {
            "basis": BRIEF_BASIS,
            "min_sessions_required": MIN_SESSIONS,
            "stale_symbols": stale_symbols,
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(BRIEF_DISCLOSURES),
    }
