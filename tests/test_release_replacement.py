"""A rejected release must not destroy the previously distributed archive."""
from pathlib import Path
import zipfile

import pytest

from scripts import build_release


def test_failed_extracted_verification_preserves_previous_archive(tmp_path, monkeypatch):
    output = tmp_path / "previous-release.zip"
    previous = b"previous verified release sentinel"
    output.write_bytes(previous)

    def fail(*args, **kwargs):
        raise RuntimeError("extracted smoke regression")

    monkeypatch.setattr(build_release, "verify_extracted_release", fail)
    with pytest.raises(RuntimeError, match="extracted smoke regression"):
        build_release.build(Path(__file__).resolve().parents[1], output, ignore_local_env_policy=True)
    assert output.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp.zip")) == []


def test_verified_candidate_replaces_the_requested_archive(tmp_path, monkeypatch):
    output = tmp_path / "release.zip"
    output.write_bytes(b"previous verified release sentinel")
    monkeypatch.setattr(build_release, "verify_extracted_release", lambda *args, **kwargs: None)
    count, size, digest = build_release.build(
        Path(__file__).resolve().parents[1], output, ignore_local_env_policy=True,
    )
    assert count > 0 and size == output.stat().st_size and len(digest) == 64
    with zipfile.ZipFile(output) as archive:
        assert "StockPilot-AI-v18/main.py" in archive.namelist()
    assert list(tmp_path.glob("*.tmp.zip")) == []
