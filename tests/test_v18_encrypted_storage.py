import sqlite3
from pathlib import Path

import pytest

from services.encrypted_storage import encrypt_database_copy, create_encrypted_backup, restore_encrypted_backup


def test_sqlcipher_migration_preserves_data_and_original(tmp_path):
    source = tmp_path / "original.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'Research user')")
    original = source.read_bytes()
    target = tmp_path / "encrypted.db"
    key = "migration'key-with-quoted-content"
    report = encrypt_database_copy(source, target, key)
    assert report["verified"] and report["tables"] == 1
    assert source.read_bytes() == original
    assert not target.read_bytes().startswith(b"SQLite format 3")
    import sqlcipher3
    with sqlcipher3.connect(target) as conn:
        conn.execute("PRAGMA key = 'migration''key-with-quoted-content'")
        assert conn.execute("SELECT name FROM users").fetchone() == ("Research user",)


def test_backup_restore_is_authenticated_and_never_overwrites(tmp_path):
    source = tmp_path / "data.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE data(value TEXT)")
        conn.execute("INSERT INTO data VALUES ('private-data')")
    archive = tmp_path / "backup.enc"
    secret = "backup-only-test-material-at-least-thirty-two-bytes"
    create_encrypted_backup(source, archive, secret)
    assert b"private-data" not in archive.read_bytes()
    restored = tmp_path / "restored.db"
    restore_encrypted_backup(archive, restored, secret)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT value FROM data").fetchone()[0] == "private-data"
    with pytest.raises(FileExistsError):
        restore_encrypted_backup(archive, restored, secret)
    with pytest.raises(ValueError):
        restore_encrypted_backup(archive, tmp_path / "wrong.db", "wrong-backup-secret-at-least-thirty-two-bytes")


def test_migration_refuses_existing_destination_and_missing_key(tmp_path):
    source = tmp_path / "data.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE data(value TEXT)")
    with pytest.raises(FileExistsError):
        encrypt_database_copy(source, source, "test-migration-key-material")
    with pytest.raises(ValueError):
        encrypt_database_copy(source, tmp_path / "output.db", "")
