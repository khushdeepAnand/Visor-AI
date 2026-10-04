from __future__ import annotations

import pytest

import database
from services.historical_replay import (
    SCENARIOS,
    InvalidChoiceError,
    ScenarioNotFoundError,
    list_scenarios,
    my_attempts,
    record_attempt,
    resolve_choice,
)


def _user(temp_db):
    conn = database.get_connection()
    user_id = conn.execute(
        "INSERT INTO users(name,email,password) VALUES(?,?,?)",
        ("Replay Trader", "replay@example.com", "hash"),
    ).lastrowid
    conn.commit(); conn.close()
    return int(user_id)


def test_at_least_three_scenarios_are_defined():
    assert len(SCENARIOS) >= 3
    # every scenario key is unique
    assert len({item["key"] for item in SCENARIOS}) == len(SCENARIOS)


def test_list_scenarios_never_leaks_the_outcome():
    for item in list_scenarios():
        assert "outcome_close" not in item
        assert "outcome_date" not in item


def test_hold_choice_is_fully_exposed_to_the_real_move():
    result = resolve_choice("NIFTY_COVID_CRASH_2020", "HOLD")
    expected_return = (8317.85 / 7610.25 - 1.0) * 100.0
    assert result["choice"]["return_pct"] == pytest.approx(expected_return, abs=1e-3)
    assert result["outcome_close"] == 8317.85


def test_exit_choice_locks_in_zero_further_exposure():
    result = resolve_choice("NIFTY_COVID_CRASH_2020", "EXIT")
    assert result["choice"]["return_pct"] == pytest.approx(0.0, abs=1e-9)


def test_buy_dip_pays_spread_and_slippage_versus_hold():
    hold = resolve_choice("NIFTY_BUDGET_RALLY_2021", "HOLD")
    buy_dip = resolve_choice("NIFTY_BUDGET_RALLY_2021", "BUY_DIP")
    # Same direction of move, but BUY_DIP enters at a worse (higher) fill,
    # so it should never out-return a frictionless HOLD on the same rally.
    assert buy_dip["choice"]["return_pct"] < hold["choice"]["return_pct"]


def test_hedge_floors_the_loss_but_only_where_offered():
    result = resolve_choice("NIFTY_UKRAINE_SHOCK_2022", "HEDGE")
    raw_return = (16247.95 / 17063.25 - 1.0) * 100.0
    assert raw_return < -2.0  # sanity: the real move is worse than the -2% floor
    assert result["choice"]["return_pct"] == pytest.approx(-2.0, abs=1e-6)

    with pytest.raises(InvalidChoiceError):
        resolve_choice("NIFTY_COVID_CRASH_2020", "HEDGE")  # not offered on this scenario


def test_unknown_scenario_raises():
    with pytest.raises(ScenarioNotFoundError):
        resolve_choice("NOT_A_REAL_SCENARIO", "HOLD")


def test_reveal_includes_every_offered_choice_for_comparison():
    result = resolve_choice("NIFTY_COVID_CRASH_2020", "BUY_DIP")
    assert set(result["all_choice_returns_pct"].keys()) == {"HOLD", "BUY_DIP", "EXIT"}


def test_record_attempt_is_deterministic_and_replaces_on_resubmit(temp_db):
    user_id = _user(temp_db)
    first = record_attempt(user_id, "NIFTY_BUDGET_RALLY_2021", "HOLD")
    second = record_attempt(user_id, "NIFTY_BUDGET_RALLY_2021", "HOLD")
    assert first["choice"]["return_pct"] == second["choice"]["return_pct"]

    attempts = my_attempts(user_id)
    assert len(attempts) == 1  # UNIQUE(user_id, challenge_key) replaces, not duplicates
    assert attempts[0]["key"] == "NIFTY_BUDGET_RALLY_2021"
    assert attempts[0]["choice"] == "HOLD"
