"""Licensed-vendor adapters using a deployment-owned normalized REST gateway.

TrueData and Global Datafeeds products expose account-specific SDK/API shapes.
StockPilot therefore consumes a tiny normalized gateway contract rather than
shipping vendor credentials or coupling the core to one commercial SDK build.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests

from .base import MarketDataProvider, ProviderUnavailableError, Quote, normalize_ohlcv


class NormalizedVendorProvider(MarketDataProvider):
    env_prefix = ""
    name = "licensed_vendor"
    label = "Licensed vendor"
    # The normalized gateway contract is REST-only. A deployment may proxy a
    # vendor stream separately, but this adapter must not advertise one.
    supports_live_stream = False
    supports_depth = True

    def __init__(self, timeout: float = 10.0) -> None:
        self.base_url = os.getenv(f"{self.env_prefix}_GATEWAY_URL", "").strip().rstrip("/")
        self.token = os.getenv(f"{self.env_prefix}_GATEWAY_TOKEN", "").strip()
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.is_configured():
            raise ProviderUnavailableError(f"{self.label} gateway is not configured.")
        try:
            response = requests.get(
                f"{self.base_url}{path}", params=params, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected an object response.")
            return payload
        except Exception as exc:
            raise ProviderUnavailableError(f"{self.label} gateway request failed: {type(exc).__name__}") from exc

    def get_quote(self, symbol: str) -> Quote:
        payload = self._get("/quote", {"symbol": symbol})
        candidate = payload.get("data")
        data: dict[str, Any] = candidate if isinstance(candidate, dict) else payload
        try:
            price = float(data.get("price") or data.get("last_price") or 0)
        except (TypeError, ValueError) as exc:
            raise ProviderUnavailableError(f"{self.label} quote price is invalid.") from exc
        timestamp = pd.to_datetime(str(data.get("timestamp") or ""), utc=True, errors="coerce")
        if price <= 0 or pd.isna(timestamp):
            raise ProviderUnavailableError(f"{self.label} quote is missing a valid price or vendor timestamp.")
        return Quote(
            symbol=symbol, price=price,
            timestamp=timestamp.isoformat(),
            source=self.label, change=float(data["change"]) if data.get("change") is not None else None,
            change_pct=float(data["change_pct"]) if data.get("change_pct") is not None else None,
            open=float(data["open"]) if data.get("open") is not None else None,
            high=float(data["high"]) if data.get("high") is not None else None,
            low=float(data["low"]) if data.get("low") is not None else None,
            previous_close=float(data["previous_close"]) if data.get("previous_close") is not None else None,
            volume=float(data["volume"]) if data.get("volume") is not None else None,
            depth=data.get("depth") if isinstance(data.get("depth"), dict) else None,
        )

    def get_history(self, symbol: str, timeframe: str, window: str) -> pd.DataFrame:
        payload = self._get("/history", {"symbol": symbol, "timeframe": timeframe, "window": window})
        rows = payload.get("data") or payload.get("candles") or []
        if not isinstance(rows, list):
            raise ProviderUnavailableError(f"{self.label} history contract is invalid.")
        if rows and isinstance(rows[0], dict):
            frame = pd.DataFrame(rows)
            index_name = next((name for name in ("timestamp", "time", "date") if name in frame.columns), None)
            if not index_name:
                raise ProviderUnavailableError(f"{self.label} history is missing vendor timestamps.")
            frame.index = pd.to_datetime(frame.pop(index_name), utc=True, errors="coerce")
        else:
            frame = pd.DataFrame(rows, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
            if not frame.empty:
                frame.index = pd.to_datetime(frame.pop("timestamp"), utc=True, errors="coerce")
        if not frame.empty and any(pd.isna(value) for value in frame.index):
            raise ProviderUnavailableError(f"{self.label} history contains invalid vendor timestamps.")
        return normalize_ohlcv(frame, source=self.label, symbol=symbol, timeframe=timeframe)


class TrueDataProvider(NormalizedVendorProvider):
    name = "truedata"
    env_prefix = "TRUEDATA"
    label = "TrueData licensed feed"


class GlobalDatafeedsProvider(NormalizedVendorProvider):
    name = "globaldatafeeds"
    env_prefix = "GLOBALDATAFEEDS"
    label = "Global Datafeeds licensed feed"
