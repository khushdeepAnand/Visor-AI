"""Tests for forward-test tracking (v9 Part F3)."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from services import forward_test as ft
from services import strategy_builder as sb


def _frame(closes: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="B")
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


ROUND_TRIP = [100.0] * 200 + [120.0] * 25 + [100.0] * 25


def _strategy() -> dict:
    return sb.compile_strategy(
        {
            "name": "Step breakout",
            "symbols": ["TESTCO"],
            "entry": [{"conditions": [{"metric": "close", "operator": "gt", "value": 110}]}],
            "exit": [{"conditions": [{"metric": "close", "operator": "lt", "value": 105}]}],
            "quantity": 10,
        }
    )


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "forward.db"
    return ft.ForwardTestStore(connection_factory=lambda: sqlite3.connect(path))


def _loader(frame: pd.DataFrame):
    def loader(symbol: str) -> pd.DataFrame:
        if symbol == "BROKEN":
            raise KeyError("no history")
        return frame

    return loader


def test_start_records_an_active_test_that_never_places_orders(store):
    started = store.start(1, strategy_id=11, name="Watch breakout", symbols=["testco"])
    assert started["status"] == "active"
    assert started["symbols"] == ["TESTCO"]
    assert started["order_placement"] == "never"
    assert ft.ORDER_PLACEMENT == "never"


def test_starting_the_same_strategy_twice_is_refused(store):
    store.start(1, strategy_id=11, name="Watch", symbols=["TESTCO"])
    with pytest.raises(ft.ForwardTestError) as error:
        store.start(1, strategy_id=11, name="Watch again", symbols=["TESTCO"])
    assert error.value.code == "forward_test_already_active"


def test_start_requires_symbols(store):
    with pytest.raises(ft.ForwardTestError) as error:
        store.start(1, strategy_id=11, name="Watch", symbols=[])
    assert error.value.code == "forward_test_symbols_required"


def test_active_test_limit_is_enforced(store):
    for index in range(ft.MAX_ACTIVE_TESTS):
        store.start(1, strategy_id=index + 1, name=f"Watch {index}", symbols=["TESTCO"])
    with pytest.raises(ft.ForwardTestError) as error:
        store.start(1, strategy_id=999, name="One too many", symbols=["TESTCO"])
    assert error.value.code == "forward_test_limit_reached"


def test_stop_then_evaluate_is_refused(store):
    started = store.start(1, strategy_id=11, name="Watch", symbols=["TESTCO"])
    stopped = store.stop(1, started["id"])
    assert stopped["status"] == "stopped"
    with pytest.raises(ft.ForwardTestError) as error:
        store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    assert error.value.code == "forward_test_inactive"


def test_stopping_an_unknown_test_is_refused(store):
    with pytest.raises(ft.ForwardTestError) as error:
        store.stop(1, 4242)
    assert error.value.code == "forward_test_not_found"


def test_tests_are_scoped_per_user(store):
    started = store.start(1, strategy_id=11, name="Watch", symbols=["TESTCO"])
    with pytest.raises(ft.ForwardTestError) as error:
        store.get(2, started["id"])
    assert error.value.code == "forward_test_not_found"
    assert store.list_tests(2) == []


def test_evaluation_records_signals_and_is_idempotent(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    first = store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    assert first["new_signals"] == 2
    assert first["order_placement"] == "never"

    second = store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    assert second["new_signals"] == 0, "re-evaluating the same history must not duplicate observations"
    assert second["signals_considered"] == 2


def test_signals_before_the_start_date_are_not_backfilled(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2030-01-01").to_pydatetime(),
    )
    result = store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    assert result["new_signals"] == 0, "starting a test must not import earlier history"
    assert store.events(started["id"]) == []


def test_loader_failures_are_reported_as_skips(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO", "BROKEN"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    result = store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    assert result["skipped"] == [{"symbol": "BROKEN", "reason": "history_unavailable", "detail": "KeyError"}]


def test_short_history_is_skipped_not_crashed(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    result = store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame([100.0] * 20)))
    assert result["skipped"][0]["reason"] == "insufficient_history"
    assert result["new_signals"] == 0


def test_scorecard_pairs_trades_and_reports_a_realized_return(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(ROUND_TRIP)))
    card = store.scorecard(1, started["id"])

    assert card["scorecard"]["closed_trades"] == 1
    trade = card["trades"][0]
    assert trade["entry_price"] == 120
    assert trade["exit_price"] == 100
    assert trade["return_pct"] == pytest.approx(-16.67, abs=0.01)
    assert card["scorecard"]["win_rate_pct"] == 0.0
    assert card["scorecard"]["max_drawdown_pct"] < 0
    assert card["open_positions"] == []


def test_open_position_is_reported_outside_the_win_rate(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    open_path = [100.0] * 200 + [120.0] * 25
    store.evaluate(1, started["id"], strategy=_strategy(), history_loader=_loader(_frame(open_path)))
    card = store.scorecard(1, started["id"])
    assert card["scorecard"]["closed_trades"] == 0
    assert card["scorecard"]["win_rate_pct"] is None
    assert card["open_positions"][0]["entry_price"] == 120


def test_scorecard_declares_its_basis_and_boundaries(store):
    started = store.start(
        1,
        strategy_id=11,
        name="Watch",
        symbols=["TESTCO"],
        now=pd.Timestamp("2024-01-01").to_pydatetime(),
    )
    card = store.scorecard(1, started["id"])
    assert card["evidence"]["is_forecast"] is False
    assert card["evidence"]["is_recommendation"] is False
    assert card["order_placement"] == "never"
    joined = " ".join(card["disclosures"]).lower()
    assert "no order is placed" in joined
    assert "not forecasts" in joined


def test_listing_shows_status_transitions(store):
    first = store.start(1, strategy_id=11, name="A", symbols=["TESTCO"])
    store.start(1, strategy_id=12, name="B", symbols=["TESTCO"])
    store.stop(1, first["id"])
    statuses = {item["name"]: item["status"] for item in store.list_tests(1)}
    assert statuses == {"A": "stopped", "B": "active"}
