from services import task_queue


def test_enabled_queue_never_runs_inline_when_broker_fails(monkeypatch):
    class Broker:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("broker unavailable")
    calls = []
    monkeypatch.setenv("STOCKPILOT_TASK_QUEUE_ENABLED", "true")
    monkeypatch.setattr(task_queue, "_celery_available", True)
    monkeypatch.setattr(task_queue, "celery_app", Broker())
    monkeypatch.setitem(task_queue._registry, "test.durable", lambda: calls.append(True))
    assert task_queue.submit("test.durable")["mode"] == "unavailable"
    assert calls == []


def test_enabled_queue_without_worker_library_does_not_run_inline(monkeypatch):
    calls = []
    monkeypatch.setenv("STOCKPILOT_TASK_QUEUE_ENABLED", "true")
    monkeypatch.setattr(task_queue, "_celery_available", False)
    monkeypatch.setitem(task_queue._registry, "test.durable", lambda: calls.append(True))
    assert task_queue.submit("test.durable")["mode"] == "unavailable"
    assert calls == []
