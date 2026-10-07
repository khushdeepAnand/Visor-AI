"""Request IDs, rate limiting and lightweight in-process API metrics."""

from __future__ import annotations

from collections import defaultdict, deque
import os
import ipaddress
import threading
import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.market_data.context import reset_request_id, set_request_id


class ApiMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._request_count = 0
        self._error_count = 0
        self._latency_total_ms = 0.0
        self._by_path: dict[str, int] = defaultdict(int)

    def observe(self, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._request_count += 1
            if int(status_code) >= 400:
                self._error_count += 1
            self._latency_total_ms += float(latency_ms)
            self._by_path[str(path)] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            count = self._request_count
            return {
                "request_count": count,
                "error_count": self._error_count,
                "error_rate": round(self._error_count / count, 6) if count else 0.0,
                "average_latency_ms": round(self._latency_total_ms / count, 3) if count else 0.0,
                "requests_by_path": dict(sorted(self._by_path.items())),
            }

    def prometheus(self) -> str:
        """Prometheus counters/summary using observed requests, without PII labels."""
        with self._lock:
            return (
                "# HELP stockpilot_http_requests_total Observed API requests.\n"
                "# TYPE stockpilot_http_requests_total counter\n"
                f"stockpilot_http_requests_total {self._request_count}\n"
                "# HELP stockpilot_http_errors_total Observed HTTP errors (status >= 400).\n"
                "# TYPE stockpilot_http_errors_total counter\n"
                f"stockpilot_http_errors_total {self._error_count}\n"
                "# HELP stockpilot_http_request_duration_seconds Observed request duration.\n"
                "# TYPE stockpilot_http_request_duration_seconds summary\n"
                f"stockpilot_http_request_duration_seconds_sum {self._latency_total_ms / 1000:g}\n"
                f"stockpilot_http_request_duration_seconds_count {self._request_count}\n"
            )


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else float(now)
        cutoff = now - int(window_seconds)
        with self._lock:
            bucket = self._events[str(key)]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= int(limit):
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


API_METRICS = ApiMetrics()
RATE_LIMITER = SlidingWindowRateLimiter()


def _client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    if os.getenv("STOCKPILOT_TRUST_PROXY", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return direct
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return direct


def _rate_limit_rule(request: Request) -> tuple[str, int, int]:
    path = request.url.path
    method = request.method.upper()
    if method == "POST" and path == "/api/v1/auth/register":
        return "register", max(1, int(os.getenv("STOCKPILOT_REGISTER_RATE_LIMIT", "5"))), 300
    if method == "POST" and path == "/api/v1/auth/login":
        return "login", max(1, int(os.getenv("STOCKPILOT_LOGIN_RATE_LIMIT", "10"))), 300
    if method == "POST" and path in {"/api/v1/auth/password-reset", "/api/v1/auth/reset-password"}:
        return "password-reset", max(1, int(os.getenv("STOCKPILOT_PASSWORD_RESET_RATE_LIMIT", "5"))), 900
    if method == "GET" and path.startswith("/api/v1/forecast-jobs/"):
        return "forecast-status", max(10, int(os.getenv("STOCKPILOT_FORECAST_STATUS_RATE_LIMIT", "120"))), 60
    if path.startswith("/api/v1/predict/") or path.startswith("/api/v1/predictions/") or path.startswith("/api/v1/forecast-jobs"):
        return "forecast", max(1, int(os.getenv("STOCKPILOT_FORECAST_RATE_LIMIT", "30"))), 60
    limit = max(10, int(os.getenv("STOCKPILOT_RATE_LIMIT_REQUESTS", "120")))
    window = max(1, int(os.getenv("STOCKPILOT_RATE_LIMIT_WINDOW_SECONDS", "60")))
    return "general", limit, window


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request_token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = (time.perf_counter() - started) * 1000.0
            API_METRICS.observe(request.url.path, 500, latency_ms)
            raise
        finally:
            reset_request_id(request_token)
        latency_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{latency_ms:.3f}"
        API_METRICS.observe(request.url.path, response.status_code, latency_ms)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/api/v1/health", "/api/v1/ready", "/api/v1/metrics", "/docs", "/openapi.json"}:
            return await call_next(request)
        scope, limit, window = _rate_limit_rule(request)
        client_host = _client_ip(request)
        allowed, retry_after = RATE_LIMITER.allow(
            f"{scope}:{client_host}", limit=limit, window_seconds=window
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Retry after the indicated number of seconds.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Window"] = str(window)
        return response
