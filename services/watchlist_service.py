"""Market-data helpers for the watchlist UI.

The service keeps quote calculation independent from the presentation layer so it can be
unit tested with deterministic data and reused by other interfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd


def normalise_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper().replace(" ", "")


def calculate_quote_snapshot(symbol: str, data: pd.DataFrame) -> dict:
    """Return current price and one-session change from an OHLCV frame."""

    clean_symbol = normalise_symbol(symbol)
    if not clean_symbol:
        raise ValueError("Stock symbol is required.")
    if data is None or data.empty or "Close" not in data.columns:
        raise ValueError(f"No usable market data is available for {clean_symbol}.")

    closes = pd.to_numeric(data["Close"], errors="coerce").dropna()
    if closes.empty:
        raise ValueError(f"No closing prices are available for {clean_symbol}.")

    current_price = float(closes.iloc[-1])
    previous_close = float(closes.iloc[-2]) if len(closes) > 1 else current_price
    daily_change = current_price - previous_close
    daily_change_percent = (
        daily_change / previous_close * 100 if previous_close else 0.0
    )

    return {
        "Symbol": clean_symbol,
        "Current Price": current_price,
        "Daily Change": daily_change,
        "Daily Change %": daily_change_percent,
        "Last Updated": data.attrs.get("fetched_at", ""),
        "Data Status": "Stale cache" if data.attrs.get("is_stale") else "Current",
    }


def load_watchlist_quotes(
    rows: Iterable[tuple],
    data_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
) -> list[dict]:
    """Enrich database watchlist rows with current quote information."""

    if data_fetcher is None:
        from stock_api import get_stock_data

        data_fetcher = get_stock_data

    quotes: list[dict] = []
    for row in rows:
        if len(row) < 2:
            continue
        watchlist_id, symbol = row[0], normalise_symbol(row[1])
        added_date = row[2] if len(row) > 2 else None
        if not symbol:
            continue

        try:
            snapshot = calculate_quote_snapshot(
                symbol,
                data_fetcher(symbol, "5d"),
            )
            snapshot["Status"] = "Available"
        except Exception as error:
            snapshot = {
                "Symbol": symbol,
                "Current Price": None,
                "Daily Change": None,
                "Daily Change %": None,
                "Last Updated": "",
                "Data Status": "Unavailable",
                "Status": str(error) or "Market data unavailable",
            }

        snapshot["Watchlist ID"] = watchlist_id
        snapshot["Added"] = added_date
        quotes.append(snapshot)

    return quotes
