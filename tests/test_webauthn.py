"""Tests for WebAuthn/passkey second-factor support."""
from __future__ import annotations

import pytest

from services import webauthn

pytestmark = pytest.mark.skipif(
    not webauthn.WEBAUTHN_AVAILABLE,
    reason="webauthn library not installed",
)

USER = {"id": 1, "email": "passkey@example.com", "name": "Pass Key"}


@pytest.fixture(autouse=True)
def _ensure_test_user():
    """webauthn_credentials.user_id has a FK to users(id)."""
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users(name,email,password,id) VALUES(?,?,?,?)",
            (USER["name"], USER["email"], "x", USER["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("DELETE FROM webauthn_credentials WHERE user_id=?", (USER["id"],))
        conn.commit()
    finally:
        conn.close()


def test_registration_options_shape():
    options = webauthn.begin_registration(USER)
    assert options["rp"]["id"] == webauthn.rp_id()
    assert "challenge" in options
    assert options["user"]["name"] == USER["email"]
    # The challenge doubles as the single-use server-side binding token.
    assert options["challenge"] in webauthn._CHALLENGES


def test_registration_challenge_is_single_use():
    options = webauthn.begin_registration(USER)
    token = options["challenge"]
    # consume once with a bogus payload -> verification fails but token burns
    with pytest.raises(ValueError):
        webauthn.complete_registration(USER, {"challenge": token, "response": {}})
    # second attempt with same token must fail as expired/used
    with pytest.raises(ValueError):
        webauthn.complete_registration(USER, {"challenge": token, "response": {}})


def test_challenge_bound_to_user():
    options = webauthn.begin_registration(USER)
    token = options["challenge"]
    with pytest.raises(ValueError):
        # different user id cannot consume this challenge
        webauthn.complete_registration({"id": 2, "email": "x@y.z"}, {"challenge": token})


def test_authentication_requires_registered_credential():
    with pytest.raises(ValueError):
        webauthn.begin_authentication(USER["id"])


def test_authentication_options_and_single_use():
    # Seed a fake credential directly (verification itself needs a browser).
    webauthn._ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM webauthn_credentials WHERE user_id=?", (USER["id"],))
        conn.execute(
            """INSERT INTO webauthn_credentials
               (user_id, credential_id, public_key, sign_count)
               VALUES (?,?,?,?)""",
            (USER["id"], "fake-cred-id", "ZmFrZS1wdWJsaWMta2V5", 0),
        )
        conn.commit()
    finally:
        conn.close()

    options = webauthn.begin_authentication(USER["id"])
    assert options["challenge"] in webauthn._CHALLENGES
    record = webauthn._CHALLENGES[options["challenge"]]
    assert record["kind"] == "authentication"
    assert record["user_id"] == USER["id"]
    assert record["challenge"]  # raw bytes stored for verification

    # Burning the challenge makes reuse fail even before crypto runs.
    webauthn._CHALLENGES[options["challenge"]]["consumed"] = True
    with pytest.raises(ValueError):
        webauthn.complete_authentication(USER["id"], {"challenge": options["challenge"]})

    # cleanup
    conn = get_connection()
    try:
        conn.execute("DELETE FROM webauthn_credentials WHERE user_id=?", (USER["id"],))
        conn.commit()
    finally:
        conn.close()


def test_delete_credential_scoped_to_user():
    webauthn._ensure_table()
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO webauthn_credentials
               (user_id, credential_id, public_key, sign_count)
               VALUES (?,?,?,?)""",
            (USER["id"], "del-cred", "ZmFrZS1wdWJsaWMta2V5", 0),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM webauthn_credentials WHERE credential_id='del-cred'"
        ).fetchone()
    finally:
        conn.close()
    cred_pk = row[0]
    # owner can delete
    assert webauthn.delete_credential(USER["id"], cred_pk) is True
    # second delete (already gone) fails
    assert webauthn.delete_credential(USER["id"], cred_pk) is False


def test_mismatched_origin_rejected(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_WEBAUTHN_ORIGINS", "https://evil.example")
    options = webauthn.begin_registration(USER)
    token = options["challenge"]
    with pytest.raises(ValueError):
        webauthn.complete_registration(USER, {"challenge": token, "response": {
            "id": "x", "rawId": "x", "type": "public-key",
            "response": {"clientDataJSON": "", "attestationObject": ""},
        }})
