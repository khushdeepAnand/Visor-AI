"""Upstox market-data adapter using REST V3 endpoints.

The adapter requires ``UPSTOX_ACCESS_TOKEN`` (or analytics token) and resolves
symbols through the daily public Upstox instrument master cache.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from urllib.parse import quote as urlquote

import pandas as pd
from .base import (
    Instrument,
    InstrumentNotFoundError,
    MarketDataProvider,
    ProviderUnavailableError,
    Quote,
    UnsupportedHistoryRangeError,
    WINDOW_DAYS,
    normalize_ohlcv,
)
from .instruments import CATALOGUE
from .upstox_auth import UpstoxDiagnostic, request_json


class UpstoxProvider(MarketDataProvider):
    name = "upstox"
    supports_live_stream = True
    supports_depth = True
    base_url = "https://api.upstox.com"

    def __init__(self, token: str | None = None, timeout: float = 15.0) -> None:
        # StockPilot is a read-only research/paper-trading terminal. Upstox's
        # long-lived Analytics Token explicitly supports Market Quote,
        # Historical Data, Option Chain and WebSocket APIs, so prefer it for
        # market-data reads when both credentials are present. The daily OAuth
        # access token is retained separately for endpoints that require it.
        self.access_token = token or os.getenv("UPSTOX_ACCESS_TOKEN")
        self.analytics_token = None if token else os.getenv("UPSTOX_ANALYTICS_TOKEN")
        self.token = token or self.analytics_token or self.access_token
        self.credential_mode = (
            "explicit" if token else "analytics" if self.analytics_token else "access" if self.access_token else "none"
        )
        self.timeout = timeout
        self.last_diagnostics: dict[str, UpstoxDiagnostic] = {}

    def is_configured(self) -> bool:
        return bool(self.token)

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderUnavailableError("UPSTOX_ACCESS_TOKEN/UPSTOX_ANALYTICS_TOKEN is not configured.")
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}

    @property
    def access_headers(self) -> dict[str, str]:
        """Headers for endpoints that require the standard OAuth access token."""
        token = self.access_token
        if not token:
            raise ProviderUnavailableError("UPSTOX_ACCESS_TOKEN is not configured.")
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def health(self) -> dict[str, object]:
        status = super().health()
        status["credential_mode"] = self.credential_mode
        return status

    def search_instruments(self, query: str, limit: int = 20) -> list[Instrument]:
        return CATALOGUE.search(query, limit)

    def _instrument(self, symbol: str) -> Instrument:
        exchange = "BSE" if str(symbol).upper().endswith(".BO") else None
        item = CATALOGUE.resolve(symbol, exchange=exchange)
        if item is None or not item.instrument_key:
            # Try to refresh the public master once when the bundled catalogue has no key.
            try:
                CATALOGUE.refresh_from_upstox(timeout=self.timeout)
                item = CATALOGUE.resolve(symbol, exchange=exchange)
            except Exception:
                pass
        if item is None or not item.instrument_key:
            raise InstrumentNotFoundError(f"Unable to resolve Upstox instrument key for {symbol}.")
        return item

    @staticmethod
    def _unit_interval(timeframe: str) -> tuple[str, int]:
        mapping = {"1m": ("minutes", 1), "5m": ("minutes", 5), "15m": ("minutes", 15), "1h": ("hours", 1), "4h": ("hours", 4), "1D": ("days", 1), "1W": ("weeks", 1)}
        if timeframe not in mapping:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    @staticmethod
    def _max_span_days(unit: str, interval: int) -> int:
        """Largest date span Upstox V3 accepts in one historical-candle request.

        Upstox rejects a wider span with HTTP 400 ``UDAPI1148``. The published
        caps are one month for minute intervals up to 15, one quarter for larger
        minute and for hour intervals, and a decade for day and week candles.
        Requests wider than the cap are split into consecutive segments.
        """
        if unit == "minutes":
            return 30 if interval <= 15 else 90
        if unit == "hours":
            return 90
        return 3650

    #: Upper bound on segmented requests for one history call. It keeps a single
    #: chart or forecast request bounded in latency and API quota, and it defines
    #: the supported timeframe/window matrix exposed by ``max_supported_days``.
    MAX_HISTORY_SEGMENTS = 8

    @classmethod
    def max_supported_days(cls, timeframe: str) -> int:
        unit, interval = cls._unit_interval(timeframe)
        return cls._max_span_days(unit, interval) * cls.MAX_HISTORY_SEGMENTS

    @staticmethod
    def _date_range(window: str) -> tuple[str, str]:
        today = date.today()
        return (today - timedelta(days=WINDOW_DAYS.get(window, 370))).isoformat(), today.isoformat()

    @classmethod
    def _segments(cls, unit: str, interval: int, from_date: date, to_date: date) -> list[tuple[date, date]]:
        """Split an inclusive date range into provider-legal chunks, newest last."""
        span = cls._max_span_days(unit, interval)
        segments: list[tuple[date, date]] = []
        end = to_date
        while end >= from_date and len(segments) < cls.MAX_HISTORY_SEGMENTS:
            start = max(from_date, end - timedelta(days=span - 1))
            segments.append((start, end))
            if start <= from_date:
                break
            end = start - timedelta(days=1)
        segments.reverse()
        return segments

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        item = self._instrument(symbol)
        unit, interval = self._unit_interval(timeframe)
        requested_days = WINDOW_DAYS.get(window, 370)
        supported_days = self.max_supported_days(timeframe)
        if requested_days > supported_days:
            raise UnsupportedHistoryRangeError(
                f"Upstox serves at most {supported_days} calendar days of {timeframe} candles "
                f"in one request set; {window} needs {requested_days}.",
                timeframe=timeframe,
                window=window,
                supported_days=supported_days,
            )
        from_iso, to_iso = self._date_range(window)
        key = urlquote(str(item.instrument_key), safe="")
        candles: list[list[object]] = []
        for start, end in self._segments(unit, interval, date.fromisoformat(from_iso), date.fromisoformat(to_iso)):
            url = f"{self.base_url}/v3/historical-candle/{key}/{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
            payload, diagnostic = request_json(
                endpoint="/v3/historical-candle",
                url=url,
                token=self.token,
                timeout=self.timeout,
            )
            self.last_diagnostics["history"] = diagnostic
            candles.extend(payload.get("data", {}).get("candles", []) or [])
        if not candles:
            raise InstrumentNotFoundError(f"Upstox returned no candles for {symbol}.")
        frame = pd.DataFrame(candles, columns=["timestamp", "Open", "High", "Low", "Close", "Volume", "open_interest"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame.set_index("timestamp", inplace=True)
        # Segments are requested on inclusive calendar boundaries, so an overlapping
        # bar can appear twice. Keep one row per timestamp before normalization.
        frame = frame.loc[~frame.index.duplicated(keep="last")]
        return normalize_ohlcv(frame, source="Upstox", symbol=item.symbol, timeframe=timeframe)

    def get_quote(self, symbol: str) -> Quote:
        item = self._instrument(symbol)
        key = str(item.instrument_key)
        response_payload, diagnostic = request_json(
            endpoint="/v2/market-quote/quotes",
            url=f"{self.base_url}/v2/market-quote/quotes",
            params={"instrument_key": key},
            token=self.token,
            timeout=self.timeout,
        )
        self.last_diagnostics["quote"] = diagnostic
        data = response_payload.get("data", {})
        response_key, payload = next(iter(data.items())) if isinstance(data, dict) and data else ("", {})
        if not payload:
            raise InstrumentNotFoundError(f"No Upstox quote for {symbol}.")
        returned_key = str(payload.get("instrument_token") or "")
        returned_symbol = str(payload.get("symbol") or "").strip().upper()
        response_symbol = str(response_key).rsplit(":", 1)[-1].strip().upper()
        if not returned_key and not returned_symbol and response_symbol != item.symbol.strip().upper():
            raise InstrumentNotFoundError(f"Upstox quote identity is missing for {symbol}.")
        if returned_key and returned_key != key:
            raise InstrumentNotFoundError(f"Upstox quote instrument mismatch for {symbol}.")
        # Upstox currently returns the literal placeholder "NA" for some index
        # quotes, including SENSEX. The exact instrument token remains authoritative.
        if returned_symbol and returned_symbol != "NA" and returned_symbol != item.symbol.strip().upper():
            raise InstrumentNotFoundError(f"Upstox quote symbol mismatch for {symbol}.")
        price = float(payload.get("last_price") or payload.get("ltp") or 0)
        if price <= 0:
            raise ProviderUnavailableError(f"Upstox returned an invalid quote price for {symbol}.")
        timestamp = payload.get("timestamp") or payload.get("last_trade_time")
        if not timestamp:
            raise ProviderUnavailableError(f"Upstox quote timestamp is unavailable for {symbol}.")
        ohlc = payload.get("ohlc") or {}
        previous = float(ohlc.get("close") or payload.get("cp") or price)
        raw_depth = payload.get("depth") or {}
        depth = {
            "bids": [[float(item.get("price", 0)), int(item.get("quantity", 0))] for item in raw_depth.get("buy", [])],
            "asks": [[float(item.get("price", 0)), int(item.get("quantity", 0))] for item in raw_depth.get("sell", [])],
        }
        return Quote(
            symbol=item.symbol, price=price, timestamp=str(timestamp), source="Upstox",
            change=price - previous, change_pct=((price / previous) - 1) * 100 if previous else None,
            open=float(str(ohlc.get("open"))) if ohlc.get("open") is not None else None,
            high=float(str(ohlc.get("high"))) if ohlc.get("high") is not None else None,
            low=float(str(ohlc.get("low"))) if ohlc.get("low") is not None else None,
            previous_close=previous, volume=float(payload.get("volume") or 0), depth=depth,
        )
