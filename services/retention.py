"""Bounded retention enforcement for persisted security and audit artifacts."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.db.factory import dao_session
from services.db.configuration import postgres_selected


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RetentionPolicy:
    audit_days: int = 365
    admin_audit_days: int = 730
    security_artifact_days: int = 7
    forecast_artifact_days: int = 30
    batch_size: int = 1000

    @classmethod
    def from_environment(cls) -> "RetentionPolicy":
        return cls(
            audit_days=_bounded_int("STOCKPILOT_AUDIT_RETENTION_DAYS", 365, 30, 3650),
            admin_audit_days=_bounded_int("STOCKPILOT_ADMIN_AUDIT_RETENTION_DAYS", 730, 30, 3650),
            security_artifact_days=_bounded_int("STOCKPILOT_SECURITY_ARTIFACT_RETENTION_DAYS", 7, 1, 365),
            forecast_artifact_days=_bounded_int("STOCKPILOT_FORECAST_ARTIFACT_RETENTION_DAYS", 30, 1, 3650),
            batch_size=_bounded_int("STOCKPILOT_RETENTION_BATCH_SIZE", 1000, 100, 10_000),
        )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _delete_by_id(connection: Any, table: str, predicate: str, parameters: tuple[Any, ...], batch_size: int) -> int:
    # The table and predicate are fixed internal policy values, never request input.
    if table not in {"auth_sessions", "password_reset_tokens", "mfa_challenges", "audit_log", "admin_audit_log"}:
        raise ValueError("Unknown retention table")
    cursor = connection.execute(connection.sql(
        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE {predicate} ORDER BY id LIMIT ?)"),  # nosec B608
        (*parameters, int(batch_size)),
    )
    return max(0, int(cursor.rowcount or 0))


def _older_than(column: str) -> str:
    if column not in {"expires_at", "revoked_at", "used_at", "consumed_at", "created_at", "updated_at", "locked_until"}:
        raise ValueError("Unknown retention timestamp")
    return (f"CAST({column} AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ)" if postgres_selected()
            else f"datetime({column}) < datetime(?)")


def enforce_database_retention(policy: RetentionPolicy, *, now: datetime | None = None) -> dict[str, int]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    security_cutoff = _iso(current - timedelta(days=policy.security_artifact_days))
    audit_cutoff = _iso(current - timedelta(days=policy.audit_days))
    admin_cutoff = _iso(current - timedelta(days=policy.admin_audit_days))
    current_iso = _iso(current)
    counts: dict[str, int] = {}
    with dao_session() as factory:
        connection = factory.db
        connection.begin_write("database-retention")
        counts["auth_sessions"] = _delete_by_id(
            connection,
            "auth_sessions",
            _older_than("expires_at") + " OR (revoked_at IS NOT NULL AND " + _older_than("revoked_at") + ")",
            (current_iso, security_cutoff),
            policy.batch_size,
        )
        counts["password_reset_tokens"] = _delete_by_id(
            connection,
            "password_reset_tokens",
            _older_than("expires_at") + " OR (used_at IS NOT NULL AND " + _older_than("used_at") + ")",
            (current_iso, security_cutoff),
            policy.batch_size,
        )
        counts["mfa_challenges"] = _delete_by_id(
            connection,
            "mfa_challenges",
            _older_than("expires_at") + " OR (consumed_at IS NOT NULL AND " + _older_than("consumed_at") + ")",
            (current_iso, security_cutoff),
            policy.batch_size,
        )
        counts["audit_log"] = _delete_by_id(
            connection,
            "audit_log",
            _older_than("created_at") + " AND action != 'research_disclaimer_acknowledged'",
            (audit_cutoff,),
            policy.batch_size,
        )
        counts["admin_audit_log"] = _delete_by_id(
            connection,
            "admin_audit_log",
            _older_than("created_at"),
            (admin_cutoff,),
            policy.batch_size,
        )
        query = f"""
            DELETE FROM auth_login_attempts
            WHERE identifier IN (
                SELECT identifier FROM auth_login_attempts
                 WHERE {_older_than('updated_at')}
                   AND (locked_until IS NULL OR {_older_than('locked_until')})
                ORDER BY updated_at LIMIT ?
            )
            """  # nosec B608
        cursor = connection.execute(connection.sql(query),
            (security_cutoff, current_iso, policy.batch_size),
        )
        counts["auth_login_attempts"] = max(0, int(cursor.rowcount or 0))
        connection.commit()
    return counts


def enforce_forecast_artifact_retention(
    policy: RetentionPolicy,
    *,
    artifact_directory: Path,
    now: datetime | None = None,
) -> dict[str, int]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current.timestamp() - policy.forecast_artifact_days * 86_400
    deleted = 0
    bytes_deleted = 0
    if not artifact_directory.is_dir():
        return {"forecast_artifacts": 0, "forecast_artifact_bytes": 0}
    for path in sorted(artifact_directory.glob("*.json")):
        if deleted >= policy.batch_size or path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            size = stat.st_size
            path.unlink()
            deleted += 1
            bytes_deleted += size
        except FileNotFoundError:
            continue
    return {"forecast_artifacts": deleted, "forecast_artifact_bytes": bytes_deleted}


def run_retention_enforcement(
    policy: RetentionPolicy | None = None,
    *,
    artifact_directory: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = policy or RetentionPolicy.from_environment()
    directory = artifact_directory or Path(__file__).resolve().parents[1] / "cache" / "model_refresh"
    database_counts = enforce_database_retention(selected, now=now)
    artifact_counts = enforce_forecast_artifact_retention(selected, artifact_directory=directory, now=now)
    return {
        "completed_at": _iso(now or datetime.now(timezone.utc)),
        "counts": {**database_counts, **artifact_counts},
        "policy": {
            "audit_days": selected.audit_days,
            "admin_audit_days": selected.admin_audit_days,
            "security_artifact_days": selected.security_artifact_days,
            "forecast_artifact_days": selected.forecast_artifact_days,
            "batch_size": selected.batch_size,
        },
    }
