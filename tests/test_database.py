# ==========================================================
# Tests for database.py
# ==========================================================

import database


def create_user():
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
        ("Database User", "database@example.com", "test-hash"),
    )
    user_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return user_id


def test_buy_stock_creates_holding_and_transaction(temp_db):
    user_id = create_user()

    database.buy_stock(user_id, "RELIANCE", "Reliance Industries", 2.5, 100.0)

    portfolio = database.get_portfolio(user_id)
    transactions = database.get_transactions(user_id)

    assert len(portfolio) == 1
    assert portfolio[0][1] == "RELIANCE"
    assert portfolio[0][3] == 2.5
    assert len(transactions) == 1
    assert transactions[0][2] == "BUY"


def test_update_and_delete_are_scoped_to_owner(temp_db):
    owner_id = create_user()

    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
        ("Other User", "other@example.com", "test-hash"),
    )
    other_id = cursor.lastrowid
    connection.commit()
    connection.close()

    database.buy_stock(owner_id, "TCS", "TCS", 1, 250)
    holding_id = database.get_portfolio(owner_id)[0][0]

    assert database.update_stock(holding_id, other_id, 3, 300) is False
    assert database.delete_stock(holding_id, other_id) is False
    assert database.update_stock(holding_id, owner_id, 3, 300) is True
    assert database.get_portfolio(owner_id)[0][3:5] == (3.0, 300.0)
    assert database.delete_stock(holding_id, owner_id) is True


def test_watchlist_prevents_duplicate_symbols_case_insensitively(temp_db):
    user_id = create_user()

    assert database.add_to_watchlist(user_id, "reliance.ns") is True
    assert database.add_to_watchlist(user_id, "RELIANCE.NS") is False
    assert len(database.get_watchlist(user_id)) == 1


def test_prediction_history_round_trip(temp_db):
    user_id = create_user()

    database.save_prediction(user_id, "INFY", 100.1, 101.2, 99.8)
    history = database.get_prediction_history(user_id)

    assert len(history) == 1
    assert history[0][1] == "INFY"
    assert history[0][2:5] == (100.1, 101.2, 99.8)


def test_save_prediction_once_prevents_same_day_duplicates(temp_db):
    user_id = create_user()

    first = database.save_prediction_once(
        user_id, "RELIANCE", 100.0, 101.0, 102.0, "2026-01-05"
    )
    second = database.save_prediction_once(
        user_id, "RELIANCE", 110.0, 111.0, 112.0, "2026-01-05"
    )

    assert first is True
    assert second is False
    assert len(database.get_prediction_history(user_id)) == 1
