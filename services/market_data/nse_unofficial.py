"""Supplementary NSE public-web provider.

NSE's public website is not a contractual developer API. This adapter therefore
uses conservative timeouts and is never the only configured production path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

from .base import Instrument, MarketDataProvider, ProviderUnavailableError, Quote
from .instruments import CATALOGUE


class NSEUnofficialProvider(MarketDataProvider):
    name = "nse_unofficial"
    supports_live_stream = False
    supports_depth = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.nseindia.com/market-data/live-equity-market",
        })

    def is_configured(self) -> bool:
        return True

    def search_instruments(self, query: str, limit: int = 20) -> list[Instrument]:
        return [i for i in CATALOGUE.search(query, limit * 2) if i.exchange == "NSE"][:limit]

    def _prime(self) -> None:
        self.session.get("https://www.nseindia.com/", timeout=self.timeout)

    def get_quote(self, symbol: str) -> Quote:
        clean = str(symbol).upper().replace(".NS", "").replace(".BO", "")
        try:
            self._prime()
            response = self.session.get("https://www.nseindia.com/api/quote-equity", params={"symbol": clean}, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            info = payload.get("priceInfo") or {}
            metadata = payload.get("metadata") or {}
            price = float(info.get("lastPrice") or 0)
            previous = float(info.get("previousClose") or price)
            intra = info.get("intraDayHighLow") or {}
            depth = (payload.get("marketDeptOrderBook") or {}).get("tradeInfo")
            if price <= 0:
                raise ValueError("invalid last price")
            return Quote(
                symbol=clean, price=price, timestamp=str(metadata.get("lastUpdateTime") or datetime.now(timezone.utc).isoformat()), source="NSE public website (unofficial)",
                change=float(info.get("change") or price - previous), change_pct=float(info.get("pChange") or ((price / previous - 1) * 100 if previous else 0)),
                open=float(info.get("open") or price), high=float(intra.get("max") or price), low=float(intra.get("min") or price), previous_close=previous,
                depth=depth,
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"NSE public quote request failed: {exc}") from exc

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        raise ProviderUnavailableError("NSE public website adapter is quote-only; use Upstox for intraday/historical candles.")
