"""Legacy stock_api facade must route through v6 providers, never yfinance directly."""
from __future__ import annotations

import pandas as pd
import pytest

import stock_api
from services.market_data.base import Quote


def _frame():
    data = pd.DataFrame({"Open":[99,100],"High":[101,102],"Low":[98,99],"Close":[100,101],"Volume":[1000,1200]}, index=pd.date_range("2026-01-05", periods=2, freq="B"))
    data.attrs.update({"source":"fixture","is_stale":False})
    return data


def test_compat_history_routes_through_manager(monkeypatch):
    calls=[]
    monkeypatch.setattr(stock_api.MANAGER, "get_history", lambda symbol, timeframe, window: calls.append((symbol,timeframe,window)) or _frame())
    result=stock_api.get_stock_data("RELIANCE", "1y")
    assert calls == [("RELIANCE","1D","1y")]
    assert result.iloc[-1].Close == 101


def test_compat_quote_routes_through_manager(monkeypatch):
    monkeypatch.setattr(stock_api.MANAGER, "get_quote", lambda symbol: Quote(symbol=symbol,price=123.45,timestamp="now",source="fixture"))
    assert stock_api.get_current_price("TCS") == 123.45


def test_news_does_not_use_yfinance_anymore():
    assert stock_api.get_stock_news("INFY") == []


def test_global_symbol_is_rejected():
    with pytest.raises(ValueError):
        stock_api.normalize_symbol("AAPL")
