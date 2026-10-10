"""Authenticated row/field-bound encryption for repository PII and broker secrets."""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "sp-field-v1:"


def _cipher() -> AESGCM:
    secret = os.getenv("STOCKPILOT_FIELD_ENCRYPTION_KEY", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("Independent persistent field encryption key must contain at least 32 bytes")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"stockpilot-field-v1").derive(secret.encode())
    return AESGCM(key)


def encrypt_field(value: str | None, *, field: str, owner: str) -> str | None:
    if value is None:
        return None
    nonce = os.urandom(12)
    aad = f"stockpilot-field-v1\0{field}\0{owner}".encode()
    encrypted = _cipher().encrypt(nonce, value.encode(), aad)
    return PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_field(value: str | None, *, field: str, owner: str) -> str | None:
    if value is None or not value.startswith(PREFIX):
        return value  # Legacy plaintext remains explicitly unmigrated.
    try:
        encrypted = base64.b64decode(value[len(PREFIX):], altchars=b"-_", validate=True)
        aad = f"stockpilot-field-v1\0{field}\0{owner}".encode()
        return _cipher().decrypt(encrypted[:12], encrypted[12:], aad).decode()
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise ValueError("Field authentication failed") from exc
