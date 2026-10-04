"""Configured operational alert delivery.

No endpoint is contacted unless ``STOCKPILOT_ALERT_WEBHOOK_URL`` is set by an
operator. Payloads contain status and diagnostics only; secrets never enter
the body or logs. This keeps scheduled safety alerts useful without making
network access a required test dependency.
"""
from __future__ import annotations

import json
import logging
import os
import requests
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)


def dispatch_operational_alert(event: str, message: str, *, details: dict[str, object] | None = None) -> bool:
    """Send one configured webhook alert; return False when unavailable/failing."""
    url = os.getenv("STOCKPILOT_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        LOGGER.warning("Operational alert not delivered: webhook is not configured (%s)", event)
        return False
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        LOGGER.error("Operational alert webhook must be an HTTP(S) endpoint without embedded credentials.")
        return False

    kind = os.getenv("STOCKPILOT_ALERT_WEBHOOK_KIND", "generic").strip().lower()
    text = f"StockPilot alert [{event}]: {message}"
    if kind == "slack":
        payload: dict[str, object] = {"text": text, "event": event, "details": details or {}}
    elif kind == "pagerduty":
        payload = {
            "routing_key": os.getenv("STOCKPILOT_PAGERDUTY_ROUTING_KEY", ""),
            "event_action": "trigger",
            "payload": {"summary": text, "severity": "error", "source": "stockpilot", "custom_details": details or {}},
        }
    else:
        payload = {"event": event, "message": message, "details": details or {}}

    try:
        with requests.post(url, json=json.loads(json.dumps(payload, default=str)), headers={"User-Agent": "StockPilot/operational-alert"},
                           timeout=float(os.getenv("STOCKPILOT_ALERT_TIMEOUT_SECONDS", "5")),  # nosec B113
                           allow_redirects=False) as response:
            status = response.status_code
        if not 200 <= status < 300:
            raise RuntimeError(f"alert webhook returned HTTP {status}")
        LOGGER.info("Operational alert delivered: %s", event)
        return True
    except Exception as exc:
        LOGGER.error("Operational alert delivery failed for %s: %s", event, exc)
        return False


__all__ = ["dispatch_operational_alert"]
