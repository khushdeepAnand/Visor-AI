"""No-code paper-strategy builder (v9 Part F1).

Compiles a visual condition tree (indicator + comparator + threshold, grouped
with AND/OR) into signals over realized OHLCV history, and translates those
signals into *paper-trading order intents* only.

Boundaries that this module deliberately enforces:

- It never places, modifies, or cancels an order, and never imports a broker
  client. `signal_to_order_intent()` returns keyword arguments that are valid
  for `services.paper_trading_v6.place_order`, and the caller decides whether
  to submit them to the paper engine. The intent is tagged
  `execution="paper_only"` so a live path can never be inferred from it.
- Metric names and their bases match `services.screener` wherever they overlap,
  so a field means the same thing in the screener and in a strategy.
- Signals are descriptive ("this rule matched on this bar"), never advice.

Metrics are computed per bar (the screener only needs the latest bar), which is
what makes historical evaluation and the Part F2 backtester possible.
"""

from __future__ import annotations

import json
import math
import sqlite3
from services.db.base import DatabaseInterface
from services.db.sqlite_impl import SQLiteDatabase
from services.db.factory import get_database
from services.db.configuration import postgres_selected
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence, cast

import pandas as pd

STRATEGY_FLAG = "strategy_builder"

MAX_CONDITIONS = 12
MAX_GROUPS = 4
MAX_SYMBOLS = 25
MAX_SAVED_STRATEGIES = 50
MIN_SESSIONS = 60

#: Comparators. The first six mirror `services.screener.OPERATORS` exactly; the
#: two crossing comparators are bar-to-bar and therefore only meaningful here.
OPERATORS: tuple[str, ...] = (
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "between",
    "cross_above",
    "cross_below",
)

CROSSING_OPERATORS = ("cross_above", "cross_below")

#: Metric catalogue. `requires` is the minimum number of sessions before the
#: metric is trustworthy; `basis` is shown in the UI so the user can see what a
#: rule is actually computed from.
STRATEGY_METRICS: dict[str, dict[str, Any]] = {
    "close": {"label": "Close", "unit": "inr", "requires": 1, "basis": "Session close."},
    "open": {"label": "Open", "unit": "inr", "requires": 1, "basis": "Session open."},
    "volume": {"label": "Volume", "unit": "shares", "requires": 1, "basis": "Session traded volume."},
    "change_pct_1d": {"label": "1-session change %", "unit": "percent", "requires": 2, "basis": "Close over previous close."},
    "change_pct_5d": {"label": "5-session change %", "unit": "percent", "requires": 6, "basis": "Close over close 5 sessions ago."},
    "change_pct_20d": {"label": "20-session change %", "unit": "percent", "requires": 21, "basis": "Close over close 20 sessions ago."},
    "gap_pct": {"label": "Gap %", "unit": "percent", "requires": 2, "basis": "Open over previous close."},
    "sma_20": {"label": "SMA 20", "unit": "inr", "requires": 20, "basis": "20-session simple moving average of close."},
    "sma_50": {"label": "SMA 50", "unit": "inr", "requires": 50, "basis": "50-session simple moving average of close."},
    "sma_200": {"label": "SMA 200", "unit": "inr", "requires": 200, "basis": "200-session simple moving average of close."},
    "close_vs_sma20_pct": {"label": "Close vs SMA 20 %", "unit": "percent", "requires": 20, "basis": "Close divided by SMA 20."},
    "close_vs_sma50_pct": {"label": "Close vs SMA 50 %", "unit": "percent", "requires": 50, "basis": "Close divided by SMA 50."},
    "close_vs_sma200_pct": {"label": "Close vs SMA 200 %", "unit": "percent", "requires": 200, "basis": "Close divided by SMA 200."},
    "rsi_14": {"label": "RSI 14", "unit": "index", "requires": 15, "basis": "Wilder RSI over 14 sessions."},
    "atr_pct_14": {"label": "ATR 14 %", "unit": "percent", "requires": 15, "basis": "Average true range over 14 sessions, as a percentage of close."},
    "volume_vs_20d_avg": {"label": "Volume vs 20-session average", "unit": "ratio", "requires": 21, "basis": "Volume divided by its trailing 20-session mean."},
    "macd_hist": {"label": "MACD histogram", "unit": "inr", "requires": 35, "basis": "MACD(12,26) minus its 9-session signal line."},
    "distance_from_52w_high_pct": {"label": "Distance from 52-week high %", "unit": "percent", "requires": 60, "basis": "Close against the highest close in the trailing 252 sessions."},
}

STRATEGY_DISCLOSURES: tuple[str, ...] = (
    "Strategy rules are evaluated on realized OHLCV history only. This is not a forecast.",
    "Signals describe rule matches. They are not investment advice or a recommendation to transact.",
    "Signals can only be routed to the paper-trading simulator. No live broker order path exists in this application.",
    "Rules evaluated on history carry look-ahead-free but survivorship-unaware assumptions; treat results as educational.",
)


class StrategyError(ValueError):
    """Raised with a stable machine-readable code for API surfacing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def describe_metrics() -> list[dict[str, Any]]:
    """Metric catalogue for the condition builder UI."""
    return [
        {
            "name": name,
            "label": meta["label"],
            "unit": meta["unit"],
            "sessions_required": meta["requires"],
            "basis": meta["basis"],
        }
        for name, meta in STRATEGY_METRICS.items()
    ]


def describe_operators() -> list[dict[str, Any]]:
    return [
        {"name": "gt", "label": "is greater than", "arity": 1},
        {"name": "gte", "label": "is greater than or equal to", "arity": 1},
        {"name": "lt", "label": "is less than", "arity": 1},
        {"name": "lte", "label": "is less than or equal to", "arity": 1},
        {"name": "eq", "label": "equals", "arity": 1},
        {"name": "between", "label": "is between", "arity": 2},
        {"name": "cross_above", "label": "crosses above", "arity": 1},
        {"name": "cross_below", "label": "crosses below", "arity": 1},
    ]


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _normalize_ohlcv(frame: Any) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise StrategyError("strategy_history_unavailable", "No usable OHLCV history was returned.")
    data = frame.copy()
    data.columns = [str(column).strip().lower() for column in data.columns]
    if "close" not in data.columns:
        raise StrategyError("strategy_history_unavailable", "History is missing a close column.")
    for column in ("open", "high", "low", "volume"):
        if column not in data.columns:
            data[column] = data["close"] if column != "volume" else 0.0
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["close"])
    if data.empty:
        raise StrategyError("strategy_history_unavailable", "History contained no numeric closes.")
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.dropna(subset=["date"]).sort_values("date").set_index("date")
    else:
        try:
            data.index = pd.to_datetime(data.index, errors="coerce")
            data = data[data.index.notna()].sort_index()
        except (TypeError, ValueError):
            pass
    return data


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI, matching `services.screener._rsi` so the field agrees."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100.0)


def metric_frame(frame: Any) -> pd.DataFrame:
    """Return a per-bar DataFrame of every metric in `STRATEGY_METRICS`.

    Values are `NaN` until the metric's `requires` window is satisfied, so a
    rule can never silently match on a half-formed indicator.
    """
    data = _normalize_ohlcv(frame)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    out = pd.DataFrame(index=data.index)
    out["close"] = close
    out["open"] = data["open"]
    out["volume"] = volume
    out["change_pct_1d"] = close.pct_change(1) * 100
    out["change_pct_5d"] = close.pct_change(5) * 100
    out["change_pct_20d"] = close.pct_change(20) * 100
    out["gap_pct"] = (data["open"] / close.shift(1) - 1) * 100

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    out["sma_20"] = sma20
    out["sma_50"] = sma50
    out["sma_200"] = sma200
    out["close_vs_sma20_pct"] = (close / sma20 - 1) * 100
    out["close_vs_sma50_pct"] = (close / sma50 - 1) * 100
    out["close_vs_sma200_pct"] = (close / sma200 - 1) * 100

    out["rsi_14"] = _rsi(close, 14)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14).mean()
    out["atr_pct_14"] = (atr / close) * 100

    volume_avg = volume.rolling(20).mean()
    out["volume_vs_20d_avg"] = volume / volume_avg.replace(0.0, float("nan"))

    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_line = ema12 - ema26
    out["macd_hist"] = macd_line - macd_line.ewm(span=9, adjust=False, min_periods=9).mean()

    rolling_high = close.rolling(252, min_periods=60).max()
    out["distance_from_52w_high_pct"] = (close / rolling_high - 1) * 100

    return out.replace([float("inf"), float("-inf")], float("nan"))


def _normalize_condition(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StrategyError("strategy_condition_invalid", f"Condition {index + 1} must be an object.")
    metric = str(raw.get("metric") or "").strip()
    if metric not in STRATEGY_METRICS:
        raise StrategyError(
            "strategy_metric_unknown",
            f"Condition {index + 1} uses unknown metric '{metric}'.",
        )
    operator = str(raw.get("operator") or raw.get("op") or "").strip().lower()
    if operator not in OPERATORS:
        raise StrategyError(
            "strategy_operator_unknown",
            f"Condition {index + 1} uses unknown comparator '{operator}'.",
        )

    compare_metric = raw.get("compare_metric")
    condition: dict[str, Any] = {"metric": metric, "operator": operator}

    if compare_metric:
        compare_metric = str(compare_metric).strip()
        if compare_metric not in STRATEGY_METRICS:
            raise StrategyError(
                "strategy_metric_unknown",
                f"Condition {index + 1} compares against unknown metric '{compare_metric}'.",
            )
        if operator == "between":
            raise StrategyError(
                "strategy_condition_invalid",
                f"Condition {index + 1}: 'between' needs two numeric bounds, not another metric.",
            )
        condition["compare_metric"] = compare_metric
        return condition

    if operator == "between":
        bounds = raw.get("values") if raw.get("values") is not None else raw.get("value")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise StrategyError(
                "strategy_condition_invalid",
                f"Condition {index + 1}: 'between' requires exactly two values.",
            )
        low, high = (_round(bounds[0]), _round(bounds[1]))
        if low is None or high is None:
            raise StrategyError("strategy_condition_invalid", f"Condition {index + 1} bounds must be numeric.")
        if low > high:
            low, high = high, low
        condition["values"] = [low, high]
        return condition

    value = _round(raw.get("value"))
    if value is None:
        raise StrategyError(
            "strategy_condition_invalid",
            f"Condition {index + 1} requires a numeric value or a compare_metric.",
        )
    condition["value"] = value
    return condition


def _normalize_group(raw: Any, group_index: int, counter: list[int]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StrategyError("strategy_group_invalid", f"Group {group_index + 1} must be an object.")
    join = str(raw.get("join") or "and").strip().lower()
    if join not in ("and", "or"):
        raise StrategyError("strategy_group_invalid", f"Group {group_index + 1} join must be 'and' or 'or'.")
    raw_conditions = raw.get("conditions")
    if not isinstance(raw_conditions, (list, tuple)) or not raw_conditions:
        raise StrategyError("strategy_group_invalid", f"Group {group_index + 1} needs at least one condition.")
    conditions = []
    for item in raw_conditions:
        counter[0] += 1
        if counter[0] > MAX_CONDITIONS:
            raise StrategyError(
                "strategy_conditions_too_many",
                f"A strategy may use at most {MAX_CONDITIONS} conditions.",
            )
        conditions.append(_normalize_condition(item, counter[0] - 1))
    return {"join": join, "conditions": conditions}


def _normalize_side(raw: Any, label: str) -> dict[str, Any]:
    if isinstance(raw, dict) and "groups" in raw:
        join = str(raw.get("join") or "and").strip().lower()
        raw_groups = raw.get("groups")
    elif isinstance(raw, (list, tuple)):
        join = "and"
        raw_groups = raw
    elif isinstance(raw, dict):
        join = "and"
        raw_groups = [raw]
    else:
        raise StrategyError("strategy_rules_invalid", f"The {label} rules are missing or malformed.")
    if join not in ("and", "or"):
        raise StrategyError("strategy_rules_invalid", f"The {label} join must be 'and' or 'or'.")
    if not isinstance(raw_groups, (list, tuple)) or not raw_groups:
        raise StrategyError("strategy_rules_invalid", f"The {label} rules need at least one group.")
    if len(raw_groups) > MAX_GROUPS:
        raise StrategyError("strategy_groups_too_many", f"At most {MAX_GROUPS} groups per side are supported.")
    counter = [0]
    groups = [_normalize_group(group, index, counter) for index, group in enumerate(raw_groups)]
    return {"join": join, "groups": groups}


def compile_strategy(payload: Any) -> dict[str, Any]:
    """Validate and normalize a builder payload into a stored definition."""
    if not isinstance(payload, dict):
        raise StrategyError("strategy_definition_invalid", "A strategy definition object is required.")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise StrategyError("strategy_name_required", "A strategy name is required.")
    if len(name) > 80:
        raise StrategyError("strategy_name_invalid", "Strategy names are limited to 80 characters.")

    symbols_raw = payload.get("symbols") or []
    if isinstance(symbols_raw, str):
        symbols_raw = [symbols_raw]
    symbols: list[str] = []
    for item in symbols_raw:
        label = str(item or "").strip().upper()
        if label and label not in symbols:
            symbols.append(label)
    if not symbols:
        raise StrategyError("strategy_symbols_required", "At least one symbol is required.")
    if len(symbols) > MAX_SYMBOLS:
        raise StrategyError("strategy_symbols_too_many", f"At most {MAX_SYMBOLS} symbols are supported.")

    entry = _normalize_side(payload.get("entry"), "entry")
    exit_raw = payload.get("exit")
    exit_rules = _normalize_side(exit_raw, "exit") if exit_raw else None

    quantity = _round(payload.get("quantity") or 1, 4) or 1.0
    if quantity <= 0:
        raise StrategyError("strategy_quantity_invalid", "Quantity must be positive.")

    stop_loss_pct = _round(payload.get("stop_loss_pct"))
    target_pct = _round(payload.get("target_pct"))
    for label, value in (("stop_loss_pct", stop_loss_pct), ("target_pct", target_pct)):
        if value is not None and not 0 < value <= 100:
            raise StrategyError("strategy_bounds_invalid", f"{label} must be between 0 and 100.")

    required = 1
    for side in (entry, exit_rules):
        if not side:
            continue
        for group in side["groups"]:
            for condition in group["conditions"]:
                required = max(required, int(STRATEGY_METRICS[condition["metric"]]["requires"]))
                compare = condition.get("compare_metric")
                if compare:
                    required = max(required, int(STRATEGY_METRICS[compare]["requires"]))

    return {
        "name": name,
        "symbols": symbols,
        "entry": entry,
        "exit": exit_rules,
        "quantity": quantity,
        "stop_loss_pct": stop_loss_pct,
        "target_pct": target_pct,
        "direction": "long_only",
        "instrument_type": "EQUITY",
        "execution": "paper_only",
        "sessions_required": max(required, MIN_SESSIONS),
    }


def _condition_label(condition: dict[str, Any]) -> str:
    metric = condition["metric"]
    operator = condition["operator"]
    if condition.get("compare_metric"):
        target: Any = condition["compare_metric"]
    elif operator == "between":
        target = f"{condition['values'][0]} and {condition['values'][1]}"
    else:
        target = condition["value"]
    words = {
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "eq": "==",
        "between": "between",
        "cross_above": "crosses above",
        "cross_below": "crosses below",
    }
    return f"{metric} {words[operator]} {target}"


def _condition_series(condition: dict[str, Any], metrics: pd.DataFrame) -> pd.Series:
    left = metrics[condition["metric"]]
    operator = condition["operator"]

    if condition.get("compare_metric"):
        right: pd.Series = metrics[condition["compare_metric"]]
    elif operator == "between":
        low, high = condition["values"]
        return cast(pd.Series, (left >= low) & (left <= high) & left.notna())
    else:
        right = pd.Series(condition["value"], index=metrics.index, dtype="float64")

    valid = left.notna() & right.notna()

    if operator in CROSSING_OPERATORS:
        previous_left = left.shift(1)
        previous_right = right.shift(1)
        valid = valid & previous_left.notna() & previous_right.notna()
        if operator == "cross_above":
            crossed = (previous_left <= previous_right) & (left > right)
        else:
            crossed = (previous_left >= previous_right) & (left < right)
        return cast(pd.Series, crossed & valid)

    if operator == "gt":
        return cast(pd.Series, (left > right) & valid)
    if operator == "gte":
        return cast(pd.Series, (left >= right) & valid)
    if operator == "lt":
        return cast(pd.Series, (left < right) & valid)
    if operator == "lte":
        return cast(pd.Series, (left <= right) & valid)
    return cast(pd.Series, (left - right).abs().le(1e-9) & valid)


def _side_series(side: dict[str, Any], metrics: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    labels: list[str] = []
    group_series: list[pd.Series] = []
    for group in side["groups"]:
        series_list = []
        for condition in group["conditions"]:
            series_list.append(_condition_series(condition, metrics))
            labels.append(_condition_label(condition))
        combined = series_list[0]
        for extra in series_list[1:]:
            combined = (combined | extra) if group["join"] == "or" else (combined & extra)
        group_series.append(combined)
    result = group_series[0]
    for extra in group_series[1:]:
        result = (result | extra) if side["join"] == "or" else (result & extra)
    return result.fillna(False), labels


def evaluate_strategy(
    strategy: dict[str, Any],
    frame: Any,
    *,
    symbol: str | None = None,
    max_signals: int = 200,
) -> dict[str, Any]:
    """Evaluate a compiled strategy over one symbol's history.

    Returns matched entry/exit bars in chronological order. Position state is
    tracked so a long-only strategy cannot emit two consecutive entries.
    """
    metrics = metric_frame(frame)
    sessions = int(len(metrics))
    required = int(strategy.get("sessions_required") or MIN_SESSIONS)
    if sessions < required:
        return {
            "symbol": symbol,
            "evaluated": False,
            "reason": "insufficient_history",
            "sessions_available": sessions,
            "sessions_required": required,
            "signals": [],
            "disclosures": list(STRATEGY_DISCLOSURES),
        }

    entry_series, entry_labels = _side_series(strategy["entry"], metrics)
    if strategy.get("exit"):
        exit_series, exit_labels = _side_series(strategy["exit"], metrics)
    else:
        exit_series = pd.Series(False, index=metrics.index)
        exit_labels = []

    stop_loss_pct = strategy.get("stop_loss_pct")
    target_pct = strategy.get("target_pct")

    signals: list[dict[str, Any]] = []
    in_position = False
    entry_price = 0.0

    for timestamp, row in metrics.iterrows():
        price = _round(row["close"], 4)
        if price is None:
            continue
        stamp = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        if in_position:
            reason = None
            if bool(exit_series.get(timestamp, False)):
                reason = "exit_rules_matched"
            elif stop_loss_pct and price <= entry_price * (1 - stop_loss_pct / 100):
                reason = "stop_loss"
            elif target_pct and price >= entry_price * (1 + target_pct / 100):
                reason = "target"
            if reason:
                signals.append(
                    {
                        "at": stamp,
                        "action": "SELL",
                        "intent": "exit",
                        "price": price,
                        "reason": reason,
                        "rules": exit_labels if reason == "exit_rules_matched" else [reason],
                    }
                )
                in_position = False
                entry_price = 0.0
                continue

        if not in_position and bool(entry_series.get(timestamp, False)):
            signals.append(
                {
                    "at": stamp,
                    "action": "BUY",
                    "intent": "entry",
                    "price": price,
                    "reason": "entry_rules_matched",
                    "rules": entry_labels,
                }
            )
            in_position = True
            entry_price = price

    trimmed = signals[-int(max_signals):] if max_signals and len(signals) > int(max_signals) else signals
    last_index = metrics.index[-1]
    return {
        "symbol": symbol,
        "evaluated": True,
        "sessions_available": sessions,
        "sessions_required": required,
        "as_of": last_index.isoformat() if hasattr(last_index, "isoformat") else str(last_index),
        "open_position": in_position,
        "signal_count": len(signals),
        "signals": trimmed,
        "latest_signal": trimmed[-1] if trimmed else None,
        "entry_rules": entry_labels,
        "exit_rules": exit_labels,
        "evidence": {
            "basis": "Realized OHLCV history only.",
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(STRATEGY_DISCLOSURES),
    }


def signal_to_order_intent(
    signal: dict[str, Any],
    *,
    symbol: str,
    quantity: float,
    instrument_type: str = "EQUITY",
) -> dict[str, Any]:
    """Translate a signal into `paper_trading_v6.place_order` keyword arguments.

    This returns data only. It never calls the paper engine, and there is no
    live-broker equivalent of this function anywhere in the codebase.
    """
    action = str(signal.get("action") or "").upper()
    if action not in ("BUY", "SELL"):
        raise StrategyError("strategy_signal_invalid", "Signal action must be BUY or SELL.")
    if float(quantity) <= 0:
        raise StrategyError("strategy_quantity_invalid", "Quantity must be positive.")
    return {
        "symbol": str(symbol).strip().upper(),
        "side": action,
        "quantity": float(quantity),
        "order_type": "MARKET",
        "instrument_type": str(instrument_type).upper(),
        "reasoning_notes": f"strategy_builder: {signal.get('reason', 'rule match')} at {signal.get('at')}",
        "execution": "paper_only",
        "routes_to": "services.paper_trading_v6.place_order",
    }


def run_strategy(
    strategy: dict[str, Any],
    *,
    history_loader: Callable[[str], Any],
    symbols: Sequence[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate a compiled strategy across its symbols."""
    universe = [str(item).strip().upper() for item in (symbols or strategy.get("symbols") or [])]
    universe = [item for item in dict.fromkeys(universe) if item]
    if not universe:
        raise StrategyError("strategy_symbols_required", "At least one symbol is required.")

    results: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for symbol in universe[:MAX_SYMBOLS]:
        try:
            frame = history_loader(symbol)
        except Exception as error:  # noqa: BLE001 - provider failures are data, not crashes
            excluded.append({"symbol": symbol, "reason": "history_unavailable", "detail": type(error).__name__})
            continue
        try:
            evaluation = evaluate_strategy(strategy, frame, symbol=symbol)
        except StrategyError as error:
            excluded.append({"symbol": symbol, "reason": error.code})
            continue
        if not evaluation.get("evaluated"):
            excluded.append(
                {
                    "symbol": symbol,
                    "reason": evaluation.get("reason", "insufficient_history"),
                    "sessions_available": evaluation.get("sessions_available"),
                }
            )
            continue
        results.append(evaluation)

    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "strategy": {
            "name": strategy.get("name"),
            "direction": strategy.get("direction", "long_only"),
            "execution": "paper_only",
            "quantity": strategy.get("quantity"),
        },
        "coverage": {
            "requested": len(universe),
            "evaluated": len(results),
            "excluded": excluded,
        },
        "results": results,
        "evidence": {
            "basis": "Realized OHLCV history only.",
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(STRATEGY_DISCLOSURES),
    }


class StrategyStore:
    """SQLite persistence for saved strategies.

    Mirrors `services.screener.ScreenerStore` so both surfaces behave the same.
    """

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection] | None = None) -> None:
        self._connection_factory = connection_factory

    def _connect(self) -> DatabaseInterface:
        if self._connection_factory is not None:
            return SQLiteDatabase(self._connection_factory())
        return get_database()

    def ensure_schema(self) -> None:
        connection = self._connect()
        try:
            if self._connection_factory is None and postgres_selected():
                connection.fetchall("SELECT id FROM strategy_definitions LIMIT 0")
                return
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_definitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def save(self, user_id: int, definition: dict[str, Any]) -> dict[str, Any]:
        self.ensure_schema()
        compiled = definition if definition.get("sessions_required") else compile_strategy(definition)
        stamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.begin_write("saved-strategies:" + str(int(user_id)))
            count_row = connection.fetchone(connection.sql(
                "SELECT COUNT(*) AS n FROM strategy_definitions WHERE user_id = ?"),
                (int(user_id),),
            )
            count = int(count_row["n"]) if count_row else 0
            existing = connection.fetchone(connection.sql(
                "SELECT id FROM strategy_definitions WHERE user_id = ? AND name = ?"),
                (int(user_id), compiled["name"]),
            )
            if existing is None and int(count) >= MAX_SAVED_STRATEGIES:
                raise StrategyError(
                    "strategy_limit_reached",
                    f"At most {MAX_SAVED_STRATEGIES} saved strategies are supported.",
                )
            payload = json.dumps(compiled)
            if existing is None:
                cursor = connection.execute(connection.sql(
                    "INSERT INTO strategy_definitions (user_id, name, definition, created_at, updated_at) VALUES (?,?,?,?,?) RETURNING id"),
                    (int(user_id), compiled["name"], payload, stamp, stamp),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    raise RuntimeError("Saved strategy did not receive an id.")
                strategy_id = int(inserted["id"])
            else:
                strategy_id = int(existing["id"])
                connection.execute(connection.sql(
                    "UPDATE strategy_definitions SET definition = ?, updated_at = ? WHERE id = ? AND user_id=?"),
                    (payload, stamp, strategy_id, int(user_id)),
                )
            connection.commit()
        finally:
            connection.close()
        return {"id": strategy_id, "name": compiled["name"], "definition": compiled, "updated_at": stamp}

    def list(self, user_id: int) -> list[dict[str, Any]]:
        self.ensure_schema()
        connection = self._connect()
        try:
            rows = connection.fetchall(connection.sql(
                "SELECT id, name, definition, created_at, updated_at FROM strategy_definitions WHERE user_id = ? ORDER BY updated_at DESC"),
                (int(user_id),),
            )
        finally:
            connection.close()
        items = []
        for row in rows:
            try:
                definition = json.loads(row["definition"])
            except (TypeError, ValueError):
                continue
            items.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "definition": definition,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return items

    def get(self, user_id: int, strategy_id: int) -> dict[str, Any]:
        self.ensure_schema()
        connection = self._connect()
        try:
            row = connection.fetchone(connection.sql(
                "SELECT id, name, definition, created_at, updated_at FROM strategy_definitions WHERE user_id = ? AND id = ?"),
                (int(user_id), int(strategy_id)),
            )
        finally:
            connection.close()
        if row is None:
            raise StrategyError("strategy_not_found", "No saved strategy with that id.")
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "definition": json.loads(row["definition"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete(self, user_id: int, strategy_id: int) -> bool:
        self.ensure_schema()
        connection = self._connect()
        try:
            cursor = connection.execute(connection.sql(
                "DELETE FROM strategy_definitions WHERE user_id = ? AND id = ?"),
                (int(user_id), int(strategy_id)),
            )
            connection.commit()
            return bool(cursor.rowcount > 0)
        finally:
            connection.close()


STRATEGIES = StrategyStore()


def starter_strategies() -> list[dict[str, Any]]:
    """Ready-made examples so the builder is not an empty canvas."""
    return [
        {
            "name": "Golden cross, RSI not overbought",
            "symbols": ["RELIANCE"],
            "entry": {
                "join": "and",
                "groups": [
                    {
                        "join": "and",
                        "conditions": [
                            {"metric": "sma_50", "operator": "cross_above", "compare_metric": "sma_200"},
                            {"metric": "rsi_14", "operator": "lt", "value": 70},
                        ],
                    }
                ],
            },
            "exit": {
                "join": "or",
                "groups": [
                    {
                        "join": "or",
                        "conditions": [
                            {"metric": "sma_50", "operator": "cross_below", "compare_metric": "sma_200"},
                            {"metric": "rsi_14", "operator": "gt", "value": 80},
                        ],
                    }
                ],
            },
            "quantity": 10,
            "stop_loss_pct": 8,
        },
        {
            "name": "Oversold bounce with volume",
            "symbols": ["TCS"],
            "entry": {
                "join": "and",
                "groups": [
                    {
                        "join": "and",
                        "conditions": [
                            {"metric": "rsi_14", "operator": "lt", "value": 32},
                            {"metric": "volume_vs_20d_avg", "operator": "gt", "value": 1.5},
                        ],
                    }
                ],
            },
            "exit": {
                "join": "or",
                "groups": [{"join": "or", "conditions": [{"metric": "rsi_14", "operator": "gt", "value": 55}]}],
            },
            "quantity": 5,
            "target_pct": 6,
            "stop_loss_pct": 4,
        },
    ]
