"""Celery task queue for StockPilot AI.

Design:
  * Optional — the app runs fine without Redis/Celery (falls back to an
    in-process synchronous executor so tests and local dev never need a broker).
  * Retries with exponential backoff; exhausted tasks land in a Redis-backed
    dead-letter list (``stockpilot:dlq``) that admins can inspect/replay.
  * Queue selection: ``STOCKPILOT_CELERY_BROKER_URL`` (default redis://).

Run a worker with:
    celery -A services.task_queue.celery_app worker -Q forecast,default -l info
"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, cast

logger = logging.getLogger("stockpilot.tasks")

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

def _broker_url() -> str:
    return os.getenv("STOCKPILOT_CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _queue_enabled() -> bool:
    return os.getenv("STOCKPILOT_TASK_QUEUE_ENABLED", "false").strip().lower() in ("1", "true", "yes")


# DLQ keys
DLQ_KEY = "stockpilot:dlq"
DLQ_MAX = 1000  # trim oldest entries beyond this


# ---------------------------------------------------------------------
# Celery app (only constructed when enabled + celery importable)
# ---------------------------------------------------------------------

celery_app = None
_celery_available = False

if _queue_enabled():
    try:
        from celery import Celery  # type: ignore
        from celery.exceptions import MaxRetriesExceededError  # type: ignore

        celery_app = Celery(
            "stockpilot",
            broker=_broker_url(),
            backend=_broker_url(),
        )
        celery_app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            timezone="UTC",
            enable_utc=True,
            task_acks_late=True,
            task_reject_on_worker_lost=True,
            broker_transport_options={"visibility_timeout": 3600},
            worker_prefetch_multiplier=1,
            beat_schedule={
                "daily-retention": {"task": "services.task_queue.tasks.run_retention", "schedule": 86400.0},
                "daily-instruments": {"task": "services.task_queue.tasks.refresh_instruments", "schedule": 86400.0},
            },
            task_default_queue="default",
            task_routes={
                "services.task_queue.*": {"queue": "forecast"},
            },
            task_annotations={
                "*": {"max_retries": int(os.getenv("STOCKPILOT_TASK_MAX_RETRIES", "3"))}
            },
        )
        _celery_available = True
    except ImportError:
        logger.info("Celery not installed; task queue falls back to inline execution.")


# ---------------------------------------------------------------------
# Dead-letter queue helpers (Redis-backed, works even without Celery)
# ---------------------------------------------------------------------

def _redis_client() -> Any:
    try:
        import redis
        return redis.Redis.from_url(
            _broker_url(), decode_responses=True,
            socket_connect_timeout=0.5, socket_timeout=0.5,
            retry_on_timeout=False,
        )
    except Exception:
        return None


def record_dead_letter(queue: str, task_name: str, payload: Any, error: str) -> None:
    """Append a failed task to the dead-letter list for admin replay."""
    entry = json.dumps({
        "id": uuid.uuid4().hex,
        "queue": queue,
        "task": task_name,
        "payload": payload,
        "error": error,
        "traceback": traceback.format_exc(),
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 1,
    }, default=str)
    client = _redis_client()
    if client is None:
        logger.error("DLQ (no redis): %s -> %s", task_name, error)
        return
    try:
        client.lpush(DLQ_KEY, entry)
        client.ltrim(DLQ_KEY, 0, DLQ_MAX - 1)
    except Exception:
        logger.error("DLQ unreachable (%s): %s -> %s", type(client).__name__, task_name, error)


def get_dead_letters(limit: int = 50) -> list[dict[str, Any]]:
    """Read recent dead-letter entries (admin inspection)."""
    client = _redis_client()
    if client is None:
        return []
    try:
        raw = client.lrange(DLQ_KEY, 0, max(0, limit - 1))
        return [json.loads(item) for item in raw]
    except Exception:
        return []


def replay_dead_letter(entry_id: str) -> bool:
    """Re-enqueue a dead-lettered task by id. Returns True if found & requeued."""
    client = _redis_client()
    if client is None:
        return False
    try:
        entries = client.lrange(DLQ_KEY, 0, -1)
        for idx, raw in enumerate(entries):
            entry = json.loads(raw)
            if entry.get("id") == entry_id:
                result = submit(entry.get("task", ""), entry.get("payload"))
                if result.get("mode") not in {"queued", "inline"}:
                    return False
                client.lrem(DLQ_KEY, 1, raw)
                return True
        return False
    except Exception:
        logger.exception("Failed to replay dead letter")
        return False


# ---------------------------------------------------------------------
# Task registry & submission
# ---------------------------------------------------------------------

_registry: dict[str, Callable[..., Any]] = {}


def task(name: Optional[str] = None, max_retries: int = 3, retry_backoff: float = 2.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator registering a function as a queue task.

    When Celery is enabled, also wraps it as a celery task with autoretry.
    """
    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        task_name = name or f"{fn.__module__}.{fn.__qualname__}"

        if _celery_available and celery_app is not None:
            @celery_app.task(  # type: ignore[untyped-decorator]
                name=task_name,
                bind=True,
                max_retries=max_retries,
                autoretry_for=(Exception,),
                retry_backoff=retry_backoff,
                retry_backoff_max=60,
                retry_jitter=True,
            )
            def _celery_task(self: Any, *args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if self.request.retries >= max_retries:
                        record_dead_letter("forecast", task_name, {"args": args, "kwargs": kwargs}, type(exc).__name__)
                    raise
            _registry[task_name] = fn
            setattr(_celery_task, "_stockpilot_original", fn)
            return cast(Callable[..., Any], _celery_task)

        _registry[task_name] = fn
        return fn

    return _wrap


def submit(task_name: str, payload: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Submit a task to the queue (or run inline when the queue is disabled).

    Returns {"mode": "queued"|"inline"|"dlq", ...}.
    """
    fn = _registry.get(task_name)
    if fn is None:
        record_dead_letter("unknown", task_name, payload, "task not registered")
        return {"mode": "dlq", "error": "task not registered"}

    payload = payload or {}

    if _celery_available and celery_app is not None:
        try:
            async_result = celery_app.send_task(task_name, args=(*args,), kwargs={**payload, **kwargs})
            return {"mode": "queued", "task_id": async_result.id}
        except Exception as exc:
            # Broker down -> fall through to inline so the user still gets work done.
            logger.warning("Broker unavailable (%s); executing inline.", exc)

    # Inline execution with retry + DLQ.
    max_retries = int(os.getenv("STOCKPILOT_TASK_MAX_RETRIES", "3"))
    backoff = float(os.getenv("STOCKPILOT_TASK_RETRY_BACKOFF", "2.0"))
    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **payload, **kwargs)
            return {"mode": "inline", "result": result, "attempts": attempt + 1}
        except Exception as exc:
            if attempt >= max_retries:
                record_dead_letter("inline", task_name, payload, str(exc))
                return {"mode": "dlq", "error": str(exc)}
            if backoff > 0:
                time.sleep(min(backoff * (2 ** attempt), 30.0))

    return {"mode": "dlq", "error": "unreachable"}


def queue_stats() -> dict[str, Any]:
    """Queue health snapshot for the admin/health endpoint."""
    client = _redis_client()
    dlq_len = 0
    connected = False
    if client is not None:
        try:
            dlq_len = client.llen(DLQ_KEY)
            connected = bool(client.ping())
        except Exception:
            connected = False
    return {
        "enabled": _queue_enabled(),
        "celery_available": _celery_available,
        "broker": _broker_url().split("@")[-1],  # never leak credentials
        "broker_connected": connected,
        "registered_tasks": sorted(_registry.keys()),
        "dead_letter_count": dlq_len,
    }


# ---------------------------------------------------------------------
# Built-in tasks
# ---------------------------------------------------------------------

@task("services.task_queue.tasks.replay_settlement")
def replay_settlement(user_id: int, limit: int = 200) -> dict[str, Any]:
    """Re-run outcome settlement for a user (slow, best done off-request)."""
    from services.outcome_settlement import settle_due_forecasts
    return {"user_id": user_id, "settled": settle_due_forecasts(user_id=user_id, limit=limit)}


@task("services.task_queue.tasks.refresh_instruments")
def refresh_instruments() -> dict[str, Any]:
    """Refresh the instrument catalogue from Upstox."""
    from api.deps import CATALOGUE
    return CATALOGUE.refresh_from_upstox()


@task("services.task_queue.tasks.run_retention")
def run_retention() -> dict[str, Any]:
    """Run the data-retention sweep."""
    from services.retention import run_retention_enforcement
    return run_retention_enforcement()
