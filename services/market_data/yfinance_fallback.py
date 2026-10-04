"""Explicit last-resort yfinance fallback for Indian symbols only."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .base import Instrument, MarketDataProvider, ProviderUnavailableError, Quote, normalize_ohlcv
from .instruments import CATALOGUE


class YFinanceFallbackProvider(MarketDataProvider):
    name = "yfinance_fallback"

    def is_configured(self) -> bool:
        try:
            import yfinance  # noqa: F401
            return True
        except Exception:
            return False

    def search_instruments(self, query: str, limit: int = 20) -> list[Instrument]:
        return CATALOGUE.search(query, limit)

    def _ticker(self, symbol: str) -> str:
        raw = str(symbol).upper().strip()
        if raw.endswith((".NS", ".BO")):
            return raw
        item = CATALOGUE.resolve(raw)
        exchange = item.exchange if item else "NSE"
        return raw + (".BO" if exchange == "BSE" else ".NS")

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        if not self.is_configured():
            raise ProviderUnavailableError("yfinance is not installed.")
        import yfinance as yf
        interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "60m", "1D": "1d", "1W": "1wk"}.get(timeframe, "1d")
        period = {"1w": "7d", "1mo": "1mo", "3mo": "3mo", "1y": "1y", "5y": "5y", "10y": "10y", "max": "max"}.get(window, "1y")
        try:
            data = yf.Ticker(self._ticker(symbol)).history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise ProviderUnavailableError(f"yfinance fallback failed: {exc}") from exc
        if timeframe == "4h" and not data.empty:
            data = data.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
        return normalize_ohlcv(data, source="Yahoo Finance fallback", symbol=symbol, timeframe=timeframe)

    def get_quote(self, symbol: str) -> Quote:
        data = self.get_history(symbol, "1D", "1mo")
        last, prev = data.iloc[-1], data.iloc[-2]
        price, previous = float(last.Close), float(prev.Close)
        return Quote(symbol=str(symbol).upper(), price=price, timestamp=datetime.utcnow().isoformat()+"Z", source="Yahoo Finance fallback", change=price-previous, change_pct=(price/previous-1)*100, open=float(last.Open), high=float(last.High), low=float(last.Low), previous_close=previous, volume=float(last.Volume))
