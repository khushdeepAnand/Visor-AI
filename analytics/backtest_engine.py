"""No-lookahead strategy backtesting for StockPilot AI."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from analytics.risk_engine import calculate_performance_metrics


def _prepare_market_data(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Market data must be a pandas DataFrame.")
    if data.empty or "Close" not in data.columns:
        raise ValueError("Market data must include non-empty Close prices.")
    frame = data.copy()
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["Close"])
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    if len(frame) < 10 or (frame["Close"] <= 0).any():
        raise ValueError("At least 10 positive close-price observations are required.")
    return frame


def _trade_log(frame: pd.DataFrame, position: pd.Series) -> list[dict[str, Any]]:
    changes = position.diff().fillna(position).astype(float)
    trades: list[dict[str, Any]] = []
    open_trade: dict[str, Any] | None = None
    for row_number, (date, change) in enumerate(changes.items()):
        price = float(frame["Close"].iloc[row_number])
        if change > 0:
            open_trade = {"entry_date": str(date), "entry_price": round(price, 4)}
        elif change < 0 and open_trade is not None:
            trade_return = price / float(open_trade["entry_price"]) - 1.0
            trades.append({
                **open_trade,
                "exit_date": str(date),
                "exit_price": round(price, 4),
                "return": round(float(trade_return), 8),
            })
            open_trade = None
    if open_trade is not None:
        price = float(frame["Close"].iloc[-1])
        trades.append({
            **open_trade,
            "exit_date": str(frame.index[-1]),
            "exit_price": round(price, 4),
            "return": round(price / float(open_trade["entry_price"]) - 1.0, 8),
            "open_at_end": True,
        })
    return trades


def _build_result(
    frame: pd.DataFrame,
    raw_signal: pd.Series,
    *,
    strategy_name: str,
    initial_capital: float,
    transaction_cost_bps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if initial_capital <= 0:
        raise ValueError("Initial capital must be positive.")
    if transaction_cost_bps < 0 or transaction_cost_bps > 500:
        raise ValueError("Transaction cost must be between 0 and 500 basis points.")

    close_returns = frame["Close"].pct_change().fillna(0.0)
    # Shift the signal one full bar to prevent using today's close to trade today's return.
    position = raw_signal.astype(float).shift(1).fillna(0.0).clip(0.0, 1.0)
    turnover = position.diff().abs().fillna(position.abs())
    transaction_cost = turnover * (transaction_cost_bps / 10_000.0)
    gross_strategy_returns = position * close_returns
    strategy_returns = position * close_returns - transaction_cost
    benchmark_returns = close_returns
    strategy_equity = float(initial_capital) * (1.0 + strategy_returns).cumprod()
    gross_strategy_equity = float(initial_capital) * (1.0 + gross_strategy_returns).cumprod()
    benchmark_equity = float(initial_capital) * (1.0 + benchmark_returns).cumprod()

    trades = _trade_log(frame, position)
    winning_trades = sum(1 for trade in trades if trade.get("return", 0.0) > 0)
    equity_records = [
        {
            "date": str(date),
            "close": round(float(frame.loc[date, "Close"]), 4),
            "position": round(float(position.loc[date]), 4),
            "strategy_equity": round(float(strategy_equity.loc[date]), 4),
            "benchmark_equity": round(float(benchmark_equity.loc[date]), 4),
        }
        for date in frame.index
    ]

    metrics = calculate_performance_metrics(strategy_equity)
    benchmark_metrics = calculate_performance_metrics(benchmark_equity)
    metrics.update({
        "trade_count": len(trades),
        "winning_trades": winning_trades,
        "trade_win_rate": round(winning_trades / len(trades), 8) if trades else 0.0,
        "turnover_events": int((turnover > 0).sum()),
        "transaction_cost_bps": round(float(transaction_cost_bps), 4),
        "gross_total_return": round(float(gross_strategy_equity.iloc[-1] / initial_capital - 1.0), 8),
        "net_total_return": metrics["total_return"],
        "total_transaction_cost": round(float((transaction_cost * strategy_equity.shift(1).fillna(initial_capital)).sum()), 4),
    })

    return {
        "strategy": strategy_name,
        "parameters": parameters,
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "cost_model": {
            "type": "turnover_percentage",
            "all_in_bps": round(float(transaction_cost_bps), 4),
            "includes": ["brokerage", "STT", "stamp duty", "exchange charges", "taxes", "slippage"],
            "note": "Configure the all-in rate for the broker, exchange, and instrument class being studied.",
        },
        "outperformed_benchmark": metrics["total_return"] > benchmark_metrics["total_return"],
        "equity_curve": equity_records,
        "trades": trades,
        "methodology": "Signals are shifted by one bar to prevent look-ahead bias; transaction costs are deducted on position changes.",
        "disclaimer": "Backtested performance is hypothetical and does not guarantee future results.",
    }


def run_moving_average_strategy(
    data: pd.DataFrame,
    *,
    fast_period: int = 20,
    slow_period: int = 50,
    initial_capital: float = 100_000.0,
    transaction_cost_bps: float = 10.0,
) -> dict[str, Any]:
    frame = _prepare_market_data(data)
    fast_period = int(fast_period)
    slow_period = int(slow_period)
    if fast_period <= 0 or slow_period <= 0 or fast_period >= slow_period:
        raise ValueError("Fast period must be positive and smaller than slow period.")
    if len(frame) <= slow_period:
        raise ValueError("Market history must be longer than the slow moving-average period.")
    fast = frame["Close"].rolling(fast_period, min_periods=fast_period).mean()
    slow = frame["Close"].rolling(slow_period, min_periods=slow_period).mean()
    signal = (fast > slow).astype(float)
    return _build_result(
        frame,
        signal,
        strategy_name="Moving Average Crossover",
        initial_capital=initial_capital,
        transaction_cost_bps=transaction_cost_bps,
        parameters={"fast_period": fast_period, "slow_period": slow_period},
    )


def run_rsi_strategy(
    data: pd.DataFrame,
    *,
    period: int = 14,
    entry_level: float = 35.0,
    exit_level: float = 65.0,
    initial_capital: float = 100_000.0,
    transaction_cost_bps: float = 10.0,
) -> dict[str, Any]:
    frame = _prepare_market_data(data)
    period = int(period)
    if period <= 1 or not 0 < entry_level < exit_level < 100:
        raise ValueError("Use a valid RSI period and entry/exit levels between 0 and 100.")
    delta = frame["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50.0)
    position_values = []
    held = 0.0
    for value in rsi:
        if value <= entry_level:
            held = 1.0
        elif value >= exit_level:
            held = 0.0
        position_values.append(held)
    signal = pd.Series(position_values, index=frame.index)
    return _build_result(
        frame,
        signal,
        strategy_name="RSI Mean Reversion",
        initial_capital=initial_capital,
        transaction_cost_bps=transaction_cost_bps,
        parameters={"period": period, "entry_level": entry_level, "exit_level": exit_level},
    )


def compare_strategies(data: pd.DataFrame, *, transaction_cost_bps: float = 10.0) -> dict[str, Any]:
    results = [
        run_moving_average_strategy(data, transaction_cost_bps=transaction_cost_bps),
        run_rsi_strategy(data, transaction_cost_bps=transaction_cost_bps),
    ]
    ranking = sorted(
        (
            {
                "strategy": result["strategy"],
                "total_return": result["metrics"]["total_return"],
                "sharpe_ratio": result["metrics"]["sharpe_ratio"],
                "max_drawdown": result["metrics"]["max_drawdown"],
            }
            for result in results
        ),
        key=lambda item: (item["sharpe_ratio"], item["total_return"]),
        reverse=True,
    )
    return {"ranking": ranking, "results": results}
