"""Shared resource limits and trusted local model-artifact loading."""

from __future__ import annotations

import hashlib
import hmac
from io import BytesIO
import os
from pathlib import Path
import re
from typing import Any, Iterable

import joblib

MODEL_WORKERS_ENV = "STOCKPILOT_MODEL_WORKERS"
MAX_MODEL_WORKERS = 4
MODEL_ARTIFACT_SUFFIXES = frozenset({".joblib", ".pickle", ".pkl"})
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class ArtifactSecurityError(ValueError):
    """A serialized artifact failed path or integrity validation."""


def get_model_worker_count(configured: object | None = None) -> int:
    """Return a positive, CPU-aware worker count with a conservative default."""

    value = os.getenv(MODEL_WORKERS_ENV, "1") if configured is None else configured
    try:
        requested = int(value) if isinstance(value, (str, int)) else 1
    except (ValueError, OverflowError):
        requested = 1
    available = max(1, os.cpu_count() or 1)
    return min(max(1, requested), MAX_MODEL_WORKERS, available)


def load_trusted_joblib_artifact(
    artifact_path: str | os.PathLike[str],
    *,
    allowed_roots: Iterable[str | os.PathLike[str]],
    expected_sha256: str | None = None,
) -> Any:
    """Load a pickle-backed artifact only from an explicitly trusted root.

    Joblib artifacts can execute code while loading. Resolving the candidate and
    roots prevents traversal or a symlink from expanding the trust boundary. If
    a digest is supplied, the exact in-memory bytes passed to joblib must match.
    """

    roots = tuple(Path(root).resolve() for root in allowed_roots)
    if not roots:
        raise ArtifactSecurityError("At least one trusted artifact root is required.")

    candidate = Path(artifact_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ArtifactSecurityError("Model artifact does not exist or cannot be resolved.") from error
    if not resolved.is_file():
        raise ArtifactSecurityError("Model artifact must be a regular file.")
    if resolved.suffix.lower() not in MODEL_ARTIFACT_SUFFIXES:
        raise ArtifactSecurityError("Model artifact type is not allowlisted.")
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ArtifactSecurityError("Model artifact is outside the trusted roots.")

    if expected_sha256 is not None and not SHA256_PATTERN.fullmatch(expected_sha256.strip()):
        raise ArtifactSecurityError("Expected model artifact SHA-256 is malformed.")

    try:
        payload = resolved.read_bytes()
    except OSError as error:
        raise ArtifactSecurityError("Model artifact could not be read.") from error
    if expected_sha256 is not None:
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256.strip().lower()):
            raise ArtifactSecurityError("Model artifact SHA-256 mismatch.")

    return joblib.load(BytesIO(payload))
