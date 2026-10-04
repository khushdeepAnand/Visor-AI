import pandas as pd

import database
from services.watchlist_service import calculate_quote_snapshot, load_watchlist_quotes


def _create_user():
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
        ("Watch User", "watch@example.com", "hash"),
    )
    user_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return user_id


def test_watchlist_add_load_remove_round_trip(temp_db):
    user_id = _create_user()
    assert database.add_to_watchlist(user_id, "RELIANCE") is True
    row = database.get_watchlist(user_id)[0]
    assert row[1] == "RELIANCE"
    assert database.remove_from_watchlist(row[0], user_id) is True
    assert database.get_watchlist(user_id) == []


def test_quote_snapshot_calculates_daily_change():
    data = pd.DataFrame({"Close": [100.0, 105.0]}, index=pd.date_range("2026-01-01", periods=2))
    snapshot = calculate_quote_snapshot("RELIANCE", data)
    assert snapshot["Current Price"] == 105.0
    assert snapshot["Daily Change"] == 5.0
    assert snapshot["Daily Change %"] == 5.0


def test_watchlist_quote_loader_survives_one_failed_symbol():
    rows = [(1, "RELIANCE", "2026-01-01"), (2, "BAD", "2026-01-02")]

    def fetch(symbol, period):
        assert period == "5d"
        if symbol == "BAD":
            raise RuntimeError("feed unavailable")
        return pd.DataFrame({"Close": [10.0, 11.0]})

    quotes = load_watchlist_quotes(rows, data_fetcher=fetch)
    assert quotes[0]["Current Price"] == 11.0
    assert quotes[1]["Data Status"] == "Unavailable"
