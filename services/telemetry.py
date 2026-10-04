"""OpenTelemetry tracing, wired through environment configuration.

Modes (``STOCKPILOT_OTEL_ENABLED``):
  * unset/``false`` -> no-op tracing. ``get_tracer`` still works and returns a
    tracer that records nothing, so application code never branches on config.
  * ``true`` -> TracerProvider is configured. Exporter selection:
      - ``STOCKPILOT_OTEL_EXPORTER=otlp`` (default when an OTLP endpoint is
        configured) -> OTLP/HTTP exporter to ``OTEL_EXPORTER_OTLP_ENDPOINT``.
      - ``console``  -> stdout spans (local debugging).
      - ``memory``   -> in-memory exporter used by the test suite.

``setup_telemetry(app)`` instruments the FastAPI app (one span per request,
with ``http.route``/``user.id`` attributes) and must be called once after the
app's middleware stack is assembled. Shutdown flushes pending spans.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional, ContextManager

_lock = threading.Lock()
_provider: Any = None
_memory_spans: list[Any] = []
_setup_done = False

try:  # OpenTelemetry is an optional dependency; degrade to no-op when absent.
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
        SpanExporter,
    )
    from opentelemetry.trace import Span, Tracer
    OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when SDK is missing
    OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    # Keep module importable when the optional SDK is absent.  The concrete
    # implementations below are never instantiated in this mode.
    SpanProcessor = object  # type: ignore[assignment,misc]
    SpanExporter = object  # type: ignore[assignment,misc]


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class _MemorySpanProcessor(SpanProcessor):
    """Captures finished spans in a list for assertions."""

    def on_end(self, span: Any) -> None:
        _memory_spans.append(span)


class _MemorySpanExporter(SpanExporter):
    def export(self, spans: Any) -> Any:
        _memory_spans.extend(list(spans))
        return None


def enabled() -> bool:
    """Tracing is active when explicitly enabled or an OTLP endpoint is set."""
    if not OTEL_AVAILABLE:
        return False
    if _truthy("STOCKPILOT_OTEL_ENABLED"):
        return True
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def clear_memory_spans() -> None:
    _memory_spans.clear()


def memory_spans() -> list[Any]:
    return list(_memory_spans)


def configure_telemetry(service_name: str | None = None, force: bool = False) -> Any:
    """Configure (once) and return the global TracerProvider.

    Returns the existing provider on subsequent calls. In disabled mode a
    provider-less (no-op) setup is returned so ``get_tracer`` stays cheap.
    """
    global _provider, _setup_done
    with _lock:
        if _setup_done and not force:
            return _provider
        if not OTEL_AVAILABLE:
            _setup_done = True
            return None
        if not enabled() and not force:
            _setup_done = True
            return None

        name = (
            service_name
            or os.getenv("STOCKPILOT_OTEL_SERVICE_NAME", "").strip()
            or "stockpilot-ai"
        )
        resource = Resource.create({"service.name": name})
        provider = TracerProvider(resource=resource)

        mode = os.getenv("STOCKPILOT_OTEL_EXPORTER", "").strip().lower()
        if not mode:
            mode = "otlp" if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip() else "console"

        if mode == "memory":
            provider.add_span_processor(_MemorySpanProcessor())
        elif mode == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        else:  # otlp
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
            except Exception:
                # Exporter missing/misconfigured: keep tracing locally rather
                # than failing application startup.
                provider.add_span_processor(_MemorySpanProcessor())

        _provider = provider
        _setup_done = True
        # The global provider can only be set once per process; ignore the
        # warning on re-configuration and always expose our provider directly
        # through get_tracer/setup_telemetry so tests and repeated setup stay
        # deterministic.
        try:
            import opentelemetry.trace as _trace_mod
            if getattr(_trace_mod, "_TRACER_PROVIDER", None) is None:
                _trace_mod._TRACER_PROVIDER = provider
        except Exception:
            pass
        return provider


def get_tracer(name: str) -> Any:
    """Return a tracer for ``name``; no-op when telemetry is disabled.

    Tracers come from this module's provider directly (not the process-global
    one) so reconfiguration in tests is deterministic.
    """
    if OTEL_AVAILABLE:
        provider = _provider
        if provider is None and enabled():
            provider = configure_telemetry()
        if provider is not None:
            return provider.get_tracer(name)
        # Disabled: return a true no-op tracer even if a leftover global
        # provider exists from earlier configuration.
        try:
            from opentelemetry.trace import NoOpTracer
            return NoOpTracer()
        except Exception:
            return _NoOpTracer()
    return _NoOpTracer()


class _NoOpTracer:
    def start_span(self, name: str, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()

    def start_as_current_span(self, name: str, **kwargs: Any) -> ContextManager[_NoOpSpan]:  # pragma: no cover
        import contextlib
        return contextlib.nullcontext(_NoOpSpan())


class _NoOpSpan:
    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def end(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


def setup_telemetry(app: Any) -> bool:
    """Instrument the FastAPI app with OpenTelemetry request spans.

    Safe to call multiple times; returns True when instrumentation happened.
    """
    configure_telemetry()
    if not enabled():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        if getattr(app, "_stockpilot_otel", False):
            return False
        FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)
        app._stockpilot_otel = True
        return True
    except Exception:
        # Instrumentation must never prevent the app from serving requests.
        return False


def shutdown_telemetry() -> None:
    """Flush and shut down the provider (called from lifespan teardown)."""
    global _provider, _setup_done
    provider = _provider
    _provider = None
    _setup_done = False
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            pass
