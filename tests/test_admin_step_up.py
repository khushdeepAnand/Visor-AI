"""Tests for step-up authentication on sensitive admin actions.

Intended repository path: ``tests/test_admin_step_up.py``.

A fake password verifier and a temporary SQLite database keep the tests hermetic;
no real credential or hash is created.
"""
from __future__ import annotations

import sqlite3

import pytest

from services.admin_step_up import (
    AdminStepUpService,
    FAILED_ATTEMPT_WINDOW_SECONDS,
    MAX_FAILED_ATTEMPTS,
    STEP_UP_ACTIONS,
    StepUpError,
    requires_step_up,
)

ADMIN = "ops@example.com"
VALID_INPUT = "correct-horse-battery"


@pytest.fixture()
def service(tmp_path):
    path = tmp_path / "stepup.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT)")
    connection.execute(
        "INSERT INTO users(id, email, password_hash) VALUES(1, ?, ?)",
        (ADMIN, "stored-hash-placeholder"),
    )
    connection.commit()
    connection.close()

    def factory() -> sqlite3.Connection:
        return sqlite3.connect(path)

    def verifier(password: str, hashed: str) -> bool:
        return password == VALID_INPUT and hashed == "stored-hash-placeholder"

    return AdminStepUpService(connection_factory=factory, password_verifier=verifier)


def test_protected_actions_cover_the_audit_requirements():
    for action in (
        "provider_mode_change",
        "model_promotion",
        "model_rollback",
        "cache_invalidation",
        "backup_restore",
        "admin_change",
    ):
        assert action in STEP_UP_ACTIONS
        assert requires_step_up(action) is True
    assert requires_step_up("view_overview") is False


def test_correct_password_grants_a_single_use_token(service):
    granted = service.challenge(
        actor_email=ADMIN,
        password=VALID_INPUT,
        action="model_promotion",
        target="pooled-cs-ridge-1.0.0",
    )
    assert granted["single_use"] is True
    token = granted["step_up_token"]

    grant = service.consume(
        actor_email=ADMIN,
        token=token,
        action="model_promotion",
        target="pooled-cs-ridge-1.0.0",
    )
    assert grant.action == "model_promotion"

    with pytest.raises(StepUpError):
        service.consume(
            actor_email=ADMIN,
            token=token,
            action="model_promotion",
            target="pooled-cs-ridge-1.0.0",
        )


def test_token_is_bound_to_action_actor_and_target(service):
    token = service.challenge(
        actor_email=ADMIN,
        password=VALID_INPUT,
        action="provider_mode_change",
        target="LIVE_ONLY",
    )["step_up_token"]
    with pytest.raises(StepUpError):
        service.consume(actor_email=ADMIN, token=token, action="backup_restore", target="LIVE_ONLY")

    token = service.challenge(
        actor_email=ADMIN,
        password=VALID_INPUT,
        action="provider_mode_change",
        target="LIVE_ONLY",
    )["step_up_token"]
    with pytest.raises(StepUpError):
        service.consume(actor_email="someone-else@example.com", token=token, action="provider_mode_change", target="LIVE_ONLY")

    token = service.challenge(
        actor_email=ADMIN,
        password=VALID_INPUT,
        action="cache_invalidation",
        target="RELIANCE:1d",
    )["step_up_token"]
    with pytest.raises(StepUpError):
        service.consume(actor_email=ADMIN, token=token, action="cache_invalidation", target="TCS:1d")


def test_wrong_password_is_denied_and_never_mints_a_token(service):
    with pytest.raises(StepUpError):
        service.challenge(actor_email=ADMIN, password="wrong", action="backup_restore")
    with pytest.raises(StepUpError):
        service.consume(actor_email=ADMIN, token="fabricated-token", action="backup_restore")


def test_unknown_action_is_rejected(service):
    with pytest.raises(StepUpError):
        service.challenge(actor_email=ADMIN, password=VALID_INPUT, action="delete_everything")


def test_repeated_failures_lock_the_actor_out(service):
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(StepUpError):
            service.challenge(actor_email=ADMIN, password="wrong", action="admin_change")
    # Even the correct password is refused while the lockout window is open.
    with pytest.raises(StepUpError):
        service.challenge(actor_email=ADMIN, password=VALID_INPUT, action="admin_change")


def test_expired_token_is_refused(service, tmp_path):
    token = service.challenge(
        actor_email=ADMIN,
        password=VALID_INPUT,
        action="cache_invalidation",
        ttl_seconds=30,
    )["step_up_token"]
    connection = sqlite3.connect(tmp_path / "stepup.db")
    connection.execute("UPDATE admin_step_up_tokens SET expires_at='2020-01-01T00:00:00+00:00'")
    connection.commit()
    connection.close()
    with pytest.raises(StepUpError):
        service.consume(actor_email=ADMIN, token=token, action="cache_invalidation")


def test_plaintext_token_is_never_persisted(service, tmp_path):
    token = service.challenge(actor_email=ADMIN, password=VALID_INPUT, action="backup_restore")["step_up_token"]
    connection = sqlite3.connect(tmp_path / "stepup.db")
    stored = [row[0] for row in connection.execute("SELECT token_hash FROM admin_step_up_tokens").fetchall()]
    connection.close()
    assert stored and token not in stored


def test_requirements_expose_policy_without_state(service):
    policy = service.requirements()
    assert policy["single_use"] is True
    assert policy["max_failed_attempts"] == MAX_FAILED_ATTEMPTS
    assert policy["failed_attempt_window_seconds"] == FAILED_ATTEMPT_WINDOW_SECONDS
    assert "model_promotion" in policy["actions"]
