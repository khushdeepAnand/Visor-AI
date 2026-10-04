from __future__ import annotations

import pytest

import database
from services.paper_trading_v6 import estimate_margin, place_order


def _user(temp_db):
    conn = database.get_connection()
    user_id = conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)", ("V6 Trader", "v6-paper@example.com", "hash")).lastrowid
    conn.commit(); conn.close()
    return int(user_id)


def test_derivative_lot_size_is_persisted_and_used_in_position_value(temp_db, monkeypatch):
    user_id = _user(temp_db)
    quote = {"symbol": "NIFTY 50", "price": 100.0, "previous_close": 99.0}
    result = place_order(user_id=user_id, symbol="NIFTY 50", side="BUY", quantity=2, instrument_type="FUTURE", expiry="2026-08-27", lot_size=25, market_quote=quote)
    assert result["notional"] == pytest.approx(5000.0, rel=0.01)
    assert result["context"]["provider"] == "supplied_market_quote"
    conn = database.get_connection()
    row = conn.execute("SELECT quantity,average_price,lot_size FROM paper_positions WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    assert row[0] == 2
    assert row[2] == 25


def test_margin_approximation_distinguishes_equity_future_and_option():
    equity = estimate_margin(instrument_type="EQUITY", quantity=1, price=100, lot_size=50)
    future = estimate_margin(instrument_type="FUTURE", quantity=1, price=100, lot_size=50)
    long_option = estimate_margin(instrument_type="OPTION", quantity=1, price=10, lot_size=50, option_side="BUY")
    assert equity == 5000
    assert future == 900
    assert long_option == 500


def test_leaderboard_requires_explicit_opt_in(temp_db):
    from services.paper_trading_v6 import ensure_account, leaderboard, set_leaderboard_opt_in

    user_id = _user(temp_db)
    account = ensure_account(user_id)
    assert account["leaderboard_opt_in"] is False
    assert all(item["user_id"] != user_id for item in leaderboard())
    assert set_leaderboard_opt_in(user_id, True) is True
    assert any(item["user_id"] == user_id for item in leaderboard())
    assert set_leaderboard_opt_in(user_id, False) is True
    assert all(item["user_id"] != user_id for item in leaderboard())
