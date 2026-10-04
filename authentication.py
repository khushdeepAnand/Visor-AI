# ==========================================================
# StockPilot AI
# Authentication Service
# ==========================================================

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

bcrypt: Any = None
try:
    import bcrypt as _bcrypt
    bcrypt = _bcrypt
except ImportError:  # Standard-library scrypt remains available offline.
    pass


# ==========================================================
# DATABASE CONFIGURATION
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_PATH = BASE_DIR / "database" / "stockpilot.db"


# ==========================================================
# VALIDATION CONFIGURATION
# ==========================================================

MINIMUM_PASSWORD_LENGTH = 8
OAUTH_PASSWORD_MARKER = "!stockpilot-google-oauth!"
APPLE_OAUTH_PASSWORD_MARKER = "!stockpilot-apple-oauth!"
MAX_FAILED_ATTEMPTS = 5
LOGIN_WINDOW_MINUTES = 15
LOCKOUT_MINUTES = 15
PASSWORD_RESET_MINUTES = 30
#: Server-side minimum account-holder age, checked on the registration date.
MINIMUM_ACCOUNT_AGE = 15
#: Upper sanity bound beyond which a date of birth is treated as invalid.
MAX_ACCOUNT_AGE = 130
# A single process-stable bcrypt hash keeps unknown-account verification on the
# same expensive code path as normal password accounts without hashing twice.
DUMMY_PASSWORD_HASH = "$2b$12$6SUqRDbU8mfTy808rJKbXu7rrrgT2sh/Qlx5qi8xidlrtV4Yps9M."

EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_connection():
    """
    Create and return a connection to the StockPilot database.
    """

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30
    )

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


# ==========================================================
# INPUT NORMALIZATION
# ==========================================================

def normalize_name(name):
    """
    Normalize a user's display name.
    """

    if name is None:
        return ""

    normalized = " ".join(
        str(name).strip().split()
    )

    return normalized


def normalize_email(email):
    """
    Normalize an email address for storage and comparison.
    """

    if email is None:
        return ""

    return str(email).strip().lower()


# ==========================================================
# VALIDATION
# ==========================================================

def validate_name(name):
    """
    Validate a user's display name.

    Returns:
        tuple:
            validation result,
            validation message
    """

    normalized_name = normalize_name(name)

    if not normalized_name:
        return False, "Name is required."

    if len(normalized_name) < 2:
        return False, "Name must contain at least 2 characters."

    if len(normalized_name) > 80:
        return False, "Name cannot exceed 80 characters."

    if not any(
        character.isalpha()
        for character in normalized_name
    ):
        return False, "Name must contain alphabetic characters."

    return True, ""


def validate_email(email):
    """
    Validate an email address.

    Returns:
        tuple:
            validation result,
            validation message
    """

    normalized_email = normalize_email(email)

    if not normalized_email:
        return False, "Email address is required."

    if len(normalized_email) > 254:
        return False, "Email address is too long."

    if not EMAIL_PATTERN.fullmatch(
        normalized_email
    ):
        return False, "Enter a valid email address."

    return True, ""


def validate_password(password):
    """
    Validate password strength.

    The rules remain understandable and suitable for the
    current project:
        - at least 8 characters
        - at least one uppercase letter
        - at least one lowercase letter
        - at least one number
    """

    if password is None:
        return False, "Password is required."

    password = str(password)

    if not password:
        return False, "Password is required."

    if len(password) < MINIMUM_PASSWORD_LENGTH:
        return (
            False,
            "Password must contain at least 8 characters."
        )

    if len(password) > 128:
        return False, "Password cannot exceed 128 characters."

    if not any(
        character.isupper()
        for character in password
    ):
        return (
            False,
            "Password must include at least one uppercase letter."
        )

    if not any(
        character.islower()
        for character in password
    ):
        return (
            False,
            "Password must include at least one lowercase letter."
        )

    if not any(
        character.isdigit()
        for character in password
    ):
        return (
            False,
            "Password must include at least one number."
        )

    return True, ""


# ==========================================================
# PASSWORD SECURITY
# ==========================================================

def _hash_password_with_scrypt(password_bytes):
    """Create a versioned scrypt hash using only the Python standard library."""

    salt = os.urandom(16)
    derived_key = hashlib.scrypt(
        password_bytes,
        salt=salt,
        n=2 ** 14,
        r=8,
        p=1,
        dklen=64,
    )

    return "$stockpilot$scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived_key).decode("ascii"),
    )


def _verify_scrypt_password(password_bytes, hashed):
    try:
        parts = str(hashed).split("$")
        if len(parts) != 8 or parts[1:3] != ["stockpilot", "scrypt"]:
            return False

        n = int(parts[3])
        r = int(parts[4])
        p = int(parts[5])
        salt = base64.urlsafe_b64decode(parts[6].encode("ascii"))
        expected = base64.urlsafe_b64decode(parts[7].encode("ascii"))

        actual = hashlib.scrypt(
            password_bytes,
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)

    except (ValueError, TypeError, IndexError, OSError):
        return False


def hash_password(password):
    """Hash a password with bcrypt, falling back to versioned scrypt.

    bcrypt is the primary scheme for normal passwords. Passwords exceeding
    bcrypt's 72-byte input limit, or environments where bcrypt is unavailable,
    use Python's memory-hard scrypt implementation instead of crashing.
    """

    if password is None:
        raise ValueError("Password is required.")

    password_bytes = str(password).encode("utf-8")

    if bcrypt is not None and len(password_bytes) <= 72:
        hashed_password = bcrypt.hashpw(
            password_bytes,
            bcrypt.gensalt(rounds=12),
        )
        return hashed_password.decode("utf-8")

    return _hash_password_with_scrypt(password_bytes)


def verify_password(password, hashed):
    """Verify bcrypt and StockPilot scrypt hashes without exposing errors."""

    if password is None or not hashed:
        return False

    password_bytes = str(password).encode("utf-8")
    hashed_text = str(hashed)

    if hashed_text.startswith("$stockpilot$scrypt$"):
        return _verify_scrypt_password(password_bytes, hashed_text)

    if bcrypt is None:
        return False

    try:
        return bcrypt.checkpw(password_bytes, hashed_text.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ==========================================================
# REGISTER
# ==========================================================

def validate_date_of_birth(date_of_birth, minimum_age=MINIMUM_ACCOUNT_AGE):
    """Return (valid, message) for a server-side date-of-birth age gate.

    ``minimum_age`` is measured in completed years as of today, so an account
    holder who turns the minimum age today is accepted. The message is generic
    on purpose: it must never leak whether a specific date was "too old" or
    merely malformed.
    """
    raw = str(date_of_birth or "").strip()
    if not raw:
        return False, "Date of birth is required."
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, "Date of birth is required and must be a valid calendar date."
    today = _utc_now().date()
    if parsed > today:
        return False, "Date of birth is required and must be a valid calendar date."
    age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
    if age < int(minimum_age):
        return False, "This workspace is for account holders aged 15 and over."
    if age > MAX_ACCOUNT_AGE:
        return False, "Date of birth is required and must be a valid calendar date."
    return True, ""


def register_user(
    name,
    email,
    password,
    date_of_birth=None
):

    normalized_name = normalize_name(name)
    normalized_email = normalize_email(email)

    valid, message = validate_name(
        normalized_name
    )

    if not valid:
        return False, message

    valid, message = validate_email(
        normalized_email
    )

    if not valid:
        return False, message

    valid, message = validate_password(
        password
    )

    if not valid:
        return False, message

    valid, message = validate_date_of_birth(
        date_of_birth
    )

    if not valid:
        return False, message

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM users
            WHERE LOWER(email) = ?
            """,
            (
                normalized_email,
            )
        )

        existing_user = cursor.fetchone()

        if existing_user:
            return (
                False,
                "An account with this email already exists."
            )

        hashed_password = hash_password(
            password
        )

        cursor.execute(
            """
            INSERT INTO users(
                name,
                email,
                password,
                date_of_birth
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                normalized_name,
                normalized_email,
                hashed_password,
                str(date_of_birth or "").strip()
            )
        )

        user_id = cursor.lastrowid

        # Create default user settings when the settings table
        # is available. This keeps registration compatible with
        # databases created before the settings feature.
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO settings(
                    user_id,
                    theme,
                    currency,
                    default_period
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    "Dark",
                    "₹",
                    "1y"
                )
            )

        except sqlite3.OperationalError:
            pass

        connection.commit()

        return (
            True,
            "Registration successful. You can now log in."
        )

    except sqlite3.IntegrityError:
        if connection is not None:
            connection.rollback()

        return (
            False,
            "An account with this email already exists."
        )

    except sqlite3.Error:
        if connection is not None:
            connection.rollback()

        return (
            False,
            "Registration could not be completed. Please try again."
        )

    finally:
        if connection is not None:
            connection.close()


# ==========================================================
# LOGIN SECURITY HELPERS
# ==========================================================

def _utc_now():
    return datetime.now(timezone.utc)


def _parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _login_lock_status(cursor, identifier):
    cursor.execute(
        """
        SELECT failed_count, window_started, locked_until
        FROM auth_login_attempts
        WHERE identifier = ?
        """,
        (identifier,),
    )
    row = cursor.fetchone()
    if not row:
        return False, 0

    locked_until = _parse_utc(row[2])
    now = _utc_now()
    if locked_until and locked_until > now:
        remaining = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        return True, remaining
    return False, 0


def _record_failed_login(cursor, identifier):
    now = _utc_now()
    cursor.execute(
        """
        SELECT failed_count, window_started
        FROM auth_login_attempts
        WHERE identifier = ?
        """,
        (identifier,),
    )
    row = cursor.fetchone()
    window_started = _parse_utc(row[1]) if row else None
    if not row or not window_started or now - window_started > timedelta(minutes=LOGIN_WINDOW_MINUTES):
        failed_count = 1
        window_started = now
    else:
        failed_count = int(row[0] or 0) + 1

    locked_until = None
    if failed_count >= MAX_FAILED_ATTEMPTS:
        locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)

    cursor.execute(
        """
        INSERT INTO auth_login_attempts(
            identifier, failed_count, window_started, locked_until, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(identifier) DO UPDATE SET
            failed_count = excluded.failed_count,
            window_started = excluded.window_started,
            locked_until = excluded.locked_until,
            updated_at = excluded.updated_at
        """,
        (
            identifier,
            failed_count,
            window_started.isoformat(),
            locked_until.isoformat() if locked_until else None,
            now.isoformat(),
        ),
    )
    return locked_until is not None


def _clear_failed_logins(cursor, identifier):
    cursor.execute("DELETE FROM auth_login_attempts WHERE identifier = ?", (identifier,))


# ==========================================================
# LOGIN
# ==========================================================

def login_user(email, password):
    """Authenticate a user with generic errors and database-backed rate limiting."""

    normalized_email = normalize_email(email)
    valid, message = validate_email(normalized_email)
    if not valid:
        return False, message
    if password is None or not str(password):
        return False, "Password is required."

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        locked, remaining = _login_lock_status(cursor, normalized_email)
        if locked:
            return False, f"Too many failed attempts. Try again in {remaining} minute(s)."

        cursor.execute(
            """
            SELECT id, name, email, password
            FROM users
            WHERE LOWER(email) = ?
            LIMIT 1
            """,
            (normalized_email,),
        )
        user = cursor.fetchone()

        oauth_account = user is not None and str(user[3] or "") in {
            OAUTH_PASSWORD_MARKER,
            APPLE_OAUTH_PASSWORD_MARKER,
        }
        selected_hash = user[3] if user is not None and not oauth_account else DUMMY_PASSWORD_HASH
        password_matches = verify_password(password, selected_hash)
        if oauth_account:
            password_matches = False

        if user is None or not password_matches:
            now_locked = _record_failed_login(cursor, normalized_email)
            connection.commit()
            if now_locked:
                return False, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minute(s)."
            if oauth_account:
                return False, "This account uses external sign-in. Select its connected provider."
            return False, "Invalid email or password."

        _clear_failed_logins(cursor, normalized_email)
        cursor.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (_utc_now().isoformat(), user[0]),
        )
        connection.commit()
        return True, {"id": user[0], "name": user[1], "email": user[2]}

    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        return False, "Unable to access your account. Please try again."
    finally:
        if connection is not None:
            connection.close()


# ==========================================================
# PASSWORD RESET
# ==========================================================

def issue_password_reset_token(email):
    """Create a one-time reset token; callers must deliver it through a trusted channel."""

    normalized_email = normalize_email(email)
    valid, _ = validate_email(normalized_email)
    if not valid:
        return None

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id FROM users WHERE LOWER(email) = ? LIMIT 1",
            (normalized_email,),
        )
        user = cursor.fetchone()
        if not user:
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = (_utc_now() + timedelta(minutes=PASSWORD_RESET_MINUTES)).isoformat()
        cursor.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (_utc_now().isoformat(), user[0]),
        )
        cursor.execute(
            """
            INSERT INTO password_reset_tokens(user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (user[0], token_hash, expires_at),
        )
        connection.commit()
        return raw_token
    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        return None
    finally:
        if connection is not None:
            connection.close()


def request_password_reset(email, send_function=None):
    """Generate and optionally deliver a reset token without revealing account existence."""

    normalized_email = normalize_email(email)
    token = issue_password_reset_token(normalized_email)
    if token and callable(send_function):
        try:
            send_function(normalized_email, token, PASSWORD_RESET_MINUTES)
        except Exception:
            # Preserve the generic response; delivery failures should be logged by caller.
            pass
    return True, "If an account exists, password-reset instructions have been sent."


def reset_password(token, new_password):
    valid, message = validate_password(new_password)
    if not valid:
        return False, message
    if not token or not str(token).strip():
        return False, "A valid reset token is required."

    token_hash = hashlib.sha256(str(token).strip().encode("utf-8")).hexdigest()
    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, user_id, expires_at, used_at
            FROM password_reset_tokens
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        if not row or row[3] is not None:
            return False, "The reset link is invalid or has already been used."
        expires_at = _parse_utc(row[2])
        if not expires_at or expires_at <= _utc_now():
            return False, "The reset link has expired. Request a new one."

        cursor.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), row[1]),
        )
        cursor.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (_utc_now().isoformat(), row[0]),
        )
        cursor.execute(
            "DELETE FROM auth_login_attempts WHERE identifier = (SELECT LOWER(email) FROM users WHERE id = ?)",
            (row[1],),
        )
        connection.commit()
        return True, "Password updated successfully. You can now sign in."
    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        return False, "Password reset could not be completed. Please try again."
    finally:
        if connection is not None:
            connection.close()


# ==========================================================
# ACCOUNT LOOKUP
# ==========================================================

def get_user_by_id(user_id):
    """
    Return basic user information using a database ID.

    This helper does not expose the password hash.
    """

    connection = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                name,
                email,
                created_at,
                COALESCE(role, 'user'),
                COALESCE(auth_provider, 'password'),
                COALESCE(token_version, 0),
                COALESCE(account_status, 'active')
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (
                user_id,
            )
        )

        user = cursor.fetchone()

        if user is None:
            return None

        try:
            providers = [
                str(row[0])
                for row in cursor.execute(
                    "SELECT provider FROM oauth_identities WHERE user_id=? ORDER BY provider",
                    (user_id,),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            providers = [part for part in str(user[5] or "").split("+") if part in {"google", "apple"}]
        try:
            mfa_row = cursor.execute(
                "SELECT enabled FROM user_mfa WHERE user_id=?",
                (user_id,),
            ).fetchone()
            mfa_enabled = bool(mfa_row and mfa_row[0])
        except sqlite3.OperationalError:
            mfa_enabled = False
        return {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "created_at": user[3],
            "role": str(user[4] or "user").strip().lower() or "user",
            "auth_provider": str(user[5] or "password"),
            "connected_providers": providers,
            "token_version": int(user[6] or 0),
            "account_status": str(user[7] or "active"),
            "mfa_enabled": mfa_enabled,
        }

    except sqlite3.Error:
        return None

    finally:
        if connection is not None:
            connection.close()

# ==========================================================
# GOOGLE OAUTH ACCOUNT LINKING
# ==========================================================

def login_or_register_google_user(name, email, google_id):
    """Create or link a verified Google identity without duplicating emails.

    Returns: (success, user-or-message, action) where action is ``created``,
    ``linked``, or ``existing``. Existing password authentication remains intact.
    """

    return login_or_register_oauth_user(
        provider="google",
        name=name,
        email=email,
        provider_subject=google_id,
        email_verified=True,
    )


def login_or_register_oauth_user(
    *,
    provider: str,
    name: str,
    email: str,
    provider_subject: str,
    email_verified: bool,
):
    """Resolve an OIDC identity by immutable provider subject, then verified email.

    Email is considered only after the provider has cryptographically established
    that it is verified. This permits a documented password-account link without
    allowing an unverified provider claim to take over an existing account.
    """

    normalized_provider = str(provider or "").strip().lower()
    normalized_name = normalize_name(name) or f"{normalized_provider.title()} User"
    normalized_email = normalize_email(email)
    subject = str(provider_subject or "").strip()
    if normalized_provider not in {"google", "apple"}:
        return False, "Unsupported identity provider.", "error"
    valid, message = validate_email(normalized_email)
    if not valid:
        return False, message, "error"
    if not email_verified:
        return False, "The identity provider email is not verified.", "error"
    if not subject:
        return False, "The identity provider did not return an account subject.", "error"

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT u.id, u.name, u.email, u.role
            FROM oauth_identities oi JOIN users u ON u.id=oi.user_id
            WHERE oi.provider=? AND oi.provider_subject=? LIMIT 1
            """,
            (normalized_provider, subject),
        )
        identity_user = cursor.fetchone()
        if identity_user is not None:
            cursor.execute(
                "UPDATE oauth_identities SET last_login_at=?, verified_email=? WHERE provider=? AND provider_subject=?",
                (_utc_now().isoformat(), normalized_email, normalized_provider, subject),
            )
            cursor.execute("UPDATE users SET last_login_at=? WHERE id=?", (_utc_now().isoformat(), identity_user[0]))
            connection.commit()
            return True, {
                "id": identity_user[0], "name": identity_user[1], "email": identity_user[2],
                "role": str(identity_user[3] or "user"),
            }, "existing"

        cursor.execute(
            "SELECT id,name,email,password,auth_provider,google_id,role FROM users WHERE LOWER(email)=? LIMIT 1",
            (normalized_email,),
        )
        user = cursor.fetchone()
        action = "linked"
        if user is None:
            marker = OAUTH_PASSWORD_MARKER if normalized_provider == "google" else APPLE_OAUTH_PASSWORD_MARKER
            cursor.execute(
                "INSERT INTO users(name,email,password,auth_provider) VALUES(?,?,?,?)",
                (normalized_name, normalized_email, marker, normalized_provider),
            )
            user_id = int(cursor.lastrowid)
            user = (user_id, normalized_name, normalized_email, marker, normalized_provider, None, "user")
            action = "created"
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO settings(user_id,theme,currency,default_period) VALUES(?,'Dark','₹','1y')",
                    (user_id,),
                )
            except sqlite3.OperationalError:
                pass

        user_id = int(user[0])
        cursor.execute(
            "SELECT provider_subject FROM oauth_identities WHERE user_id=? AND provider=? LIMIT 1",
            (user_id, normalized_provider),
        )
        existing = cursor.fetchone()
        if existing is not None and str(existing[0]) != subject:
            connection.rollback()
            return False, "This account is already linked to a different provider identity.", "error"

        cursor.execute(
            """
            INSERT INTO oauth_identities(user_id,provider,provider_subject,verified_email,last_login_at)
            VALUES(?,?,?,?,?)
            """,
            (user_id, normalized_provider, subject, normalized_email, _utc_now().isoformat()),
        )
        providers = {part for part in str(user[4] or "password").split("+") if part}
        providers.add(normalized_provider)
        auth_provider = "+".join(sorted(providers - {"password"})) if str(user[3]) in {
            OAUTH_PASSWORD_MARKER, APPLE_OAUTH_PASSWORD_MARKER,
        } else "+".join(["password", *sorted(providers - {"password"})])
        if normalized_provider == "google":
            cursor.execute(
                "UPDATE users SET google_id=?,auth_provider=?,last_login_at=? WHERE id=?",
                (subject, auth_provider, _utc_now().isoformat(), user_id),
            )
        else:
            cursor.execute(
                "UPDATE users SET auth_provider=?,last_login_at=? WHERE id=?",
                (auth_provider, _utc_now().isoformat(), user_id),
            )
        connection.commit()
        return True, {
            "id": user_id, "name": user[1], "email": user[2], "role": str(user[6] or "user"),
        }, action
    except sqlite3.IntegrityError:
        if connection is not None:
            connection.rollback()
        return False, "This provider identity could not be linked safely.", "error"
    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        return False, "External sign-in could not access the user account.", "error"
    finally:
        if connection is not None:
            connection.close()
