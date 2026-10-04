from pathlib import Path

import pytest

from scripts import check_dangling_refs as refs


def test_import_gate_catches_flattened_packages_without_importing(tmp_path):
    package = tmp_path / "services"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (tmp_path / "main.py").write_text("from services.missing import app\n")
    errors = refs.check_source_imports(tmp_path)
    assert any("services.missing" in error for error in errors)
    (package / "missing.py").write_text("app = object()\nraise RuntimeError('must not execute')\n")
    assert refs.check_source_imports(tmp_path) == []


def test_import_gate_checks_relative_modules_and_skips_dependencies(tmp_path):
    package = tmp_path / "api"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text("from .missing import app\nimport optional_external\n")
    assert any("api.missing" in error for error in refs.check_source_imports(tmp_path))


def test_import_gate_validates_imported_symbols_and_package_children(tmp_path):
    package = tmp_path / "api"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "service.py").write_text("def present():\n    return 1\n")
    (tmp_path / "main.py").write_text("from api.service import absent\nfrom api import service\n")
    errors = refs.check_source_imports(tmp_path)
    assert len(errors) == 1 and "absent" in errors[0]


def test_encrypted_database_roundtrip_and_wrong_key(tmp_path, monkeypatch):
    import database
    import sqlite3

    monkeypatch.setattr(database, "DATABASE_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DATABASE", str(tmp_path / "encrypted.db"))
    monkeypatch.setattr(database, "SQLCIPHER_KEY", "quoted'passphrase-for-encryption-testing")
    monkeypatch.setattr(database, "SQLCIPHER_KEY_FILE", None)
    conn = database._open_connection()
    conn.execute("CREATE TABLE evidence(value TEXT)")
    conn.execute("INSERT INTO evidence VALUES ('private record')")
    conn.commit()
    conn.close()
    assert not Path(database.DATABASE).read_bytes().startswith(b"SQLite format 3")
    conn = database._open_connection()
    assert conn.execute("SELECT value FROM evidence").fetchone()[0] == "private record"
    conn.close()
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(database.DATABASE) as plain:
            plain.execute("SELECT * FROM evidence")
    monkeypatch.setattr(database, "SQLCIPHER_KEY", "wrong-passphrase-for-encryption-testing")
    with pytest.raises(Exception):
        database._open_connection()


def test_missing_encryption_key_file_fails_closed(monkeypatch, tmp_path):
    import database

    monkeypatch.setattr(database, "SQLCIPHER_KEY", None)
    monkeypatch.setattr(database, "SQLCIPHER_KEY_FILE", str(tmp_path / "missing.key"))
    with pytest.raises(RuntimeError, match="key file"):
        database._get_encryption_key()


def test_release_smoke_cannot_be_skipped(monkeypatch, tmp_path):
    from scripts import build_release

    commands = []
    monkeypatch.setattr(build_release, "_run", lambda command, cwd, env: commands.append((command, cwd)))
    build_release.verify_extracted_release(tmp_path, backend=False, frontend=False, frontend_audit=False, launchers=False)
    assert any("import main" in " ".join(command) for command, _ in commands)
    assert any("smoke" in command for command, _ in commands)
    assert all(cwd == tmp_path for _, cwd in commands)
