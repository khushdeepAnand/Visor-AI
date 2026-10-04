from __future__ import annotations

import pytest

import database
from services.alerts import create_price_alert, delete_price_alert, evaluate_price_alerts, list_price_alerts
from services.paper_trading import execute_paper_order, get_paper_account, list_paper_orders, reset_paper_account


def _user(temp_db, email="paper@example.com"):
    conn = database.get_connection()
    cursor = conn.execute(
        "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
        ("Paper User", email, "hash"),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id


def test_paper_buy_sell_and_account_summary(temp_db):
    user_id = _user(temp_db)
    buy = execute_paper_order(user_id, "RELIANCE", "BUY", 10, 100)
    assert buy["status"] == "FILLED"
    account = get_paper_account(user_id, {"RELIANCE": 110})
    assert account["cash_balance"] == 999000
    assert account["market_value"] == 1100
    assert account["unrealized_pnl"] == 100

    execute_paper_order(user_id, "RELIANCE", "SELL", 4, 120)
    account = get_paper_account(user_id, {"RELIANCE": 120})
    assert account["positions"][0]["quantity"] == 6
    assert len(list_paper_orders(user_id)) == 2


def test_paper_order_rejects_insufficient_cash_or_quantity(temp_db):
    user_id = _user(temp_db)
    with pytest.raises(ValueError, match="cash"):
        execute_paper_order(user_id, "RELIANCE", "BUY", 20_000, 100)
    with pytest.raises(ValueError, match="position"):
        execute_paper_order(user_id, "RELIANCE", "SELL", 1, 100)


def test_paper_account_reset_clears_orders_and_positions(temp_db):
    user_id = _user(temp_db)
    execute_paper_order(user_id, "TCS", "BUY", 2, 200)
    assert reset_paper_account(user_id, 500_000)
    account = get_paper_account(user_id)
    assert account["cash_balance"] == 500_000
    assert account["positions"] == []
    assert list_paper_orders(user_id) == []


def test_alert_crud_and_trigger_evaluation(temp_db):
    user_id = _user(temp_db)
    above = create_price_alert(user_id, "RELIANCE", "ABOVE", 150)
    below = create_price_alert(user_id, "TCS", "BELOW", 300)
    alerts = list_price_alerts(user_id)
    assert {item["id"] for item in alerts} == {above, below}
    triggered = evaluate_price_alerts(user_id, {"RELIANCE": 151, "TCS": 299})
    assert {item["id"] for item in triggered} == {above, below}
    assert delete_price_alert(user_id, above)
    assert len(list_price_alerts(user_id)) == 1


def test_audit_log_and_user_data_deletion(temp_db):
    user_id = _user(temp_db)
    alert_id = create_price_alert(user_id, "RELIANCE", "ABOVE", 200)
    events = database.get_audit_events(user_id)
    assert any(item["entity_id"] == str(alert_id) for item in events)
    assert database.delete_user_data(user_id)
    conn = database.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM price_alerts WHERE user_id = ?", (user_id,)).fetchone()[0] == 0
    conn.close()


def test_forecast_range_alert_includes_full_band(temp_db):
    user_id = _user(temp_db)
    alert_id = create_price_alert(user_id, "RELIANCE", "FORECAST_HIGH_ABOVE", 150, training_window="1mo", confidence_level=0.8)
    triggered = evaluate_price_alerts(
        user_id,
        {"RELIANCE": 140},
        {"RELIANCE": {"low": 135, "median": 145, "high": 155, "confidence_level": 0.8, "currency": "INR"}},
    )
    assert triggered[0]["id"] == alert_id
    assert triggered[0]["forecast"] == {"low": 135.0, "median": 145.0, "high": 155.0, "confidence_level": 0.8, "currency": "INR"}
