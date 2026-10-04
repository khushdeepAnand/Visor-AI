"""Model training resource-limit and artifact-security tests."""

import hashlib
from pathlib import Path

import joblib
import pytest

from model_runtime import (
    MAX_MODEL_WORKERS,
    MODEL_WORKERS_ENV,
    ArtifactSecurityError,
    get_model_worker_count,
    load_trusted_joblib_artifact,
)


def test_model_workers_default_to_one(monkeypatch):
    monkeypatch.delenv(MODEL_WORKERS_ENV, raising=False)

    assert get_model_worker_count() == 1


def test_model_workers_are_configurable_and_bounded(monkeypatch):
    monkeypatch.setattr("model_runtime.os.cpu_count", lambda: 32)
    monkeypatch.setenv(MODEL_WORKERS_ENV, "1000")

    assert get_model_worker_count() == MAX_MODEL_WORKERS
    assert get_model_worker_count(-1) == 1
    assert get_model_worker_count("invalid") == 1


def test_joblib_artifact_must_be_inside_an_allowlisted_root(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.pkl"
    joblib.dump({"unsafe_location": True}, outside)

    with pytest.raises(ArtifactSecurityError, match="outside the trusted roots"):
        load_trusted_joblib_artifact(outside, allowed_roots=(trusted,))


def test_joblib_artifact_digest_is_checked_before_loading(tmp_path: Path) -> None:
    artifact = tmp_path / "model.joblib"
    expected = {"model": "fixture"}
    joblib.dump(expected, artifact)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert load_trusted_joblib_artifact(
        artifact, allowed_roots=(tmp_path,), expected_sha256=digest
    ) == expected
    with pytest.raises(ArtifactSecurityError, match="SHA-256 mismatch"):
        load_trusted_joblib_artifact(
            artifact, allowed_roots=(tmp_path,), expected_sha256="0" * 64
        )


def test_joblib_artifact_rejects_non_allowlisted_extension(tmp_path: Path) -> None:
    artifact = tmp_path / "model.bin"
    joblib.dump({"model": "fixture"}, artifact)

    with pytest.raises(ArtifactSecurityError, match="type is not allowlisted"):
        load_trusted_joblib_artifact(artifact, allowed_roots=(tmp_path,))
