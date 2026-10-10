import sqlite3
from pathlib import Path
import database


def test_public_migration_reuses_verified_export_and_handles_quoted_schema(temp_db, monkeypatch):
    conn = database.get_connection()
    conn.execute('CREATE TABLE "private ""notes"("content" TEXT)')
    conn.execute('INSERT INTO "private ""notes" VALUES(?)', ("preserve exactly",))
    conn.commit()
    conn.close()
    key = "quoted' persistent compatibility key"
    report = database.encrypt_database(key)
    assert report["success"] and report["verified"]
    assert Path(report["backup_path"]).read_bytes()[:16] != b"SQLite format 3\0"
    monkeypatch.setattr(database, "SQLCIPHER_KEY", key)
    monkeypatch.setattr(database, "SQLCIPHER_KEY_FILE", None)
    assert database.verify_encryption()["encrypted"]
    conn = database.get_connection()
    try:
        assert conn.execute('SELECT content FROM "private ""notes"').fetchone()[0] == "preserve exactly"
    finally:
        conn.close()
    monkeypatch.setattr(database, "SQLCIPHER_KEY", "wrong key")
    assert not database.verify_encryption()["encrypted"]
    with sqlite3.connect(temp_db) as plain:
        try:
            plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("Migrated database is readable without its key")
