"""Bounded, thread-safe forecast execution and background job primitives."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, cast

import pandas as pd

# OpenTelemetry instrumentation
from services.otel_instrumentation import trace_forecast_operation


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ForecastExecutionOutcome:
    symbol: str
    frame: pd.DataFrame
    result: dict[str, Any]
    trace: dict[str, Any]


class ForecastExecutionService:
    """Content-addressed result cache with one producer per identical input."""

    def __init__(self, *, ttl_seconds: int | None = None, max_entries: int | None = None) -> None:
        self.ttl_seconds = max(
            1,
            int(ttl_seconds) if ttl_seconds is not None else _positive_int("STOCKPILOT_FORECAST_CACHE_SECONDS", 300),
        )
        self.max_entries = max(
            1,
            int(max_entries) if max_entries is not None else _positive_int("STOCKPILOT_FORECAST_CACHE_MAX_ENTRIES", 128),
        )
        self._lock = threading.RLock()
        self._cache: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
        self._flights: dict[str, Future[dict[str, Any]]] = {}
        self._stats: Counter[str] = Counter()

    @staticmethod
    def _content_key(
        symbol: str,
        timeframe: str,
        window: str,
        confidence: float,
        frame: pd.DataFrame,
        engine_version: str,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(str(symbol).strip().upper().encode("utf-8"))
        digest.update(str(timeframe).strip().encode("utf-8"))
        digest.update(str(window).strip().lower().encode("utf-8"))
        digest.update(format(float(confidence), ".8g").encode("ascii"))
        digest.update(str(engine_version).encode("utf-8"))
        from forecasting.live_decay import tier_controls
        from forecasting.model_promotion import active_promotion_receipt
        digest.update(json.dumps({"controls": tier_controls(), "promotion": active_promotion_receipt()},
                                 sort_keys=True, default=str).encode("utf-8"))
        digest.update("\x1f".join(map(str, frame.columns)).encode("utf-8"))
        digest.update("\x1f".join(map(str, frame.dtypes)).encode("utf-8"))
        hashed_values = pd.util.hash_pandas_object(frame, index=True).values
        digest.update(cast(Any, hashed_values).tobytes())
        return digest.hexdigest()

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._cache.items() if expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
            self._stats["expired"] += 1

    def execute(
        self,
        *,
        symbol: str,
        timeframe: str,
        window: str,
        confidence: float,
        history_loader: Callable[[], tuple[str, pd.DataFrame]],
        forecaster: Callable[[str, pd.DataFrame], dict[str, Any]],
        engine_version: str = "default",
    ) -> ForecastExecutionOutcome:
        with trace_forecast_operation("execute", symbol, timeframe=timeframe, window=window, confidence=confidence):
            trace_id = uuid.uuid4().hex[:16]
            started = time.perf_counter()
            history_started = time.perf_counter()
            normalized, frame = history_loader()
            history_ms = (time.perf_counter() - history_started) * 1000.0
            key = self._content_key(normalized, timeframe, window, confidence, frame, engine_version)

            owner = False
            cached_result: dict[str, Any] | None = None
            with self._lock:
                now = time.monotonic()
                self._prune_locked(now)
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    self._stats["hits"] += 1
                    cached_result = copy.deepcopy(cached[1])
                    flight = None
                    cache_status = "hit"
                else:
                    flight = self._flights.get(key)
                    if flight is None:
                        flight = Future()
                        self._flights[key] = flight
                        self._stats["misses"] += 1
                        owner = True
                        cache_status = "miss"
                    else:
                        self._stats["coalesced"] += 1
                        cache_status = "coalesced"

            forecast_ms = 0.0
            if cached_result is not None:
                result = cached_result
            elif not owner:
                assert flight is not None
                wait_started = time.perf_counter()
                result = copy.deepcopy(flight.result())
                forecast_ms = (time.perf_counter() - wait_started) * 1000.0
            else:
                assert flight is not None
                forecast_started = time.perf_counter()
                try:
                    produced = forecaster(normalized, frame)
                    if not isinstance(produced, dict):
                        raise TypeError("Forecast engine returned an invalid result type.")
                    result = copy.deepcopy(produced)
                    forecast_ms = (time.perf_counter() - forecast_started) * 1000.0
                    with self._lock:
                        self._cache[key] = (time.monotonic() + self.ttl_seconds, copy.deepcopy(result))
                        self._cache.move_to_end(key)
                        self._stats["writes"] += 1
                        while len(self._cache) > self.max_entries:
                            self._cache.popitem(last=False)
                            self._stats["evictions"] += 1
                    flight.set_result(copy.deepcopy(result))
                except BaseException as exc:
                    with self._lock:
                        self._stats["failures"] += 1
                    flight.set_exception(exc)
                    # The owner raises this exception directly; retrieving it avoids
                    # an unobserved-Future warning when there were no waiters.
                    try:
                        flight.exception()
                    except BaseException:
                        pass
                    raise
                finally:
                    with self._lock:
                        if self._flights.get(key) is flight:
                            self._flights.pop(key, None)

            total_ms = (time.perf_counter() - started) * 1000.0
            trace = {
                "trace_id": trace_id,
                "cache": cache_status,
                "timings_ms": {
                    "history": round(history_ms, 3),
                    "forecast_or_wait": round(forecast_ms, 3),
                    "total": round(total_ms, 3),
                },
            }
            return ForecastExecutionOutcome(normalized, frame, result, trace)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            self._prune_locked(time.monotonic())
            return {
                "ttl_seconds": self.ttl_seconds,
                "max_entries": self.max_entries,
                "entries": len(self._cache),
                "in_flight": len(self._flights),
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "coalesced": self._stats["coalesced"],
                "writes": self._stats["writes"],
                "failures": self._stats["failures"],
                "evictions": self._stats["evictions"],
                "expired": self._stats["expired"],
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._stats.clear()


class ForecastJobError(RuntimeError):
    code = "forecast_job_error"


class ForecastJobQuotaExceeded(ForecastJobError):
    code = "forecast_job_quota_exceeded"


class ForecastJobQueueFull(ForecastJobError):
    code = "forecast_job_queue_full"


class ForecastJobNotFound(ForecastJobError):
    code = "forecast_job_not_found"


class ForecastJobManager:
    """In-process bounded job manager using Windows-compatible worker threads."""

    FINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        max_queue: int | None = None,
        per_user_quota: int | None = None,
        max_retained: int | None = None,
        retention_seconds: int | None = None,
        runtime_limit_seconds: int | None = None,
    ) -> None:
        self.max_workers = max(1, int(max_workers) if max_workers is not None else _positive_int("STOCKPILOT_FORECAST_JOB_WORKERS", 2))
        self.max_queue = max(0, int(max_queue) if max_queue is not None else _positive_int("STOCKPILOT_FORECAST_JOB_QUEUE", 16, 0))
        self.per_user_quota = max(1, int(per_user_quota) if per_user_quota is not None else _positive_int("STOCKPILOT_FORECAST_JOB_USER_QUOTA", 3))
        self.max_retained = max(1, int(max_retained) if max_retained is not None else _positive_int("STOCKPILOT_FORECAST_JOB_RETAINED", 200))
        self.retention_seconds = max(1, int(retention_seconds) if retention_seconds is not None else _positive_int("STOCKPILOT_FORECAST_JOB_RETENTION_SECONDS", 3600))
        self.runtime_limit_seconds = max(1, int(runtime_limit_seconds) if runtime_limit_seconds is not None else _positive_int("STOCKPILOT_FORECAST_JOB_RUNTIME_SECONDS", 300))
        self._lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._duplicates: dict[tuple[int, str], str] = {}
        self._stats: Counter[str] = Counter()

    def start(self) -> None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="stockpilot-forecast",
                )

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
            for job in self._jobs.values():
                if job["status"] != "queued":
                    continue
                job["cancel_requested"] = True
                future = job.get("future")
                if future is None or future.cancel():
                    self._finish_locked(job, "cancelled")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def request_key(request: dict[str, Any]) -> str:
        canonical = "|".join(
            [
                str(request.get("symbol", "")).strip().upper(),
                str(request.get("timeframe", "")).strip(),
                str(request.get("training_window", "")).strip().lower(),
                format(float(request.get("confidence", 0.8)), ".8g"),
            ]
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.retention_seconds
        removable = [
            job_id
            for job_id, job in self._jobs.items()
            if job["status"] in self.FINAL_STATUSES and float(job.get("finished_epoch") or 0) <= cutoff
        ]
        for job_id in removable:
            self._remove_locked(job_id)
        while len(self._jobs) >= self.max_retained:
            final_id = next((job_id for job_id, job in self._jobs.items() if job["status"] in self.FINAL_STATUSES), None)
            if final_id is None:
                break
            self._remove_locked(final_id)

    def _remove_locked(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            duplicate_key = (job["owner_id"], job["request_key"])
            if self._duplicates.get(duplicate_key) == job_id:
                self._duplicates.pop(duplicate_key, None)

    def submit(
        self,
        *,
        owner_id: int,
        request: dict[str, Any],
        runner: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
        error_handler: Callable[[BaseException], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        owner_id = int(owner_id)
        key = self.request_key(request)
        with self._lock:
            self._prune_locked()
            duplicate_id = self._duplicates.get((owner_id, key))
            duplicate = self._jobs.get(duplicate_id) if duplicate_id else None
            if duplicate is not None and duplicate["status"] not in self.FINAL_STATUSES:
                self._stats["coalesced"] += 1
                return self._public_locked(duplicate), True

            outstanding = sum(
                1 for job in self._jobs.values()
                if job["owner_id"] == owner_id and job["status"] not in self.FINAL_STATUSES
            )
            if outstanding >= self.per_user_quota:
                self._stats["quota_rejections"] += 1
                raise ForecastJobQuotaExceeded("Per-user forecast job quota reached.")
            global_outstanding = sum(1 for job in self._jobs.values() if job["status"] not in self.FINAL_STATUSES)
            if global_outstanding >= self.max_workers + self.max_queue:
                self._stats["queue_rejections"] += 1
                raise ForecastJobQueueFull("Forecast job queue is full.")
            if len(self._jobs) >= self.max_retained:
                self._stats["queue_rejections"] += 1
                raise ForecastJobQueueFull("Forecast job retention capacity is full.")

            self.start()
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = {
                "job_id": job_id,
                "owner_id": owner_id,
                "request_key": key,
                "request": copy.deepcopy(request),
                "status": "queued",
                "created_at": now,
                "created_epoch": time.time(),
                "started_at": None,
                "started_monotonic": None,
                "finished_at": None,
                "finished_epoch": None,
                "result": None,
                "trace": None,
                "error": None,
                "cancel_requested": False,
                "future": None,
            }
            self._jobs[job_id] = job
            self._duplicates[(owner_id, key)] = job_id
            self._stats["submitted"] += 1
            executor = self._executor
            assert executor is not None
            future = executor.submit(self._run, job_id, runner, error_handler)
            job["future"] = future
            return self._public_locked(job), False

    def _run(
        self,
        job_id: str,
        runner: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
        error_handler: Callable[[BaseException], dict[str, Any]],
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job["cancel_requested"]:
                self._finish_locked(job, "cancelled")
                return
            job["status"] = "running"
            job["started_at"] = _utc_now()
            job["started_monotonic"] = time.monotonic()
            request = copy.deepcopy(job["request"])
        try:
            result, trace = runner(request)
        except BaseException as exc:
            try:
                safe_error = error_handler(exc)
            except BaseException:
                safe_error = {
                    "code": "forecast_unavailable",
                    "message": "The research range could not be produced right now.",
                    "retryable": True,
                }
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["error"] = copy.deepcopy(safe_error)
                    self._finish_locked(job, "cancelled" if job["cancel_requested"] else "failed")
                    self._stats["failed"] += 1
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                if job["status"] in self.FINAL_STATUSES:
                    return
                if job["cancel_requested"]:
                    self._finish_locked(job, "cancelled")
                else:
                    job["result"] = copy.deepcopy(result)
                    job["trace"] = copy.deepcopy(trace)
                    self._finish_locked(job, "succeeded")
                    self._stats["succeeded"] += 1

    def _finish_locked(self, job: dict[str, Any], status: str) -> None:
        job["status"] = status
        job["finished_at"] = _utc_now()
        job["finished_epoch"] = time.time()
        duplicate_key = (job["owner_id"], job["request_key"])
        if self._duplicates.get(duplicate_key) == job["job_id"]:
            self._duplicates.pop(duplicate_key, None)

    def _public_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        started = job.get("started_monotonic")
        if started is None:
            elapsed = 0.0
        elif job["status"] in self.FINAL_STATUSES and job.get("finished_epoch") and job.get("created_epoch"):
            started_epoch = datetime.fromisoformat(job["started_at"]).timestamp()
            elapsed = max(0.0, float(job["finished_epoch"]) - started_epoch)
        else:
            elapsed = max(0.0, time.monotonic() - float(started))
        if job["status"] in {"running", "cancellation_requested"} and elapsed > self.runtime_limit_seconds:
            job["cancel_requested"] = True
            job["error"] = {
                "code": "forecast_timed_out",
                "message": "The forecast exceeded its bounded server runtime.",
                "retryable": True,
            }
            self._finish_locked(job, "failed")
            self._stats["failed"] += 1
            self._stats["timed_out"] += 1
        payload = {
            "job_id": job["job_id"],
            "status": job["status"],
            "request": copy.deepcopy(job["request"]),
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "runtime": {
                "elapsed_ms": round(elapsed * 1000.0, 3),
                "limit_seconds": self.runtime_limit_seconds,
                "over_limit": elapsed > self.runtime_limit_seconds,
            },
        }
        if job.get("trace") is not None:
            payload["execution"] = copy.deepcopy(job["trace"])
        if job.get("result") is not None:
            payload["result"] = copy.deepcopy(job["result"])
        if job.get("error") is not None:
            payload["error"] = copy.deepcopy(job["error"])
        return payload

    def get(self, job_id: str, *, owner_id: int) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(str(job_id))
            if job is None or job["owner_id"] != int(owner_id):
                raise ForecastJobNotFound("Forecast job was not found.")
            return self._public_locked(job)

    def cancel_or_delete(self, job_id: str, *, owner_id: int) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None or job["owner_id"] != int(owner_id):
                raise ForecastJobNotFound("Forecast job was not found.")
            if job["status"] in self.FINAL_STATUSES:
                payload = self._public_locked(job)
                self._remove_locked(job["job_id"])
                return {"job_id": job["job_id"], "status": "deleted", "previous_status": payload["status"]}
            job["cancel_requested"] = True
            future: Future[Any] | None = job.get("future")
            if job["status"] == "queued" and future is not None and future.cancel():
                self._finish_locked(job, "cancelled")
            elif job["status"] == "running":
                job["status"] = "cancellation_requested"
            self._stats["cancellations"] += 1
            return self._public_locked(job)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            statuses = Counter(job["status"] for job in self._jobs.values())
            return {
                "max_workers": self.max_workers,
                "max_queue": self.max_queue,
                "per_user_quota": self.per_user_quota,
                "max_retained": self.max_retained,
                "retained": len(self._jobs),
                "statuses": dict(sorted(statuses.items())),
                "submitted": self._stats["submitted"],
                "coalesced": self._stats["coalesced"],
                "succeeded": self._stats["succeeded"],
                "failed": self._stats["failed"],
                "quota_rejections": self._stats["quota_rejections"],
                "queue_rejections": self._stats["queue_rejections"],
                "cancellations": self._stats["cancellations"],
                "timed_out": self._stats["timed_out"],
                "runtime_limit_seconds": self.runtime_limit_seconds,
            }

    def clear(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                future = job.get("future")
                if future is not None:
                    future.cancel()
            self._jobs.clear()
            self._duplicates.clear()
            self._stats.clear()
