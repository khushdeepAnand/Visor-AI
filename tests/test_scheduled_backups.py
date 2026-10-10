import sqlite3
from pathlib import Path

import pytest

from services import encrypted_storage


def test_scheduled_backup_restores_main_and_registry_before_retention(tmp_path):
    sources = [tmp_path / "main.db", tmp_path / "promotion.registry.db"]
    for source in sources:
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE sample(value TEXT)")
            conn.execute("INSERT INTO sample VALUES('preserve')")
    destination = tmp_path / "backups"
    result = encrypted_storage.backup_and_rehearse(sources, destination, "backup-test-secret-material-32-bytes")
    assert len(result["backups"]) == 2
    assert all(item["restore_verified"] for item in result["backups"])
    assert len(list(destination.glob("*.enc"))) == 2
    assert not list(destination.glob("*.db"))
    for item in result["backups"]:
        restored = tmp_path / (Path(item["path"]).stem + ".db")
        encrypted_storage.restore_encrypted_backup(Path(item["path"]), restored, "backup-test-secret-material-32-bytes")
        with sqlite3.connect(restored) as conn:
            assert conn.execute("SELECT value FROM sample").fetchone()[0] == "preserve"


def test_missing_backup_key_never_creates_plaintext_fallback(tmp_path):
    with pytest.raises(ValueError, match="32 bytes"):
        encrypted_storage.backup_and_rehearse([], tmp_path / "backups", "")
    assert not (tmp_path / "backups").exists()
