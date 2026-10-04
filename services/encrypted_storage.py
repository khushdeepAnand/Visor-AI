"""Verified SQLCipher exports and authenticated, consistent database backups."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import sqlcipher3  # type: ignore[import-untyped]
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _cipher_connection(path: Path, key: str | None) -> Any:
    conn = sqlcipher3.connect(str(path), timeout=30)
    if key:
        literal = key.replace("'", "''")
        conn.execute(f"PRAGMA key = '{literal}'")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def _inventory(conn: Any) -> dict[str, tuple[int, str]]:
    result = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        identifier = '"' + name.replace('"', '""') + '"'
        rows = conn.execute(f"SELECT * FROM {identifier}").fetchall()  # nosec B608
        digests = sorted(hashlib.sha256(repr(tuple(row)).encode()).hexdigest() for row in rows)
        result[name] = (len(rows), hashlib.sha256("\n".join(digests).encode()).hexdigest())
    return result


def encrypt_database_copy(source: Path, target: Path, key: str, *, source_key: str | None = None) -> dict[str, Any]:
    """Export into a new encrypted file; verify every table without changing source."""
    if not key:
        raise ValueError("A persistent database encryption key is required.")
    if target.exists():
        raise FileExistsError("Encryption destination already exists; refusing overwrite.")
    if not source.is_file():
        raise FileNotFoundError("Source database does not exist.")
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = _cipher_connection(source, source_key)
    try:
        conn.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(target), key))
        conn.execute("BEGIN EXCLUSIVE")
        before = _inventory(conn)
        conn.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.execute(f"PRAGMA encrypted.user_version = {version}")
        conn.commit()
        conn.execute("DETACH DATABASE encrypted")
        check = _cipher_connection(target, key)
        try:
            after = _inventory(check)
            if before != after or check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Encrypted export failed preservation/integrity verification.")
        finally:
            check.close()
        return {"verified": True, "tables": len(before), "rows": sum(item[0] for item in before.values())}
    except Exception:
        conn.rollback()
        target.unlink(missing_ok=True)
        raise
    finally:
        conn.close()


def _backup_cipher(secret: str) -> Fernet:
    if len(secret.encode()) < 32:
        raise ValueError("Backup encryption secret must contain at least 32 bytes.")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"stockpilot-backup-v1").derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def create_encrypted_backup(source: Path, target: Path, secret: str, *, database_key: str | None = None) -> dict[str, Any]:
    """Take a transactionally consistent snapshot, then encrypt/authenticate it."""
    if target.exists():
        raise FileExistsError("Backup destination already exists.")
    cipher = _backup_cipher(secret)
    if database_key:
        with tempfile.TemporaryDirectory(prefix="stockpilot-encrypted-snapshot-") as directory:
            snapshot = Path(directory) / "snapshot.db"
            encrypt_database_copy(source, snapshot, database_key, source_key=database_key)
            data = snapshot.read_bytes()  # temporary snapshot is itself encrypted
    else:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn, sqlite3.connect(":memory:") as snapshot_conn:
            conn.backup(snapshot_conn)
            data = snapshot_conn.serialize()  # plaintext snapshot never touches disk
    digest = hashlib.sha256(data).hexdigest()
    payload = {"version": 1, "sha256": digest, "encrypted_database": bool(database_key), "data": base64.b64encode(data).decode()}
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(cipher.encrypt(json.dumps(payload).encode()))
        handle.flush()
        os.fsync(handle.fileno())
    return {"sha256": digest, "bytes": len(data), "encrypted": True}


def restore_encrypted_backup(source: Path, target: Path, secret: str, *, database_key: str | None = None) -> dict[str, Any]:
    """Authenticate, verify and restore to a new path; never overwrite live data."""
    if target.exists():
        raise FileExistsError("Restore destination already exists.")
    try:
        payload = json.loads(_backup_cipher(secret).decrypt(source.read_bytes()))
        data = base64.b64decode(payload["data"], validate=True)
    except (InvalidToken, ValueError, KeyError) as exc:
        raise ValueError("Backup authentication or format validation failed.") from exc
    if payload.get("version") != 1 or hashlib.sha256(data).hexdigest() != payload.get("sha256"):
        raise ValueError("Backup integrity validation failed.")
    if payload.get("encrypted_database") and not database_key:
        raise ValueError("Encrypted database restore requires its database key.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(data)
    try:
        conn = _cipher_connection(target, database_key)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored database integrity check failed.")
        finally:
            conn.close()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"verified": True, "sha256": payload["sha256"]}
