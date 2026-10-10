"""Market-data provider contracts for StockPilot AI v6.

The provider layer deliberately uses a small common surface so the rest of the
application never depends on a broker-specific SDK.  Providers return pandas
frames with a standard OHLCV schema and metadata stored in ``DataFrame.attrs``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from .corporate_actions import adjust_history_frame

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1D", "1W"}
WINDOWS = {"1w", "1mo", "3mo", "1y", "5y", "10y", "max"}

#: Calendar days of history each named window requests. Providers use this to
#: decide how many segmented requests a window needs and whether the window is
#: servable at all for a given candle interval.
WINDOW_DAYS = {
    "1w": 10,
    "1mo": 35,
    "3mo": 100,
    "1y": 370,
    "5y": 365 * 5 + 10,
    "10y": 365 * 10 + 20,
    "max": 365 * 20,
}


class MarketDataError(RuntimeError):
    """Base error raised by a market-data provider."""


class ProviderUnavailableError(MarketDataError):
    """Provider is unavailable, misconfigured, or rate limited."""


class InstrumentNotFoundError(MarketDataError):
    """Requested symbol cannot be resolved by a provider."""


class UnsupportedHistoryRangeError(MarketDataError):
    """The requested timeframe/window pair exceeds what the provider serves.

    This is a permanent property of the combination rather than a transient
    outage, so callers surface it as an explicit client error naming the largest
    window the timeframe supports instead of a generic retryable failure.
    """

    def __init__(self, message: str, *, timeframe: str, window: str, supported_days: int) -> None:
        super().__init__(message)
        self.timeframe = timeframe
        self.window = window
        self.supported_days = supported_days

    def supported_windows(self) -> list[str]:
        """Named windows that fit inside this timeframe's provider limit."""
        return [name for name, days in sorted(WINDOW_DAYS.items(), key=lambda item: item[1]) if days <= self.supported_days]


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    timestamp: str
    source: str
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    is_stale: bool = False
    depth: dict[str, Any] | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Instrument:
    symbol: str
    name: str
    exchange: str
    segment: str
    instrument_type: str
    instrument_key: str | None = None
    isin: str | None = None
    lot_size: float | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None
    underlying_symbol: str | None = None
    # Optional research metadata. Broker contract masters often omit these
    # fields; callers must treat missing values as unknown, never inferred.
    sector: str | None = None
    market_cap_bucket: str | None = None
    listing_year: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketDataProvider(ABC):
    """Provider interface used by the API, ML pipeline and background jobs."""

    name = "base"
    supports_live_stream = False
    supports_depth = False

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        """Return normalized OHLCV data sorted oldest -> newest."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    def search_instruments(self, query: str, limit: int = 20) -> list[Instrument]:
        return []

    def resolve_instrument(self, symbol: str) -> Instrument | None:
        matches = self.search_instruments(symbol, limit=20)
        clean = str(symbol).upper().replace(".NS", "").replace(".BO", "")
        for item in matches:
            if item.symbol.upper().replace(".NS", "").replace(".BO", "") == clean:
                return item
        return matches[0] if matches else None

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.is_configured(),
            "supports_live_stream": self.supports_live_stream,
            "supports_depth": self.supports_depth,
        }


def normalize_ohlcv(frame: pd.DataFrame, *, source: str, symbol: str, timeframe: str) -> pd.DataFrame:
    """Coerce provider output into the StockPilot OHLCV contract."""
    if frame is None or frame.empty:
        raise InstrumentNotFoundError(f"No market data returned for {symbol}.")
    data = frame.copy()
    rename = {str(c).lower(): c for c in data.columns}
    mapping = {}
    for target in OHLCV_COLUMNS:
        lower = target.lower()
        if target not in data.columns and lower in rename:
            mapping[rename[lower]] = target
    data.rename(columns=mapping, inplace=True)
    missing = [c for c in OHLCV_COLUMNS if c not in data.columns]
    if missing:
        raise ProviderUnavailableError(f"{source} response is missing OHLCV columns: {', '.join(missing)}")
    data = data[OHLCV_COLUMNS].copy()
    for column in OHLCV_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data.replace([float("inf"), float("-inf")], pd.NA, inplace=True)
    data.dropna(subset=OHLCV_COLUMNS, inplace=True)
    data = data.loc[~data.index.duplicated(keep="last")].sort_index()
    data, applied_labels = adjust_history_frame(data, symbol)
    data.attrs.update({
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "is_stale": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "corporate_action_adjusted": bool(applied_labels),
        "corporate_action_events_applied": applied_labels,
    })
    if applied_labels:
        data.attrs["price_adjustment"] = "adjusted"
    data.attrs.update(assess_corporate_actions(data))
    from .quality import validate_prices
    try:
        return validate_prices(data)
    except ValueError as exc:
        raise ProviderUnavailableError(f"{source} history failed price-quality validation") from exc


def assess_corporate_actions(frame: pd.DataFrame) -> dict[str, Any]:
    """Flag ratio-like discontinuities without claiming an inferred split is verified."""

    if frame.empty or "Close" not in frame.columns:
        return {"price_adjustment": "unknown", "corporate_action_status": "clear", "corporate_action_candidates": []}
    close = pd.to_numeric(frame["Close"], errors="coerce")
    ratios = close / close.shift(1)
    known_ratios = (0.2, 0.25, 1 / 3, 0.4, 0.5, 2 / 3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
    candidates: list[dict[str, Any]] = []
    for timestamp, ratio in ratios.dropna().items():
        value = float(ratio)
        nearest = min(known_ratios, key=lambda expected: abs(value - expected) / expected)
        if abs(value - nearest) / nearest <= 0.035:
            candidates.append({
                "timestamp": pd.Timestamp(str(timestamp)).isoformat(),
                "observed_ratio": round(value, 6),
                "nearest_action_ratio": round(nearest, 6),
            })
        if len(candidates) >= 8:
            break
    return {
        "price_adjustment": str(frame.attrs.get("price_adjustment") or "unknown"),
        "corporate_action_status": "suspected" if candidates else "clear",
        "corporate_action_candidates": candidates,
    }


def instrument_dicts(items: Iterable[Instrument]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in items]
