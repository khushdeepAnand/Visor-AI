"""Structured, automatically-redacted application logging for StockPilot AI.

The JsonFormatter emits one JSON object per line. A RedactingFilter scrubs
tokens, passwords, cookies, and known secret env values out of every record
before it is rendered, and the formatter re-scrubs the final serialized line
(catching exception text rendered from tracebacks) as defense in depth. This
makes the "never log secrets" rule enforced in code rather than by convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "stockpilot.log")

MASK = "[REDACTED]"

# Environment variables whose live values are scrubbed verbatim from logs.
SECRET_ENV_VARS: tuple[str, ...] = (
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_ANALYTICS_TOKEN",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "SMTP_PASSWORD",
    "STOCKPILOT_JWT_SECRET",
)

# Key=value or "key": "value" pairs that must never reach a log line.
_SECRET_KEY_VALUE = re.compile(
    r'(?i)("(?:%s)"\s*:\s*")([^"]+)(")' % "|".join(
        ["access_token", "refresh_token", "id_token", "api_key", "apikey",
         "client_secret", "password", "passwd", "pwd", "token", "secret",
         "authorization", "jwks", "jwt", "x_api_key"]
    )
)
_SECRET_ASSIGN_VALUE = re.compile(
    r"(?i)((?:%s)\s*[=:]\s*[\"']?)([A-Za-z0-9_\-\.+/]+)" % "|".join(
        ["access_token", "refresh_token", "id_token", "api_key", "apikey",
         "client_secret", "client_password", "password", "passwd", "token",
         "authorization"]
    )
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-\.+/=]{8,}")
_SESSION_COOKIE = re.compile(r"(stockpilot_session=[A-Za-z0-9_\-\.]{8,})")
_JWT = re.compile(r"(eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-\.]{8,}\.[A-Za-z0-9_\-]{8,})")
_GOOGLE_SECRET = re.compile(r"(GOCSPX-[A-Za-z0-9_\-]{10,})")


def _secret_env_values() -> tuple[str, ...]:
    """Snapshot the live secret env values once, safely."""
    return tuple(
        value
        for name in SECRET_ENV_VARS
        if (value := os.getenv(name, "").strip()) and value.lower() not in {"", "none", "changeme", "your-value"}
    )


def redact(text: str) -> str:
    """Return ``text`` with secrets replaced by the mask."""
    for value in _secret_env_values():
        if value in text:
            text = text.replace(value, MASK)
    text = _BEARER.sub(r"\1" + MASK, text)
    text = _SESSION_COOKIE.sub(MASK, text)
    text = _JWT.sub(MASK, text)
    text = _GOOGLE_SECRET.sub(MASK, text)
    text = _SECRET_KEY_VALUE.sub(r"\1" + MASK + r"\3", text)
    text = _SECRET_ASSIGN_VALUE.sub(r"\1" + MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrub secret material from the record before the formatter renders it."""

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._secret_values = _secret_env_values()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError, KeyError):  # malformed args should not abort logging
            message = str(getattr(record, "msg", ""))
            if record.args:
                try:
                    message = "%s %s" % (message, record.args)
                except Exception:
                    pass
        record.msg = redact(message)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line.

    The serialized line is re-scrubbed so secret-bearing exception text and
    any extra detail fields cannot slip through the filter.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key in ("request_id", "user_id", "symbol", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return redact(json.dumps(payload, ensure_ascii=True, default=str))


def configure_logging() -> logging.Logger:
    """Configure safe rotating JSON file logging once per process.

    One rotating file handler is attached to the ``stockpilot`` logger
    (``propagate=False`` keeps it out of uvicorn's console noise). Child
    loggers such as ``stockpilot.errors`` and ``stockpilot.streaming``
    inherit this handler and the redaction filter.
    """
    logger = logging.getLogger("stockpilot")
    if logger.handlers:
        return logger

    os.makedirs(LOG_DIR, exist_ok=True)
    logger.setLevel(getattr(logging, os.getenv("STOCKPILOT_LOG_LEVEL", "INFO").upper(), logging.INFO))
    logger.propagate = False

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=max(250_000, int(os.getenv("STOCKPILOT_LOG_MAX_BYTES", "2000000"))),
        backupCount=max(1, int(os.getenv("STOCKPILOT_LOG_BACKUPS", "5"))),
        encoding="utf-8",
    )
    file_handler.addFilter(RedactingFilter())
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    return logger