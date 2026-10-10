"""Exercise account operations against a real encrypted database."""
import sqlite3

import pytest

import authentication as auth
import database


def test_registration_login_and_duplicate_account_use_sqlcipher(temp_db, monkeypatch):
    # The fixture created plaintext; use a fresh path for encrypted schema creation.
    encrypted = temp_db.parent / "encrypted-auth.db"
    monkeypatch.setattr(database, "DATABASE", str(encrypted))
    monkeypatch.setattr(auth, "DATABASE_PATH", encrypted)
    monkeypatch.setattr(database, "SQLCIPHER_KEY", "encrypted-auth-test-key-only")
    database.create_tables()

    success, _ = auth.register_user("Research User", "encrypted@example.com", "Passw0rd123", "1990-01-01")
    assert success
    assert auth.login_user("encrypted@example.com", "Passw0rd123")[0]
    assert not auth.login_user("encrypted@example.com", "WrongPass123")[0]
    assert not auth.register_user("Research User", "encrypted@example.com", "Passw0rd123", "1990-01-01")[0]
    from services.db.sqlite_impl import SQLiteDatabase, SQLiteUserDAO
    dao_database = SQLiteDatabase()
    try:
        user = SQLiteUserDAO(dao_database).get_user_by_email("encrypted@example.com")
        assert user["name"] == "Research User"
    finally:
        dao_database.close()
    connection = database.get_connection()
    try:
        connection.execute(
            "INSERT INTO prediction_history(user_id,symbol,forecast_low,forecast_high,forecast_status) VALUES(?,?,?,?,?)",
            (user["id"], "RELIANCE", 90., 110., "model_supported"),
        )
        connection.commit()
    finally:
        connection.close()
    assert database.get_prediction_details(user["id"])[0]["symbol"] == "RELIANCE"
    assert database.get_pending_range_forecasts(user_id=user["id"])[0]["symbol"] == "RELIANCE"
    assert not encrypted.read_bytes().startswith(b"SQLite format 3")
    with sqlite3.connect(encrypted) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("SELECT * FROM users").fetchall()


def test_authentication_does_not_fall_back_when_cipher_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "SQLCIPHER_KEY", "encrypted-auth-test-key-only")
    monkeypatch.setattr(database, "SQLCIPHER_AVAILABLE", False)
    monkeypatch.setattr(auth, "DATABASE_PATH", tmp_path / "must-not-be-plaintext.db")
    with pytest.raises(RuntimeError, match="encryption requested"):
        auth.get_connection()
    assert not (tmp_path / "must-not-be-plaintext.db").exists()
