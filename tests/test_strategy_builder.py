"""Tests for the no-code paper-strategy builder (v9 Part F1)."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

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
            "volume": [100_000] * len(closes),
        }
    )


def _step_path(first: float, second: float, third: float) -> list[float]:
    """200 sessions at `first`, 50 at `second`, 50 at `third`."""
    return [first] * 200 + [second] * 50 + [third] * 50


def _simple_strategy(**overrides) -> dict:
    payload = {
        "name": "Step breakout",
        "symbols": ["TESTCO"],
        "entry": {"join": "and", "groups": [{"join": "and", "conditions": [{"metric": "close", "operator": "gt", "value": 110}]}]},
        "exit": {"join": "and", "groups": [{"join": "and", "conditions": [{"metric": "close", "operator": "lt", "value": 105}]}]},
        "quantity": 10,
    }
    payload.update(overrides)
    return sb.compile_strategy(payload)


def test_metric_catalogue_is_documented():
    metrics = {item["name"]: item for item in sb.describe_metrics()}
    assert "rsi_14" in metrics
    for meta in metrics.values():
        assert meta["basis"], "every metric must say what it is computed from"
        assert meta["sessions_required"] >= 1


def test_compile_rejects_unknown_metric_and_operator():
    with pytest.raises(sb.StrategyError) as unknown_metric:
        sb.compile_strategy(
            {
                "name": "bad",
                "symbols": ["X"],
                "entry": [{"join": "and", "conditions": [{"metric": "moon_phase", "operator": "gt", "value": 1}]}],
            }
        )
    assert unknown_metric.value.code == "strategy_metric_unknown"

    with pytest.raises(sb.StrategyError) as unknown_op:
        sb.compile_strategy(
            {
                "name": "bad",
                "symbols": ["X"],
                "entry": [{"join": "and", "conditions": [{"metric": "rsi_14", "operator": "vibes", "value": 1}]}],
            }
        )
    assert unknown_op.value.code == "strategy_operator_unknown"


def test_compile_requires_name_symbols_and_values():
    with pytest.raises(sb.StrategyError) as no_name:
        sb.compile_strategy({"symbols": ["X"], "entry": [{"conditions": [{"metric": "close", "operator": "gt", "value": 1}]}]})
    assert no_name.value.code == "strategy_name_required"

    with pytest.raises(sb.StrategyError) as no_symbols:
        sb.compile_strategy({"name": "n", "entry": [{"conditions": [{"metric": "close", "operator": "gt", "value": 1}]}]})
    assert no_symbols.value.code == "strategy_symbols_required"

    with pytest.raises(sb.StrategyError) as no_value:
        sb.compile_strategy({"name": "n", "symbols": ["X"], "entry": [{"conditions": [{"metric": "close", "operator": "gt"}]}]})
    assert no_value.value.code == "strategy_condition_invalid"


def test_between_requires_two_bounds_and_sorts_them():
    compiled = sb.compile_strategy(
        {
            "name": "band",
            "symbols": ["X"],
            "entry": [{"conditions": [{"metric": "rsi_14", "operator": "between", "values": [70, 30]}]}],
        }
    )
    assert compiled["entry"]["groups"][0]["conditions"][0]["values"] == [30, 70]

    with pytest.raises(sb.StrategyError) as bad:
        sb.compile_strategy(
            {
                "name": "band",
                "symbols": ["X"],
                "entry": [{"conditions": [{"metric": "rsi_14", "operator": "between", "values": [30]}]}],
            }
        )
    assert bad.value.code == "strategy_condition_invalid"


def test_condition_and_group_limits_are_enforced():
    conditions = [{"metric": "close", "operator": "gt", "value": index} for index in range(sb.MAX_CONDITIONS + 1)]
    with pytest.raises(sb.StrategyError) as too_many:
        sb.compile_strategy({"name": "n", "symbols": ["X"], "entry": [{"conditions": conditions}]})
    assert too_many.value.code == "strategy_conditions_too_many"

    groups = [{"conditions": [{"metric": "close", "operator": "gt", "value": 1}]}] * (sb.MAX_GROUPS + 1)
    with pytest.raises(sb.StrategyError) as too_many_groups:
        sb.compile_strategy({"name": "n", "symbols": ["X"], "entry": groups})
    assert too_many_groups.value.code == "strategy_groups_too_many"


def test_sessions_required_follows_the_slowest_metric():
    compiled = sb.compile_strategy(
        {
            "name": "slow",
            "symbols": ["X"],
            "entry": [{"conditions": [{"metric": "close", "operator": "gt", "compare_metric": "sma_200"}]}],
        }
    )
    assert compiled["sessions_required"] >= 200


def test_metrics_are_nan_until_their_window_is_satisfied():
    metrics = sb.metric_frame(_frame([100.0] * 60))
    assert pd.isna(metrics["sma_200"].iloc[-1]), "SMA 200 must not be computed from 60 sessions"
    assert not pd.isna(metrics["sma_20"].iloc[-1])
    assert pd.isna(metrics["change_pct_1d"].iloc[0])


def test_history_without_close_is_refused():
    with pytest.raises(sb.StrategyError) as error:
        sb.metric_frame(pd.DataFrame({"open": [1, 2, 3]}))
    assert error.value.code == "strategy_history_unavailable"


def test_entry_and_exit_signals_pair_up_long_only():
    strategy = _simple_strategy()
    result = sb.evaluate_strategy(strategy, _frame(_step_path(100, 120, 100)), symbol="TESTCO")
    assert result["evaluated"] is True
    actions = [signal["action"] for signal in result["signals"]]
    assert actions == ["BUY", "SELL"], "a long-only strategy must not stack entries"
    assert result["signals"][0]["price"] == 120
    assert result["signals"][1]["reason"] == "exit_rules_matched"
    assert result["open_position"] is False


def test_crossing_comparator_fires_only_on_the_crossing_bar():
    strategy = sb.compile_strategy(
        {
            "name": "cross",
            "symbols": ["TESTCO"],
            "entry": [{"conditions": [{"metric": "close", "operator": "cross_above", "value": 110}]}],
        }
    )
    result = sb.evaluate_strategy(strategy, _frame(_step_path(100, 120, 120)), symbol="TESTCO")
    buys = [signal for signal in result["signals"] if signal["action"] == "BUY"]
    assert len(buys) == 1, "a crossing must fire once, not on every bar above the level"


def test_stop_loss_and_target_exit_with_their_own_reason():
    stopped = sb.evaluate_strategy(
        _simple_strategy(exit=None, stop_loss_pct=5),
        _frame(_step_path(100, 120, 100)),
        symbol="TESTCO",
    )
    assert "stop_loss" in [signal["reason"] for signal in stopped["signals"]]

    targeted = sb.evaluate_strategy(
        _simple_strategy(exit=None, target_pct=10),
        _frame(_step_path(100, 120, 140)),
        symbol="TESTCO",
    )
    assert "target" in [signal["reason"] for signal in targeted["signals"]]


def test_insufficient_history_is_reported_not_raised():
    result = sb.evaluate_strategy(_simple_strategy(), _frame([100.0] * 30), symbol="TESTCO")
    assert result["evaluated"] is False
    assert result["reason"] == "insufficient_history"
    assert result["signals"] == []


def test_order_intent_is_paper_only_and_validated():
    signal = {"action": "BUY", "reason": "entry_rules_matched", "at": "2024-06-03T00:00:00"}
    intent = sb.signal_to_order_intent(signal, symbol="testco", quantity=5)
    assert intent["execution"] == "paper_only"
    assert intent["order_type"] == "MARKET"
    assert intent["symbol"] == "TESTCO"
    assert intent["routes_to"] == "services.paper_trading_v6.place_order"

    with pytest.raises(sb.StrategyError) as bad_action:
        sb.signal_to_order_intent({"action": "SHORT"}, symbol="X", quantity=1)
    assert bad_action.value.code == "strategy_signal_invalid"


def test_order_intent_keys_match_the_paper_engine_signature():
    """The intent must be directly usable as place_order kwargs."""
    import inspect

    from services import paper_trading_v6

    accepted = set(inspect.signature(paper_trading_v6.place_order).parameters)
    intent = sb.signal_to_order_intent({"action": "BUY"}, symbol="X", quantity=1)
    order_keys = set(intent) - {"execution", "routes_to"}
    assert order_keys <= accepted, f"unknown place_order kwargs: {sorted(order_keys - accepted)}"


def test_run_strategy_reports_loader_failures_as_exclusions():
    def loader(symbol: str) -> pd.DataFrame:
        if symbol == "BROKEN":
            raise KeyError("no history")
        return _frame(_step_path(100, 120, 100))

    strategy = _simple_strategy(symbols=["TESTCO", "BROKEN"])
    result = sb.run_strategy(strategy, history_loader=loader)
    assert result["coverage"]["evaluated"] == 1
    assert result["coverage"]["excluded"][0]["reason"] == "history_unavailable"
    assert result["strategy"]["execution"] == "paper_only"
    assert result["evidence"]["is_recommendation"] is False


def test_saved_strategies_round_trip(tmp_path):
    path = tmp_path / "strategies.db"
    store = sb.StrategyStore(connection_factory=lambda: sqlite3.connect(path))
    saved = store.save(7, _simple_strategy())
    assert saved["id"] > 0

    listed = store.list(7)
    assert [item["name"] for item in listed] == ["Step breakout"]

    fetched = store.get(7, saved["id"])
    assert fetched["definition"]["quantity"] == 10

    # Re-saving the same name updates instead of duplicating.
    store.save(7, _simple_strategy(quantity=25))
    assert len(store.list(7)) == 1
    assert store.get(7, saved["id"])["definition"]["quantity"] == 25

    assert store.delete(7, saved["id"]) is True
    assert store.list(7) == []


def test_saved_strategies_are_scoped_per_user(tmp_path):
    path = tmp_path / "strategies.db"
    store = sb.StrategyStore(connection_factory=lambda: sqlite3.connect(path))
    saved = store.save(1, _simple_strategy())
    with pytest.raises(sb.StrategyError) as error:
        store.get(2, saved["id"])
    assert error.value.code == "strategy_not_found"


def test_starter_strategies_compile():
    for starter in sb.starter_strategies():
        compiled = sb.compile_strategy(starter)
        assert compiled["execution"] == "paper_only"
        assert compiled["direction"] == "long_only"


def test_disclosures_state_the_paper_only_boundary():
    joined = " ".join(sb.STRATEGY_DISCLOSURES).lower()
    assert "paper" in joined
    assert "not a forecast" in joined
    assert "advice" in joined
