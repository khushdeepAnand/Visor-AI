"""OpenTelemetry instrumentation helpers for StockPilot AI v15.

This module provides easy-to-use context managers and decorators to add
automatic tracing to provider calls, forecast jobs, and other critical paths.
"""
from __future__ import annotations

import contextlib
import functools
import time
from typing import Any, Callable, Optional, Iterator

from services.telemetry import get_tracer, enabled

# Global tracer instances for key subsystems
_forecast_tracer = get_tracer("stockpilot.forecast")
_market_data_tracer = get_tracer("stockpilot.market_data")
_provider_tracer = get_tracer("stockpilot.provider")
_reconciliation_tracer = get_tracer("stockpilot.reconciliation")
_auth_tracer = get_tracer("stockpilot.auth")


@contextlib.contextmanager
def trace_forecast_operation(operation: str, symbol: str, **attributes: Any) -> Iterator[Any]:
    """Trace a forecast operation with standard attributes."""
    if not enabled():
        yield
        return
    
    tracer = _forecast_tracer
    with tracer.start_as_current_span(f"forecast.{operation}") as span:
        span.set_attribute("stockpilot.symbol", symbol)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        start_time = time.monotonic()
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 2))


@contextlib.contextmanager
def trace_provider_call(provider: str, operation: str, symbol: str = "", **attributes: Any) -> Iterator[Any]:
    """Trace a market data provider call."""
    if not enabled():
        yield
        return
    
    tracer = _provider_tracer
    with tracer.start_as_current_span(f"provider.{provider}.{operation}") as span:
        span.set_attribute("provider", provider)
        span.set_attribute("operation", operation)
        if symbol:
            span.set_attribute("symbol", symbol)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        start_time = time.monotonic()
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 2))


@contextlib.contextmanager
def trace_market_data_operation(operation: str, symbol: str = "", **attributes: Any) -> Iterator[Any]:
    """Trace a market data manager operation."""
    if not enabled():
        yield
        return
    
    tracer = _market_data_tracer
    with tracer.start_as_current_span(f"market_data.{operation}") as span:
        if symbol:
            span.set_attribute("symbol", symbol)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        start_time = time.monotonic()
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 2))


@contextlib.contextmanager
def trace_reconciliation_operation(operation: str, **attributes: Any) -> Iterator[Any]:
    """Trace a challenger reconciliation operation."""
    if not enabled():
        yield
        return
    
    tracer = _reconciliation_tracer
    with tracer.start_as_current_span(f"reconciliation.{operation}") as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        start_time = time.monotonic()
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 2))


def traced_provider_call(provider: str, operation: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to trace a provider call function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_provider_call(provider, operation):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def traced_forecast_operation(operation: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to trace a forecast operation function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(symbol: str, *args: Any, **kwargs: Any) -> Any:
            with trace_forecast_operation(operation, symbol):
                return func(symbol, *args, **kwargs)
        return wrapper
    return decorator


def traced_market_data_operation(operation: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to trace a market data operation function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            symbol = kwargs.get("symbol") or (args[0] if args else "")
            with trace_market_data_operation(operation, str(symbol)):
                return func(*args, **kwargs)
        return wrapper
    return decorator
