"""JWT cookie authentication bridge for the FastAPI/Next.js rebuild."""
from __future__ import annotations

import os
import secrets
import hashlib
import base64
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from authentication import get_user_by_id, login_user, register_user
from services.db.factory import dao_session
from services.db.configuration import postgres_selected

JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.getenv("STOCKPILOT_SESSION_HOURS", "12"))
MFA_CHALLENGE_MINUTES = 5
MFA_MAX_ATTEMPTS = 5
RECOVERY_CODE_COUNT = 10
LOCAL_ENVIRONMENTS = {"development", "test"}
LOCAL_DEVELOPMENT_KEY = secrets.token_urlsafe(32)


def _explicit_secret() -> str | None:
    value = os.getenv("STOCKPILOT_JWT_SECRET", "")
    return value if len(value.encode("utf-8")) >= 32 else None


def _secret() -> str:
    explicit = _explicit_secret()
    if explicit:
        return explicit
    environment = os.getenv("STOCKPILOT_ENV", "").strip().lower()
    provider_mode = os.getenv("STOCKPILOT_PROVIDER_MODE", "LIVE_ONLY").strip().upper()
    if environment in LOCAL_ENVIRONMENTS and provider_mode != "OFFLINE_DEMO":
        return LOCAL_DEVELOPMENT_KEY
    raise RuntimeError(
        "STOCKPILOT_JWT_SECRET must contain at least 32 bytes outside explicit local development."
    )


def validate_jwt_configuration() -> None:
    _secret()


def _device_label(request: Any | None) -> str:
    if request is None:
        return "Unknown device"
    user_agent = str(getattr(request, "headers", {}).get("user-agent", ""))[:512]
    lowered = user_agent.lower()
    if "edg/" in lowered:
        browser = "Edge"
    elif "firefox/" in lowered or "fxios/" in lowered:
        browser = "Firefox"
    elif "chrome/" in lowered or "crios/" in lowered:
        browser = "Chrome"
    elif "safari/" in lowered:
        browser = "Safari"
    else:
        browser = "Browser"

    if "android" in lowered:
        platform = "Android"
    elif "iphone" in lowered or "ipad" in lowered:
        platform = "iOS"
    elif "windows" in lowered:
        platform = "Windows"
    elif "macintosh" in lowered or "mac os" in lowered:
        platform = "macOS"
    elif "linux" in lowered:
        platform = "Linux"
    else:
        platform = "unknown OS"
    return f"{browser} on {platform}"[:80]


def create_access_token(user: dict[str, Any], request: Any | None = None) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=TOKEN_TTL_HOURS)
    jti = secrets.token_urlsafe(32)
    current = get_user_by_id(int(user["id"]))
    if current is None:
        raise RuntimeError("Account cannot be loaded for session issuance.")
    token_version = int(current.get("token_version", 0))
    payload = {
        "sub": str(user["id"]),
        "email": user.get("email"),
        "name": user.get("name"),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": "stockpilot-ai",
        "aud": "stockpilot-web",
        "jti": jti,
        "ver": token_version,
    }
    token = jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)
    with dao_session() as factory:
        db = factory.db
        db.execute(db.sql(
            "INSERT INTO auth_sessions(user_id,jti_hash,token_version,issued_at,expires_at,device_label) VALUES(?,?,?,?,?,?)"),
            (
                int(user["id"]),
                _hash_jti(jti),
                token_version,
                now.isoformat(),
                expires.isoformat(),
                _device_label(request),
            ),
        )
        db.commit()
    return token


def _hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM], issuer="stockpilot-ai", audience="stockpilot-web")
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired session.") from exc


def authenticate(email: str, password: str) -> tuple[bool, dict[str, Any] | str]:
    return login_user(email, password)


def register(name: str, email: str, password: str, date_of_birth: str | None = None) -> tuple[bool, dict[str, Any] | str]:
    ok, result = register_user(name, email, password, date_of_birth)
    if not ok:
        return False, result
    ok, user = login_user(email, password)
    return (True, user) if ok else (False, str(user))


def user_from_token(token: str) -> dict[str, Any] | None:
    payload = decode_access_token(token)
    user = get_user_by_id(int(payload["sub"]))
    if not user or user.get("account_status") != "active" or int(payload.get("ver", -1)) != int(user.get("token_version", 0)):
        return None
    with dao_session() as factory:
        db = factory.db
        session = db.fetchone(db.sql(
            "SELECT revoked_at FROM auth_sessions WHERE user_id=? AND jti_hash=? AND token_version=?"),
            (int(user["id"]), _hash_jti(str(payload.get("jti") or "")), int(payload.get("ver", -1))),
        )
    if session is None or session["revoked_at"] is not None:
        return None
    return user


def revoke_token(token: str) -> None:
    payload = decode_access_token(token)
    with dao_session() as factory:
        db = factory.db
        db.execute(db.sql(
            "UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND jti_hash=?"),
            (int(payload["sub"]), _hash_jti(str(payload.get("jti") or ""))),
        )
        db.commit()


def list_sessions(user_id: int, token: str | None = None) -> list[dict[str, Any]]:
    current_jti_hash = None
    if token:
        try:
            current_jti_hash = _hash_jti(str(decode_access_token(token).get("jti") or ""))
        except ValueError:
            pass
    with dao_session() as factory:
        db = factory.db
        rows = db.fetchall(db.sql(
            "SELECT id,issued_at,expires_at,revoked_at,device_label,jti_hash FROM auth_sessions WHERE user_id=? ORDER BY id DESC LIMIT 50"),
            (int(user_id),),
        )
        return [
            {
                "id": row["id"],
                "issued_at": row["issued_at"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
                "device_label": row["device_label"] or "Unknown device",
                "current": current_jti_hash is not None and hmac.compare_digest(str(row["jti_hash"]), current_jti_hash),
            }
            for row in rows
        ]


def revoke_session(user_id: int, session_id: int) -> bool:
    with dao_session() as factory:
        db = factory.db
        cursor = db.execute(db.sql(
            "UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND revoked_at IS NULL"),
            (int(session_id), int(user_id)),
        )
        db.commit()
        return cursor.rowcount > 0


def revoke_all_sessions(user_id: int) -> None:
    with dao_session() as factory:
        db = factory.db
        db.begin_write("security:" + str(user_id))
        db.execute(db.sql("UPDATE users SET token_version=token_version+1 WHERE id=?"), (int(user_id),))
        db.execute(db.sql("UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL"), (int(user_id),))
        db.commit()


def revoke_other_sessions(user_id: int, token: str) -> None:
    payload = decode_access_token(token)
    current_jti_hash = _hash_jti(str(payload.get("jti") or ""))
    with dao_session() as factory:
        db = factory.db
        db.execute(db.sql(
            "UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND jti_hash<>? AND revoked_at IS NULL"),
            (int(user_id), current_jti_hash),
        )
        db.commit()


def _mfa_key_material() -> bytes:
    configured = os.getenv("STOCKPILOT_MFA_SECRET", "")
    if configured and len(configured.encode("utf-8")) < 32:
        raise RuntimeError("STOCKPILOT_MFA_SECRET must contain at least 32 bytes when configured.")
    return (configured or _secret()).encode("utf-8")


def _mfa_fernet() -> Fernet:
    derived = hashlib.sha256(b"stockpilot:mfa:encryption:v1\0" + _mfa_key_material()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _encrypt_totp_secret(secret: str) -> str:
    return _mfa_fernet().encrypt(secret.encode("ascii")).decode("ascii")


def _decrypt_totp_secret(encrypted: str) -> str:
    try:
        return _mfa_fernet().decrypt(str(encrypted).encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise RuntimeError("MFA configuration cannot be decrypted.") from exc


def _recovery_hash(code: str) -> str:
    normalized = "".join(character for character in str(code).upper() if character.isalnum())
    key = hmac.new(_mfa_key_material(), b"stockpilot:mfa:recovery:v1", hashlib.sha256).digest()
    return hmac.new(key, normalized.encode("ascii", "ignore"), hashlib.sha256).hexdigest()


def _new_recovery_codes() -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return [
        f"{''.join(secrets.choice(alphabet) for _ in range(4))}-{''.join(secrets.choice(alphabet) for _ in range(4))}"
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def mfa_status(user_id: int) -> dict[str, Any]:
    from services.webauthn import _ensure_table
    _ensure_table()
    with dao_session() as factory:
        db = factory.db
        row = db.fetchone(db.sql(
            "SELECT enabled FROM user_mfa WHERE user_id=?"),
            (int(user_id),),
        )
        remaining_row = db.fetchone(db.sql(
            "SELECT COUNT(*) AS n FROM mfa_recovery_codes WHERE user_id=? AND used_at IS NULL"),
            (int(user_id),),
        )
        passkey_row = db.fetchone(db.sql("SELECT COUNT(*) AS n FROM webauthn_credentials WHERE user_id=?"), (int(user_id),))
        remaining = remaining_row["n"] if remaining_row else 0
        passkeys = passkey_row["n"] if passkey_row else 0
        return {"enabled": bool((row and row["enabled"]) or passkeys), "totp_enabled": bool(row and row["enabled"]),
                "passkeys": int(passkeys), "recovery_codes_remaining": int(remaining or 0)}


def begin_mfa_enrollment(user: dict[str, Any]) -> dict[str, str]:
    if mfa_status(int(user["id"]))["totp_enabled"]:
        raise ValueError("Multi-factor authentication is already enabled.")
    secret = pyotp.random_base32(length=32)
    encrypted = _encrypt_totp_secret(secret)
    now = datetime.now(timezone.utc).isoformat()
    with dao_session() as factory:
        db = factory.db
        db.begin_write("security:" + str(user["id"]))
        current = db.fetchone(db.sql("SELECT enabled FROM user_mfa WHERE user_id=?"), (int(user["id"]),))
        if current and current["enabled"]:
            raise ValueError("Multi-factor authentication is already enabled.")
        db.execute(db.sql(
            """
            INSERT INTO user_mfa(user_id,encrypted_totp_secret,enabled,last_totp_counter,updated_at)
            VALUES(?,?,0,NULL,?)
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_totp_secret=excluded.encrypted_totp_secret,
                enabled=0,
                last_totp_counter=NULL,
                updated_at=excluded.updated_at
            """),
            (int(user["id"]), encrypted, now),
        )
        db.execute(db.sql("DELETE FROM mfa_recovery_codes WHERE user_id=?"), (int(user["id"]),))
        db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=str(user.get("email") or user["id"]),
        issuer_name="StockPilot AI",
    )
    return {"secret": secret, "provisioning_uri": uri}


def _matching_totp_counter(secret: str, code: str, *, now: datetime | None = None) -> int | None:
    normalized = "".join(str(code).split())
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    totp = pyotp.TOTP(secret)
    current_counter = timestamp // int(totp.interval)
    for offset in (-1, 0, 1):
        candidate = current_counter + offset
        if candidate >= 0 and hmac.compare_digest(totp.at(candidate * int(totp.interval)), normalized):
            return candidate
    return None


def _verify_mfa_code(connection: Any, user_id: int, code: str, *, require_enabled: bool) -> tuple[bool, str | None]:
    query = "SELECT encrypted_totp_secret,enabled,last_totp_counter FROM user_mfa WHERE user_id=?"
    if postgres_selected():
        query += " FOR UPDATE"
    row = connection.fetchone(connection.sql(query),
        (int(user_id),),
    )
    if row is None or (require_enabled and not bool(row["enabled"])):
        return False, None
    counter = _matching_totp_counter(_decrypt_totp_secret(str(row["encrypted_totp_secret"])), code)
    if counter is not None and (row["last_totp_counter"] is None or counter > int(row["last_totp_counter"])):
        cursor = connection.execute(connection.sql(
            "UPDATE user_mfa SET last_totp_counter=?,updated_at=? WHERE user_id=? AND (last_totp_counter IS NULL OR last_totp_counter<?)"),
            (counter, datetime.now(timezone.utc).isoformat(), int(user_id), counter),
        )
        if cursor.rowcount == 1:
            return True, "totp"
    if not require_enabled:
        return False, None
    code_hash = _recovery_hash(code)
    recovery = connection.fetchone(connection.sql(
        "SELECT id FROM mfa_recovery_codes WHERE user_id=? AND code_hash=? AND used_at IS NULL LIMIT 1"),
        (int(user_id), code_hash),
    )
    if recovery is None:
        return False, None
    cursor = connection.execute(connection.sql(
        "UPDATE mfa_recovery_codes SET used_at=? WHERE id=? AND used_at IS NULL"),
        (datetime.now(timezone.utc).isoformat(), int(recovery["id"])),
    )
    return cursor.rowcount == 1, "recovery"


def enable_mfa(user_id: int, code: str) -> list[str]:
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        valid, method = _verify_mfa_code(connection, user_id, code, require_enabled=False)
        if not valid or method != "totp":
            connection.rollback()
            raise ValueError("The verification code is invalid or expired.")
        recovery_codes = _new_recovery_codes()
        connection.execute(connection.sql(
            "UPDATE user_mfa SET enabled=1,updated_at=? WHERE user_id=?"),
            (datetime.now(timezone.utc).isoformat(), int(user_id)),
        )
        for code_value in recovery_codes:
            connection.execute(connection.sql("INSERT INTO mfa_recovery_codes(user_id,code_hash) VALUES(?,?)"),
                               (int(user_id), _recovery_hash(code_value)))
        connection.commit()
        return recovery_codes


def verify_current_mfa(user_id: int, code: str) -> str:
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        valid, method = _verify_mfa_code(connection, user_id, code, require_enabled=True)
        if not valid or method is None:
            connection.rollback()
            raise ValueError("The verification code is invalid or expired.")
        connection.commit()
        return method


def regenerate_recovery_codes(user_id: int, code: str) -> list[str]:
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        valid, _ = _verify_mfa_code(connection, user_id, code, require_enabled=True)
        if not valid:
            connection.rollback()
            raise ValueError("The verification code is invalid or expired.")
        recovery_codes = _new_recovery_codes()
        connection.execute(connection.sql("DELETE FROM mfa_recovery_codes WHERE user_id=?"), (int(user_id),))
        for code_value in recovery_codes:
            connection.execute(connection.sql("INSERT INTO mfa_recovery_codes(user_id,code_hash) VALUES(?,?)"),
                               (int(user_id), _recovery_hash(code_value)))
        connection.commit()
        return recovery_codes


def disable_mfa(user_id: int, code: str) -> None:
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        valid, _ = _verify_mfa_code(connection, user_id, code, require_enabled=True)
        if not valid:
            connection.rollback()
            raise ValueError("The verification code is invalid or expired.")
        connection.execute(connection.sql("DELETE FROM user_mfa WHERE user_id=?"), (int(user_id),))
        connection.commit()


def issue_mfa_challenge(user: dict[str, Any], *, next_path: str = "/", device_hash: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=MFA_CHALLENGE_MINUTES)
    jti = secrets.token_urlsafe(32)
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    payload = {
        "sub": str(user["id"]),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": "stockpilot-ai",
        "aud": "stockpilot-mfa",
        "jti": jti,
        "type": "mfa_challenge",
        "next": safe_next,
        "device_hash": device_hash,
    }
    token = jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user["id"]))
        connection.execute(connection.sql(
            "UPDATE mfa_challenges SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL"),
            (now.isoformat(), int(user["id"])),
        )
        connection.execute(connection.sql(
            "INSERT INTO mfa_challenges(user_id,jti_hash,expires_at) VALUES(?,?,?)"),
            (int(user["id"]), _hash_jti(jti), expires.isoformat()),
        )
        connection.commit()
    return token


def complete_mfa_challenge(token: str, code: str, *, device_hash: str | None = None) -> tuple[dict[str, Any], str, str]:
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            issuer="stockpilot-ai",
            audience="stockpilot-mfa",
        )
    except jwt.PyJWTError as exc:
        raise ValueError("The verification challenge is invalid or expired.") from exc
    if payload.get("type") != "mfa_challenge":
        raise ValueError("The verification challenge is invalid or expired.")
    if payload.get("device_hash") and payload["device_hash"] != device_hash:
        raise ValueError("Verification must complete on the device that started sign-in.")
    user_id = int(payload["sub"])
    now = datetime.now(timezone.utc)
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        query = "SELECT id,expires_at,failed_attempts,consumed_at FROM mfa_challenges WHERE user_id=? AND jti_hash=? LIMIT 1"
        if postgres_selected():
            query += " FOR UPDATE"
        row = connection.fetchone(connection.sql(query),
            (user_id, _hash_jti(str(payload.get("jti") or ""))),
        )
        if row is None or row["consumed_at"] is not None or datetime.fromisoformat(str(row["expires_at"])).astimezone(timezone.utc) <= now:
            connection.rollback()
            raise ValueError("The verification challenge is invalid or expired.")
        if int(row["failed_attempts"] or 0) >= MFA_MAX_ATTEMPTS:
            connection.rollback()
            raise ValueError("The verification challenge is invalid or expired.")
        valid, method = _verify_mfa_code(connection, user_id, code, require_enabled=True)
        if not valid or method is None:
            failed_attempts = int(row["failed_attempts"] or 0) + 1
            connection.execute(connection.sql(
                "UPDATE mfa_challenges SET failed_attempts=?,consumed_at=CASE WHEN ?>=? THEN ? ELSE consumed_at END WHERE id=?"),
                (failed_attempts, failed_attempts, MFA_MAX_ATTEMPTS, now.isoformat(), int(row["id"])),
            )
            connection.commit()
            raise ValueError("The verification code is invalid or expired.")
        cursor = connection.execute(connection.sql(
            "UPDATE mfa_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL"),
            (now.isoformat(), int(row["id"])),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise ValueError("The verification challenge is invalid or expired.")
        connection.commit()
    user = get_user_by_id(user_id)
    if not user or user.get("account_status") != "active":
        raise ValueError("The verification challenge is invalid or expired.")
    if payload.get("device_hash"):
        from services.login_anomaly import confirm_device
        confirm_device(user_id, str(payload["device_hash"]))
    return user, str(payload.get("next") or "/"), method


def consume_mfa_challenge_for_passkey(token: str, *, device_hash: str | None = None) -> tuple[int, str]:
    """Validate + consume an MFA challenge when a passkey assertion succeeded.

    The passkey cryptography was verified separately by services.webauthn;
    this only enforces the challenge lifecycle (signature, expiry, single-use,
    attempt cap) and returns ``(user_id, next_path)``.
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            issuer="stockpilot-ai",
            audience="stockpilot-mfa",
        )
    except jwt.PyJWTError as exc:
        raise ValueError("The verification challenge is invalid or expired.") from exc
    if payload.get("type") != "mfa_challenge":
        raise ValueError("The verification challenge is invalid or expired.")
    if payload.get("device_hash") and payload["device_hash"] != device_hash:
        raise ValueError("Verification must complete on the device that started sign-in.")
    user_id = int(payload["sub"])
    now = datetime.now(timezone.utc)
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("security:" + str(user_id))
        query = "SELECT id,expires_at,failed_attempts,consumed_at FROM mfa_challenges WHERE user_id=? AND jti_hash=? LIMIT 1"
        if postgres_selected():
            query += " FOR UPDATE"
        row = connection.fetchone(connection.sql(query),
            (user_id, _hash_jti(str(payload.get("jti") or ""))),
        )
        if row is None or row["consumed_at"] is not None or int(row["failed_attempts"] or 0) >= MFA_MAX_ATTEMPTS or datetime.fromisoformat(str(row["expires_at"])).astimezone(timezone.utc) <= now:
            connection.rollback()
            raise ValueError("The verification challenge is invalid or expired.")
        cursor = connection.execute(connection.sql(
            "UPDATE mfa_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL"),
            (now.isoformat(), int(row["id"])),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise ValueError("The verification challenge is invalid or expired.")
        connection.commit()
    if payload.get("device_hash"):
        from services.login_anomaly import confirm_device
        confirm_device(user_id, str(payload["device_hash"]))
    return user_id, str(payload.get("next") or "/")


def production_secret_configured() -> bool:
    return _explicit_secret() is not None
