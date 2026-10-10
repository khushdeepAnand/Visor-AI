"""Constrained administrator identity, promotion, and audit logging.

StockPilot has at most three possible administrators. The identities come from
local configuration (``STOCKPILOT_ADMIN_EMAILS``), never from the public
registration form and never from hardcoded credentials, and promotion happens
only through the explicit idempotent bootstrap below.

The privilege an administrator receives is deliberately narrow: application
health, provider and model metadata, safe maintenance actions, bounded settings,
and aggregate counts. It never includes access to another account's portfolio,
watchlist, orders, alerts, reports, prediction history, or journal, and never
includes secrets of any kind.
"""
from __future__ import annotations

import json
import os
import sqlite3
import logging
import math
from typing import Any, Iterable

from database import get_connection, DATABASE_ERRORS
from services.db.factory import dao_session
from services.db.configuration import postgres_selected

#: Hard ceiling on privileged identities. Exceeding it is a configuration error.
MAX_ADMINS = 3

ADMIN_EMAILS_VAR = "STOCKPILOT_ADMIN_EMAILS"


class AdminConfigurationError(RuntimeError):
    """The administrator allowlist is unusable, so no promotion may happen."""


def _normalize(email: str) -> str:
    return str(email or "").strip().lower()


def configured_admin_emails(raw: str | None = None) -> list[str]:
    """Return the configured administrator emails, or fail closed.

    More than :data:`MAX_ADMINS` distinct entries raises instead of silently
    truncating the list, because quietly dropping an entry would make the
    effective privilege set depend on ordering.
    """
    source = os.getenv(ADMIN_EMAILS_VAR, "") if raw is None else raw
    seen: list[str] = []
    for candidate in str(source).replace(";", ",").split(","):
        email = _normalize(candidate)
        if not email:
            continue
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise AdminConfigurationError(f"{ADMIN_EMAILS_VAR} contains an entry that is not an email address.")
        if email not in seen:
            seen.append(email)
    if len(seen) > MAX_ADMINS:
        raise AdminConfigurationError(
            f"{ADMIN_EMAILS_VAR} lists {len(seen)} identities; at most {MAX_ADMINS} administrators are allowed."
        )
    return seen


def bootstrap_admins(emails: Iterable[str] | None = None) -> dict[str, Any]:
    """Promote configured accounts to ``admin`` and demote every other account.

    Idempotent: running it twice produces the same rows. Accounts that do not
    exist yet are reported as pending rather than created, so an administrator
    must still register through the normal flow with their own password.
    """
    allowlist = list(emails) if emails is not None else configured_admin_emails()
    allowlist = [_normalize(email) for email in allowlist if _normalize(email)]
    if len(set(allowlist)) > MAX_ADMINS:
        raise AdminConfigurationError(f"At most {MAX_ADMINS} administrators are allowed.")
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("role-bootstrap")
        promoted: list[str] = []
        pending: list[str] = []
        for email in allowlist:
            row = connection.fetchone(connection.sql("SELECT id FROM users WHERE LOWER(email)=? LIMIT 1"), (email,))
            if row is None:
                pending.append(email)
                continue
            connection.execute(connection.sql("UPDATE users SET role='admin' WHERE id=?"), (row["id"],))
            promoted.append(email)
        if allowlist:
            placeholders = ",".join("?" for _ in allowlist)
            cursor = connection.execute(connection.sql(
                f"UPDATE users SET role='user' WHERE role='admin' AND LOWER(email) NOT IN ({placeholders})"),  # nosec B608
                tuple(allowlist),
            )
        else:
            cursor = connection.execute("UPDATE users SET role='user' WHERE role='admin'")
        demoted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        connection.commit()
        return {"promoted": promoted, "pending": pending, "demoted": demoted, "max_admins": MAX_ADMINS}


def is_admin_email(email: str) -> bool:
    try:
        return _normalize(email) in configured_admin_emails()
    except AdminConfigurationError:
        return False


OPERATIONAL_ROLES = {"support-ops": "STOCKPILOT_SUPPORT_OPS_EMAILS", "model-ops": "STOCKPILOT_MODEL_OPS_EMAILS"}


def effective_role(user: dict[str, Any]) -> str:
    """Stored role and current deployment allowlist must both authorize access."""
    role, email = str(user.get("role", "user")), _normalize(str(user.get("email", "")))
    if role == "admin":
        return role if is_admin_email(email) else "user"
    variable = OPERATIONAL_ROLES.get(role)
    if variable and email and email in {_normalize(item) for item in os.getenv(variable, "").replace(";", ",").split(",")}:
        return role
    return "user"


def bootstrap_operational_roles() -> dict[str, Any]:
    """Assign configured operational accounts; registration cannot choose roles."""
    assignments: dict[str, str] = {}
    for role, variable in OPERATIONAL_ROLES.items():
        for entry in os.getenv(variable, "").replace(";", ",").split(","):
            email = _normalize(entry)
            if not email:
                continue
            if "@" not in email or email in assignments:
                raise AdminConfigurationError("Operational identities must be valid and belong to only one role")
            assignments[email] = role
    with dao_session() as factory:
        conn = factory.db
        conn.begin_write("role-bootstrap")
        conn.execute("UPDATE users SET role='user' WHERE role IN ('support-ops','model-ops')")
        for email, role in assignments.items():
            conn.execute(conn.sql("UPDATE users SET role=? WHERE LOWER(email)=? AND role!='admin'"), (role, email))
        conn.commit()
    return {"configured": len(assignments)}


def record_admin_action(
    *,
    actor_id: int | None,
    actor_email: str | None,
    action: str,
    target: str | None = None,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a sanitized audit row.

    Only whitelisted, non-sensitive scalars are stored. Values are stringified
    and truncated so a caller cannot use the audit log as a place to persist a
    secret or a large blob.
    """
    def is_sensitive_key(key: object) -> bool:
        normalized = str(key).strip().lower().replace("-", "_")
        if any(marker in normalized for marker in ("token", "secret", "password", "cookie", "authorization")):
            return True
        return normalized == "key" or normalized.endswith("_key")

    safe_detail: dict[str, str] | None = None
    if detail:
        safe_detail = {
            str(key)[:40]: str(value)[:120]
            for key, value in detail.items()
            if not is_sensitive_key(key)
        }
    try:
        with dao_session() as factory:
            connection = factory.db
            connection.execute(connection.sql(
            """INSERT INTO admin_audit_log(actor_user_id, actor_email, action, target, outcome, detail)
            VALUES(?,?,?,?,?,?)"""),
            (
                actor_id,
                _normalize(actor_email or "") or None,
                str(action)[:80],
                (str(target)[:120] if target is not None else None),
                str(outcome)[:40],
                json.dumps(safe_detail) if safe_detail else None,
            ),
            )
            connection.commit()
    except DATABASE_ERRORS:
        # An audit write must never break the operation it describes; the API
        # layer already logs the failure server-side with a support id.
        logging.getLogger(__name__).error("Administrator audit persistence failed; no stored audit event is claimed.")


def apply_account_actions(user_ids: list[int], action: str, *, actor_id: int,
                          actor_email: str, reason: str) -> dict[str, Any]:
    """Apply a bounded batch and its audit records in one transaction."""
    ids = sorted(set(user_ids))
    if not ids or len(ids) > 100 or any(type(uid) is not int or uid <= 0 for uid in ids):
        raise ValueError("Select between 1 and 100 valid accounts")
    if action not in {"suspend", "reinstate", "force_logout", "reset_lockout", "force_mfa_reset"}:
        raise ValueError("Unknown account action")
    reason = reason.strip()
    if not 12 <= len(reason) <= 160:
        raise ValueError("An explicit justification of 12–160 characters is required")
    with dao_session() as factory:
        conn = factory.db
        conn.begin_write()
        accounts = []
        for uid in ids:
            conn.begin_write("security:" + str(uid))
            query = "SELECT email FROM users WHERE id=?"
            if postgres_selected():
                query += " FOR UPDATE"
            row = conn.fetchone(conn.sql(query), (uid,))
            if row is None:
                raise ValueError("An account was not found; no accounts changed")
            accounts.append((uid, row["email"]))
        for uid, email in accounts:
            if action in {"suspend", "force_logout", "force_mfa_reset"}:
                conn.execute(conn.sql("UPDATE users SET token_version=token_version+1 WHERE id=?"), (uid,))
                conn.execute(conn.sql("UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL"), (uid,))
                conn.execute(conn.sql("UPDATE mfa_challenges SET consumed_at=CURRENT_TIMESTAMP WHERE user_id=? AND consumed_at IS NULL"), (uid,))
            if action == "suspend":
                conn.execute(conn.sql("UPDATE users SET account_status='suspended' WHERE id=?"), (uid,))
            elif action == "reinstate":
                conn.execute(conn.sql("UPDATE users SET account_status='active' WHERE id=?"), (uid,))
            elif action == "reset_lockout":
                conn.execute(conn.sql("DELETE FROM auth_login_attempts WHERE identifier=LOWER(?)"), (email,))
            elif action == "force_mfa_reset":
                conn.execute(conn.sql("DELETE FROM user_mfa WHERE user_id=?"), (uid,))
                conn.execute(conn.sql("DELETE FROM mfa_recovery_codes WHERE user_id=?"), (uid,))
                exists = postgres_selected() or conn.fetchone("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webauthn_credentials'")
                if exists:
                    conn.execute(conn.sql("DELETE FROM webauthn_credentials WHERE user_id=?"), (uid,))
            conn.execute(conn.sql("""INSERT INTO admin_audit_log(actor_user_id,actor_email,action,target,outcome,detail)
                VALUES(?,?,?,?,?,?)"""), (actor_id, _normalize(actor_email), f"account:{action}", f"user:{uid}", "ok", json.dumps({"reason": reason})))
        conn.commit()
    return {"updated": True, "action": action, "user_ids": ids}


def admin_audit_events(limit: int = 100) -> list[dict[str, Any]]:
    with dao_session() as factory:
        connection = factory.db
        rows = connection.fetchall(connection.sql(
            """SELECT id, actor_email, action, target, outcome, detail, created_at
            FROM admin_audit_log ORDER BY id DESC LIMIT ?"""),
            (max(1, min(int(limit), 1000)),),
        )
    return [
        {
            "id": row["id"],
            "actor_email": row["actor_email"],
            "action": row["action"],
            "target": row["target"],
            "outcome": row["outcome"],
            "detail": json.loads(row["detail"]) if row["detail"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]


#: The only settings an administrator may change, with hard bounds enforced
#: server-side. A key that is not listed here cannot be written at all, so the
#: settings surface can never become an arbitrary key/value store.
BOUNDED_SETTINGS: dict[str, dict[str, Any]] = {
    "forecast_default_confidence": {"type": "float", "min": 0.60, "max": 0.95, "default": 0.80,
                                    "label": "Default interval confidence level"},
    "forecast_zone_coverage_tolerance": {"type": "float", "min": 0.02, "max": 0.20, "default": 0.10,
                                         "label": "Maximum coverage gap that still allows derived zones"},
    "quote_cache_seconds": {"type": "int", "min": 1, "max": 300, "default": 15,
                            "label": "Quote cache lifetime in seconds"},
    "history_max_segments": {"type": "int", "min": 1, "max": 12, "default": 8,
                             "label": "Maximum segmented history requests per call"},
    "alert_evaluation_limit": {"type": "int", "min": 1, "max": 200, "default": 50,
                               "label": "Maximum alerts evaluated per request"},
    "enable_lightgbm_challenger": {"type": "bool", "default": False,
                                   "label": "Evaluate the LightGBM challenger (never promoted automatically)"},
    "enable_scheduler_jobs": {"type": "bool", "default": False,
                              "label": "Run background maintenance jobs"},
}


def _ensure_settings_table(connection: Any) -> None:
    if postgres_selected():
        connection.fetchall("SELECT key,value FROM app_settings LIMIT 0")
        return
    connection.execute(
        """CREATE TABLE IF NOT EXISTS app_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT
        )"""
    )


def _coerce(spec: dict[str, Any], raw: Any) -> Any:
    """Validate one value against its bound, raising ``ValueError`` if outside."""
    kind = spec["type"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError("Expected a boolean value.")
    number = float(raw)
    if not math.isfinite(number):
        raise ValueError("Expected a finite numeric value.")
    if kind == "int":
        if number != int(number):
            raise ValueError("Expected a whole number.")
        number = int(number)
    if number < spec["min"] or number > spec["max"]:
        raise ValueError(f"Value must be between {spec['min']} and {spec['max']}.")
    return number


def get_settings() -> dict[str, Any]:
    """Effective settings: stored overrides on top of the declared defaults."""
    values = {key: spec["default"] for key, spec in BOUNDED_SETTINGS.items()}
    with dao_session() as factory:
        connection = factory.db
        _ensure_settings_table(connection)
        for row in connection.fetchall("SELECT key, value FROM app_settings"):
            key, raw = row["key"], row["value"]
            spec = BOUNDED_SETTINGS.get(str(key))
            if spec is None:
                continue
            try:
                values[str(key)] = _coerce(spec, json.loads(raw))
            except (ValueError, json.JSONDecodeError):
                # A stored value that no longer satisfies its bound falls back to
                # the default rather than propagating an out-of-range setting.
                continue
    return values


def describe_settings() -> list[dict[str, Any]]:
    effective = get_settings()
    return [
        {"key": key, "value": effective[key], **{k: v for k, v in spec.items()}}
        for key, spec in BOUNDED_SETTINGS.items()
    ]


def update_settings(updates: dict[str, Any], *, actor_email: str | None = None) -> dict[str, Any]:
    """Write allowlisted settings atomically after validating every value.

    Validation happens for the whole batch before anything is written, so a
    rejected value cannot leave a partially applied configuration behind. The
    single transaction also makes concurrent writers safe.
    """
    unknown = [key for key in updates if key not in BOUNDED_SETTINGS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    coerced: dict[str, Any] = {}
    for key, raw in updates.items():
        try:
            coerced[key] = _coerce(BOUNDED_SETTINGS[key], raw)
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
    with dao_session() as factory:
        connection = factory.db
        _ensure_settings_table(connection)
        connection.begin_write("bounded-settings")
        for key, value in coerced.items():
            connection.execute(connection.sql(
                """INSERT INTO app_settings(key, value, updated_at, updated_by)
                VALUES(?,?,CURRENT_TIMESTAMP,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP, updated_by=excluded.updated_by"""),
                (key, json.dumps(value), _normalize(actor_email or "") or None),
            )
        connection.commit()
    return get_settings()


def aggregate_counts() -> dict[str, int]:
    """Aggregate-only counts. No row belonging to an individual user is read."""
    with dao_session() as factory:
        connection = factory.db

        def _count(table: str) -> int:
            counts = {
                "users": "SELECT COUNT(*) AS n FROM users",
                "watchlist": "SELECT COUNT(*) AS n FROM watchlist",
                "stocks": "SELECT COUNT(*) AS n FROM portfolio",
                "paper_orders": "SELECT COUNT(*) AS n FROM paper_orders",
                "range_forecasts": "SELECT COUNT(*) AS n FROM prediction_history",
                "price_alerts": "SELECT COUNT(*) AS n FROM price_alerts",
            }
            query = counts.get(table)
            if query is None:
                return 0
            try:
                row = connection.fetchone(query)
                return int(row["n"]) if row else 0
            except DATABASE_ERRORS:
                if postgres_selected():
                    raise
                return 0

        return {
            "users": _count("users"),
            "admins": _admin_count(connection),
            "watchlist_entries": _count("watchlist"),
            "portfolio_rows": _count("stocks"),
            "paper_orders": _count("paper_orders"),
            "saved_forecasts": _count("range_forecasts"),
            "price_alerts": _count("price_alerts"),
        }


def _admin_count(connection: Any) -> int:
    row = connection.fetchone("SELECT COUNT(*) AS n FROM users WHERE role='admin'")
    return int(row["n"]) if row else 0
