"""Tests for the operational guardrails (kill switch, flags, banners).

Intended repository path: ``tests/test_forecast_guardrails.py``.

The store is exercised through an injected SQLite connection factory, so these
tests never touch the application database.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.forecast_guardrails import (
    FEATURE_FLAGS,
    ForecastRequestContext,
    GuardrailError,
    GuardrailStore,
    MAX_KILL_SWITCH_HOURS,
)

REASON = "Upstox history endpoint is returning stale candles."


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "guardrails.db"

    def factory() -> sqlite3.Connection:
        return sqlite3.connect(path)

    return GuardrailStore(connection_factory=factory)


# -- kill switches ---------------------------------------------------------
def test_symbol_kill_switch_blocks_only_that_symbol(store):
    store.create_kill_switch(scope="symbol", target="reliance", reason=REASON, expires_in_hours=2)
    blocked = store.evaluate_forecast_request(ForecastRequestContext(symbol="RELIANCE", timeframe="1d"))
    allowed = store.evaluate_forecast_request(ForecastRequestContext(symbol="TCS", timeframe="1d"))
    assert blocked["blocked"] is True
    assert blocked["code"] == "forecast_disabled_by_operator"
    assert REASON in blocked["reason"]
    assert allowed["blocked"] is False


@pytest.mark.parametrize(
    "scope,target,context",
    [
        ("asset_class", "options", {"asset_class": "options"}),
        ("timeframe", "15m", {"timeframe": "15m"}),
        ("model_version", "pooled-cs-ridge-1.0.0", {"model_version": "pooled-cs-ridge-1.0.0"}),
        ("global", None, {"symbol": "INFY"}),
    ],
)
def test_every_supported_scope_blocks(store, scope, target, context):
    store.create_kill_switch(scope=scope, target=target, reason=REASON, expires_in_hours=1)
    assert store.evaluate_forecast_request(context)["blocked"] is True


def test_unknown_scope_and_asset_class_are_rejected(store):
    with pytest.raises(GuardrailError):
        store.create_kill_switch(scope="exchange", target="NSE", reason=REASON, expires_in_hours=1)
    with pytest.raises(GuardrailError):
        store.create_kill_switch(scope="asset_class", target="crypto", reason=REASON, expires_in_hours=1)


def test_reason_and_expiry_are_mandatory(store):
    with pytest.raises(GuardrailError):
        store.create_kill_switch(scope="symbol", target="TCS", reason="bad", expires_in_hours=1)
    with pytest.raises(GuardrailError):
        store.create_kill_switch(scope="symbol", target="TCS", reason=REASON, expires_in_hours=0)
    with pytest.raises(GuardrailError):
        store.create_kill_switch(
            scope="symbol",
            target="TCS",
            reason=REASON,
            expires_in_hours=MAX_KILL_SWITCH_HOURS + 1,
        )


def test_expired_switch_stops_blocking(store, tmp_path):
    created = store.create_kill_switch(scope="symbol", target="TCS", reason=REASON, expires_in_hours=1)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    connection = sqlite3.connect(tmp_path / "guardrails.db")
    connection.execute("UPDATE forecast_kill_switches SET expires_at=? WHERE id=?", (past, created["id"]))
    connection.commit()
    connection.close()
    assert store.evaluate_forecast_request({"symbol": "TCS"})["blocked"] is False
    assert store.list_kill_switches() == []
    assert store.list_kill_switches(include_inactive=True)[0]["expired"] is True


def test_revoke_is_the_rollback_path(store):
    created = store.create_kill_switch(scope="symbol", target="TCS", reason=REASON, expires_in_hours=4)
    store.revoke_kill_switch(created["id"], reason="Provider recovered and history is fresh again.")
    assert store.evaluate_forecast_request({"symbol": "TCS"})["blocked"] is False
    with pytest.raises(GuardrailError):
        store.revoke_kill_switch(created["id"], reason="Provider recovered and history is fresh again.")


# -- feature flags ---------------------------------------------------------
def test_flags_known_names_default_to_marked_safe_and_reject_unknowns(store):
    states = {item["name"]: item for item in store.flag_states()}
    assert set(states) == set(FEATURE_FLAGS)
    # The MD release gate reverted these to enabled by default so the shipped
    # app matches v5/6.0 behaviour; rollback, kill switches and validation
    # still remove the flag entirely when evidence is bad.
    assert all(item["enabled"] for item in states.values())
    with pytest.raises(GuardrailError):
        store.set_feature_flag("arbitrary_flag", enabled=True, rollout_percent=50, reason=REASON)


def test_staged_rollout_is_deterministic_and_partial(store):
    store.set_feature_flag(
        "forecast_corridor_v2",
        enabled=True,
        rollout_percent=50,
        reason="Staged rollout to half of signed-in users.",
    )
    subjects = [f"user-{index}" for index in range(200)]
    first = [store.is_feature_enabled("forecast_corridor_v2", subject=subject) for subject in subjects]
    second = [store.is_feature_enabled("forecast_corridor_v2", subject=subject) for subject in subjects]
    assert first == second
    assert 0 < sum(first) < len(subjects)


def test_enabling_requires_a_rollout_percentage(store):
    with pytest.raises(GuardrailError):
        store.set_feature_flag("scenario_studio", enabled=True, rollout_percent=0, reason=REASON)


def test_auto_rollback_needs_evidence_then_disables(store):
    store.set_feature_flag(
        "scenario_studio",
        enabled=True,
        rollout_percent=100,
        reason="Enable the scenario studio for all users.",
    )
    small = store.evaluate_auto_rollback("scenario_studio", samples=10, failures=10)
    assert small["action"] == "insufficient_samples"
    assert store.is_feature_enabled("scenario_studio", subject="user-1") is True

    healthy = store.evaluate_auto_rollback("scenario_studio", samples=200, failures=2)
    assert healthy["action"] == "within_budget"
    assert store.is_feature_enabled("scenario_studio", subject="user-1") is True

    rolled_back = store.evaluate_auto_rollback("scenario_studio", samples=200, failures=40)
    assert rolled_back["action"] == "rolled_back"
    assert store.is_feature_enabled("scenario_studio", subject="user-1") is False
    state = next(item for item in store.flag_states() if item["name"] == "scenario_studio")
    assert state["auto_rolled_back_at"]
    assert state["auto_rollback_detail"]["failure_rate"] == 0.2


# -- status banners --------------------------------------------------------
def test_banner_requires_preview_confirmation_before_publication(store):
    draft = store.draft_banner(
        level="degraded",
        headline="Delayed data",
        body="Broker history is delayed by about ten minutes while the provider recovers.",
        ends_in_hours=3,
    )
    assert draft["published"] is False
    assert store.active_banners() == []
    with pytest.raises(GuardrailError):
        store.publish_banner(draft["id"], confirmed_preview=False)
    store.publish_banner(draft["id"], confirmed_preview=True)
    active = store.active_banners()
    assert len(active) == 1
    assert active[0]["level"] == "degraded"


def test_banner_is_time_bounded_and_withdrawable(store):
    draft = store.draft_banner(
        level="maintenance",
        headline="Scheduled maintenance",
        body="Forecast jobs pause briefly while the local database is backed up.",
        ends_in_hours=1,
    )
    store.publish_banner(draft["id"], confirmed_preview=True)
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert store.active_banners(at=later) == []
    store.withdraw_banner(draft["id"])
    assert store.active_banners() == []


def test_invalid_banner_input_is_rejected(store):
    with pytest.raises(GuardrailError):
        store.draft_banner(level="party", headline="Hello", body="A long enough body for the check.")
    with pytest.raises(GuardrailError):
        store.draft_banner(level="info", headline="Hi", body="short")


def test_operations_summary_shape(store):
    summary = store.operations_summary()
    assert set(summary) == {"kill_switches", "feature_flags", "banners"}
    assert "symbol" in summary["kill_switches"]["scopes"]
    assert summary["kill_switches"]["max_hours"] == MAX_KILL_SWITCH_HOURS
