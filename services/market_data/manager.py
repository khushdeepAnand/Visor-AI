"""Provider orchestration, fallback, backoff and cache."""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from threading import RLock, Thread
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from .base import Instrument, MarketDataProvider, MarketDataError, ProviderUnavailableError, Quote, UnsupportedHistoryRangeError
from .demo import DemoIndianProvider
from .nse_unofficial import NSEUnofficialProvider
from .upstox import UpstoxProvider
from .yfinance_fallback import YFinanceFallbackProvider
from .instruments import CATALOGUE
from .licensed_vendor import GlobalDatafeedsProvider, TrueDataProvider
from .redis_cache import REDIS_HOT_CACHE, history_from_json, history_to_json
from .context import ProviderMode, build_market_context, renew_market_context

# OpenTelemetry instrumentation
from services.otel_instrumentation import trace_provider_call, trace_market_data_operation

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
CACHE_DIR = ROOT / "cache" / "market_v6"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ProviderManager:
    def __init__(self) -> None:
        self.providers: dict[str, MarketDataProvider] = {
            "upstox": UpstoxProvider(),
            "nse": NSEUnofficialProvider(),
            "demo": DemoIndianProvider(),
            "yfinance": YFinanceFallbackProvider(),
            "truedata": TrueDataProvider(),
            "globaldatafeeds": GlobalDatafeedsProvider(),
        }
        self.provider_mode = ProviderMode.configured()
        requested = [p.strip().lower() for p in os.getenv("STOCKPILOT_PROVIDER_ORDER", "upstox,yfinance,nse,demo").split(",") if p.strip()]
        self.order = [p for p in requested if p in self.providers]
        if not self.order:
            self.order = ["upstox", "yfinance", "nse", "demo"]
        self.memory: dict[str, tuple[float, Any]] = {}
        self._memory_kinds: dict[str, str] = {}
        self._history_flights: dict[str, Future[pd.DataFrame]] = {}
        self._cache_stats: Counter[str] = Counter()
        self.lock = RLock()
        self.ttl_quote = int(os.getenv("STOCKPILOT_QUOTE_CACHE_SECONDS", "3"))
        self.ttl_history = int(os.getenv("STOCKPILOT_HISTORY_CACHE_SECONDS", "60"))
        self.memory_max_entries = max(16, int(os.getenv("STOCKPILOT_MARKET_MEMORY_CACHE_MAX_ENTRIES", "512")))
        self.failures: dict[str, dict[str, Any]] = {}
        self._failure_streaks: Counter[str] = Counter()
        self._last_success: dict[str, float] = {}
        self._provider_requests: Counter[str] = Counter()
        self._provider_successes: Counter[str] = Counter()
        self._provider_failures: Counter[str] = Counter()
        self._provider_fallbacks: Counter[str] = Counter()
        self._provider_latency_ms: dict[str, float] = {}
        self._provider_data_as_of: dict[str, float] = {}
        self._readiness: dict[str, Any] = {"status": "not_started", "checked_at": None, "providers": []}
        self._readiness_thread: Thread | None = None
        self._provider_next_allowed: dict[str, float] = {}
        self._provider_intervals = {
            "upstox": float(os.getenv("STOCKPILOT_UPSTOX_MIN_INTERVAL_SECONDS", "0.08")),
            "nse": float(os.getenv("STOCKPILOT_NSE_MIN_INTERVAL_SECONDS", "0.35")),
            "demo": 0.0,
            "yfinance": float(os.getenv("STOCKPILOT_YFINANCE_MIN_INTERVAL_SECONDS", "0.35")),
            "truedata": float(os.getenv("STOCKPILOT_TRUEDATA_MIN_INTERVAL_SECONDS", "0.05")),
            "globaldatafeeds": float(os.getenv("STOCKPILOT_GLOBALDATAFEEDS_MIN_INTERVAL_SECONDS", "0.05")),
        }

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = str(symbol or "").strip().upper()
        if not value:
            raise ValueError("Symbol is required.")
        # India-only suffixes are accepted as input but stripped internally.
        if value.endswith(".NS") or value.endswith(".BO"):
            value = value[:-3]
        forbidden = ("-USD", "=F", "^DJ", "^IX", "^GSPC")
        if any(token in value for token in forbidden):
            raise ValueError("StockPilot v6 is India-only; crypto/commodity/global tickers are not supported.")
        # The demo provider must never turn an arbitrary foreign ticker into a
        # synthetic 'Indian' instrument.  Require an exact symbol from the
        # NSE/BSE catalogue (bundled master or refreshed broker BOD master).
        if CATALOGUE.resolve(value) is None:
            raise ValueError(f"{value} is not present in the configured NSE/BSE instrument universe.")
        return value

    def _cache_key(self, kind: str, *parts: str) -> str:
        raw = "|".join([self.provider_mode.value, ",".join(self.order), kind, *parts])
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_mem(self, key: str, ttl: int):
        with self.lock:
            item = self.memory.get(key)
        if item and time.time() - item[0] <= ttl:
            return item[1]
        return None

    def _set_mem(self, key: str, value: Any, *, kind: str = "other") -> None:
        with self.lock:
            self.memory[key] = (time.time(), value)
            self._memory_kinds[key] = kind
            while len(self.memory) > self.memory_max_entries:
                oldest = min(self.memory, key=lambda item: self.memory[item][0])
                self.memory.pop(oldest, None)
                self._memory_kinds.pop(oldest, None)
                self._cache_stats["evictions"] += 1

    def _history_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.json"

    def _record_failure(self, provider: str, error: Exception, *, latency_ms: float | None = None) -> None:
        diagnostic = getattr(error, "diagnostic", None)
        classification = getattr(diagnostic, "classification", None) or type(error).__name__
        with self.lock:
            self.failures[provider] = {"at": time.time(), "classification": str(classification)}
            self._failure_streaks[provider] += 1
            self._provider_requests[provider] += 1
            self._provider_failures[provider] += 1
            if latency_ms is not None:
                self._provider_latency_ms[provider] = round(latency_ms, 2)

    def _record_success(
        self,
        provider: str,
        *,
        latency_ms: float | None = None,
        fallback_used: bool = False,
        data_as_of: str | None = None,
    ) -> None:
        with self.lock:
            self._failure_streaks[provider] = 0
            self._last_success[provider] = time.time()
            self._provider_requests[provider] += 1
            self._provider_successes[provider] += 1
            if fallback_used:
                self._provider_fallbacks[provider] += 1
            if latency_ms is not None:
                self._provider_latency_ms[provider] = round(latency_ms, 2)
            if data_as_of:
                timestamp = pd.to_datetime(data_as_of, utc=True, errors="coerce")
                if not pd.isna(timestamp):
                    self._provider_data_as_of[provider] = timestamp.timestamp()
            self.failures.pop(provider, None)

    def effective_order(self) -> list[str]:
        """Demote repeatedly failing providers in-memory while preserving configured priority."""
        with self.lock:
            indexed = list(enumerate(self.order))
            return [name for _, name in sorted(indexed, key=lambda item: (self._failure_streaks[item[1]] >= 3, item[0]))]

    def set_order(self, order: list[str]) -> list[str]:
        normalized = [str(name).strip().lower() for name in order]
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("Provider order must contain unique provider names.")
        unknown = [name for name in normalized if name not in self.providers]
        if unknown:
            raise ValueError(f"Unknown provider(s): {', '.join(unknown)}")
        with self.lock:
            self.order = normalized
            self.memory.clear()
            self._memory_kinds.clear()
        return list(self.order)

    def _pace_provider(self, provider: str) -> None:
        """Apply a small per-provider spacing guard before an outbound call.

        This is intentionally conservative and configurable. Broker rate limits
        differ by endpoint/account, so provider responses still remain the source
        of truth and failures are handled by backoff/fallback below.
        """
        interval = max(0.0, float(self._provider_intervals.get(provider, 0.1)))
        if interval <= 0:
            return
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self._provider_next_allowed.get(provider, 0.0) - now)
            self._provider_next_allowed[provider] = max(now, self._provider_next_allowed.get(provider, 0.0)) + interval
        if wait > 0:
            time.sleep(wait)

    def _usable_providers(self, *, history: bool = False):
        if self.provider_mode is ProviderMode.OFFLINE_DEMO:
            selected = ["demo"]
        elif self.provider_mode is ProviderMode.LIVE_ONLY:
            selected = [name for name in self.effective_order() if name not in {"demo", "yfinance"}]
        else:
            selected = [name for name in self.effective_order() if name != "demo"]
        for name in selected:
            provider = self.providers[name]
            if not provider.is_configured():
                continue
            if history and name == "nse":
                continue
            yield name, provider
            if self.provider_mode is ProviderMode.LIVE_ONLY:
                return

    @staticmethod
    def _credential_mode(name: str, provider: MarketDataProvider) -> str:
        configured = getattr(provider, "credential_mode", None)
        if configured:
            return str(configured)
        if name == "demo":
            return "offline_demo"
        if name in {"nse", "yfinance"}:
            return "public"
        return "configured"

    @staticmethod
    def _is_live_provider(name: str) -> bool:
        return name not in {"demo", "yfinance"}

    def _context(
        self,
        *,
        requested_symbol: str,
        symbol: str,
        provider_name: str,
        provider: MarketDataProvider,
        timeframe: str,
        as_of: str | None,
        errors: list[str],
    ) -> dict[str, Any]:
        return build_market_context(
            requested_symbol=requested_symbol,
            instrument=CATALOGUE.resolve(symbol),
            provider=provider_name,
            credential_mode=self._credential_mode(provider_name, provider),
            timeframe=timeframe,
            as_of=as_of,
            is_live=self._is_live_provider(provider_name),
            is_stale=False,
            fallback_used=bool(errors),
            fallback_reason=" | ".join(errors) if errors else None,
            provider_mode=self.provider_mode,
        )

    @staticmethod
    def _normalize_timeframe(timeframe: str) -> str:
        raw = str(timeframe or "").strip()
        return {"1d": "1D", "1w": "1W"}.get(raw.lower(), raw.lower())

    @staticmethod
    def _normalize_window(window: str) -> str:
        return str(window or "").strip().lower()

    @staticmethod
    def _copy_history(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy(deep=True)
        result.attrs.update(frame.attrs)
        if isinstance(result.attrs.get("context"), dict):
            result.attrs["context"] = renew_market_context(result.attrs["context"])
        return result

    def get_history(self, symbol: str, timeframe: str = "1D", window: str = "1y") -> pd.DataFrame:
            with trace_market_data_operation("get_history", symbol):
                requested_symbol = str(symbol)
                symbol = self.normalize_symbol(symbol)
                timeframe = self._normalize_timeframe(timeframe)
                window = self._normalize_window(window)
                key = self._cache_key("history", symbol, timeframe, window)
                with self.lock:
                    item = self.memory.get(key)
                    cached = item[1] if item and time.time() - item[0] <= self.ttl_history else None
                    if isinstance(cached, pd.DataFrame):
                        self._cache_stats["history_memory_hits"] += 1
                        return self._copy_history(cached)
                    if item is not None:
                        self.memory.pop(key, None)
                        self._memory_kinds.pop(key, None)
                        self._cache_stats["history_expired"] += 1
                    flight = self._history_flights.get(key)
                    if flight is None:
                        flight = Future()
                        self._history_flights[key] = flight
                        self._cache_stats["history_misses"] += 1
                        owner = True
                    else:
                        self._cache_stats["history_coalesced"] += 1
                        owner = False
                if not owner:
                    return self._copy_history(flight.result())
                try:
                    result = self._load_history(requested_symbol, symbol, timeframe, window, key)
                    flight.set_result(result.copy(deep=True))
                    return result
                except BaseException as exc:
                    flight.set_exception(exc)
                    try:
                        flight.exception()
                    except BaseException:
                        pass
                    raise
                finally:
                    with self.lock:
                        if self._history_flights.get(key) is flight:
                            self._history_flights.pop(key, None)

    def _load_history(self, requested_symbol: str, symbol: str, timeframe: str, window: str, key: str) -> pd.DataFrame:
            with trace_market_data_operation("_load_history", symbol, timeframe=timeframe, window=window):
                cached = None
                if not isinstance(cached, pd.DataFrame):
                    cached = REDIS_HOT_CACHE.get_history(key)
                if isinstance(cached, pd.DataFrame):
                    with self.lock:
                        self._cache_stats["history_redis_hits"] += 1
                    result = self._copy_history(cached)
                    self._set_mem(key, result.copy(), kind="history")
                    return result
                errors: list[str] = []
                for name, provider in self._usable_providers(history=True):
                    for attempt in range(2):
                        try:
                            started = time.perf_counter()
                            self._pace_provider(name)
                            with trace_provider_call(name, "get_history", symbol, timeframe=timeframe, window=window):
                                data = provider.get_history(symbol, timeframe, window)
                            as_of = pd.Timestamp(data.index[-1]).isoformat() if len(data.index) else None
                            self._record_success(
                                name,
                                latency_ms=(time.perf_counter() - started) * 1000.0,
                                fallback_used=bool(errors),
                                data_as_of=as_of,
                            )
                            data.attrs["provider"] = name
                            data.attrs["context"] = self._context(
                                requested_symbol=requested_symbol, symbol=symbol, provider_name=name,
                                provider=provider, timeframe=timeframe, as_of=as_of, errors=errors,
                            )
                            self._set_mem(key, data.copy(), kind="history")
                            REDIS_HOT_CACHE.set_history(key, data, self.ttl_history)
                            try:
                                self._history_path(key).write_text(history_to_json(data), encoding="utf-8")
                            except OSError:
                                pass
                            with self.lock:
                                self._cache_stats["history_provider_loads"] += 1
                            return data
                        except UnsupportedHistoryRangeError:
                            # A permanent limit of the timeframe/window pair, not an
                            # outage. Retrying or failing over cannot help, and a stale
                            # cache must not stand in for a range the caller cannot ask
                            # for, so it propagates unchanged to the API layer.
                            raise
                        except Exception as exc:
                            latency = (time.perf_counter() - started) * 1000.0
                            self._record_failure(name, exc, latency_ms=latency)
                            errors.append(f"{name}: {type(exc).__name__}")
                            if attempt == 0:
                                time.sleep(0.25)
                path = self._history_path(key)
                if path.exists():
                    try:
                        data = history_from_json(path.read_text(encoding="utf-8"))
                        data.attrs.update({"is_stale": True, "source": f"{data.attrs.get('source', 'cached')} (stale cache)"})
                        if isinstance(data.attrs.get("context"), dict):
                            data.attrs["context"] = renew_market_context(
                                data.attrs["context"], is_stale=True, fallback_used=True,
                                fallback_reason=" | ".join(errors) or "Live providers unavailable; serving stale cache.",
                            )
                        with self.lock:
                            self._cache_stats["history_stale_disk_hits"] += 1
                        self._set_mem(key, data.copy(), kind="history")
                        return data
                    except Exception:
                        pass
            raise MarketDataError("All market-data providers failed: " + " | ".join(errors))
    
    def get_quote(self, symbol: str, timeframe: str = "quote") -> Quote:
            with trace_market_data_operation("get_quote", symbol):
                requested_symbol = str(symbol)
                symbol = self.normalize_symbol(symbol)
                key = self._cache_key("quote", symbol, timeframe)
                cached = self._get_mem(key, self.ttl_quote)
                if not isinstance(cached, Quote):
                    cached = REDIS_HOT_CACHE.get_quote(key)
                if isinstance(cached, Quote):
                    result = replace(cached)
                    if isinstance(cached.context, dict):
                        result.context = renew_market_context(cached.context)
                    self._set_mem(key, result, kind="quote")
                    return result
                errors: list[str] = []
                for name, provider in self._usable_providers(history=False):
                    try:
                        started = time.perf_counter()
                        self._pace_provider(name)
                        with trace_provider_call(name, "get_quote", symbol):
                            quote = provider.get_quote(symbol)
                        if float(quote.price) <= 0 or not str(quote.timestamp or "").strip():
                            raise ProviderUnavailableError(f"{name} returned an invalid quote contract.")
                        self._record_success(
                            name,
                            latency_ms=(time.perf_counter() - started) * 1000.0,
                            fallback_used=bool(errors),
                            data_as_of=quote.timestamp,
                        )
                        quote.context = self._context(
                            requested_symbol=requested_symbol, symbol=symbol, provider_name=name,
                            provider=provider, timeframe=timeframe, as_of=quote.timestamp, errors=errors,
                        )
                        quote.context["is_stale"] = bool(quote.is_stale)
                        self._set_mem(key, quote, kind="quote")
                        REDIS_HOT_CACHE.set_quote(key, quote, self.ttl_quote)
                        return quote
                    except Exception as exc:
                        latency = (time.perf_counter() - started) * 1000.0
                        self._record_failure(name, exc, latency_ms=latency)
                        errors.append(f"{name}: {type(exc).__name__}")
                raise MarketDataError("All quote providers failed: " + " | ".join(errors))
    
    def search(self, query: str, limit: int = 20) -> list[Instrument]:
            return CATALOGUE.search(query, limit)

    def health(self) -> dict[str, Any]:
            effective = self.effective_order()
            now = time.time()
            with self.lock:
                successes = sum(self._provider_successes.values())
                fallback_loads = sum(self._provider_fallbacks.values())
                metrics = {
                    "requests": sum(self._provider_requests.values()),
                    "successes": successes,
                    "failures": sum(self._provider_failures.values()),
                    "fallback_loads": fallback_loads,
                    "fallback_rate": round(fallback_loads / successes, 4) if successes else 0.0,
                    "stale_cache_hits": int(self._cache_stats["history_stale_disk_hits"]),
                    "providers": {
                        name: {
                            "requests": int(self._provider_requests[name]),
                            "successes": int(self._provider_successes[name]),
                            "failures": int(self._provider_failures[name]),
                            "fallbacks": int(self._provider_fallbacks[name]),
                            "last_latency_ms": self._provider_latency_ms.get(name),
                            "seconds_since_success": round(now - self._last_success[name], 1) if name in self._last_success else None,
                            "data_age_seconds": round(max(0.0, now - self._provider_data_as_of[name]), 1) if name in self._provider_data_as_of else None,
                        }
                        for name in self.order
                    },
                }
            return {
                "provider_mode": self.provider_mode.value,
                "order": self.order,
                "effective_order": effective,
                "active_provider": next((name for name, _ in self._usable_providers()), None),
                "providers": [
                    {
                        **self.providers[name].health(),
                        "provider": name,
                        "failure_streak": int(self._failure_streaks[name]),
                        "last_success_at": self._last_success.get(name),
                        "demoted": self._failure_streaks[name] >= 3,
                    }
                    for name in effective
                ],
                "recent_failures": self.failures,
                "metrics": metrics,
                "cache_entries": len(self.memory),
                "redis": REDIS_HOT_CACHE.health(),
                "readiness_probe": self.provider_readiness(),
            }

    def cache_diagnostics(self) -> dict[str, Any]:
            with self.lock:
                live_keys = set(self.memory)
                kinds = Counter(self._memory_kinds.get(key, "other") for key in live_keys)
                counters = {
                    key: self._cache_stats[key]
                    for key in (
                        "history_memory_hits", "history_redis_hits", "history_stale_disk_hits",
                        "history_misses", "history_coalesced", "history_provider_loads",
                        "history_expired", "evictions",
                    )
                }
                return {
                    "max_entries": self.memory_max_entries,
                    "entries": len(live_keys),
                    "entries_by_kind": dict(sorted(kinds.items())),
                    "history_in_flight": len(self._history_flights),
                    **counters,
                }

    def start_readiness_probe(self) -> None:
            """Probe local provider configuration in a daemon thread; never delay startup."""
            with self.lock:
                if self._readiness_thread is not None and self._readiness_thread.is_alive():
                    return
                self._readiness = {"status": "checking", "checked_at": None, "providers": []}
                self._readiness_thread = Thread(
                    target=self._probe_readiness,
                    name="stockpilot-provider-readiness",
                    daemon=True,
                )
                self._readiness_thread.start()

    def _probe_readiness(self) -> None:
            providers: list[dict[str, Any]] = []
            symbol = os.getenv("STOCKPILOT_READINESS_SYMBOL", "RELIANCE").strip().upper() or "RELIANCE"
            probe_names = [name for name, _provider in self._usable_providers()]
            for name in probe_names:
                provider = self.providers[name]
                try:
                    health = provider.health()
                    configured = bool(health.get("configured", provider.is_configured()))
                    result: dict[str, Any] = {
                        "provider": name,
                        "configured": configured,
                        "status": "not_configured" if not configured else "checking",
                        "quote": "not_run",
                        "history": "not_run",
                    }
                    if configured:
                        started = time.perf_counter()
                        try:
                            self._pace_provider(name)
                            quote = provider.get_quote(symbol)
                            result["quote"] = "passed" if float(quote.price) > 0 else "failed"
                        except Exception as exc:
                            self._record_failure(name, exc)
                            result["quote"] = "failed"
                            result["classification"] = self.failures[name]["classification"]
                        result["quote_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
                        if name != "nse":
                            started = time.perf_counter()
                            try:
                                self._pace_provider(name)
                                frame = provider.get_history(symbol, "1D", "1mo")
                                result["history"] = "passed" if len(frame) > 0 else "failed"
                                result["history_rows"] = len(frame)
                            except Exception as exc:
                                self._record_failure(name, exc)
                                result["history"] = "failed"
                                result["classification"] = self.failures[name]["classification"]
                            result["history_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
                        result["status"] = "ready" if result["quote"] == "passed" and result["history"] in {"passed", "not_run"} else "unavailable"
                    providers.append(result)
                except Exception as exc:
                    providers.append({"provider": name, "configured": False, "status": "error", "classification": type(exc).__name__})
            with self.lock:
                self._readiness = {"status": "complete", "checked_at": time.time(), "providers": providers}

    def provider_readiness(self) -> dict[str, Any]:
            with self.lock:
                return {
                    "status": self._readiness["status"],
                    "checked_at": self._readiness["checked_at"],
                    "providers": [dict(item) for item in self._readiness["providers"]],
                }


MANAGER = ProviderManager()
