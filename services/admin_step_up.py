"""Step-up authentication for sensitive administrator actions.

Intended repository path: ``services/admin_step_up.py``.

Section 6 of the audit prompt requires "step-up authentication or recent-password
confirmation for provider-mode changes, model promotion/rollback, targeted cache
clearing, backup restore, and administrator changes". Session authentication
alone is not enough for those actions, because a borrowed or hijacked browser
session would otherwise be sufficient to change provider trust or promote a
model.

How it works
------------
1. The administrator calls :func:`AdminStepUpService.challenge` with their
   password and the action they intend to perform.
2. The password is verified with the project's existing password hasher; the
   password itself is never stored, logged, or audited.
3. On success a single-use, action-scoped, short-lived token is returned. Only
   the SHA-256 digest of the token is persisted, so a database reader cannot
   replay it.
4. The action endpoint calls :func:`AdminStepUpService.consume` with the token.
   The token is bound to the actor, the action, and (optionally) a specific
   target, and it is deleted on first use.

Every challenge attempt and every consumption is written to the sanitized admin
audit log, and failed attempts are rate limited per actor to make brute force
impractical.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence, Iterator
from services.db.base import DatabaseInterface
from services.db.factory import dao_session
from services.db.sqlite_impl import SQLiteDatabase
from services.db.configuration import postgres_selected

try:  # pragma: no cover - layout dependent
    from database import get_connection as _default_get_connection
except Exception:  # pragma: no cover
    _default_get_connection = None  # type: ignore[assignment]

_record_admin_action: Any
try:  # pragma: no cover - layout dependent
    from services.admin_registry import record_admin_action as _packaged_record_admin_action
    _record_admin_action = _packaged_record_admin_action
except Exception:  # pragma: no cover
    try:
        from admin_registry import record_admin_action as _flat_record_admin_action  # type: ignore[import-not-found]
        _record_admin_action = _flat_record_admin_action
    except Exception:
        _record_admin_action = None

try:  # pragma: no cover - layout dependent
    from authentication import verify_password as _default_verify_password
except Exception:  # pragma: no cover
    _default_verify_password = None  # type: ignore[assignment]

ConnectionFactory = Callable[[], sqlite3.Connection]
PasswordVerifier = Callable[[str, str], bool]

#: Actions that may never run on session authentication alone.
STEP_UP_ACTIONS: tuple[str, ...] = (
    "provider_mode_change",
    "model_promotion",
    "model_rollback",
    "cache_invalidation",
    "backup_restore",
    "admin_change",
    "forecast_kill_switch",
    "status_banner_publish",
)

#: Token lifetime. Long enough to complete a confirmation dialog, short enough
#: that a stolen token is nearly useless.
TOKEN_TTL_SECONDS = 300

#: Failed password attempts allowed per actor inside the lockout window.
MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPT_WINDOW_SECONDS = 900


class StepUpError(PermissionError):
    """Step-up verification failed; the caller must not perform the action."""


class StepUpConfigurationError(RuntimeError):
    """The service cannot verify passwords, so it fails closed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _normalize_email(email: str | None) -> str:
    return str(email or "").strip().lower()


def _normalize_action(action: str) -> str:
    key = str(action or "").strip().lower()
    if key not in STEP_UP_ACTIONS:
        raise StepUpError(f"{action!r} is not a step-up protected action.")
    return key


def _normalize_target(target: str | None) -> str:
    return str(target or "*").strip()[:120] or "*"


@dataclass(frozen=True)
class StepUpGrant:
    """Proof that an operator re-authenticated for one specific action."""

    actor_email: str
    action: str
    target: str
    granted_at: str
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_email": self.actor_email,
            "action": self.action,
            "target": self.target,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
        }


class AdminStepUpService:
    def __init__(
        self,
        connection_factory: ConnectionFactory | None = None,
        password_verifier: PasswordVerifier | None = None,
    ) -> None:
        self._factory = connection_factory
        self._verify = password_verifier

    # -- infrastructure ----------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        factory = self._factory or _default_get_connection
        if factory is None:  # pragma: no cover
            raise StepUpConfigurationError("No database connection factory is available.")
        connection = factory()
        self._ensure_schema(connection)
        return connection

    @contextmanager
    def _database(self) -> Iterator[DatabaseInterface]:
        if self._factory is not None or not postgres_selected():
            db = SQLiteDatabase(self._connect())
            try:
                yield db
            finally:
                db.close()
        else:
            with dao_session() as factory:
                factory.db.fetchall("SELECT token_hash FROM admin_step_up_tokens LIMIT 0")
                factory.db.fetchall("SELECT actor_email FROM admin_step_up_failures LIMIT 0")
                yield factory.db

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_step_up_tokens(
                token_hash TEXT PRIMARY KEY,
                actor_email TEXT NOT NULL,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_step_up_failures(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_email TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            """
        )
        connection.commit()

    @staticmethod
    def _audit(
        *,
        actor_id: int | None,
        actor_email: str | None,
        action: str,
        target: str | None,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if _record_admin_action is None:  # pragma: no cover
            return
        try:
            _record_admin_action(
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                target=target,
                outcome=outcome,
                detail=detail,
            )
        except Exception:  # pragma: no cover
            pass

    def _password_hash_for(self, connection: DatabaseInterface, email: str) -> tuple[int | None, str | None]:
        password_column = "password"
        if isinstance(connection, SQLiteDatabase):
            columns = {row["name"] for row in connection.fetchall("PRAGMA table_info(users)")}
            if "password" not in columns and "password_hash" in columns:
                password_column = "password_hash"  # Legacy injected fixtures, not the application schema.
        query = ("SELECT id,password_hash AS stored_hash FROM users WHERE LOWER(email)=? LIMIT 1"
                 if password_column == "password_hash" else
                 "SELECT id,password AS stored_hash FROM users WHERE LOWER(email)=? LIMIT 1")
        row = connection.fetchone(connection.sql(query), (email,))
        if row is None:
            return None, None
        return (int(row["id"]) if row["id"] is not None else None), (str(row["stored_hash"]) if row["stored_hash"] else None)

    def _prune_locked(self, connection: DatabaseInterface, now: datetime) -> None:
        connection.execute(connection.sql("DELETE FROM admin_step_up_tokens WHERE expires_at <= ?"), (_iso(now),))
        connection.execute(connection.sql(
            "DELETE FROM admin_step_up_failures WHERE occurred_at <= ?"),
            (_iso(now - timedelta(seconds=FAILED_ATTEMPT_WINDOW_SECONDS)),),
        )

    def _recent_failures(self, connection: DatabaseInterface, email: str, now: datetime) -> int:
        row = connection.fetchone(connection.sql(
            "SELECT COUNT(*) AS n FROM admin_step_up_failures WHERE actor_email=? AND occurred_at > ?"),
            (email, _iso(now - timedelta(seconds=FAILED_ATTEMPT_WINDOW_SECONDS))),
        )
        return int(row["n"]) if row else 0

    # -- public API --------------------------------------------------------
    def challenge(
        self,
        *,
        actor_email: str,
        password: str,
        action: str,
        target: str | None = None,
        actor_id: int | None = None,
        ttl_seconds: int = TOKEN_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Re-verify the operator's password and mint a single-use token."""
        email = _normalize_email(actor_email)
        action_key = _normalize_action(action)
        target_key = _normalize_target(target)
        verifier = self._verify or _default_verify_password
        if verifier is None:
            raise StepUpConfigurationError("No password verifier is available; step-up fails closed.")
        if not email or not str(password or ""):
            raise StepUpError("Re-enter your account password to continue.")
        lifetime = max(30, min(int(ttl_seconds), TOKEN_TTL_SECONDS))

        now = _utc_now()
        with self._database() as connection:
            connection.begin_write("admin-step-up:" + email)
            self._prune_locked(connection, now)
            if self._recent_failures(connection, email, now) >= MAX_FAILED_ATTEMPTS:
                connection.commit()
                self._audit(
                    actor_id=actor_id,
                    actor_email=email,
                    action="admin_step_up_locked",
                    target=action_key,
                    outcome="locked",
                    detail={"window_seconds": str(FAILED_ATTEMPT_WINDOW_SECONDS)},
                )
                raise StepUpError(
                    "Too many failed confirmations. Wait for the lockout window to pass before trying again."
                )

            user_id, password_hash = self._password_hash_for(connection, email)
            verified = False
            if password_hash:
                try:
                    verified = bool(verifier(password, password_hash))
                except Exception:
                    verified = False
            if not verified:
                connection.execute(connection.sql(
                    "INSERT INTO admin_step_up_failures(actor_email, action, occurred_at) VALUES(?,?,?)"),
                    (email, action_key, _iso(now)),
                )
                connection.commit()
                self._audit(
                    actor_id=actor_id or user_id,
                    actor_email=email,
                    action="admin_step_up_failed",
                    target=action_key,
                    outcome="denied",
                    detail={"target": target_key},
                )
                raise StepUpError("That password was not accepted, so the action was not performed.")

            token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=lifetime)
            connection.execute(connection.sql(
                """INSERT INTO admin_step_up_tokens(token_hash, actor_email, actor_user_id, action, target, created_at, expires_at)
                   VALUES(?,?,?,?,?,?,?)"""),
                (_digest(token), email, actor_id or user_id, action_key, target_key, _iso(now), _iso(expires_at)),
            )
            connection.execute(connection.sql("DELETE FROM admin_step_up_failures WHERE actor_email=?"), (email,))
            connection.commit()

        self._audit(
            actor_id=actor_id,
            actor_email=email,
            action="admin_step_up_granted",
            target=action_key,
            outcome="ok",
            detail={"target": target_key, "expires_at": _iso(expires_at)},
        )
        # The token is returned once and never stored in plaintext.
        return {
            "step_up_token": token,
            "action": action_key,
            "target": target_key,
            "expires_at": _iso(expires_at),
            "single_use": True,
        }

    def consume(
        self,
        *,
        actor_email: str,
        token: str,
        action: str,
        target: str | None = None,
        actor_id: int | None = None,
    ) -> StepUpGrant:
        """Validate and burn a step-up token, or raise :class:`StepUpError`."""
        email = _normalize_email(actor_email)
        action_key = _normalize_action(action)
        target_key = _normalize_target(target)
        if not token:
            raise StepUpError("This action requires password confirmation.")

        now = _utc_now()
        with self._database() as connection:
            connection.begin_write("admin-step-up-token:" + _digest(token))
            self._prune_locked(connection, now)
            row = connection.fetchone(connection.sql(
                """SELECT actor_email, action, target, created_at, expires_at
                   FROM admin_step_up_tokens WHERE token_hash=?"""),
                (_digest(token),),
            )
            if row is None:
                connection.commit()
                raise StepUpError("That confirmation has expired or was already used. Confirm again to continue.")
            stored_email, stored_action, stored_target, created_at, expires_at = (row[key] for key in ("actor_email", "action", "target", "created_at", "expires_at"))
            # Burn the token before validating the binding: a mismatched attempt
            # must not be retryable with the same token.
            deleted = connection.execute(connection.sql("DELETE FROM admin_step_up_tokens WHERE token_hash=?"), (_digest(token),))
            if deleted.rowcount != 1:
                connection.rollback()
                raise StepUpError("That confirmation has expired or was already used. Confirm again to continue.")
            connection.commit()

        expiry = _parse_iso(expires_at)
        if expiry is None or expiry <= now:
            self._audit(
                actor_id=actor_id,
                actor_email=email,
                action="admin_step_up_rejected",
                target=action_key,
                outcome="expired",
            )
            raise StepUpError("That confirmation expired. Confirm again to continue.")
        if _normalize_email(stored_email) != email or str(stored_action) != action_key:
            self._audit(
                actor_id=actor_id,
                actor_email=email,
                action="admin_step_up_rejected",
                target=action_key,
                outcome="mismatch",
            )
            raise StepUpError("That confirmation does not match this action.")
        if str(stored_target) != "*" and str(stored_target) != target_key:
            self._audit(
                actor_id=actor_id,
                actor_email=email,
                action="admin_step_up_rejected",
                target=action_key,
                outcome="target_mismatch",
            )
            raise StepUpError("That confirmation was issued for a different target.")

        self._audit(
            actor_id=actor_id,
            actor_email=email,
            action="admin_step_up_consumed",
            target=action_key,
            outcome="ok",
            detail={"target": target_key},
        )
        return StepUpGrant(
            actor_email=email,
            action=action_key,
            target=target_key,
            granted_at=str(created_at),
            expires_at=str(expires_at),
        )

    def requirements(self) -> dict[str, Any]:
        """Describe the policy for the admin UI, without leaking any state."""
        return {
            "actions": list(STEP_UP_ACTIONS),
            "token_ttl_seconds": TOKEN_TTL_SECONDS,
            "single_use": True,
            "max_failed_attempts": MAX_FAILED_ATTEMPTS,
            "failed_attempt_window_seconds": FAILED_ATTEMPT_WINDOW_SECONDS,
            "notes": "Password confirmation is required in addition to an authenticated admin session.",
        }


#: Process-wide service used by the API layer.
STEP_UP = AdminStepUpService()


def requires_step_up(action: str) -> bool:
    return str(action or "").strip().lower() in STEP_UP_ACTIONS


__all__: Sequence[str] = (
    "AdminStepUpService",
    "FAILED_ATTEMPT_WINDOW_SECONDS",
    "MAX_FAILED_ATTEMPTS",
    "STEP_UP",
    "STEP_UP_ACTIONS",
    "StepUpConfigurationError",
    "StepUpError",
    "StepUpGrant",
    "TOKEN_TTL_SECONDS",
    "requires_step_up",
)
