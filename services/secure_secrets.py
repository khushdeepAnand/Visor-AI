"""Windows DPAPI-backed runtime secret storage.

Values are encrypted for the current Windows user and never written to the
project tree. Explicit process environment values retain highest precedence;
the plaintext ``.env`` file remains a fallback for non-Windows deployments.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Mapping, MutableMapping


STORE_VERSION = 1
DPAPI_DESCRIPTION = "StockPilot AI secrets"
DPAPI_ENTROPY = b"StockPilot AI DPAPI store v1"
SENSITIVE_KEYS = frozenset({
    "APPLE_PRIVATE_KEY",
    "GLOBALDATAFEEDS_GATEWAY_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "REDIS_URL",
    "SENTRY_DSN",
    "SMTP_PASSWORD",
    "STOCKPILOT_JWT_SECRET",
    "STOCKPILOT_LLM_API_KEY",
    "STOCKPILOT_MFA_SECRET",
    "STOCKPILOT_DB_ENCRYPTION_KEY",
    "STOCKPILOT_FIELD_ENCRYPTION_KEY",
    "STOCKPILOT_BACKUP_SECRET",
    "STOCKPILOT_AUDIT_SECRET",
    "TRUEDATA_GATEWAY_TOKEN",
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_ANALYTICS_TOKEN",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
})


class SecretStoreError(RuntimeError):
    """The local encrypted store could not be read or written safely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def default_store_path() -> Path:
    override = os.getenv("STOCKPILOT_SECRET_STORE_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise SecretStoreError("LOCALAPPDATA is unavailable; the Windows secret store path cannot be resolved.")
    return Path(local_app_data) / "StockPilotAI" / "secrets.dpapi.json"


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    return _DataBlob(len(data), pointer), buffer


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("DPAPI secret storage is available only on Windows.")
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(DPAPI_ENTROPY)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    description = DPAPI_DESCRIPTION if protect else None
    result = function(
        ctypes.byref(input_blob),
        description,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    del input_buffer, entropy_buffer
    if not result:
        raise SecretStoreError("Windows DPAPI could not process the secret store.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _protect(value: str) -> str:
    return base64.b64encode(_dpapi(value.encode("utf-8"), protect=True)).decode("ascii")


def _unprotect(value: str) -> str:
    try:
        encrypted = base64.b64decode(value.encode("ascii"), validate=True)
        return _dpapi(encrypted, protect=False).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise SecretStoreError("The DPAPI secret store contains invalid encrypted data.") from exc


def read_secure_secrets(path: Path | None = None) -> dict[str, str]:
    store_path = path or default_store_path()
    if not store_path.exists():
        return {}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SecretStoreError("The DPAPI secret store is unreadable.") from exc
    if payload.get("version") != STORE_VERSION or not isinstance(payload.get("secrets"), dict):
        raise SecretStoreError("The DPAPI secret store format is unsupported.")
    result: dict[str, str] = {}
    for name, encrypted in payload["secrets"].items():
        if name not in SENSITIVE_KEYS or not isinstance(encrypted, str):
            raise SecretStoreError("The DPAPI secret store contains an unsupported entry.")
        result[name] = _unprotect(encrypted)
    return result


def write_secure_secrets(values: Mapping[str, str], path: Path | None = None) -> Path:
    unsupported = set(values) - SENSITIVE_KEYS
    if unsupported:
        raise SecretStoreError(f"Unsupported secret name: {sorted(unsupported)[0]}")
    clean = {name: str(value) for name, value in values.items() if str(value)}
    store_path = path or default_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STORE_VERSION,
        "scope": "current_windows_user",
        "secrets": {name: _protect(value) for name, value in sorted(clean.items())},
    }
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=store_path.parent,
            prefix=f".{store_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, store_path)
    except OSError as exc:
        raise SecretStoreError("The DPAPI secret store could not be written atomically.") from exc
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return store_path


def load_windows_secure_secrets(
    *,
    environ: MutableMapping[str, str] | None = None,
    path: Path | None = None,
) -> tuple[str, ...]:
    """Load DPAPI values without overriding an explicit process environment."""
    target = environ if environ is not None else os.environ
    if sys.platform != "win32":
        return ()
    if target.get("STOCKPILOT_ENV", "").strip().lower() == "test" and path is None:
        return ()
    values = read_secure_secrets(path)
    loaded: list[str] = []
    for name, value in values.items():
        if name not in target:
            target[name] = value
            loaded.append(name)
    return tuple(sorted(loaded))


def update_secure_secrets(updates: Mapping[str, str | None], path: Path | None = None) -> Path:
    values = read_secure_secrets(path)
    for name, value in updates.items():
        if name not in SENSITIVE_KEYS:
            raise SecretStoreError(f"Unsupported secret name: {name}")
        if value:
            values[name] = value
        else:
            values.pop(name, None)
    return write_secure_secrets(values, path)
