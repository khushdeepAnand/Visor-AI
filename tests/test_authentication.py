# ==========================================================
# Tests for authentication.py
# ==========================================================

from datetime import date, datetime, timezone

import authentication as auth


# ----------------------------------------------------------
# Email validation
# ----------------------------------------------------------

def test_validate_email_accepts_normal_address():
    valid, message = auth.validate_email("student@example.com")
    assert valid is True
    assert message == ""


def test_validate_email_rejects_missing_at_symbol():
    valid, message = auth.validate_email("not-an-email")
    assert valid is False


def test_validate_email_rejects_empty_string():
    valid, message = auth.validate_email("")
    assert valid is False
    assert "required" in message.lower()


def test_normalize_email_lowercases_and_strips():
    assert auth.normalize_email("  Student@EXAMPLE.com ") == "student@example.com"


# ----------------------------------------------------------
# Password validation
# ----------------------------------------------------------

def test_validate_password_accepts_strong_password():
    valid, message = auth.validate_password("Passw0rd123")
    assert valid is True


def test_validate_password_rejects_too_short():
    valid, message = auth.validate_password("Sh0rt")
    assert valid is False
    assert "8 characters" in message


def test_validate_password_rejects_missing_uppercase():
    valid, message = auth.validate_password("lowercase123")
    assert valid is False
    assert "uppercase" in message.lower()


def test_validate_password_rejects_missing_number():
    valid, message = auth.validate_password("NoNumbersHere")
    assert valid is False
    assert "number" in message.lower()


# ----------------------------------------------------------
# Name validation
# ----------------------------------------------------------

def test_validate_name_rejects_empty():
    valid, message = auth.validate_name("")
    assert valid is False


def test_validate_name_rejects_numbers_only():
    valid, message = auth.validate_name("12345")
    assert valid is False


def test_validate_name_accepts_normal_name():
    valid, message = auth.validate_name("Khushdeep Anand")
    assert valid is True


# ----------------------------------------------------------
# Password hashing
# ----------------------------------------------------------

def test_hash_and_verify_password_round_trip():
    hashed = auth.hash_password("Passw0rd123")

    assert hashed != "Passw0rd123"
    assert auth.verify_password("Passw0rd123", hashed) is True
    assert auth.verify_password("WrongPassword1", hashed) is False


def test_verify_password_handles_corrupted_hash_safely():
    # Should return False, not raise, on a malformed stored hash.
    assert auth.verify_password("anything", "not-a-real-bcrypt-hash") is False


# ----------------------------------------------------------
# Register / login flow (uses the isolated temp_db fixture)
# ----------------------------------------------------------

def test_register_then_login_succeeds(temp_db):
    success, message = auth.register_user(
        "Test User",
        "test.user@example.com",
        "Passw0rd123",
        "1985-06-15",
    )

    assert success is True

    success, result = auth.login_user(
        "test.user@example.com",
        "Passw0rd123",
    )

    assert success is True
    assert result["email"] == "test.user@example.com"


def test_register_duplicate_email_fails(temp_db):
    auth.register_user("First User", "dupe@example.com", "Passw0rd123", "1985-06-15")

    success, message = auth.register_user(
        "Second User",
        "dupe@example.com",
        "Passw0rd456",
        "1985-06-15",
    )

    assert success is False
    assert "already exists" in message.lower()


def test_login_wrong_password_gives_generic_message(temp_db):
    auth.register_user("Test User", "test2@example.com", "Passw0rd123", "1985-06-15")

    success, message = auth.login_user("test2@example.com", "WrongPass1")

    assert success is False
    # Deliberately generic — should not reveal whether the email exists.
    assert message == "Invalid email or password."


def test_login_unknown_email_gives_same_generic_message(temp_db):
    success, message = auth.login_user("nobody@example.com", "WhateverPass1")

    assert success is False
    assert message == "Invalid email or password."


def test_long_utf8_password_uses_safe_fallback_and_verifies():
    password = "StrongPass9" + "🔐" * 30
    hashed = auth.hash_password(password)

    assert auth.verify_password(password, hashed) is True
    assert auth.verify_password(password + "x", hashed) is False


# ----------------------------------------------------------
# Age gate (server-side, minimum 15)
# ----------------------------------------------------------

def _today_minus_15_exact() -> str:
    # The age gate measures against ``authentication._utc_now().date()``, so the
    # boundary dates must come from the same UTC clock (never local time) or the
    # day-alignment drifts across midnight and trips the 15/14 boundary.
    today = datetime.now(timezone.utc).date()
    try:
        return date(today.year - 15, today.month, today.day).isoformat()
    except ValueError:  # 29 February in a non-leap target year.
        return date(today.year - 15, today.month, 28).isoformat()


def test_register_succeeds_exactly_at_age_15(temp_db):
    success, result = auth.register_user(
        "Boundary User",
        "boundary-15@example.com",
        "Passw0rd123",
        _today_minus_15_exact(),
    )
    assert success is True, result
    assert "Registration successful" in result


def test_register_rejects_14_years_364_days(temp_db):
    from datetime import timedelta

    just_under = (date.fromisoformat(_today_minus_15_exact()) + timedelta(days=1)).isoformat()
    success, message = auth.register_user(
        "Underage User",
        "underage@example.com",
        "Passw0rd123",
        just_under,
    )
    assert success is False
    assert "aged 15" in message.lower()


def test_register_requires_a_date_of_birth(temp_db):
    success, message = auth.register_user("No Dob User", "nodob@example.com", "Passw0rd123", None)
    assert success is False
    assert "date of birth is required" in message.lower()


def test_api_register_age_boundary(temp_db):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    accepted = client.post(
        "/api/v1/auth/register",
        json={"name": "Api Age 15", "email": "api-age15@example.com", "password": "StrongPass9", "date_of_birth": _today_minus_15_exact()},
    )
    assert accepted.status_code == 200, accepted.text
    # The account is visible to its holder without any date-of-birth leak.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert "date_of_birth" not in me.text

    client2 = TestClient(app)
    rejected = client2.post(
        "/api/v1/auth/register",
        json={
            "name": "Api Age 14",
            "email": "api-age14@example.com",
            "password": "StrongPass9",
            "date_of_birth": (date.fromisoformat(_today_minus_15_exact()) + timedelta(days=1)).isoformat(),
        },
    )
    assert rejected.status_code == 400
    assert "aged 15" in rejected.text.lower()

    client3 = TestClient(app)
    missing = client3.post(
        "/api/v1/auth/register",
        json={"name": "Api No Dob", "email": "api-nodob@example.com", "password": "StrongPass9"},
    )
    assert missing.status_code == 400
    assert "date of birth is required" in missing.text.lower()
