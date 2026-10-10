"""Manage StockPilot's Windows DPAPI secret store without exposing values."""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.secure_secrets import (
    SENSITIVE_KEYS,
    SecretStoreError,
    default_store_path,
    read_secure_secrets,
    update_secure_secrets,
    write_secure_secrets,
)


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in SENSITIVE_KEYS:
            values[name] = value.strip().strip('"').strip("'")
    return values


def _scrub_dotenv(path: Path, names: set[str]) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            name = line.split("=", 1)[0].strip()
            if name in names:
                line = f"{name}="
        updated.append(line)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_value(name: str, *, stdin: bool) -> str:
    value = sys.stdin.readline().rstrip("\r\n") if stdin else getpass.getpass(f"New value for {name}: ")
    if not value:
        raise SecretStoreError("An empty value was refused; use the delete command to remove a secret.")
    return value


def _status(required: list[str]) -> int:
    values = read_secure_secrets()
    for name in sorted(SENSITIVE_KEYS):
        if name in values:
            print(f"{name}: configured")
    missing = [name for name in required if name not in values]
    if missing:
        print("Missing required secret(s): " + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"Store: {default_store_path()}")
    return 0


def _bootstrap(env_file: Path) -> int:
    existing = read_secure_secrets()
    fallback = _dotenv_values(env_file)
    updates: dict[str, str] = {}
    for name in ("STOCKPILOT_JWT_SECRET", "STOCKPILOT_MFA_SECRET", "STOCKPILOT_DB_ENCRYPTION_KEY", "STOCKPILOT_FIELD_ENCRYPTION_KEY", "STOCKPILOT_BACKUP_SECRET", "STOCKPILOT_AUDIT_SECRET"):
        updates[name] = existing.get(name) or fallback.get(name) or secrets.token_urlsafe(48)
    update_secure_secrets(updates)
    from scripts.migrate_encryption import migrate_in_place
    migration = migrate_in_place(env_file.parent / "database" / "stockpilot.db", updates["STOCKPILOT_DB_ENCRYPTION_KEY"])
    print(f"Database protection: {migration['state']}; preservation verified.")
    _scrub_dotenv(env_file, set(SENSITIVE_KEYS))
    print("DPAPI secret store initialized; plaintext secret assignments were cleared.")
    return 0


def _rotate_group(group: str) -> int:
    names = {
        "upstox": ("UPSTOX_ANALYTICS_TOKEN", "UPSTOX_ACCESS_TOKEN", "UPSTOX_API_KEY", "UPSTOX_API_SECRET"),
        "google": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    }
    if group == "jwt":
        update_secure_secrets({"STOCKPILOT_JWT_SECRET": secrets.token_urlsafe(48)})
        print("JWT secret rotated. Restart the backend; all existing session tokens are invalid.")
        return 0
    updates: dict[str, str] = {}
    for name in names[group]:
        value = getpass.getpass(f"Replacement {name} (Enter to preserve): ")
        if value:
            updates[name] = value
    if not updates:
        raise SecretStoreError("No replacement values were supplied.")
    update_secure_secrets(updates)
    print(f"{group.title()} local secrets rotated. Complete provider-side revocation and restart the backend.")
    return 0


def _migrate_environment(env_file: Path) -> int:
    """Preserve effective values and an encrypted copy before clearing fallback."""
    existing = read_secure_secrets()
    fallback = {name: value for name, value in _dotenv_values(env_file).items() if value}
    if not fallback:
        print("No populated allowlisted fallback credentials to move.")
        return 0
    backup = default_store_path().with_name("env-fallback-" + uuid.uuid4().hex + ".dpapi.json")
    write_secure_secrets(fallback, backup)
    if read_secure_secrets(backup) != fallback:
        raise SecretStoreError("Encrypted fallback backup did not verify; plaintext was preserved.")
    updates = {name: value for name, value in fallback.items() if name not in existing}
    if updates:
        update_secure_secrets(updates)
    expected = {**fallback, **existing}  # Existing DPAPI values have precedence.
    if read_secure_secrets() != expected:
        raise SecretStoreError("Effective secret preservation did not verify; plaintext was preserved.")
    _scrub_dotenv(env_file, set(fallback))
    print("Fallback credentials moved; effective DPAPI values preserved.")
    print(f"Verified current-user encrypted fallback backup: {backup}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="List configured names, never values")
    status.add_argument("--require", nargs="*", default=[])
    set_parser = subparsers.add_parser("set", help="Set one allowlisted secret")
    set_parser.add_argument("name", choices=sorted(SENSITIVE_KEYS))
    set_parser.add_argument("--stdin", action="store_true", help="Read exactly one line from standard input")
    delete = subparsers.add_parser("delete", help="Delete one secret")
    delete.add_argument("name", choices=sorted(SENSITIVE_KEYS))
    bootstrap = subparsers.add_parser("bootstrap", help="Initialize required secrets and scrub plaintext fallbacks")
    bootstrap.add_argument("--env-file", type=Path, default=Path(".env"))
    migrate = subparsers.add_parser("migrate-env", help="Preserve an encrypted fallback backup, move missing values to DPAPI, and clear plaintext assignments")
    migrate.add_argument("--env-file", type=Path, default=Path(".env"))
    rotate = subparsers.add_parser("rotate", help="Rotate a supported credential group atomically")
    rotate.add_argument("group", choices=("jwt", "upstox", "google"))
    args = parser.parse_args()

    try:
        if args.command == "status":
            return _status(args.require)
        if args.command == "set":
            update_secure_secrets({args.name: _read_value(args.name, stdin=args.stdin)})
            print(f"{args.name}: updated")
            return 0
        if args.command == "delete":
            update_secure_secrets({args.name: None})
            print(f"{args.name}: deleted")
            return 0
        if args.command == "bootstrap":
            return _bootstrap(args.env_file.resolve())
        if args.command == "migrate-env":
            return _migrate_environment(args.env_file.resolve())
        return _rotate_group(args.group)
    except SecretStoreError as exc:
        print(f"Secret-store error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
