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
import database
from database import DATABASE_ERRORS, INTEGRITY_ERRORS, OPERATIONAL_ERRORS
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager

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

DATABASE_PATH = Path(database.DATABASE)



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

    return database._open_connection(DATABASE_PATH)


@contextmanager
def _auth_database():
    """Use the existing selected abstraction; retain the local path test seam."""
    if database.postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            yield factory.db
    else:
        from services.db.sqlite_impl import SQLiteDatabase
        db = SQLiteDatabase(get_connection())
        try:
            yield db
        finally:
            db.close()


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

    try:
        with _auth_database() as db:
            db.begin_write("identity:" + normalized_email)
            if db.fetchone(db.sql("SELECT id FROM users WHERE LOWER(email)=?"), (normalized_email,)):
                return False, "An account with this email already exists."
            cursor = db.execute(db.sql("INSERT INTO users(name,email,password,date_of_birth) VALUES(?,?,?,?) RETURNING id"),
                                (normalized_name, normalized_email, hash_password(password), str(date_of_birth or "").strip()))
            user_id = cursor.fetchone()["id"]
            try:
                db.execute(db.sql("INSERT INTO settings(user_id,theme,currency,default_period) VALUES(?,?,?,?) ON CONFLICT(user_id) DO NOTHING"),
                           (user_id, "Dark", "₹", "1y"))
            except OPERATIONAL_ERRORS:
                if database.postgres_selected():
                    raise  # Selected PG requires its migrated schema; no partial success.
            db.commit()
            return True, "Registration successful. You can now log in."
    except INTEGRITY_ERRORS:
        return False, "An account with this email already exists."
    except DATABASE_ERRORS:
        return False, "Registration could not be completed. Please try again."


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


def _login_lock_status(db, identifier):
    row = db.fetchone(db.sql(
        """
        SELECT failed_count, window_started, locked_until
        FROM auth_login_attempts
        WHERE identifier = ?
        """),
        (identifier,),
    )
    if not row:
        return False, 0

    locked_until = _parse_utc(row["locked_until"])
    now = _utc_now()
    if locked_until and locked_until > now:
        remaining = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        return True, remaining
    return False, 0


def _record_failed_login(db, identifier):
    now = _utc_now()
    row = db.fetchone(db.sql(
        """
        SELECT failed_count, window_started
        FROM auth_login_attempts
        WHERE identifier = ?
        """),
        (identifier,),
    )
    window_started = _parse_utc(row["window_started"]) if row else None
    if not row or not window_started or now - window_started > timedelta(minutes=LOGIN_WINDOW_MINUTES):
        failed_count = 1
        window_started = now
    else:
        failed_count = int(row["failed_count"] or 0) + 1

    locked_until = None
    if failed_count >= MAX_FAILED_ATTEMPTS:
        locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)

    db.execute(db.sql(
        """
        INSERT INTO auth_login_attempts(
            identifier, failed_count, window_started, locked_until, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(identifier) DO UPDATE SET
            failed_count = excluded.failed_count,
            window_started = excluded.window_started,
            locked_until = excluded.locked_until,
            updated_at = excluded.updated_at
        """),
        (
            identifier,
            failed_count,
            window_started.isoformat(),
            locked_until.isoformat() if locked_until else None,
            now.isoformat(),
        ),
    )
    return locked_until is not None


def _clear_failed_logins(db, identifier):
    db.execute(db.sql("DELETE FROM auth_login_attempts WHERE identifier = ?"), (identifier,))


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

    try:
        with _auth_database() as db:
            db.begin_write("identity:" + normalized_email)
            locked, remaining = _login_lock_status(db, normalized_email)
            if locked:
                return False, f"Too many failed attempts. Try again in {remaining} minute(s)."
            user = db.fetchone(db.sql("SELECT id,name,email,password FROM users WHERE LOWER(email)=? LIMIT 1"), (normalized_email,))
            oauth_account = user is not None and str(user["password"] or "") in {OAUTH_PASSWORD_MARKER, APPLE_OAUTH_PASSWORD_MARKER}
            selected_hash = user["password"] if user is not None and not oauth_account else DUMMY_PASSWORD_HASH
            password_matches = verify_password(password, selected_hash)
            if oauth_account:
                password_matches = False
            if user is None or not password_matches:
                now_locked = _record_failed_login(db, normalized_email)
                db.commit()
                if now_locked:
                    return False, f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minute(s)."
                if oauth_account:
                    return False, "This account uses external sign-in. Select its connected provider."
                return False, "Invalid email or password."
            _clear_failed_logins(db, normalized_email)
            db.execute(db.sql("UPDATE users SET last_login_at=? WHERE id=?"), (_utc_now().isoformat(), user["id"]))
            db.commit()
            return True, {"id": user["id"], "name": user["name"], "email": user["email"]}
    except DATABASE_ERRORS:
        return False, "Unable to access your account. Please try again."


# ==========================================================
# PASSWORD RESET
# ==========================================================

def issue_password_reset_token(email):
    """Create a one-time reset token; callers must deliver it through a trusted channel."""

    normalized_email = normalize_email(email)
    valid, _ = validate_email(normalized_email)
    if not valid:
        return None

    try:
        with _auth_database() as db:
            db.begin_write("identity:" + normalized_email)
            user = db.fetchone(db.sql("SELECT id FROM users WHERE LOWER(email)=? LIMIT 1"), (normalized_email,))
            if not user:
                return None
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires_at = (_utc_now() + timedelta(minutes=PASSWORD_RESET_MINUTES)).isoformat()
            db.execute(db.sql("UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL"),
                       (_utc_now().isoformat(), user["id"]))
            db.execute(db.sql("INSERT INTO password_reset_tokens(user_id,token_hash,expires_at) VALUES(?,?,?)"),
                       (user["id"], token_hash, expires_at))
            db.commit()
            return raw_token
    except DATABASE_ERRORS:
        return None


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
    try:
        with _auth_database() as db:
            db.begin_write("reset-token:" + token_hash)
            query = "SELECT id,user_id,expires_at,used_at FROM password_reset_tokens WHERE token_hash=? LIMIT 1"
            if database.postgres_selected():
                query += " FOR UPDATE"
            row = db.fetchone(db.sql(query), (token_hash,))
            if not row or row["used_at"] is not None:
                return False, "The reset link is invalid or has already been used."
            expires_at = _parse_utc(row["expires_at"])
            if not expires_at or expires_at <= _utc_now():
                return False, "The reset link has expired. Request a new one."
            db.execute(db.sql("UPDATE users SET password=? WHERE id=?"), (hash_password(new_password), row["user_id"]))
            db.execute(db.sql("UPDATE password_reset_tokens SET used_at=? WHERE id=?"), (_utc_now().isoformat(), row["id"]))
            db.execute(db.sql("DELETE FROM auth_login_attempts WHERE identifier=(SELECT LOWER(email) FROM users WHERE id=?)"), (row["user_id"],))
            db.commit()
            return True, "Password updated successfully. You can now sign in."
    except DATABASE_ERRORS:
        return False, "Password reset could not be completed. Please try again."


# ==========================================================
# ACCOUNT LOOKUP
# ==========================================================

def get_user_by_id(user_id):
    """
    Return basic user information using a database ID.

    This helper does not expose the password hash.
    """

    try:
        with _auth_database() as db:
            user = db.fetchone(db.sql("""SELECT id,name,email,created_at,COALESCE(role,'user') AS role,
                COALESCE(auth_provider,'password') AS auth_provider,COALESCE(token_version,0) AS token_version,
                COALESCE(account_status,'active') AS account_status FROM users WHERE id=? LIMIT 1"""), (user_id,))
            if user is None:
                return None
            try:
                providers = [str(row["provider"]) for row in db.fetchall(db.sql("SELECT provider FROM oauth_identities WHERE user_id=? ORDER BY provider"), (user_id,))]
            except OPERATIONAL_ERRORS:
                if database.postgres_selected():
                    raise
                providers = [part for part in str(user["auth_provider"] or "").split("+") if part in {"google", "apple"}]
            try:
                mfa_row = db.fetchone(db.sql("SELECT enabled FROM user_mfa WHERE user_id=?"), (user_id,))
                mfa_enabled = bool(mfa_row and mfa_row["enabled"])
            except OPERATIONAL_ERRORS:
                if database.postgres_selected():
                    raise
                mfa_enabled = False
            return {
                "id": user["id"], "name": user["name"], "email": user["email"], "created_at": user["created_at"],
                "role": str(user["role"] or "user").strip().lower() or "user",
                "auth_provider": str(user["auth_provider"] or "password"), "connected_providers": providers,
                "token_version": int(user["token_version"] or 0), "account_status": str(user["account_status"] or "active"),
                "mfa_enabled": mfa_enabled,
            }
    except DATABASE_ERRORS:
        return None

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

    try:
        with _auth_database() as db:
            db.begin_write("identity:" + normalized_email)
            # All identity writers acquire the email key first, then the immutable
            # provider-subject key. Unique constraints are the final race guard.
            db.begin_write("oauth:" + normalized_provider + ":" + subject)
            identity_user = db.fetchone(db.sql("""SELECT u.id,u.name,u.email,u.role
                FROM oauth_identities oi JOIN users u ON u.id=oi.user_id
                WHERE oi.provider=? AND oi.provider_subject=? LIMIT 1"""), (normalized_provider, subject))
            if identity_user is not None:
                db.execute(db.sql("UPDATE oauth_identities SET last_login_at=?,verified_email=? WHERE provider=? AND provider_subject=?"),
                           (_utc_now().isoformat(), normalized_email, normalized_provider, subject))
                db.execute(db.sql("UPDATE users SET last_login_at=? WHERE id=?"), (_utc_now().isoformat(), identity_user["id"]))
                db.commit()
                return True, {"id": identity_user["id"], "name": identity_user["name"], "email": identity_user["email"],
                              "role": str(identity_user["role"] or "user")}, "existing"
            user = db.fetchone(db.sql("SELECT id,name,email,password,auth_provider,google_id,role FROM users WHERE LOWER(email)=? LIMIT 1"), (normalized_email,))
            action = "linked"
            if user is None:
                marker = OAUTH_PASSWORD_MARKER if normalized_provider == "google" else APPLE_OAUTH_PASSWORD_MARKER
                cursor = db.execute(db.sql("INSERT INTO users(name,email,password,auth_provider) VALUES(?,?,?,?) RETURNING id"),
                                    (normalized_name, normalized_email, marker, normalized_provider))
                user_id = int(cursor.fetchone()["id"])
                user = {"id": user_id, "name": normalized_name, "email": normalized_email, "password": marker,
                        "auth_provider": normalized_provider, "google_id": None, "role": "user"}
                action = "created"
                try:
                    db.execute(db.sql("INSERT INTO settings(user_id,theme,currency,default_period) VALUES(?,'Dark','₹','1y') ON CONFLICT(user_id) DO NOTHING"), (user_id,))
                except OPERATIONAL_ERRORS:
                    if database.postgres_selected():
                        raise
            user_id = int(user["id"])
            existing = db.fetchone(db.sql("SELECT provider_subject FROM oauth_identities WHERE user_id=? AND provider=? LIMIT 1"), (user_id, normalized_provider))
            if existing is not None and str(existing["provider_subject"]) != subject:
                db.rollback()
                return False, "This account is already linked to a different provider identity.", "error"
            db.execute(db.sql("INSERT INTO oauth_identities(user_id,provider,provider_subject,verified_email,last_login_at) VALUES(?,?,?,?,?)"),
                       (user_id, normalized_provider, subject, normalized_email, _utc_now().isoformat()))
            providers = {part for part in str(user["auth_provider"] or "password").split("+") if part}
            providers.add(normalized_provider)
            auth_provider = "+".join(sorted(providers - {"password"})) if str(user["password"]) in {
                OAUTH_PASSWORD_MARKER, APPLE_OAUTH_PASSWORD_MARKER,
            } else "+".join(["password", *sorted(providers - {"password"})])
            if normalized_provider == "google":
                db.execute(db.sql("UPDATE users SET google_id=?,auth_provider=?,last_login_at=? WHERE id=?"),
                           (subject, auth_provider, _utc_now().isoformat(), user_id))
            else:
                db.execute(db.sql("UPDATE users SET auth_provider=?,last_login_at=? WHERE id=?"),
                           (auth_provider, _utc_now().isoformat(), user_id))
            db.commit()
            return True, {"id": user_id, "name": user["name"], "email": user["email"], "role": str(user["role"] or "user")}, action
    except INTEGRITY_ERRORS:
        return False, "This provider identity could not be linked safely.", "error"
    except DATABASE_ERRORS:
        return False, "External sign-in could not access the user account.", "error"
