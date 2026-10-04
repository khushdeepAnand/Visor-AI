"""Tests for the Celery/inline task queue with retries and DLQ."""
from __future__ import annotations

import pytest

from services import task_queue


def test_task_registration_and_inline_execution():
    @task_queue.task("test.echo")
    def echo(value):
        return value * 2

    result = task_queue.submit("test.echo", {"value": 21})
    assert result["mode"] == "inline"
    assert result["result"] == 42


def test_unknown_task_goes_to_dlq_path():
    result = task_queue.submit("does.not.exist", {})
    assert result["mode"] == "dlq"


def test_failing_task_retries_then_dlq(monkeypatch):
    calls = {"n": 0}
    recorded = []
    monkeypatch.setenv("STOCKPILOT_TASK_MAX_RETRIES", "2")
    monkeypatch.setenv("STOCKPILOT_TASK_RETRY_BACKOFF", "0")
    monkeypatch.setattr(task_queue, "record_dead_letter",
                        lambda q, t, p, e: recorded.append((t, e)))

    @task_queue.task("test.always_fails")
    def always_fails():
        calls["n"] += 1
        raise RuntimeError("boom")

    result = task_queue.submit("test.always_fails", {})
    assert result["mode"] == "dlq"
    # initial attempt + 2 retries = 3 calls
    assert calls["n"] == 3
    assert recorded and recorded[0][0] == "test.always_fails"


def test_transient_failure_recovers(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_TASK_RETRY_BACKOFF", "0")
    attempts = {"n": 0}

    @task_queue.task("test.flaky")
    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    result = task_queue.submit("test.flaky", {})
    assert result["mode"] == "inline"
    assert result["result"] == "ok"


def test_queue_stats_shape():
    stats = task_queue.queue_stats()
    assert set(stats) >= {
        "enabled", "celery_available", "broker_connected",
        "registered_tasks", "dead_letter_count",
    }
    assert isinstance(stats["registered_tasks"], list)
    # credentials must never leak in broker label
    assert "@" not in stats["broker"]


def test_dead_letter_replay_keeps_entry_when_resubmission_fails(monkeypatch):
    import json
    class Client:
        removed = False
        def lrange(self, *args):
            return [json.dumps({"id": "retry-one", "task": "test.echo", "payload": {"value": 1}})]
        def lrem(self, *args):
            self.removed = True
    client = Client()
    monkeypatch.setattr(task_queue, "_redis_client", lambda: client)
    monkeypatch.setattr(task_queue, "submit", lambda *args: {"mode": "dlq"})
    assert task_queue.replay_dead_letter("retry-one") is False
    assert not client.removed
