"""Technical screener over indicators the product already computes.

The screener is intentionally *user-defined*: it evaluates the filters the caller
supplies against realized price/volume statistics and returns the symbols that
match, with the computed metric values attached so a match can be checked by hand.

Design constraints that keep this out of recommendation territory:

* no filter is preselected, weighted, or scored for the user, and results carry no
  ranking of quality -- ordering is by an explicitly named metric;
* every metric is a realized statistic (returns, moving-average distance, RSI,
  volume ratio, realized range, distance from 52-week extremes); none is a
  forecast and none is combined into a composite "signal strength";
* symbols without enough history are returned in `excluded` with a reason.

Saved screens are stored per user in SQLite using the same connection factory
pattern as `services/forecast_guardrails.py`, so they inherit the existing
database location, WAL settings, and isolation in tests.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence, cast

import numpy as np
import pandas as pd

try:  # package layout
    from database import get_connection as _default_get_connection
except Exception:  # pragma: no cover - standalone/flat use
    _default_get_connection = None  # type: ignore[assignment]

__all__ = [
    "MAX_FILTERS",
    "MAX_SAVED_SCREENS",
    "MAX_SYMBOLS",
    "MIN_SESSIONS",
    "OPERATORS",
    "SCREENER_FIELDS",
    "ScreenerError",
    "ScreenerStore",
    "SCREENS",
    "compute_metrics",
    "describe_fields",
    "run_screen",
    "validate_filters",
]

ConnectionFactory = Callable[[], sqlite3.Connection]
HistoryLoader = Callable[[str], pd.DataFrame]

#: Bounds. A screener request must stay cheap enough to serve synchronously.
MAX_SYMBOLS = 250
MAX_FILTERS = 12
MAX_SAVED_SCREENS = 50

#: 200-session history is required for the slowest supported moving average.
MIN_SESSIONS = 60
SMA200_SESSIONS = 200

OPERATORS = ("gt", "gte", "lt", "lte", "eq", "between")

#: Every screenable field, with the evidence it is computed from. The `requires`
#: value is the number of sessions the metric needs before it is reported.
SCREENER_FIELDS: dict[str, dict[str, Any]] = {
    "last_close": {"label": "Last close", "unit": "INR", "requires": 1, "basis": "Most recent closing price."},
    "change_pct_1d": {"label": "1-session change", "unit": "%", "requires": 2, "basis": "Close over previous close."},
    "change_pct_5d": {"label": "5-session change", "unit": "%", "requires": 6, "basis": "Close over close 5 sessions earlier."},
    "change_pct_20d": {"label": "20-session change", "unit": "%", "requires": 21, "basis": "Close over close 20 sessions earlier."},
    "rsi_14": {"label": "RSI (14)", "unit": "index", "requires": 15, "basis": "Wilder RSI on closes."},
    "sma_20": {"label": "SMA 20", "unit": "INR", "requires": 20, "basis": "Simple mean of last 20 closes."},
    "sma_50": {"label": "SMA 50", "unit": "INR", "requires": 50, "basis": "Simple mean of last 50 closes."},
    "sma_200": {"label": "SMA 200", "unit": "INR", "requires": SMA200_SESSIONS, "basis": "Simple mean of last 200 closes."},
    "close_vs_sma20_pct": {"label": "Close vs SMA 20", "unit": "%", "requires": 20, "basis": "Distance of close from SMA 20."},
    "close_vs_sma50_pct": {"label": "Close vs SMA 50", "unit": "%", "requires": 50, "basis": "Distance of close from SMA 50."},
    "close_vs_sma200_pct": {"label": "Close vs SMA 200", "unit": "%", "requires": SMA200_SESSIONS, "basis": "Distance of close from SMA 200."},
    "volume": {"label": "Last volume", "unit": "shares", "requires": 1, "basis": "Most recent session volume."},
    "volume_vs_20d_avg": {"label": "Volume vs 20-session average", "unit": "x", "requires": 20, "basis": "Last volume divided by its own 20-session mean."},
    "realized_range_pct_14d": {"label": "Realized range (14)", "unit": "%", "requires": 14, "basis": "Mean high-low span over 14 sessions, in percent of mean close."},
    "distance_from_52w_high_pct": {"label": "Distance from 52-week high", "unit": "%", "requires": 60, "basis": "Close against the highest close in the loaded window (up to 250 sessions)."},
    "distance_from_52w_low_pct": {"label": "Distance from 52-week low", "unit": "%", "requires": 60, "basis": "Close against the lowest close in the loaded window (up to 250 sessions)."},
    "gap_pct": {"label": "Opening gap", "unit": "%", "requires": 2, "basis": "Last open against previous close."},
}

SCREENER_DISCLOSURES = (
    "Filters are yours; nothing is preselected, weighted, or scored for you.",
    "Every field is a realized statistic on past sessions. No field is a forecast, target, or recommendation.",
    "A match is not a trade idea. Matching symbols are returned with their metric values so you can verify each match.",
    "Symbols with insufficient history are excluded with a reason rather than defaulted.",
)


class ScreenerError(ValueError):
    """Raised for invalid screener input or storage limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def describe_fields() -> list[dict[str, Any]]:
    """Field catalogue for the UI, including what each metric is computed from."""
    return [
        {
            "name": name,
            "label": meta["label"],
            "unit": meta["unit"],
            "sessions_required": meta["requires"],
            "basis": meta["basis"],
        }
        for name, meta in SCREENER_FIELDS.items()
    ]


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _pct(current: float, reference: float) -> float | None:
    if reference is None or not math.isfinite(reference) or reference == 0:
        return None
    return _round((current - reference) / abs(reference) * 100.0)


def _normalize_history(frame: Any) -> pd.DataFrame | None:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    data = frame.copy()
    data.columns = [str(column).strip().lower() for column in data.columns]
    if "close" not in data.columns:
        return None
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
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
    return data.reset_index(drop=True) if not data.empty else None


def _rsi(closes: pd.Series, window: int = 14) -> float | None:
    """Wilder RSI on closes; returns None when the window is not satisfied."""
    if len(closes) < window + 1:
        return None
    delta = closes.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1.0 / window, adjust=False).mean().iloc[-1]
    avg_loss = losses.ewm(alpha=1.0 / window, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = float(avg_gain) / float(avg_loss)
    return _round(100.0 - (100.0 / (1.0 + rs)))


def compute_metrics(symbol: str, frame: Any) -> dict[str, Any]:
    """Compute every screenable metric for one symbol.

    Metrics whose session requirement is not met are reported as `None` instead of
    being approximated from a shorter window.
    """
    label = str(symbol or "").strip().upper()
    data = _normalize_history(frame)
    if data is None:
        return {"symbol": label, "available": False, "reason": "history_unavailable"}
    sessions = int(len(data))
    if sessions < MIN_SESSIONS:
        return {
            "symbol": label,
            "available": False,
            "reason": "insufficient_history",
            "sessions_available": sessions,
            "sessions_required": MIN_SESSIONS,
        }

    closes = data["close"].astype(float)
    last_close = float(closes.iloc[-1])
    window = closes.tail(250)
    metrics: dict[str, Any] = {
        "last_close": _round(last_close),
        "change_pct_1d": _pct(last_close, float(closes.iloc[-2])) if sessions >= 2 else None,
        "change_pct_5d": _pct(last_close, float(closes.iloc[-6])) if sessions >= 6 else None,
        "change_pct_20d": _pct(last_close, float(closes.iloc[-21])) if sessions >= 21 else None,
        "rsi_14": _rsi(closes),
        "sma_20": _round(float(closes.tail(20).mean())) if sessions >= 20 else None,
        "sma_50": _round(float(closes.tail(50).mean())) if sessions >= 50 else None,
        "sma_200": _round(float(closes.tail(SMA200_SESSIONS).mean())) if sessions >= SMA200_SESSIONS else None,
        "distance_from_52w_high_pct": _pct(last_close, float(window.max())),
        "distance_from_52w_low_pct": _pct(last_close, float(window.min())),
    }
    for source, target in (("sma_20", "close_vs_sma20_pct"), ("sma_50", "close_vs_sma50_pct"), ("sma_200", "close_vs_sma200_pct")):
        metrics[target] = _pct(last_close, metrics[source]) if metrics.get(source) else None

    if "volume" in data.columns and data["volume"].notna().sum() >= 20:
        volumes = data["volume"].astype(float)
        baseline = float(volumes.tail(20).mean())
        metrics["volume"] = _round(volumes.iloc[-1], 0)
        metrics["volume_vs_20d_avg"] = _round(float(volumes.iloc[-1]) / baseline, 3) if baseline > 0 else None
    else:
        metrics["volume"] = None
        metrics["volume_vs_20d_avg"] = None

    if {"high", "low"}.issubset(data.columns):
        tail = data.tail(14)
        spans = (tail["high"] - tail["low"]).dropna()
        mean_close = float(tail["close"].mean())
        metrics["realized_range_pct_14d"] = (
            _round(float(spans.mean()) / mean_close * 100.0) if not spans.empty and mean_close else None
        )
    else:
        metrics["realized_range_pct_14d"] = None

    if "open" in data.columns and sessions >= 2 and pd.notna(data["open"].iloc[-1]):
        metrics["gap_pct"] = _pct(float(data["open"].iloc[-1]), float(closes.iloc[-2]))
    else:
        metrics["gap_pct"] = None

    as_of = data["date"].iloc[-1]
    return {
        "symbol": label,
        "available": True,
        "as_of": pd.Timestamp(as_of).isoformat() if pd.notna(as_of) else None,
        "sessions_available": sessions,
        "metrics": metrics,
    }


def validate_filters(filters: Iterable[Any]) -> list[dict[str, Any]]:
    """Validate and canonicalize filter definitions.

    Raises:
        ScreenerError: for unknown fields, unknown operators, or bad bounds.
    """
    rows = list(filters or [])
    if not rows:
        raise ScreenerError("screener_filters_required", "Supply at least one filter.")
    if len(rows) > MAX_FILTERS:
        raise ScreenerError("screener_filters_too_many", f"A maximum of {MAX_FILTERS} filters is supported.")
    cleaned: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ScreenerError("screener_filter_invalid", f"Filter {index + 1} must be an object.")
        field = str(row.get("field") or "").strip()
        if field not in SCREENER_FIELDS:
            raise ScreenerError(
                "screener_field_unknown",
                f"Filter {index + 1} uses unknown field '{field}'. Call the field catalogue for supported names.",
            )
        operator = str(row.get("op") or row.get("operator") or "").strip().lower()
        if operator not in OPERATORS:
            raise ScreenerError(
                "screener_operator_unknown",
                f"Filter {index + 1} uses unknown operator '{operator}'. Supported: {', '.join(OPERATORS)}.",
            )
        entry: dict[str, Any] = {"field": field, "op": operator}
        if operator == "between":
            try:
                low = float(cast(Any, row.get("low")))
                high = float(cast(Any, row.get("high")))
            except (TypeError, ValueError):
                raise ScreenerError("screener_bounds_invalid", f"Filter {index + 1} requires numeric low and high.") from None
            if not (math.isfinite(low) and math.isfinite(high)) or low > high:
                raise ScreenerError("screener_bounds_invalid", f"Filter {index + 1} requires low <= high.")
            entry["low"] = low
            entry["high"] = high
        else:
            try:
                value = float(cast(Any, row.get("value")))
            except (TypeError, ValueError):
                raise ScreenerError("screener_value_invalid", f"Filter {index + 1} requires a numeric value.") from None
            if not math.isfinite(value):
                raise ScreenerError("screener_value_invalid", f"Filter {index + 1} requires a finite value.")
            entry["value"] = value
        cleaned.append(entry)
    return cleaned


def _passes(metric_value: Any, rule: dict[str, Any]) -> bool | None:
    """Return True/False, or None when the metric is unavailable for this symbol."""
    if metric_value is None:
        return None
    try:
        value = float(metric_value)
    except (TypeError, ValueError):
        return None
    operator = rule["op"]
    threshold = float(rule.get("value", 0.0))
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return math.isclose(value, threshold, rel_tol=1e-9, abs_tol=1e-9)
    return float(rule["low"]) <= value <= float(rule["high"])


def run_screen(
    *,
    symbols: Iterable[str],
    history_loader: HistoryLoader,
    filters: Iterable[Any],
    sort_by: str | None = None,
    descending: bool = True,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate the supplied filters across the supplied symbols.

    Raises:
        ScreenerError: for invalid filters, an unknown sort field, or no symbols.
    """
    rules = validate_filters(filters)
    requested = [str(item).strip().upper() for item in symbols if str(item or "").strip()]
    deduped: list[str] = []
    for symbol in requested:
        if symbol not in deduped:
            deduped.append(symbol)
    if not deduped:
        raise ScreenerError("screener_symbols_required", "Supply at least one symbol to screen.")
    if len(deduped) > MAX_SYMBOLS:
        raise ScreenerError("screener_symbols_too_many", f"A maximum of {MAX_SYMBOLS} symbols can be screened per request.")
    if sort_by is not None and str(sort_by) not in SCREENER_FIELDS:
        raise ScreenerError("screener_sort_unknown", f"Unknown sort field '{sort_by}'.")

    matches: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    evaluated = 0
    for symbol in deduped:
        try:
            frame = history_loader(symbol)
        except Exception as exc:
            excluded.append({"symbol": symbol, "reason": "history_unavailable", "detail": type(exc).__name__})
            continue
        computed = compute_metrics(symbol, frame)
        if not computed.get("available"):
            excluded.append({key: value for key, value in computed.items() if key != "available"})
            continue
        evaluated += 1
        metrics = computed["metrics"]
        verdicts = [(rule, _passes(metrics.get(rule["field"]), rule)) for rule in rules]
        if any(result is None for _, result in verdicts):
            missing = sorted({rule["field"] for rule, result in verdicts if result is None})
            excluded.append(
                {
                    "symbol": symbol,
                    "reason": "metric_unavailable",
                    "fields": missing,
                    "sessions_available": computed.get("sessions_available"),
                }
            )
            continue
        if all(result for _, result in verdicts):
            matches.append(
                {
                    "symbol": symbol,
                    "as_of": computed.get("as_of"),
                    "sessions_available": computed.get("sessions_available"),
                    "metrics": metrics,
                    "matched_filters": rules,
                }
            )

    if sort_by:
        matches.sort(
            key=lambda row: (row["metrics"].get(sort_by) is None, row["metrics"].get(sort_by) or 0.0),
            reverse=bool(descending),
        )
    else:
        matches.sort(key=lambda row: row["symbol"])

    bounded_limit = max(1, min(int(limit), MAX_SYMBOLS))
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "filters": rules,
        "sort": {"field": sort_by, "descending": bool(descending)} if sort_by else {"field": "symbol", "descending": False},
        "coverage": {
            "requested": len(deduped),
            "evaluated": evaluated,
            "matched": len(matches),
            "excluded": excluded,
        },
        "matches": matches[:bounded_limit],
        "truncated": len(matches) > bounded_limit,
        "evidence": {
            "basis": "Realized daily OHLCV history only.",
            "min_sessions_required": MIN_SESSIONS,
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(SCREENER_DISCLOSURES),
    }


class ScreenerStore:
    """Per-user saved screens, stored in the existing SQLite database."""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._factory = connection_factory

    def _connect(self) -> sqlite3.Connection:
        factory = self._factory or _default_get_connection
        if factory is None:  # pragma: no cover - only in a broken install
            raise ScreenerError("screener_storage_unavailable", "No database connection factory is available.")
        connection = factory()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS screener_saved_screens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                filters TEXT NOT NULL,
                sort_field TEXT,
                sort_descending INTEGER NOT NULL DEFAULT 1,
                symbols TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            );
            """
        )
        connection.commit()
        return connection

    @staticmethod
    def _row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "name": row[1],
            "filters": json.loads(row[2]),
            "sort": {"field": row[3], "descending": bool(row[4])},
            "symbols": json.loads(row[5]) if row[5] else None,
            "created_at": row[6],
            "updated_at": row[7],
        }

    def save_screen(
        self,
        *,
        user_id: int,
        name: str,
        filters: Iterable[Any],
        sort_by: str | None = None,
        descending: bool = True,
        symbols: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Create or replace a named screen for one user."""
        label = str(name or "").strip()
        if not 1 <= len(label) <= 60:
            raise ScreenerError("screener_name_invalid", "Screen name must be 1-60 characters.")
        rules = validate_filters(filters)
        if sort_by is not None and str(sort_by) not in SCREENER_FIELDS:
            raise ScreenerError("screener_sort_unknown", f"Unknown sort field '{sort_by}'.")
        symbol_list = [str(item).strip().upper() for item in (symbols or []) if str(item or "").strip()]
        if len(symbol_list) > MAX_SYMBOLS:
            raise ScreenerError("screener_symbols_too_many", f"A saved screen can pin at most {MAX_SYMBOLS} symbols.")
        stamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT COUNT(*) FROM screener_saved_screens WHERE user_id = ? AND name != ?",
                (int(user_id), label),
            ).fetchone()
            if existing and int(existing[0]) >= MAX_SAVED_SCREENS:
                raise ScreenerError(
                    "screener_saved_limit",
                    f"A maximum of {MAX_SAVED_SCREENS} saved screens per account is supported.",
                )
            connection.execute(
                """
                INSERT INTO screener_saved_screens(user_id, name, filters, sort_field, sort_descending, symbols, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    filters = excluded.filters,
                    sort_field = excluded.sort_field,
                    sort_descending = excluded.sort_descending,
                    symbols = excluded.symbols,
                    updated_at = excluded.updated_at
                """,
                (
                    int(user_id),
                    label,
                    json.dumps(rules),
                    sort_by,
                    1 if descending else 0,
                    json.dumps(symbol_list) if symbol_list else None,
                    stamp,
                    stamp,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT id, name, filters, sort_field, sort_descending, symbols, created_at, updated_at
                FROM screener_saved_screens WHERE user_id = ? AND name = ?
                """,
                (int(user_id), label),
            ).fetchone()
        finally:
            connection.close()
        if row is None:  # pragma: no cover - defensive
            raise ScreenerError("screener_storage_unavailable", "The screen could not be stored.")
        return self._row(row)

    def list_screens(self, *, user_id: int) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, name, filters, sort_field, sort_descending, symbols, created_at, updated_at
                FROM screener_saved_screens WHERE user_id = ? ORDER BY updated_at DESC
                """,
                (int(user_id),),
            ).fetchall()
        finally:
            connection.close()
        return [self._row(row) for row in rows]

    def get_screen(self, *, user_id: int, screen_id: int) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id, name, filters, sort_field, sort_descending, symbols, created_at, updated_at
                FROM screener_saved_screens WHERE user_id = ? AND id = ?
                """,
                (int(user_id), int(screen_id)),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ScreenerError("screener_not_found", "That saved screen does not exist for this account.")
        return self._row(row)

    def delete_screen(self, *, user_id: int, screen_id: int) -> dict[str, Any]:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM screener_saved_screens WHERE user_id = ? AND id = ?",
                (int(user_id), int(screen_id)),
            )
            connection.commit()
            deleted = cursor.rowcount
        finally:
            connection.close()
        if not deleted:
            raise ScreenerError("screener_not_found", "That saved screen does not exist for this account.")
        return {"deleted": True, "id": int(screen_id)}


#: Process-wide store used by the API layer.
SCREENS = ScreenerStore()
