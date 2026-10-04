"""Deterministic credential-free Indian market provider for local development/tests.

This is intentionally labelled DEMO in every payload. It exists so the rebuilt
full stack starts without private broker credentials; it must never be presented
as exchange data.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .base import Instrument, MarketDataProvider, Quote, normalize_ohlcv
from .instruments import CATALOGUE

IST = ZoneInfo("Asia/Kolkata")


def _seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)


class DemoIndianProvider(MarketDataProvider):
    name = "demo_india"
    supports_live_stream = True
    supports_depth = True

    def is_configured(self) -> bool:
        return True

    def search_instruments(self, query: str, limit: int = 20) -> list[Instrument]:
        return CATALOGUE.search(query, limit)

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        timeframe = timeframe if timeframe in {"1m", "5m", "15m", "1h", "4h", "1D", "1W"} else "1D"
        window = window if window in {"1w", "1mo", "3mo", "1y", "5y", "10y", "max"} else "1y"
        now = datetime.now(IST).replace(second=0, microsecond=0)
        # Keep demo series large enough for indicator/model warm-up in short modes.
        points_by_pair = {
            ("1m", "1w"): 1500, ("1m", "1mo"): 4000,
            ("5m", "1w"): 800, ("5m", "1mo"): 1800,
            ("15m", "1w"): 450, ("15m", "1mo"): 900,
            ("1h", "1mo"): 500, ("4h", "3mo"): 500,
            ("1D", "1y"): 320, ("1D", "5y"): 1300,
            ("1W", "5y"): 300,
        }
        n = points_by_pair.get((timeframe, window), 600 if timeframe != "1D" else 320)
        freq = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1D": "B", "1W": "W-FRI"}[timeframe]
        index = pd.date_range(end=now, periods=n, freq=freq, tz=IST)
        if timeframe in {"1m", "5m", "15m", "1h", "4h"}:
            index = index[(index.dayofweek < 5)]
            # Keep intraday demo data inside the regular cash session.
            index = index[(index.time >= datetime.strptime("09:15", "%H:%M").time()) & (index.time <= datetime.strptime("15:30", "%H:%M").time())]
            if len(index) < 260:
                index = pd.date_range(end=now, periods=n, freq=freq, tz=IST)
        rng = np.random.default_rng(_seed(symbol + timeframe + window))
        base = 100 + (_seed(symbol) % 4500)
        returns = rng.normal(0.00015, 0.006 if timeframe in {"1D", "1W"} else 0.0018, len(index))
        close = base * np.exp(np.cumsum(returns))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.0008, len(index)))
        spread = np.maximum(close * np.abs(rng.normal(0.002, 0.0012, len(index))), 0.05)
        high = np.maximum(open_, close) + spread
        low = np.maximum(0.05, np.minimum(open_, close) - spread)
        volume = rng.integers(50_000, 3_000_000, len(index))
        frame = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index)
        return normalize_ohlcv(frame, source="StockPilot DEMO (synthetic India-only)", symbol=symbol, timeframe=timeframe)

    def get_quote(self, symbol: str) -> Quote:
        frame = self.get_history(symbol, "1m", "1w")
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        price = float(last["Close"])
        previous = float(prev["Close"])
        spread = max(price * 0.0005, 0.05)
        return Quote(
            symbol=str(symbol).upper().replace(".NS", "").replace(".BO", ""),
            price=round(price, 2),
            timestamp=datetime.now(IST).isoformat(),
            source="StockPilot DEMO (synthetic India-only)",
            change=round(price - previous, 2),
            change_pct=round((price / previous - 1) * 100, 4),
            open=round(float(last["Open"]), 2), high=round(float(last["High"]), 2), low=round(float(last["Low"]), 2),
            previous_close=round(previous, 2), volume=float(last["Volume"]),
            depth={
                "bids": [[round(price - spread * i, 2), int(1000 + 250 * i)] for i in range(1, 6)],
                "asks": [[round(price + spread * i, 2), int(950 + 230 * i)] for i in range(1, 6)],
            },
        )
