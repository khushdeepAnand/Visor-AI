"""Legacy compatibility facade for StockPilot AI v6.

New code must import ``services.market_data`` directly.  This module keeps older
analytics/scripts working during cutover without allowing yfinance to become the
primary source again.  All history/quotes go through the provider manager; its
final fallback may be yfinance for Indian instruments only.
"""
from __future__ import annotations

from typing import Any
import pandas as pd

from services.market_data.base import InstrumentNotFoundError, MarketDataError, ProviderUnavailableError
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER
from services.news import get_news

DEFAULT_PERIOD = "1y"

class MarketDataNotFoundError(InstrumentNotFoundError):
    pass
class MarketDataUnavailableError(ProviderUnavailableError):
    pass


def normalize_symbol(symbol: str) -> str:
    return MANAGER.normalize_symbol(symbol)


def _period_to_window(period: str) -> tuple[str, int | None]:
    p = str(period or DEFAULT_PERIOD).lower()
    direct = {"1mo":"1mo","3mo":"3mo","1y":"1y","5y":"5y","10y":"10y","max":"max"}
    if p in direct: return direct[p], None
    if p == "6mo": return "1y", 126
    if p == "2y": return "5y", 504
    return "1y", None


def get_stock_data(symbol: str, period: str = DEFAULT_PERIOD) -> pd.DataFrame:
    window, tail = _period_to_window(period)
    try:
        data = MANAGER.get_history(symbol, timeframe="1D", window=window)
    except MarketDataError as exc:
        raise MarketDataUnavailableError(str(exc)) from exc
    if tail:
        data = data.tail(tail).copy()
    return data


def get_current_price(symbol: str) -> float:
    try:
        return float(MANAGER.get_quote(symbol).price)
    except MarketDataError as exc:
        raise MarketDataUnavailableError(str(exc)) from exc


def get_stock_info(symbol: str) -> dict[str, Any]:
    clean = normalize_symbol(symbol)
    item = CATALOGUE.resolve(clean)
    quote = None
    try: quote = MANAGER.get_quote(clean)
    except Exception: pass
    return {
        "symbol": clean,
        "shortName": item.name if item else clean,
        "longName": item.name if item else clean,
        "exchange": item.exchange if item else "NSE/BSE",
        "currency": "INR",
        "sector": None,
        "industry": None,
        "website": None,
        "marketCap": None,
        "currentPrice": quote.price if quote else None,
        "source": quote.source if quote else "instrument master",
    }


def get_company_name(symbol: str) -> str:
    return str(get_stock_info(symbol).get("longName") or normalize_symbol(symbol))

def get_sector(symbol: str) -> str: return str(get_stock_info(symbol).get("sector") or "Not available from instrument master")
def get_industry(symbol: str) -> str: return str(get_stock_info(symbol).get("industry") or "Not available from instrument master")
def get_market_cap(symbol: str) -> float: return float(get_stock_info(symbol).get("marketCap") or 0.0)
def get_currency(symbol: str) -> str: return "INR"
def get_website(symbol: str) -> str: return str(get_stock_info(symbol).get("website") or "")
def get_exchange(symbol: str) -> str: return str(get_stock_info(symbol).get("exchange") or "NSE/BSE")

def get_stock_news(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    return list(get_news(symbol, limit).get("items") or [])

def get_market_service_health() -> dict[str, Any]:
    health = MANAGER.health()
    return {"status": "Operational", **health}

def clear_stock_cache() -> None:
    with MANAGER.lock:
        MANAGER.memory.clear()
