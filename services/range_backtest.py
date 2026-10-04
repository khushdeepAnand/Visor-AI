"""Walk-forward range-forecast backtest and naive-beat gate.

Honest backtesting harness (Streak/AlgoTest-style) for the *range* forecaster:

- expanding-window chronological replay, no shuffling, no future leakage;
- each origin fits the model only on bars up to and including the origin, then
  forecasts the bar ``horizon`` sessions ahead;
- trading fills happen at the **next bar open** after the origin (after the
  model's information set closes), sized as one share, with spread + slippage +
  charges applied on both fills; exits at the target close;
- accuracy is scored independently of the trade sim: coverage of the target
  close inside the published range, directional agreement with the realised
  move, and MAE/MASE relative to the naive persistence forecast;
- the gate enforces the trust contract on the unseen replay (or the report says
  FAIL): (a) the interval must be *calibrated* — empirical coverage inside the
  nominal band (e.g. 8 of 10 actuals inside an 80% interval, and not so wide
  that it becomes useless); and (b) the "mind the naive" rule — the midpoint
  must never be worse than naive persistence (default floor 0%; a positive
  floor can be configured once a model demonstrates genuine edge). No number
  here is invented: every statistic is computed from the replayed origins.

This module never places an order.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable, Sequence, cast

import numpy as np
import pandas as pd

from services.historical_replay import DEFAULT_SLIPPAGE_BPS, DEFAULT_SPREAD_BPS

#: Least number of bars before an origin so the expanding window is meaningful.
MIN_TRAIN_BARS = 40

#: Default naive-beat floor: the gate must never publish ranges whose midpoint
#: is worse than naive persistence (0%). A positive floor (e.g. 0.10) can be
#: configured once a model demonstrates genuine edge on unseen replays.
DEFAULT_NAIVE_IMPROVEMENT_FLOOR = 0.0

#: Nominal coverage the published interval should achieve (0.80 = "8 of 10").
DEFAULT_NOMINAL_COVERAGE = 0.80

#: Allowed deviation of empirical walk-forward coverage from nominal before the
#: gate fails: too low = fabricated precision, too high = ranges too wide to use.
DEFAULT_COVERAGE_TOLERANCE = 0.15

#: Origins required before a gate can be decided.
MIN_GATE_SAMPLES = 8

#: Fraction of origins that must produce a usable (non-abstained) range.
MIN_USABLE_FRACTION = 0.6

DISCLOSURES: tuple[str, ...] = (
    "Walk-forward replay expands the fit window chronologically and never uses future bars at an origin.",
    "Fills are simulated at the next bar open plus spread, slippage and charges; real fills will differ.",
    "Trade sizing is one share and ignores corporate actions, taxes and financing; results are indicative only.",
    "Past rule behaviour is not a forecast of future results.",
)

Forecaster = Callable[[str, pd.DataFrame], dict[str, Any]]


class BacktestGateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_frame(frame: Any) -> pd.DataFrame:
    """Title-case or lowercase OHLCV frame onto a DatetimeIndex Close series."""
    if frame is None or not isinstance(frame, pd.DataFrame):
        raise BacktestGateError("backtest_frame_invalid", "A history frame is required.")
    working = frame.copy()
    renamed = {str(column): str(column).strip().lower() for column in working.columns}
    working = working.rename(columns=renamed)
    required = {"open", "high", "low", "close"}
    missing = [name for name in required if name not in working.columns]
    if missing:
        raise BacktestGateError(
            "backtest_frame_invalid",
            f"History is missing required columns: {', '.join(sorted(missing))}.",
        )
    for column in ("open", "high", "low", "close"):
        working[column] = pd.to_numeric(working[column], errors="coerce")
    if not isinstance(working.index, pd.DatetimeIndex):
        try:
            working.index = pd.to_datetime(working.index)
        except (TypeError, ValueError):
            raise BacktestGateError("backtest_frame_invalid", "History must carry a date index.")
    working = working[~working.index.duplicated(keep="last")].sort_index()
    working = working[
        (working["open"] > 0) & (working["high"] > 0) & (working["low"] > 0) & (working["close"] > 0)
    ]
    return cast(pd.DataFrame, working)


def walk_forward_origins(n: int, horizon: int, *, max_origins: int = 24, min_train: int = MIN_TRAIN_BARS) -> list[int]:
    """Chronological expanding-window origins over the last available bars."""
    n = int(n)
    horizon = max(1, int(horizon))
    if n < min_train + horizon + 1:
        return []
    allowed = list(range(min_train, n - horizon))
    if not allowed:
        return []
    step = max(1, int(math.ceil(len(allowed) / max(1, int(max_origins)))))
    origins = list(allowed[::step])
    # Always include the most recent allowed origin so the replay reaches the present.
    if origins[-1] != allowed[-1]:
        origins.append(allowed[-1])
    return origins


def _fill_price(price: float, side: str, *, spread_bps: float, slippage_bps: float, charges_bps: float) -> float:
    """Buy above / sell below the reference by cost bps, matching the cost contract."""
    cost = (float(spread_bps) / 2.0 + float(slippage_bps) + float(charges_bps)) / 10_000.0
    return price * (1.0 + cost) if side == "BUY" else price * (1.0 - cost)


def _forecast_view(prediction: dict[str, Any], last_close: float, horizon: int) -> dict[str, Any]:
    """Pull the horizon-targeted range + direction from a forecast result."""
    if not isinstance(prediction, dict):
        return {"usable": False, "reason": "prediction_not_a_dict"}
    forecast: dict[str, Any] = {}
    if isinstance(prediction.get("forecast"), dict):
        if horizon == 1:
            forecast = prediction["forecast"]
        else:
            for entry in (prediction.get("multi_horizon") or {}).get("horizons", []):
                if int(entry.get("sessions") or 0) == horizon and isinstance(entry.get("forecast"), dict):
                    forecast = entry["forecast"]
                    break
            if not forecast:
                return {"usable": False, "reason": "requested_horizon_unavailable"}
    else:
        forecast = prediction
    low, median, high = forecast.get("low"), forecast.get("median"), forecast.get("high")
    if low is None or high is None or median is None:
        return {"usable": False, "reason": "range_missing"}
    try:
        low, median, high = float(low), float(median), float(high)
    except (TypeError, ValueError):
        return {"usable": False, "reason": "range_not_numeric"}
    if not (math.isfinite(low) and math.isfinite(median) and math.isfinite(high)) or high <= low:
        return {"usable": False, "reason": "range_invalid"}
    direction = 0
    if math.isfinite(last_close) and last_close > 0:
        direction = 1 if median > last_close else (-1 if median < last_close else 0)
    return {
        "usable": True,
        "low": low,
        "median": median,
        "high": high,
        "direction": direction,
        "confidence_level": forecast.get("confidence_level"),
    }


def _mean(values: Sequence[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return float(np.mean(finite)) if finite else None


def _max_drawdown_pct(equity: Sequence[float]) -> float | None:
    peak = None
    worst = 0.0
    for value in equity:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            worst = min(worst, (value - peak) / peak * 100.0)
    return round(worst, 4) if peak is not None else None


def walk_forward_metrics(
    *,
    symbol: str,
    frame: Any,
    forecaster: Forecaster,
    horizon: int = 1,
    confidence: float = 0.80,
    spread_bps: float = DEFAULT_SPREAD_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    charges_bps: float = 0.0,
    max_origins: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay the model over expanding windows and score accuracy + trades.

    The forecaster receives the exact ``frame`` slices the deployment would feed
    it (e.g. ``forecast_range`` expects title-case OHLCV columns), untouched by
    the harness's internal normalization. Scoring uses fully normalised values,
    so either column style may be supplied.
    """
    history = normalize_frame(frame)
    n = len(history)
    horizon = max(1, int(horizon))
    origins = walk_forward_origins(n, horizon, max_origins=max_origins)
    if not origins:
        raise BacktestGateError(
            "backtest_insufficient_history",
            f"At least {MIN_TRAIN_BARS + horizon + 1} sessions are required for a walk-forward replay; {n} were available.",
        )

    open_vals = history["open"].to_numpy(dtype=float)
    close_vals = history["close"].to_numpy(dtype=float)
    high_vals = history["high"].to_numpy(dtype=float)
    low_vals = history["low"].to_numpy(dtype=float)
    index = history.index

    records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []
    naive_errors: list[float] = []
    model_errors: list[float] = []
    coverage_hits = 0
    containment_hits = 0
    containment_decidable = 0
    direction_correct = 0
    direction_decidable = 0
    naive_direction_correct = 0
    naive_direction_decidable = 0
    usable = 0
    equity: list[float] = []
    running = 0.0

    for origin in origins:
        item: dict[str, Any] = {"origin": origin, "origin_at": index[origin].isoformat()}
        last_close = close_vals[origin]
        naive = last_close
        target_close = close_vals[origin + horizon]
        naive_error = abs(target_close - naive)
        naive_errors.append(naive_error)
        item["naive_persistence"] = round(naive, 4)
        item["target_close"] = round(target_close, 4)
        item["realised_move_pct"] = round((target_close / last_close - 1.0) * 100.0, 4)

        prior_close = close_vals[origin - horizon] if origin - horizon >= 0 else close_vals[0]
        # Naive persistence benchmark direction: sign of the most recent drift.
        if last_close != prior_close:
            drift = 1 if last_close > prior_close else -1
            actual = 1 if target_close > last_close else (-1 if target_close < last_close else 0)
            naive_direction_decidable += 1
            naive_direction_correct += int(actual == drift and actual != 0)

        try:
            prediction = forecaster(symbol, frame.iloc[: origin + 1])
        except Exception as error:
            item["usable"] = False
            item["reason"] = f"forecaster_error:{type(error).__name__}"
            records.append(item)
            continue
        view = _forecast_view(prediction, last_close, horizon)
        item.update({key: view[key] for key in ("low", "median", "high", "direction", "usable") if key in view})
        item["abstained"] = not view["usable"]
        if not view["usable"]:
            item["reason"] = view.get("reason")
            records.append(item)
            continue
        usable += 1
        model_error = abs(target_close - view["median"])
        model_errors.append(model_error)
        hit = view["low"] <= target_close <= view["high"]
        coverage_hits += int(hit)
        item["coverage_hit"] = hit
        item["model_error"] = round(model_error, 4)
        item["target_timestamp"] = index[origin + horizon].isoformat()

        # Intraday range containment: the highest high and lowest low actually
        # traded across the forecast window must both stay inside the predicted
        # band. This is the sharpness-anchored counterpart to close coverage —
        # a band can cover every close yet still be useless intraday, and this
        # metric exposes that directly.
        exposure = slice(origin + 1, origin + horizon + 1)
        next_high = float(np.max(high_vals[exposure]))
        next_low = float(np.min(low_vals[exposure]))
        containment_hit = next_low >= view["low"] and next_high <= view["high"]
        containment_decidable += 1
        containment_hits += int(containment_hit)
        item["containment_hit"] = containment_hit
        item["actual_range"] = {"low": round(next_low, 4), "high": round(next_high, 4)}

        actual_direction = 1 if target_close > last_close else (-1 if target_close < last_close else 0)
        if view["direction"] != 0:
            direction_decidable += 1
            direction_correct += int(actual_direction == view["direction"])

        # Next-bar-open fills, one share, long/short around the model direction.
        entry_open = open_vals[origin + 1] if origin + 1 < n else last_close
        item["entry_fill"] = round(_fill_price(entry_open, "BUY" if view["direction"] >= 0 else "SELL", spread_bps=spread_bps, slippage_bps=slippage_bps, charges_bps=charges_bps), 4)
        if view["direction"] == 0:
            item["trade"] = None
        else:
            exit_fill = _fill_price(target_close, "SELL" if view["direction"] >= 0 else "BUY", spread_bps=spread_bps, slippage_bps=slippage_bps, charges_bps=charges_bps)
            gross = abs(target_close - entry_open)
            net = (exit_fill - item["entry_fill"]) if view["direction"] >= 0 else (item["entry_fill"] - exit_fill)
            running += net
            equity.append(running)
            item["trade"] = {
                "direction": "LONG" if view["direction"] > 0 else "SHORT",
                "entry_at": index[origin + 1].isoformat(),
                "exit_at": index[origin + horizon].isoformat(),
                "entry_open": round(entry_open, 4),
                "exit_close": round(target_close, 4),
                "entry_fill": item["entry_fill"],
                "exit_fill": round(exit_fill, 4),
                "gross_pnl": round(gross, 4),
                "costs": round(gross - net, 4),
                "net_pnl": round(net, 4),
                "net_return_pct": round((net / max(entry_open, 1e-9)) * 100.0, 4),
            }
            trade_records.append(item["trade"])
        records.append(item)

    if usable == 0:
        raise BacktestGateError("backtest_no_usable_ranges", "No origin produced a usable range; the gate cannot be decided.")

    model_mae = _mean(model_errors) or 0.0
    naive_mae = _mean(naive_errors) or 0.0
    mase = (model_mae / naive_mae) if naive_mae and naive_mae > 0 else None
    improvement = ((naive_mae - model_mae) / naive_mae * 100.0) if naive_mae and naive_mae > 0 else 0.0
    wins = [trade for trade in trade_records if trade["net_pnl"] > 0]
    returns = [trade["net_return_pct"] for trade in trade_records]
    return_mean = _mean(returns)
    return_std = float(np.std(returns)) if len(returns) > 1 else 0.0
    trade_sharpe = None
    if return_mean is not None and return_std and return_std > 0:
        trade_sharpe = round((return_mean / return_std) * math.sqrt(len(returns)), 4)

    return {
        "symbol": symbol,
        "horizon": horizon,
        "confidence_level": round(float(confidence), 4),
        "origins": len(origins),
        "usable_origins": usable,
        "abstained_origins": len(origins) - usable,
        "walk_forward": {
            "method": "expanding_window_chronological_no_shuffle",
            "min_train_bars": MIN_TRAIN_BARS,
            "fills": "next_bar_open_plus_costs",
            "costs_bps": {
                "spread_bps": round(float(spread_bps), 2),
                "slippage_bps": round(float(slippage_bps), 2),
                "charges_bps": round(float(charges_bps), 2),
            },
        },
        "accuracy": {
            "empirical_coverage": round(coverage_hits / usable, 6),
            "coverage_hits": coverage_hits,
            "range_containment_coverage": round(containment_hits / containment_decidable, 6) if containment_decidable else None,
            "containment_hits": containment_hits,
            "containment_denominator": containment_decidable,
            "directional_accuracy": round(direction_correct / direction_decidable, 6) if direction_decidable else None,
            "naive_directional_accuracy": round(naive_direction_correct / naive_direction_decidable, 6) if naive_direction_decidable else None,
            "model_mae": round(model_mae, 6),
            "naive_mae": round(naive_mae, 6),
            "mase": round(float(mase), 6) if mase is not None else None,
            "mae_improvement_vs_naive_pct": round(improvement, 4),
            "beats_naive_baseline": bool(model_mae < naive_mae),
        },
        "trades": {
            "count": len(trade_records),
            "wins": len(wins),
            "losses": len(trade_records) - len(wins),
            "win_rate_pct": round(len(wins) / len(trade_records) * 100.0, 4) if trade_records else None,
            "gross_pnl": round(sum(trade["gross_pnl"] for trade in trade_records), 4),
            "costs": round(sum(trade["costs"] for trade in trade_records), 4),
            "net_pnl": round(running, 4),
            "trade_sharpe": trade_sharpe,
            "max_drawdown_pct": _max_drawdown_pct(equity),
            "filled_as": "next_bar_open",
        },
        "replay": records,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "disclosures": list(DISCLOSURES),
        "evidence": {
            "basis": "Expanding-window walk-forward replay over realised history; not a forecast.",
            "is_forecast": False,
            "is_recommendation": False,
        },
    }


def naive_beat_gate(
    metrics: dict[str, Any],
    *,
    improvement_floor: float = DEFAULT_NAIVE_IMPROVEMENT_FLOOR,
    min_samples: int = MIN_GATE_SAMPLES,
    nominal_coverage: float = DEFAULT_NOMINAL_COVERAGE,
    coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE,
) -> dict[str, Any]:
    """Decide the deployment gate from a walk-forward metrics payload.

    The gate enforces both halves of the trust contract:

    * calibration: the published interval must land near its nominal coverage
      (e.g. 8 of 10 actuals inside an 80% interval) — neither under-covering
      (fabricated precision) nor over-covering (ranges too wide to be useful);
    * mind-the-naive: the midpoint must never be worse than naive persistence
      (default floor 0%; a positive floor can be configured once a model has
      demonstrated genuine edge on unseen replays).
    """
    accuracy = metrics.get("accuracy") or {}
    usable = int(metrics.get("usable_origins") or 0)
    improvement = float(accuracy.get("mae_improvement_vs_naive_pct") or 0.0)
    beats = bool(accuracy.get("beats_naive_baseline"))
    coverage = float(accuracy.get("empirical_coverage") or 0.0)
    nominal = float(nominal_coverage)
    tolerance = float(coverage_tolerance)
    coverage_low = nominal - tolerance
    coverage_high = nominal + tolerance
    floor_hit = improvement >= improvement_floor * 100.0
    coverage_ok = coverage_low <= coverage <= coverage_high
    enough_samples = usable >= min_samples
    usable_fraction = usable / max(1, int(metrics.get("origins") or 0))
    usable_ok = usable_fraction >= MIN_USABLE_FRACTION
    passed = bool(floor_hit and coverage_ok and enough_samples and usable_ok)
    reasons: list[str] = []
    if not floor_hit:
        reasons.append(
            f"Midpoint is {abs(improvement):.2f}% {'worse' if improvement < 0 else 'short'} "
            f"of naive persistence; floor is >= {improvement_floor * 100:.0f}% improvement."
        )
    if not coverage_ok:
        reasons.append(
            f"Empirical coverage {coverage:.1%} is outside the required band "
            f"[{coverage_low:.0%}, {coverage_high:.0%}] for a nominal {nominal:.0%} interval."
        )
    if not enough_samples:
        reasons.append(f"Only {usable} usable replays are available; at least {min_samples} are required.")
    if not usable_ok:
        reasons.append(f"Only {usable_fraction:.0%} of origins produced usable ranges; at least {MIN_USABLE_FRACTION:.0%} are required.")
    return {
        "gate": "naive_beat_by_floor",
        "passed": passed,
        "requirement": (
            f"calibrated {nominal:.0%} interval (empirical coverage in "
            f"[{coverage_low:.0%}, {coverage_high:.0%}]) and midpoint >= {improvement_floor * 100:.0f}% "
            f"better than naive persistence on {min_samples}+ usable walk-forward origins"
        ),
        "improvement_pct": round(improvement, 4),
        "beats_naive": beats,
        "coverage_ok": coverage_ok,
        "empirical_coverage": round(coverage, 4),
        "usable_origins": usable,
        "min_samples": min_samples,
        "reasons": reasons,
    }


def range_model_gate_report(
    symbol: str,
    frame: Any,
    forecaster: Forecaster,
    *,
    horizon: int = 1,
    confidence: float = 0.80,
    improvement_floor: float = DEFAULT_NAIVE_IMPROVEMENT_FLOOR,
    nominal_coverage: float = DEFAULT_NOMINAL_COVERAGE,
    coverage_tolerance: float = DEFAULT_COVERAGE_TOLERANCE,
    spread_bps: float = DEFAULT_SPREAD_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    charges_bps: float = 1.0,
    max_origins: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the replay and attach the gate decision in one call."""
    report = walk_forward_metrics(
        symbol=symbol,
        frame=frame,
        forecaster=forecaster,
        horizon=horizon,
        confidence=confidence,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        charges_bps=charges_bps,
        max_origins=max_origins,
        now=now,
    )
    report["gate"] = naive_beat_gate(
        report,
        improvement_floor=improvement_floor,
        nominal_coverage=nominal_coverage,
        coverage_tolerance=coverage_tolerance,
    )
    return report


__all__: Sequence[str] = (
    "DEFAULT_NAIVE_IMPROVEMENT_FLOOR",
    "DEFAULT_NOMINAL_COVERAGE",
    "DEFAULT_COVERAGE_TOLERANCE",
    "DISCLOSURES",
    "MIN_GATE_SAMPLES",
    "MIN_TRAIN_BARS",
    "BacktestGateError",
    "naive_beat_gate",
    "normalize_frame",
    "range_model_gate_report",
    "walk_forward_metrics",
    "walk_forward_origins",
)
