from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

from scripts import manage_secrets
from services import secure_secrets


def _fake_protect(value: str) -> str:
    return base64.b64encode(("protected:" + value).encode("utf-8")).decode("ascii")


def _fake_unprotect(value: str) -> str:
    decoded = base64.b64decode(value).decode("utf-8")
    assert decoded.startswith("protected:")
    return decoded.removeprefix("protected:")


def test_secure_store_round_trip_contains_no_plaintext(tmp_path: Path, monkeypatch):
    store = tmp_path / "secrets.dpapi.json"
    monkeypatch.setattr(secure_secrets, "_protect", _fake_protect)
    monkeypatch.setattr(secure_secrets, "_unprotect", _fake_unprotect)

    secure_secrets.write_secure_secrets(
        {"STOCKPILOT_JWT_SECRET": "fixture-jwt-material", "UPSTOX_ANALYTICS_TOKEN": "fixture-token"},
        store,
    )

    serialized = store.read_text(encoding="utf-8")
    assert "fixture-jwt-material" not in serialized
    assert "fixture-token" not in serialized
    assert secure_secrets.read_secure_secrets(store) == {
        "STOCKPILOT_JWT_SECRET": "fixture-jwt-material",
        "UPSTOX_ANALYTICS_TOKEN": "fixture-token",
    }


def test_secure_loader_preserves_explicit_process_environment(tmp_path: Path, monkeypatch):
    store = tmp_path / "secrets.dpapi.json"
    monkeypatch.setattr(secure_secrets, "_protect", _fake_protect)
    monkeypatch.setattr(secure_secrets, "_unprotect", _fake_unprotect)
    monkeypatch.setattr(secure_secrets.sys, "platform", "win32")
    secure_secrets.write_secure_secrets(
        {"STOCKPILOT_JWT_SECRET": "stored", "UPSTOX_ACCESS_TOKEN": "stored-token"},
        store,
    )
    environ = {"STOCKPILOT_JWT_SECRET": "explicit"}

    loaded = secure_secrets.load_windows_secure_secrets(environ=environ, path=store)

    assert environ["STOCKPILOT_JWT_SECRET"] == "explicit"
    assert environ["UPSTOX_ACCESS_TOKEN"] == "stored-token"
    assert loaded == ("UPSTOX_ACCESS_TOKEN",)


def test_bootstrap_migrates_and_scrubs_plaintext_secrets(tmp_path: Path, monkeypatch):
    store = tmp_path / "secrets.dpapi.json"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STOCKPILOT_ENV=development\nSTOCKPILOT_JWT_SECRET=legacy-secret-material\nUPSTOX_ACCESS_TOKEN=legacy-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCKPILOT_SECRET_STORE_PATH", str(store))
    monkeypatch.setattr(secure_secrets, "_protect", _fake_protect)
    monkeypatch.setattr(secure_secrets, "_unprotect", _fake_unprotect)

    assert manage_secrets._bootstrap(env_file) == 0

    values = secure_secrets.read_secure_secrets(store)
    assert values["STOCKPILOT_JWT_SECRET"] == "legacy-secret-material"
    assert len(values["STOCKPILOT_MFA_SECRET"]) >= 32
    text = env_file.read_text(encoding="utf-8")
    assert "STOCKPILOT_JWT_SECRET=\n" in text
    assert "UPSTOX_ACCESS_TOKEN=\n" in text
    assert "legacy-secret-material" not in text
    assert "legacy-token" not in text


def test_unknown_secret_names_are_refused(tmp_path: Path):
    try:
        secure_secrets.write_secure_secrets({"UNAPPROVED_SECRET": "value"}, tmp_path / "store.json")
    except secure_secrets.SecretStoreError as exc:
        assert "Unsupported secret name" in str(exc)
    else:
        raise AssertionError("unsupported secret name was accepted")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_windows_dpapi_current_user_round_trip(tmp_path: Path):
    store = tmp_path / "native.dpapi.json"
    secure_secrets.write_secure_secrets({"STOCKPILOT_JWT_SECRET": "native-round-trip-value"}, store)
    assert "native-round-trip-value" not in store.read_text(encoding="utf-8")
    assert secure_secrets.read_secure_secrets(store)["STOCKPILOT_JWT_SECRET"] == "native-round-trip-value"
