"""Tests for the strategy and multi-leg backtesters (v9 Part F2)."""

from __future__ import annotations

import pandas as pd
import pytest

from services import strategy_backtest as bt
from services import strategy_builder as sb


def _frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [250_000] * len(closes),
        }
    )


def _strategy(entry_above: float, exit_rule: dict | None, **overrides) -> dict:
    payload = {
        "name": "Step breakout",
        "symbols": ["TESTCO"],
        "entry": [{"conditions": [{"metric": "close", "operator": "cross_above", "value": entry_above}]}],
        "quantity": 10,
    }
    if exit_rule is not None:
        # A level comparison re-fires on every bar that stays beyond the level,
        # so the tests state the intent as a crossing: enter or exit once, at
        # the bar where the level is actually breached.
        rule = dict(exit_rule)
        crossings = {"gt": "cross_above", "gte": "cross_above", "lt": "cross_below", "lte": "cross_below"}
        rule["operator"] = crossings.get(str(rule.get("operator")), rule.get("operator"))
        payload["exit"] = [{"conditions": [rule]}]
    payload.update(overrides)
    return sb.compile_strategy(payload)


WINNING_PATH = [100.0] * 200 + [120.0] * 50 + [140.0] * 50
LOSING_PATH = [100.0] * 200 + [120.0] * 50 + [100.0] * 50


def test_winning_trade_reports_gross_costs_and_net_separately():
    strategy = _strategy(110, {"metric": "close", "operator": "gt", "value": 135})
    result = bt.backtest_strategy(strategy, _frame(WINNING_PATH), symbol="TESTCO")

    assert result["summary"]["trades"] == 1
    trade = result["trades"][0]
    assert trade["entry_price"] == 120
    assert trade["exit_price"] == 140
    assert trade["gross_pnl"] == pytest.approx(200.0, abs=0.01)
    assert trade["costs"] > 0, "spread and slippage must be charged, not silently dropped"
    assert trade["net_pnl"] == pytest.approx(trade["gross_pnl"] - trade["costs"], abs=0.02)
    assert trade["net_pnl"] < trade["gross_pnl"]


def test_cost_fields_reconcile_at_the_summary_level():
    strategy = _strategy(110, {"metric": "close", "operator": "gt", "value": 135})
    summary = bt.backtest_strategy(strategy, _frame(WINNING_PATH), symbol="TESTCO")["summary"]
    assert summary["net_pnl"] == pytest.approx(summary["gross_pnl"] - summary["costs"], abs=0.05)
    assert summary["win_rate_pct"] == 100.0


def test_losing_trade_is_counted_as_a_loss():
    strategy = _strategy(110, {"metric": "close", "operator": "lt", "value": 105})
    result = bt.backtest_strategy(strategy, _frame(LOSING_PATH), symbol="TESTCO")
    assert result["summary"]["wins"] == 0
    assert result["summary"]["losses"] == 1
    assert result["summary"]["win_rate_pct"] == 0.0
    assert result["summary"]["net_pnl"] < result["summary"]["gross_pnl"]


def test_buys_fill_worse_than_the_close_and_sells_fill_better_than_nothing():
    strategy = _strategy(110, {"metric": "close", "operator": "gt", "value": 135})
    trade = bt.backtest_strategy(strategy, _frame(WINNING_PATH), symbol="TESTCO")["trades"][0]
    assert trade["entry_fill"] > trade["entry_price"], "a buy must not fill better than the close"
    assert trade["exit_fill"] < trade["exit_price"], "a sell must not fill better than the close"


def test_open_position_is_excluded_from_the_summary():
    strategy = _strategy(110, None)
    result = bt.backtest_strategy(strategy, _frame(WINNING_PATH), symbol="TESTCO")
    assert result["summary"]["trades"] == 0
    assert result["open_position"] is not None
    assert result["open_position"]["state"] == "unrealized_excluded_from_summary"


def test_backtest_refuses_short_history():
    strategy = _strategy(110, {"metric": "close", "operator": "lt", "value": 105})
    with pytest.raises(bt.BacktestError) as error:
        bt.backtest_strategy(strategy, _frame([100.0] * 40), symbol="TESTCO")
    assert error.value.code == "backtest_insufficient_history"


def test_assumptions_are_disclosed_in_the_payload():
    strategy = _strategy(110, {"metric": "close", "operator": "gt", "value": 135})
    result = bt.backtest_strategy(strategy, _frame(WINNING_PATH), symbol="TESTCO")
    assert result["assumptions"]["positions"] == "one_at_a_time"
    assert result["assumptions"]["spread_bps"] > 0
    assert result["evidence"]["is_forecast"] is False
    joined = " ".join(result["disclosures"]).lower()
    assert "simulated" in joined
    assert "no live broker order" in joined


# --- multi-leg -------------------------------------------------------------

FLAT = _frame([100.0] * 200)


def test_multi_leg_declares_that_entries_are_model_priced():
    result = bt.backtest_multi_leg(
        legs=[{"type": "call", "side": "buy", "strike_offset_pct": 5}],
        frame=FLAT,
        underlying="TESTCO",
        days_to_expiry=30,
    )
    assert result["pricing_basis"] == "model_priced_black_scholes"
    assert result["is_chain_priced"] is False
    assert any("not stored" in line or "model-priced" in line for line in result["limitations"])


def test_out_of_the_money_long_call_expires_worthless_on_a_flat_underlying():
    result = bt.backtest_multi_leg(
        legs=[{"type": "call", "side": "buy", "strike_offset_pct": 5, "contracts": 1}],
        frame=FLAT,
        days_to_expiry=30,
    )
    assert result["summary"]["cycles"] > 1
    assert result["summary"]["win_rate_pct"] == 0.0
    assert result["summary"]["net_pnl"] < 0, "a worthless long call must lose the premium paid"
    for cycle in result["cycles"]:
        assert cycle["strikes"] == [105.0]
        assert cycle["net_pnl"] == pytest.approx(-cycle["entry_premiums"][0], abs=0.02)


def test_short_at_the_money_straddle_keeps_the_premium_when_price_does_not_move():
    result = bt.backtest_multi_leg(
        legs=[
            {"type": "call", "side": "sell", "strike_offset_pct": 0},
            {"type": "put", "side": "sell", "strike_offset_pct": 0},
        ],
        frame=FLAT,
        days_to_expiry=30,
    )
    assert result["summary"]["win_rate_pct"] == 100.0
    first = result["cycles"][0]
    assert first["net_pnl"] == pytest.approx(sum(first["entry_premiums"]), abs=0.02)


def test_costs_per_cycle_are_charged_against_gross():
    legs = [{"type": "call", "side": "sell", "strike_offset_pct": 0}]
    free = bt.backtest_multi_leg(legs=legs, frame=FLAT, days_to_expiry=30)
    charged = bt.backtest_multi_leg(legs=legs, frame=FLAT, days_to_expiry=30, costs_per_cycle=1.5)
    assert charged["summary"]["gross_pnl"] == pytest.approx(free["summary"]["gross_pnl"], abs=0.01)
    assert charged["summary"]["costs"] == pytest.approx(1.5 * charged["summary"]["cycles"], abs=0.01)
    assert charged["summary"]["net_pnl"] < free["summary"]["net_pnl"]


def test_entry_cadence_controls_the_number_of_cycles():
    monthly = bt.backtest_multi_leg(
        legs=[{"type": "call", "side": "buy", "strike_offset_pct": 5}],
        frame=FLAT,
        days_to_expiry=30,
    )
    weekly = bt.backtest_multi_leg(
        legs=[{"type": "call", "side": "buy", "strike_offset_pct": 5}],
        frame=FLAT,
        days_to_expiry=30,
        entry_every_sessions=10,
    )
    assert weekly["summary"]["cycles"] > monthly["summary"]["cycles"]


def test_multi_leg_input_validation():
    with pytest.raises(bt.BacktestError) as no_legs:
        bt.backtest_multi_leg(legs=[], frame=FLAT)
    assert no_legs.value.code == "backtest_legs_required"

    with pytest.raises(bt.BacktestError) as bad_vol:
        bt.backtest_multi_leg(legs=[{"type": "call", "strike_offset_pct": 0}], frame=FLAT, volatility=0)
    assert bad_vol.value.code == "backtest_volatility_invalid"

    with pytest.raises(bt.BacktestError) as no_strike:
        bt.backtest_multi_leg(legs=[{"type": "call", "side": "buy"}], frame=FLAT)
    assert no_strike.value.code == "backtest_leg_invalid"

    with pytest.raises(bt.BacktestError) as short_history:
        bt.backtest_multi_leg(legs=[{"type": "call", "strike_offset_pct": 0}], frame=_frame([100.0] * 20), days_to_expiry=30)
    assert short_history.value.code == "backtest_insufficient_history"


def test_multi_leg_leg_cap_is_enforced():
    legs = [{"type": "call", "side": "buy", "strike_offset_pct": 1}] * (bt.MAX_LEGS + 1)
    with pytest.raises(bt.BacktestError) as error:
        bt.backtest_multi_leg(legs=legs, frame=FLAT)
    assert error.value.code == "backtest_legs_too_many"
