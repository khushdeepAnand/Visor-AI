"""Small optional job/provider telemetry adapter; never fails a job."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator
from .telemetry import get_tracer

@contextmanager
def job_span(job: str, provider: str = "unknown") -> Iterator[object]:
    span = get_tracer("stockpilot.jobs").start_span(f"job.{job}")
    try:
        span.set_attribute("job.name", job); span.set_attribute("provider", provider)
        yield span
    except Exception as exc:
        try: span.record_exception(exc)
        except Exception: pass
        raise
    finally: span.end()
