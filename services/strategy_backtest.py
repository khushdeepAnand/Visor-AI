"""Backtesting for builder strategies and multi-leg option structures (v9 Part F2).

Two entry points:

- `backtest_strategy()` replays a compiled `services.strategy_builder` strategy
  over realized OHLCV history (long-only, one position at a time).
- `backtest_multi_leg()` replays a repeated option structure across expiry
  cycles.

Honesty constraints baked into the output:

- Every result reports `gross_pnl`, `costs`, and `net_pnl` separately, matching
  the cost contract already used elsewhere in the app (`FORECAST_CONTRACT.md`).
  No new or renamed cost fields are introduced.
- The multi-leg backtester has **no historical option chains** to price
  against, because this application does not store them. It therefore prices
  entries with the existing Black-Scholes model in
  `derivatives/options_engine.py` and reports
  `pricing_basis="model_priced_black_scholes"` with `is_chain_priced=False`.
  That limitation is surfaced in the payload, not hidden in a docstring.
- Settlement reuses `derivatives.payoff`'s audited per-leg expiry maths rather
  than re-deriving it, so a payoff diagram and a backtest can never disagree.
- Nothing here places an order. Fills are simulated.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import pandas as pd

from derivatives.options_engine import black_scholes

# Deliberate reuse of the audited expiry helpers so backtest settlement and the
# payoff diagram can never diverge. Both are module-private by convention only.
from derivatives.payoff import _leg_payoff as leg_expiry_payoff
from derivatives.payoff import normalize_leg, build_payoff, complete_strategy_status
from services.historical_replay import DEFAULT_SLIPPAGE_BPS, DEFAULT_SPREAD_BPS
from services.strategy_builder import (
    MIN_SESSIONS,
    StrategyError,
    evaluate_strategy,
    metric_frame,
)

EQUITY_BACKTEST_FLAG = "strategy_builder"
MULTI_LEG_FLAG = "multi_leg_backtest"

MAX_LEGS = 8
MAX_CYCLES = 60
MIN_CYCLE_SESSIONS = 5

BACKTEST_DISCLOSURES: tuple[str, ...] = (
    "Backtests replay realized history. Past rule behaviour is not a forecast of future results.",
    "Fills are simulated with a fixed spread and slippage assumption; real fills will differ.",
    "No live broker order was placed, modified, or cancelled to produce these results.",
    "Results are survivorship-unaware and ignore corporate actions, taxes, and financing costs.",
)

MULTI_LEG_LIMITATIONS: tuple[str, ...] = (
    "Historical option chains are not stored by this application, so entry premiums are model-priced, not traded prices.",
    "Model pricing uses the supplied volatility assumption for every cycle; realized implied volatility varied.",
    "Assignment, early exercise, and margin calls are not simulated.",
)


class BacktestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _fill_price(price: float, side: str, spread_bps: float, slippage_bps: float) -> float:
    """Buys fill above the close, sells below it, by half-spread plus slippage."""
    penalty = (float(spread_bps) / 2 + float(slippage_bps)) / 10_000
    return price * (1 + penalty) if side == "BUY" else price * (1 - penalty)


def _max_drawdown_pct(equity: Sequence[float]) -> float | None:
    peak = None
    worst = 0.0
    for value in equity:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            worst = min(worst, (value - peak) / peak * 100)
    return _round(worst) if peak is not None else None


def backtest_strategy(
    strategy: dict[str, Any],
    frame: Any,
    *,
    symbol: str | None = None,
    quantity: float | None = None,
    spread_bps: float = DEFAULT_SPREAD_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay a compiled strategy's signals over one symbol's history."""
    try:
        evaluation = evaluate_strategy(strategy, frame, symbol=symbol, max_signals=0)
    except StrategyError as error:
        raise BacktestError(error.code, error.message) from error
    if not evaluation.get("evaluated"):
        raise BacktestError(
            "backtest_insufficient_history",
            f"At least {evaluation.get('sessions_required', MIN_SESSIONS)} sessions are required; "
            f"{evaluation.get('sessions_available', 0)} were available.",
        )

    size = float(quantity if quantity is not None else strategy.get("quantity") or 1)
    if size <= 0:
        raise BacktestError("backtest_quantity_invalid", "Quantity must be positive.")

    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = []
    running = 0.0
    open_entry: dict[str, Any] | None = None

    for signal in evaluation["signals"]:
        price = float(signal["price"])
        if signal["action"] == "BUY" and open_entry is None:
            open_entry = {"at": signal["at"], "price": price, "fill": _fill_price(price, "BUY", spread_bps, slippage_bps)}
            continue
        if signal["action"] == "SELL" and open_entry is not None:
            exit_fill = _fill_price(price, "SELL", spread_bps, slippage_bps)
            gross = (price - open_entry["price"]) * size
            net = (exit_fill - open_entry["fill"]) * size
            costs = gross - net
            running += net
            equity_curve.append(running)
            trades.append(
                {
                    "entry_at": open_entry["at"],
                    "entry_price": _round(open_entry["price"], 4),
                    "entry_fill": _round(open_entry["fill"], 4),
                    "exit_at": signal["at"],
                    "exit_price": _round(price, 4),
                    "exit_fill": _round(exit_fill, 4),
                    "exit_reason": signal.get("reason"),
                    "quantity": size,
                    "gross_pnl": _round(gross),
                    "costs": _round(costs),
                    "net_pnl": _round(net),
                    "net_return_pct": _round((exit_fill / open_entry["fill"] - 1) * 100),
                }
            )
            open_entry = None

    wins = [trade for trade in trades if (trade["net_pnl"] or 0) > 0]
    returns = [trade["net_return_pct"] for trade in trades if trade["net_return_pct"] is not None]

    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "symbol": symbol,
        "strategy": {"name": strategy.get("name"), "direction": strategy.get("direction", "long_only")},
        "assumptions": {
            "quantity": size,
            "spread_bps": float(spread_bps),
            "slippage_bps": float(slippage_bps),
            "fill_basis": "Session close adjusted by half-spread plus slippage.",
            "positions": "one_at_a_time",
        },
        "sessions_available": evaluation.get("sessions_available"),
        "summary": {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(trades) - len(wins),
            "win_rate_pct": _round(len(wins) / len(trades) * 100) if trades else None,
            "gross_pnl": _round(sum(trade["gross_pnl"] or 0 for trade in trades)),
            "costs": _round(sum(trade["costs"] or 0 for trade in trades)),
            "net_pnl": _round(running),
            "avg_net_return_pct": _round(sum(returns) / len(returns)) if returns else None,
            "best_net_pnl": _round(max((trade["net_pnl"] or 0) for trade in trades)) if trades else None,
            "worst_net_pnl": _round(min((trade["net_pnl"] or 0) for trade in trades)) if trades else None,
            "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        },
        "trades": trades,
        "open_position": (
            {"entry_at": open_entry["at"], "entry_price": _round(open_entry["price"], 4), "state": "unrealized_excluded_from_summary"}
            if open_entry
            else None
        ),
        "evidence": {
            "basis": "Realized OHLCV history only.",
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(BACKTEST_DISCLOSURES),
    }


def _cycle_legs(
    templates: Sequence[dict[str, Any]],
    *,
    spot: float,
    days_to_expiry: int,
    volatility: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> list[dict[str, Any]]:
    """Instantiate strike-relative leg templates at one entry spot."""
    legs: list[dict[str, Any]] = []
    for index, template in enumerate(templates):
        offset = template.get("strike_offset_pct")
        if offset is None and template.get("strike") is None:
            raise BacktestError(
                "backtest_leg_invalid",
                f"Leg {index + 1} needs either strike_offset_pct or an absolute strike.",
            )
        strike = (
            float(template["strike"])
            if offset is None
            else round(spot * (1 + float(offset) / 100), 2)
        )
        leg_type = str(template.get("type") or "call").strip().lower()
        payload = {
            "type": leg_type,
            "side": template.get("side", "buy"),
            "strike": strike,
            "contracts": template.get("contracts", 1),
            "lot_size": template.get("lot_size") or 1,
        }
        if leg_type in ("call", "put", "ce", "pe", "c", "p"):
            priced = black_scholes(
                spot=spot,
                strike=strike,
                days_to_expiry=int(days_to_expiry),
                volatility=float(volatility),
                risk_free_rate=float(risk_free_rate),
                dividend_yield=float(dividend_yield),
                option_type="put" if leg_type in ("put", "pe", "p") else "call",
            )
            premium = priced.get("theoretical_price", priced.get("price"))
            if premium is None:
                raise BacktestError("backtest_pricing_failed", "The option pricing model returned no price.")
            payload["premium"] = float(premium)
        else:
            payload["premium"] = float(template.get("premium", spot))
        legs.append(normalize_leg(payload, index))
    return legs


def backtest_multi_leg(
    *,
    legs: Iterable[dict[str, Any]],
    frame: Any,
    underlying: str | None = None,
    days_to_expiry: int = 30,
    entry_every_sessions: int | None = None,
    volatility: float = 0.20,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
    costs_per_cycle: float = 0.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay one option structure across successive expiry cycles.

    Each cycle enters at a session close, prices every option leg with the
    existing Black-Scholes model, and settles at the close `days_to_expiry`
    sessions later using the same expiry maths as the payoff diagram.
    """
    templates = [dict(leg) for leg in legs]
    if not templates:
        raise BacktestError("backtest_legs_required", "At least one leg is required.")
    if len(templates) > MAX_LEGS:
        raise BacktestError("backtest_legs_too_many", f"At most {MAX_LEGS} legs are supported.")

    horizon = int(days_to_expiry)
    if horizon < MIN_CYCLE_SESSIONS:
        raise BacktestError("backtest_horizon_invalid", f"days_to_expiry must be at least {MIN_CYCLE_SESSIONS}.")
    if float(volatility) <= 0:
        raise BacktestError("backtest_volatility_invalid", "volatility must be positive (decimal, e.g. 0.20).")

    try:
        metrics = metric_frame(frame)
    except StrategyError as error:
        raise BacktestError("backtest_history_unavailable", error.message) from error

    closes = metrics["close"].dropna()
    if len(closes) < horizon + MIN_CYCLE_SESSIONS:
        raise BacktestError(
            "backtest_insufficient_history",
            f"At least {horizon + MIN_CYCLE_SESSIONS} sessions are required; {len(closes)} were available.",
        )

    step = int(entry_every_sessions or horizon)
    if step < 1:
        raise BacktestError("backtest_step_invalid", "entry_every_sessions must be at least 1.")

    cycles: list[dict[str, Any]] = []
    running = 0.0
    equity_curve: list[float] = []
    all_breakevens: list[list[float]] = []
    index = 0
    while index + horizon < len(closes) and len(cycles) < MAX_CYCLES:
        entry_stamp = closes.index[index]
        exit_stamp = closes.index[index + horizon]
        entry_spot = float(closes.iloc[index])
        exit_spot = float(closes.iloc[index + horizon])
        cycle_legs = _cycle_legs(
            templates,
            spot=entry_spot,
            days_to_expiry=horizon,
            volatility=float(volatility),
            risk_free_rate=float(risk_free_rate),
            dividend_yield=float(dividend_yield),
        )
        
        # Compute breakevens at entry using payoff module
        payoff_result = build_payoff(
            legs=cycle_legs,
            spot=entry_spot,
            costs=float(costs_per_cycle),
        )
        cycle_breakevens = payoff_result.get("breakevens", [])
        all_breakevens.append(cycle_breakevens)
        
        # Mark-to-market P&L at each session during the cycle (using Black-Scholes model)
        mtm_pnl_series: list[dict[str, Any]] = []
        for session_idx in range(index, min(index + horizon + 1, len(closes))):
            session_stamp = closes.index[session_idx]
            session_spot = float(closes.iloc[session_idx])
            days_remaining = max(1, horizon - (session_idx - index))
            mtm_legs = _cycle_legs(
                templates,
                spot=session_spot,
                days_to_expiry=days_remaining,
                volatility=float(volatility),
                risk_free_rate=float(risk_free_rate),
                dividend_yield=float(dividend_yield),
            )
            mtm_gross = sum(leg_expiry_payoff(leg, session_spot) for leg in mtm_legs)
            mtm_costs = float(costs_per_cycle)
            mtm_net = mtm_gross - mtm_costs
            mtm_pnl_series.append({
                "at": session_stamp.isoformat() if hasattr(session_stamp, "isoformat") else str(session_stamp),
                "spot": _round(session_spot, 4),
                "days_remaining": days_remaining,
                "gross_pnl": _round(mtm_gross),
                "costs": _round(mtm_costs),
                "net_pnl": _round(mtm_net),
            })
        
        gross = sum(leg_expiry_payoff(leg, exit_spot) for leg in cycle_legs)
        costs = float(costs_per_cycle)
        net = gross - costs
        running += net
        equity_curve.append(running)
        cycles.append(
            {
                "entry_at": entry_stamp.isoformat() if hasattr(entry_stamp, "isoformat") else str(entry_stamp),
                "exit_at": exit_stamp.isoformat() if hasattr(exit_stamp, "isoformat") else str(exit_stamp),
                "entry_spot": _round(entry_spot, 4),
                "exit_spot": _round(exit_spot, 4),
                "underlying_move_pct": _round((exit_spot / entry_spot - 1) * 100),
                "strikes": [leg["strike"] for leg in cycle_legs if leg.get("strike") is not None],
                "entry_premiums": [_round(leg["premium"], 4) for leg in cycle_legs],
                "gross_pnl": _round(gross),
                "costs": _round(costs),
                "net_pnl": _round(net),
                "breakevens": [_round(b, 2) for b in cycle_breakevens],
                "mark_to_market": mtm_pnl_series,
            }
        )
        index += step

    if not cycles:
        raise BacktestError("backtest_no_cycles", "No complete expiry cycle fitted inside the available history.")

    wins = [cycle for cycle in cycles if (cycle["net_pnl"] or 0) > 0]
    nets = [cycle["net_pnl"] or 0 for cycle in cycles]

    # Compute aggregate breakevens across all cycles (using first cycle's entry as reference)
    first_cycle_legs = _cycle_legs(
        templates,
        spot=float(closes.iloc[0]),
        days_to_expiry=horizon,
        volatility=float(volatility),
        risk_free_rate=float(risk_free_rate),
        dividend_yield=float(dividend_yield),
    )
    aggregate_payoff = build_payoff(
        legs=first_cycle_legs,
        spot=float(closes.iloc[0]),
        costs=float(costs_per_cycle),
    )
    aggregate_breakevens = aggregate_payoff.get("breakevens", [])

    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "underlying": underlying,
        "analysis_label": "Multi-leg expiry-cycle replay",
        "pricing_basis": "model_priced_black_scholes",
        "is_chain_priced": False,
        "assumptions": {
            "days_to_expiry": horizon,
            "entry_every_sessions": step,
            "volatility": float(volatility),
            "risk_free_rate": float(risk_free_rate),
            "dividend_yield": float(dividend_yield),
            "costs_per_cycle": float(costs_per_cycle),
            "settlement": "Intrinsic value at the close of the exit session.",
        },
        "legs": templates,
        "summary": {
            "cycles": len(cycles),
            "wins": len(wins),
            "losses": len(cycles) - len(wins),
            "win_rate_pct": _round(len(wins) / len(cycles) * 100),
            "gross_pnl": _round(sum(cycle["gross_pnl"] or 0 for cycle in cycles)),
            "costs": _round(sum(cycle["costs"] or 0 for cycle in cycles)),
            "net_pnl": _round(running),
            "avg_net_pnl": _round(sum(nets) / len(nets)),
            "best_net_pnl": _round(max(nets)),
            "worst_net_pnl": _round(min(nets)),
            "max_drawdown_pct": _max_drawdown_pct(equity_curve),
            "breakevens": [_round(b, 2) for b in aggregate_breakevens],
        },
        "cycles": cycles,
        "limitations": list(MULTI_LEG_LIMITATIONS),
        "evidence": {
            "basis": "Realized underlying closes with model-priced option entries. Mark-to-market uses Black-Scholes model pricing at each session.",
            "is_forecast": False,
            "is_recommendation": False,
        },
        "disclosures": list(BACKTEST_DISCLOSURES) + [
            "Breakevens and mark-to-market P&L are model-derived (Black-Scholes), not exchange-quoted.",
            "Live mark-to-market requires real-time option chain data which this application does not store.",
        ],
    }
