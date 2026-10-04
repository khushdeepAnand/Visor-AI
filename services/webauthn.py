"""WebAuthn / passkey second-factor support.

Passkeys are registered by an authenticated user (step-up) and then usable
*instead of* a TOTP code when completing an MFA login challenge.

Flow:
  1. Authenticated user -> POST /auth/webauthn/register/options  -> challenge
  2. Browser creates credential -> POST /auth/webauthn/register/verify
  3. At next login the server issues an MFA challenge as usual, then the
     browser obtains assertion options and posts the assertion to
     /auth/webauthn/mfa/verify, which consumes the same challenge cookie.

Security notes:
  * Challenges are single-use, short-lived, and bound to the user.
  * Stored credentials keep a signature counter; sign-count rollback fails.
  * RP id / origin are taken from configuration (never from client input).
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from webauthn.helpers import options_to_json, structs as wa_structs
    from webauthn.registration.generate_registration_options import (
        generate_registration_options,
    )
    from webauthn.registration.verify_registration_response import (
        verify_registration_response,
    )
    from webauthn.authentication.generate_authentication_options import (
        generate_authentication_options,
    )
    from webauthn.authentication.verify_authentication_response import (
        verify_authentication_response,
    )
    WEBAUTHN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when lib absent
    WEBAUTHN_AVAILABLE = False

from database import get_connection

# Allow a single lowercase b64 variant used by browsers.
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

def rp_id() -> str:
    return os.getenv("STOCKPILOT_WEBAUTHN_RP_ID", "localhost").strip().lower()


def rp_name() -> str:
    return os.getenv("STOCKPILOT_WEBAUTHN_RP_NAME", "StockPilot AI").strip()


def expected_origins() -> list[str]:
    raw = os.getenv(
        "STOCKPILOT_WEBAUTHN_ORIGINS",
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:3000,http://127.0.0.1:8000",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


def _ensure_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webauthn_credentials(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                credential_id TEXT NOT NULL UNIQUE,
                public_key TEXT NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                transports TEXT,
                label TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_id)"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------
# In-memory challenge store (single-use, short-lived)
# ---------------------------------------------------------------------

_CHALLENGES: dict[str, dict[str, Any]] = {}
_CHALLENGE_TTL = timedelta(minutes=5)


def _new_challenge(kind: str, user_id: int, challenge_bytes: bytes) -> tuple[str, dict[str, Any]]:
    # Prune expired
    now = datetime.now(timezone.utc)
    for key in [k for k, v in _CHALLENGES.items() if v["expires"] <= now]:
        _CHALLENGES.pop(key, None)
    # The lookup key IS the base64url challenge the browser received, so the
    # frontend simply echoes options.challenge back -- single-use + bound user.
    token = _b64url_encode(challenge_bytes)
    record = {
        "user_id": user_id,
        "kind": kind,          # "registration" | "authentication"
        "challenge": challenge_bytes,
        "expires": now + _CHALLENGE_TTL,
        "consumed": False,
    }
    _CHALLENGES[token] = record
    return token, record


def _consume_challenge(token: str, user_id: int, kind: str) -> dict[str, Any]:
    record = _CHALLENGES.get(token)
    if (
        record is None
        or record["consumed"]
        or record["expires"] <= datetime.now(timezone.utc)
        or int(record["user_id"]) != int(user_id)
        or record["kind"] != kind
    ):
        raise ValueError("The passkey challenge is invalid or expired.")
    record["consumed"] = True
    return record


# ---------------------------------------------------------------------
# Credential storage
# ---------------------------------------------------------------------

def list_credentials(user_id: int) -> list[dict[str, Any]]:
    _ensure_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, credential_id, label, transports, created_at, last_used_at
               FROM webauthn_credentials WHERE user_id=? ORDER BY created_at""",
            (int(user_id),),
        ).fetchall()
        return [
            {
                "id": r[0],
                "credential_id": r[1],
                "label": r[2] or "Passkey",
                "transports": json.loads(r[3] or "[]"),
                "created_at": r[4],
                "last_used_at": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def has_credentials(user_id: int) -> bool:
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM webauthn_credentials WHERE user_id=? LIMIT 1",
            (int(user_id),),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------

def begin_registration(user: dict[str, Any]) -> dict[str, Any]:
    """Return registration options for an already-authenticated user."""
    if not WEBAUTHN_AVAILABLE:
        raise ValueError("WebAuthn support is not installed (pip install webauthn).")
    _ensure_table()

    user_id = int(user["id"])
    conn = get_connection()
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT credential_id FROM webauthn_credentials WHERE user_id=?",
                (user_id,),
            ).fetchall()
        }
    finally:
        conn.close()

    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name=rp_name(),
        user_id=str(user_id).encode(),
        user_name=str(user["email"]),
        user_display_name=str(user.get("name") or user["email"]),
        exclude_credentials=[
            wa_structs.PublicKeyCredentialDescriptor(id=_b64url_decode(cid))
            for cid in existing
        ] or None,
        timeout=60000,
    )
    token, _ = _new_challenge("registration", user_id, bytes(options.challenge))
    payload = json.loads(options_to_json(options))
    # payload["challenge"] already equals token (b64url of raw challenge);
    # frontend must echo it back verbatim as body["challenge"].
    return dict(payload)


def complete_registration(user: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Verify attestation and persist the new credential."""
    if not WEBAUTHN_AVAILABLE:
        raise ValueError("WebAuthn support is not installed.")
    user_id = int(user["id"])
    challenge_token = body.get("challenge")
    if not challenge_token:
        raise ValueError("The passkey challenge is invalid or expired.")
    record = _consume_challenge(str(challenge_token), user_id, "registration")
    challenge = record.get("challenge")
    if not challenge:
        raise ValueError("The passkey challenge is invalid or expired.")

    client_response = body.get("response") or body
    try:
        verification = verify_registration_response(
            credential=client_response,
            expected_challenge=challenge,
            expected_origin=expected_origins(),
            expected_rp_id=rp_id(),
        )
    except Exception as exc:
        raise ValueError(f"Passkey registration failed verification: {exc}") from exc

    credential_id = _b64url_encode(bytes(verification.credential_id))
    public_key = _b64url_encode(bytes(verification.credential_public_key))
    try:
        transports = json.dumps(
            list((client_response.get("response") or {}).get("transports") or [])
        )
    except Exception:
        transports = "[]"

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO webauthn_credentials
               (user_id, credential_id, public_key, sign_count, transports, label)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(credential_id) DO UPDATE SET
                 sign_count=excluded.sign_count,
                 transports=excluded.transports""",
            (user_id, credential_id, public_key, int(verification.sign_count),
             transports, str(body.get("label") or "Passkey")),
        )
        conn.commit()
    finally:
        conn.close()

    from database import record_audit_event
    record_audit_event(user_id, "webauthn.register", entity_type="credential",
                       entity_id=credential_id)
    return {"registered": True, "credential_id": credential_id}


# ---------------------------------------------------------------------
# Authentication (alternative MFA factor)
# ---------------------------------------------------------------------

def begin_authentication(user_id: int) -> dict[str, Any]:
    """Return assertion options for a user's registered passkeys."""
    if not WEBAUTHN_AVAILABLE:
        raise ValueError("WebAuthn support is not installed.")
    _ensure_table()

    creds = list_credentials(user_id)
    if not creds:
        raise ValueError("No passkeys registered for this account.")
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=[
            wa_structs.PublicKeyCredentialDescriptor(id=_b64url_decode(c["credential_id"]))
            for c in creds
        ],
        timeout=60000,
    )
    token, _ = _new_challenge("authentication", int(user_id), bytes(options.challenge))
    payload = json.loads(options_to_json(options))
    # payload["challenge"] == token; frontend echoes it back verbatim.
    return dict(payload)


def complete_authentication(user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """Verify an assertion; returns {"verified": True} or raises ValueError."""
    if not WEBAUTHN_AVAILABLE:
        raise ValueError("WebAuthn support is not installed.")
    challenge_token = body.get("challenge")
    if not challenge_token:
        raise ValueError("The passkey challenge is invalid or expired.")
    record = _consume_challenge(str(challenge_token), int(user_id), "authentication")
    challenge = record.get("challenge")
    if not challenge:
        raise ValueError("The passkey challenge is invalid or expired.")

    client_response = body.get("response") or body
    credential_id = client_response.get("id")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, public_key, sign_count FROM webauthn_credentials "
            "WHERE credential_id=? AND user_id=?",
            (str(credential_id), int(user_id)),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("The passkey is not registered for this account.")

    try:
        verification = verify_authentication_response(
            credential=client_response,
            expected_challenge=challenge,
            expected_origin=expected_origins(),
            expected_rp_id=rp_id(),
            credential_public_key=_b64url_decode(str(row[1])),
            credential_current_sign_count=int(row[2]),
        )
    except Exception as exc:
        raise ValueError(f"Passkey verification failed: {exc}") from exc

    new_count = int(verification.new_sign_count)
    if new_count and new_count <= int(row[2]):
        # Counter rollback indicates a cloned authenticator -> revoke it.
        conn = get_connection()
        try:
            conn.execute("DELETE FROM webauthn_credentials WHERE id=?", (int(row[0]),))
            conn.commit()
        finally:
            conn.close()
        from database import record_audit_event
        record_audit_event(user_id, "webauthn.counter_rollback",
                           entity_type="credential", entity_id=str(credential_id))
        raise ValueError("This passkey was revoked because its counter regressed.")

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count=?, last_used_at=? WHERE id=?",
            (new_count, datetime.now(timezone.utc).isoformat(), int(row[0])),
        )
        conn.commit()
    finally:
        conn.close()
    return {"verified": True}


def delete_credential(user_id: int, credential_id: int) -> bool:
    _ensure_table()
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM webauthn_credentials WHERE id=? AND user_id=?",
            (int(credential_id), int(user_id)),
        )
        conn.commit()
        return bool(cursor.rowcount > 0)
    finally:
        conn.close()
