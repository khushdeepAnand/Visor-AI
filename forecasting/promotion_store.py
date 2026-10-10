"""Signed, append-only model decisions using the configured SQLite/SQLCipher driver.

Database triggers prevent application updates/deletes. HMAC chains detect edits.
Database-owner attacks and history truncation require an external WORM anchor;
local filesystem storage alone cannot provide that guarantee.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from database import _open_connection


def signing_key() -> bytes:
    value = os.getenv("STOCKPILOT_AUDIT_SECRET", "")
    if len(value.encode()) < 32:
        raise RuntimeError("STOCKPILOT_AUDIT_SECRET must contain at least 32 bytes for model decisions")
    return value.encode()


def canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def sign_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in payload.items() if key != "signature"}
    return {**content, "signature": hmac.new(signing_key(), canonical(content).encode(), hashlib.sha256).hexdigest()}


def valid_signature(payload: Mapping[str, Any]) -> bool:
    try:
        expected = sign_payload(payload)["signature"]
        return hmac.compare_digest(str(payload.get("signature", "")), str(expected))
    except (RuntimeError, ValueError, TypeError):
        return False


def registry_path(manifest: Path) -> Path:
    return manifest.with_suffix(".registry.db")


def connect_registry(manifest: Path) -> Any:
    connection = _open_connection(registry_path(manifest))
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS model_decisions(
            id INTEGER PRIMARY KEY, action TEXT NOT NULL, payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL, entry_hash TEXT NOT NULL UNIQUE);
        CREATE TRIGGER IF NOT EXISTS model_decisions_no_update BEFORE UPDATE ON model_decisions
            BEGIN SELECT RAISE(ABORT, 'model decisions are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS model_decisions_no_delete BEFORE DELETE ON model_decisions
            BEGIN SELECT RAISE(ABORT, 'model decisions are append-only'); END;
        CREATE TABLE IF NOT EXISTS model_controls(key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_control_events(id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS model_control_events_no_update BEFORE UPDATE ON model_control_events
            BEGIN SELECT RAISE(ABORT, 'model controls are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS model_control_events_no_delete BEFORE DELETE ON model_control_events
            BEGIN SELECT RAISE(ABORT, 'model controls are append-only'); END;
    """)
    return connection


def _entry_hash(sequence: int, action: str, payload: str, previous: str) -> str:
    content = canonical({"sequence": sequence, "action": action, "payload": payload, "previous": previous})
    return hmac.new(signing_key(), content.encode(), hashlib.sha256).hexdigest()


def _verified_rows(connection: Any) -> list[tuple[Any, ...]]:
    rows = connection.execute("SELECT id,action,payload_json,previous_hash,entry_hash FROM model_decisions ORDER BY id").fetchall()
    previous = ""
    for expected_sequence, row in enumerate(rows, 1):
        sequence, action, payload, predecessor, digest = row
        if sequence != expected_sequence or predecessor != previous or not hmac.compare_digest(
            digest, _entry_hash(sequence, action, payload, predecessor)
        ):
            raise RuntimeError("Model decision audit integrity failed")
        previous = digest
    return cast(list[tuple[Any, ...]], rows)


def append_decision(manifest: Path, action: str, payload: Mapping[str, Any], *, actor: str, reason: str,
                    export: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    signing_key()
    if action not in {"promote", "reject", "rollback", "revoke"}:
        raise ValueError("Unsupported model decision")
    signed = sign_payload(payload)
    event = {"action": action, "actor": actor[:120], "reason": reason[:500],
             "recorded_at": datetime.now(timezone.utc).isoformat(), "manifest": signed}
    text = canonical(event)
    connection = connect_registry(manifest)
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = _verified_rows(connection)
        events = [json.loads(row[2]) for row in rows]
        if action == "rollback" and any(event["action"] == "revoke" and
                event["manifest"]["receipt"].get("artifact_hash") == signed["receipt"].get("artifact_hash")
                for event in events):
            raise ValueError("Prior artifact was revoked; re-evaluation required")
        if action == "revoke":
            current = [event for event in events if event["action"] != "reject"]
            if not current or canonical(current[-1]["manifest"]) != canonical(signed):
                raise RuntimeError("Active model changed during revocation; retry")
        sequence = len(rows) + 1
        previous = rows[-1][4] if rows else ""
        digest = _entry_hash(sequence, action, text, previous)
        connection.execute("INSERT INTO model_decisions VALUES(?,?,?,?,?)", (sequence, action, text, previous, digest))
        # Serialize the export with all writers, including other processes. If
        # export fails, close rolls back the decision and the prior receipt stays
        # valid. A crash between export and commit fails closed on head mismatch.
        if export is not None:
            export(signed)
        connection.commit()
        return signed
    finally:
        connection.close()


def history(manifest: Path) -> list[dict[str, Any]]:
    if not registry_path(manifest).exists():
        return []
    connection = connect_registry(manifest)
    try:
        return [json.loads(row[2]) for row in _verified_rows(connection)]
    finally:
        connection.close()


def is_current(manifest: Path, payload: Mapping[str, Any]) -> bool:
    if not valid_signature(payload):
        return False
    events = history(manifest)
    activating = [event for event in events if event["action"] != "reject"]
    return bool(activating and activating[-1]["action"] != "revoke"
                and canonical(activating[-1]["manifest"]) == canonical(payload))
