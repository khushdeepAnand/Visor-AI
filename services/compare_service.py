"""India-only multi-symbol comparison orchestration for StockPilot AI v6."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd

from forecasting.interval_forecast import TRAINING_WINDOWS, WINDOW_TIMEFRAME_DEFAULTS, forecast_range
from forecasting.model_promotion import active_promotion_receipt
from indicators import add_indicators
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER
from services.watchlist_service import calculate_quote_snapshot


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(number) else number


def _latest_indicator_snapshot(data: pd.DataFrame) -> dict[str, float]:
    if data is None or data.empty:
        raise ValueError("Indicator data is unavailable.")
    latest = data.iloc[-1]
    return {
        "rsi_14": _safe_float(latest.get("RSI")),
        "macd": _safe_float(latest.get("MACD")),
        "macd_signal": _safe_float(latest.get("MACD_Signal")),
        "sma_20": _safe_float(latest.get("SMA_20")),
        "sma_50": _safe_float(latest.get("SMA_50")),
        "vwap": _safe_float(latest.get("VWAP")),
        "volatility_pct": _safe_float(latest.get("Volatility")),
    }


def _legacy_prediction_to_range(raw: dict[str, Any], current_price: float) -> dict[str, Any]:
    """Normalize injected legacy predictors used by compatibility tests/extensions.

    Production code never calls the point-forecast path. This adapter only keeps
    extension hooks from v5 from breaking while ensuring the public result remains
    range-first.
    """
    if "error" in raw:
        raise ValueError(str(raw["error"]))
    if isinstance(raw.get("forecast"), dict):
        forecast = raw["forecast"]
        return {
            "low": _safe_float(forecast.get("low"), current_price),
            "median": _safe_float(forecast.get("median"), current_price),
            "high": _safe_float(forecast.get("high"), current_price),
            "confidence_level": _safe_float(forecast.get("confidence_level"), 0.80),
            "currency": str(forecast.get("currency") or "INR"),
            "direction": str(forecast.get("direction") or "neutral"),
        }
    interval = raw.get("Prediction Interval") or {}
    median = _safe_float(raw.get("Consensus Prediction"), current_price)
    return {
        "low": _safe_float(interval.get("lower"), median),
        "median": median,
        "high": _safe_float(interval.get("upper"), median),
        "confidence_level": 0.80,
        "currency": "INR",
        "direction": "bullish" if median > current_price else "bearish" if median < current_price else "neutral",
    }


def build_symbol_comparison(
    symbol: str,
    *,
    training_window: str = "1y",
    timeframe: str | None = None,
    confidence_level: float = 0.80,
    data_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
    info_fetcher: Callable[[str], dict[str, Any]] | None = None,
    indicator_adder: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    predictor: Callable[[str, pd.DataFrame], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one comparison row from a common market-data/forecast pipeline.

    The default path is fully v6: NSE/BSE catalogue validation, provider manager,
    session-aware indicators, and range forecasts. Optional callables are retained
    only as deterministic test/extension seams.
    """
    training_window = str(training_window).lower()
    if training_window not in TRAINING_WINDOWS:
        raise ValueError(f"training_window must be one of {sorted(TRAINING_WINDOWS)}")
    resolved_tf = timeframe or WINDOW_TIMEFRAME_DEFAULTS[training_window]

    # Production path validates against the India-only catalogue. Injected data is
    # still normalized but not forced through a live catalogue so tests can remain
    # network-independent.
    normalized = MANAGER.normalize_symbol(symbol) if data_fetcher is None else str(symbol or "").strip().upper().replace(".NS", "").replace(".BO", "")
    if not normalized:
        raise ValueError("A stock symbol is required.")

    if data_fetcher is None:
        market_data = MANAGER.get_history(normalized, resolved_tf, training_window)
    else:
        market_data = data_fetcher(normalized, training_window)
    if market_data is None or market_data.empty:
        raise ValueError(f"No market data is available for {normalized}.")

    indicators = (indicator_adder or add_indicators)(market_data)
    current_price = float(pd.to_numeric(market_data["Close"], errors="coerce").dropna().iloc[-1])

    if predictor is None:
        forecast_result = forecast_range(
            normalized,
            market_data,
            confidence_level=confidence_level,
            training_window=training_window,
            timeframe=resolved_tf,
            cqr_promotion_receipt=active_promotion_receipt(),
        )
        forecast = dict(forecast_result["forecast"])
        validation = dict(forecast_result.get("validation") or {})
        drift = dict(forecast_result.get("drift") or {})
        evidence = dict(forecast_result.get("evidence") or {})
        forecast_status = str(forecast_result.get("forecast_status") or "abstained")
        abstention_reason = forecast_result.get("abstention_reason")
    else:
        injected = predictor(normalized, market_data)
        forecast = _legacy_prediction_to_range(injected, current_price)
        validation = {
            "beats_naive_baseline": bool(injected.get("Best Model Beats Baseline", False)),
            "compatibility_adapter": True,
        }
        drift = {}
        evidence = {}
        forecast_status = "model_supported" if validation["beats_naive_baseline"] else "baseline_only"
        abstention_reason = None

    if info_fetcher is not None:
        info = info_fetcher(normalized) or {}
        company = info.get("longName") or info.get("shortName") or info.get("displayName") or normalized
        exchange = info.get("exchange") or ""
    else:
        instrument = CATALOGUE.resolve(normalized)
        company = instrument.name if instrument else normalized
        exchange = instrument.exchange if instrument else ""

    if data_fetcher is None:
        try:
            quote = MANAGER.get_quote(normalized).to_dict()
        except Exception:
            quote = {
                "symbol": normalized,
                "price": current_price,
                "source": str(market_data.attrs.get("provider") or market_data.attrs.get("source") or "history"),
                "timestamp": str(market_data.attrs.get("fetched_at") or ""),
                "is_stale": bool(market_data.attrs.get("is_stale", False)),
            }
    else:
        legacy_quote = calculate_quote_snapshot(normalized, market_data)
        quote = {
            "symbol": normalized,
            "price": legacy_quote["Current Price"],
            "change": legacy_quote["Daily Change"],
            "change_pct": legacy_quote["Daily Change %"],
            "timestamp": legacy_quote["Last Updated"],
            "source": "injected",
            "is_stale": legacy_quote["Data Status"] != "Current",
        }

    return {
        "symbol": normalized,
        "company": str(company),
        "exchange": str(exchange),
        "quote": quote,
        "indicators": _latest_indicator_snapshot(indicators),
        "forecast": forecast,
        "validation": validation,
        "drift": drift,
        "evidence": evidence,
        "forecast_status": forecast_status,
        "abstention_reason": abstention_reason,
        "training": {"window": training_window, "timeframe": resolved_tf, "rows": len(market_data)},
    }


def compare_symbols(
    symbols: Iterable[str],
    *,
    training_window: str = "1y",
    timeframe: str | None = None,
    confidence_level: float = 0.80,
) -> list[dict[str, Any]]:
    """Compare up to four unique Indian symbols using identical parameters."""
    unique: list[str] = []
    for symbol in symbols:
        clean = str(symbol or "").strip()
        if clean and clean.upper() not in {item.upper() for item in unique}:
            unique.append(clean)
    if not 2 <= len(unique) <= 4:
        raise ValueError("Compare between 2 and 4 unique NSE/BSE symbols.")
    return [
        build_symbol_comparison(
            symbol,
            training_window=training_window,
            timeframe=timeframe,
            confidence_level=confidence_level,
        )
        for symbol in unique
    ]
