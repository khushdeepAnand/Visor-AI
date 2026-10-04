"""Offline, preservation-verified SQLite to SQLCipher migration.

Keep the application stopped throughout migration and rollback. Keys come from
the environment/DPAPI store, never command-line arguments or printed output.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.encrypted_storage import encrypt_database_copy, _cipher_connection
from services.secure_secrets import load_windows_secure_secrets


def migrate_in_place(path: Path, key: str) -> dict[str, object]:
    if not path.exists():
        return {"verified": True, "state": "new_database_will_be_encrypted"}
    with path.open("rb") as handle:
        plaintext = handle.read(16) == b"SQLite format 3\x00"
    if not plaintext:
        conn = _cipher_connection(path, key)
        conn.close()
        return {"verified": True, "state": "already_encrypted"}
    temporary = path.with_name(path.name + ".encrypted-next")
    if temporary.exists():
        raise FileExistsError("Prior migration output exists; review it before retrying.")
    report = encrypt_database_copy(path, temporary, key)
    # Preserve the pre-migration logical data in an encrypted rollback copy.
    backup = path.with_name(path.stem + ".pre-v18.encrypted.db")
    if backup.exists():
        temporary.unlink()
        raise FileExistsError("Rollback copy already exists; refusing overwrite.")
    shutil.copy2(temporary, backup)
    # Do not retain stale plaintext WAL pages after replacing the main file.
    conn = _cipher_connection(path, None)
    try:
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and checkpoint[0] != 0:
            raise RuntimeError("Database is busy; stop every application process first.")
    finally:
        conn.close()
    os.replace(temporary, path)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return {**report, "state": "encrypted", "rollback_copy": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "database" / "stockpilot.db")
    parser.add_argument("--offline", action="store_true", required=True, help="Confirm every app process is stopped")
    args = parser.parse_args()
    load_windows_secure_secrets()
    key = os.environ.get("STOCKPILOT_DB_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("Provision a persistent database key before migration.")
    result = migrate_in_place(args.database.resolve(), key)
    print(f"Database migration: {result['state']}; preservation verified={result['verified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
