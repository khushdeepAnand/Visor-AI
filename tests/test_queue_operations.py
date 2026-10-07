import json
from services import task_queue as queue


def test_replay_restores_celery_args_and_kwargs(monkeypatch):
    entry = json.dumps({"id": "failed-1", "task": "test.replay", "payload": {"args": [7], "kwargs": {"limit": 12}}})
    class Redis:
        removed = False
        def lrange(self, *args): return [entry]
        def lrem(self, *args): self.removed = True
    redis = Redis()
    monkeypatch.setattr(queue, "_redis_client", lambda: redis)
    monkeypatch.setattr(queue, "_celery_available", False)
    monkeypatch.setenv("STOCKPILOT_TASK_QUEUE_ENABLED", "false")
    monkeypatch.setenv("STOCKPILOT_TASK_MAX_RETRIES", "0")
    monkeypatch.setitem(queue._registry, "test.replay", lambda user_id, limit: {"user_id": user_id, "limit": limit})
    assert queue.replay_dead_letter("failed-1") is True
    assert redis.removed is True


def test_job_console_never_exposes_payload_or_traceback(monkeypatch):
    monkeypatch.setattr(queue, "get_dead_letters", lambda limit: [{"id": "1", "task": "known", "payload": {"secret": "private"}, "traceback": "private", "error": "private token", "failed_at": "now", "attempts": 3}])
    result = queue.operation_snapshot()
    assert "private" not in json.dumps(result)
    assert result["dead_letters"][0]["id"] == "1"
