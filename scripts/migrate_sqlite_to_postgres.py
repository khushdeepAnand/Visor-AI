"""Inventory/reconcile SQLite and explicitly approved, empty-target PostgreSQL transfer.

Default is read-only inventory. Never selects DB_BACKEND or cuts over traffic.
URLs and encryption keys are loaded through existing backend secret conventions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import base64
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APPLICATION_TABLES = frozenset("""users admin_audit_log oauth_identities auth_sessions user_mfa mfa_recovery_codes
    mfa_challenges auth_login_attempts password_reset_tokens symbols portfolio watchlist transactions
    prediction_history settings model_health paper_accounts paper_positions paper_orders paper_trade_journal
    paper_badges paper_challenge_entries user_workspace_layouts price_alerts chart_preferences chart_drawings
    audit_log sentiment_snapshots webauthn_credentials login_devices login_anomalies app_settings
    admin_step_up_tokens admin_step_up_failures forecast_kill_switches feature_flag_state status_banners
    saved_chart_layouts screener_saved_screens strategy_definitions forward_tests forward_test_events""".split())

TIMESTAMP_COLUMNS = frozenset("""created_at updated_at last_login_at issued_at expires_at revoked_at consumed_at used_at
    origin_timestamp target_timestamp data_timestamp feature_timestamp settlement_data_timestamp settled_at evaluated_at
    filled_at cancelled_at earned_at added_date buy_date prediction_date transaction_date last_triggered_at last_email_at
    snapshot_at first_seen last_seen confirmed_at acknowledged_at occurred_at starts_at ends_at published_at withdrawn_at
    auto_rolled_back_at started_at stopped_at last_evaluated_at bar_at recorded_at""".split())


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def inventory(source: Path, *, database_key: str | None = None) -> dict[str, Any]:
    """Inspect a consistent read transaction; never emit row values or hashes."""
    from services.encrypted_storage import _cipher_connection
    if not source.is_file():
        raise FileNotFoundError("SQLite source is missing; refusing to create it")
    connection = _cipher_connection(source, database_key)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {}
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            columns = connection.execute("PRAGMA table_info(" + _identifier(name) + ")").fetchall()
            # Metadata-only identifier is escaped by _identifier; values never enter SQL.
            count = connection.execute("SELECT count(*) FROM " + _identifier(name)).fetchone()[0]  # nosec B608
            tables[name] = {"rows": count, "columns": [row[1] for row in columns]}
        return {"source": str(source.resolve()), "mode": "inventory-only", "integrity_ok": connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
                "tables": tables, "unmapped_tables": sorted(set(tables) - APPLICATION_TABLES),
                "production_cutover": False}
    finally:
        connection.close()


def _value(value: Any, column: Any) -> Any:
    import sqlalchemy as sa
    if value is None:
        return None
    if column.name in TIMESTAMP_COLUMNS:
        # Text timestamps are deliberately preserved (forecast hashes depend on
        # their original representation), but malformed values are not imported.
        try:
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid timestamp value in migration source") from None
    if column.name == "date_of_birth" and value != "":
        date.fromisoformat(str(value))
    if isinstance(column.type, sa.DateTime):
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        return parsed if column.type.timezone else parsed.replace(tzinfo=None)
    if isinstance(column.type, sa.JSON):
        if isinstance(value, str):
            return json.loads(value, parse_constant=_reject_json_constant)
        json.dumps(value, allow_nan=False)
        return value
    if column.name.endswith("_json") or (column.table.name, column.name) in {
        ("webauthn_credentials", "transports"), ("strategy_definitions", "definition"),
        ("screener_saved_screens", "filters"), ("screener_saved_screens", "symbols"),
        ("forward_tests", "symbols"),
    }:
        json.loads(value, parse_constant=_reject_json_constant) if isinstance(value, str) else json.dumps(value, allow_nan=False)
    return value


def _reject_json_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON constants are not accepted")


def _digest(rows: list[dict[str, Any]]) -> str:
    # Internal preservation check only. Hashes of PII/auth data are never reported.
    encoded = sorted(json.dumps(row, sort_keys=True, default=str, allow_nan=False) for row in rows)
    return hashlib.sha256("\n".join(encoded).encode()).hexdigest()


def transfer(source: Path, url: str, *, approved: bool, backup: Path,
             backup_secret: str, database_key: str | None = None) -> dict[str, Any]:
    """Single transaction; requires an authenticated, restore-tested source backup.

    Quiesce application writers before invoking. A retry against matching data is
    reconciliation-only. An existing nonmatching target is never overwritten.
    """
    import sqlalchemy as sa
    from sqlalchemy.pool import NullPool
    from services.db.configuration import migration_url, sqlalchemy_url
    from services.encrypted_storage import create_encrypted_backup, restore_encrypted_backup, _cipher_connection, _backup_cipher
    if not approved:
        raise ValueError("Explicit data migration approval is required")
    url = migration_url(configured_url=url, sqlite_fallback="")
    if not url.startswith(("postgresql://", "postgresql+", "postgres://")):
        raise ValueError("Migration destination must be PostgreSQL")
    before = inventory(source, database_key=database_key)
    if not before["integrity_ok"] or before["foreign_key_violations"] or before["unmapped_tables"]:
        raise ValueError("Source integrity/relationships/table mapping must be resolved before transfer")
    create_encrypted_backup(source.resolve(), backup, backup_secret, database_key=database_key)
    with tempfile.TemporaryDirectory(prefix="stockpilot-migration-restore-") as temporary:
        restored = Path(temporary) / "snapshot.db"
        # Read the exact backed-up snapshot, not a live SQLite file that may change.
        if database_key:
            restore_encrypted_backup(backup, restored, backup_secret, database_key=database_key)
            connection = _cipher_connection(restored, database_key)
        else:
            payload = json.loads(_backup_cipher(backup_secret).decrypt(backup.read_bytes()))
            data = base64.b64decode(payload["data"], validate=True)
            if payload.get("version") != 1 or hashlib.sha256(data).hexdigest() != payload.get("sha256"):
                raise ValueError("Authenticated backup content verification failed")
            connection = sqlite3.connect(":memory:")
            connection.deserialize(data)
        try:
            integrity_ok = connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except Exception:
            connection.close()
            raise
        if not integrity_ok:
            connection.close()
            raise ValueError("Backup restore integrity verification failed")
        engine = sa.create_engine(sqlalchemy_url(url), poolclass=NullPool)
        try:
            metadata = sa.MetaData()
            metadata.reflect(bind=engine, only=lambda name, _: name in APPLICATION_TABLES)
            missing = set(before["tables"]) - set(metadata.tables)
            if missing:
                raise ValueError("Destination lacks required source tables; apply/audit Alembic before transfer")
            source_rows = {}
            for table in metadata.sorted_tables:
                if table.name not in before["tables"]:
                    continue
                # Table is reflected from APPLICATION_TABLES and identifier-quoted.
                cursor = connection.execute("SELECT * FROM " + _identifier(table.name))  # nosec B608
                names = [column[0] for column in cursor.description]
                if set(names) != set(table.c.keys()):
                    raise ValueError("Column mapping differs; refusing partial transfer")
                source_rows[table.name] = [
                    {name: _value(value, table.c[name]) for name, value in zip(names, row)}
                    for row in cursor.fetchall()
                ]
            report: dict[str, Any] = {"mode": "approved-transfer", "backup_restore_verified": True, "production_cutover": False, "tables": {}}
            with engine.begin() as target:
                # Serialize cooperating migration runs. Existing writers must be
                # stopped by the operator; this is deliberately not a live sync.
                target.execute(sa.text("SELECT pg_advisory_xact_lock(20261010, 18)"))
                nonempty = any(target.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                               for table in metadata.sorted_tables)
                if nonempty:
                    for table in metadata.sorted_tables:
                        actual = [dict(row) for row in target.execute(sa.select(table)).mappings()]
                        if _digest(actual) != _digest(source_rows.get(table.name, [])):
                            raise ValueError("Existing destination differs; refusing overwrite or merge")
                    report["mode"] = "already-matches"
                else:
                    for table in metadata.sorted_tables:
                        rows = source_rows.get(table.name, [])
                        if rows:
                            target.execute(table.insert(), rows)
                for table in metadata.sorted_tables:
                    expected = source_rows.get(table.name, [])
                    actual = [dict(row) for row in target.execute(sa.select(table)).mappings()]
                    if _digest(expected) != _digest(actual):
                        raise RuntimeError("Content reconciliation failed; rolling back all imported rows")
                    report["tables"][table.name] = {"source_rows": len(expected), "target_rows": len(actual), "content_verified": True}
                # Repair serial counters after preserved explicit IDs. Sequence
                # changes are nontransactional; failures can leave gaps, never rows.
                for table in metadata.sorted_tables:
                    if "id" in table.c:
                        identifier = _identifier(table.name)
                        # Identifier is reflected from the fixed application allowlist and quoted.
                        query = f"""SELECT setval(pg_get_serial_sequence(:table_name, 'id'),
                            GREATEST(COALESCE((SELECT MAX(id) FROM {identifier}), 0), 1),
                            EXISTS(SELECT 1 FROM {identifier}))"""  # nosec B608
                        target.execute(sa.text(query), {"table_name": table.name})
            return report
        finally:
            connection.close()
            engine.dispose()


def main() -> int:
    from services.secure_secrets import load_windows_secure_secrets
    load_windows_secure_secrets()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    import database
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(database.DATABASE))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approve-data-migration", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        if args.report.exists() or args.report.resolve() in {args.source.resolve(), args.backup.resolve() if args.backup else None}:
            raise FileExistsError("Report must be a new path, distinct from source and backup")
        if not args.report.parent.is_dir():
            raise FileNotFoundError("Report parent directory must already exist")
        key = database._get_encryption_key()
        database_key = key.decode() if key else None
        if args.apply:
            if not args.approve_data_migration or not args.backup:
                raise ValueError("Approved apply requires --approve-data-migration and --backup")
            url = os.getenv("DATABASE_MIGRATION_URL")
            if not url:
                raise ValueError("DATABASE_MIGRATION_URL is required for approved transfer")
            result = transfer(args.source, url, approved=True, backup=args.backup,
                              backup_secret=os.getenv("STOCKPILOT_BACKUP_SECRET", ""), database_key=database_key)
        else:
            result = inventory(args.source, database_key=database_key)
        with args.report.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        print("Migration report written. No backend switch or production cutover performed.")
        return 0
    except Exception as exc:
        print(f"Migration/inventory failed ({type(exc).__name__}); no success claimed. Secrets and row values suppressed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
