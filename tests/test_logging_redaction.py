# ==========================================================
# Redacted logging enforcement tests (v11, §1)
# ==========================================================
#
# Token, password, cookie, API-key, and known secret env values must never
# reach a rendered log line. Both the JsonFormatter output and the
# RedactingFilter (which runs on every logging handler attached to the
# stockpilot logger) are exercised with realistic payloads, including
# exception text that commonly carries provider messages.

import io
import json
import logging

from utils.logging_config import JsonFormatter, RedactingFilter, redact


class _Capture(logging.Handler):
    def __init__(self, fmt=None):
        super().__init__(level=logging.DEBUG)
        self.setFormatter(fmt or JsonFormatter())
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def _logger_with_redaction():
    logger = logging.getLogger("stockpilot.redact-test")
    logger.handlers.clear()
    logger.propagate = False
    capture = _Capture()
    capture.addFilter(RedactingFilter())
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    return logger, capture


def test_redact_masks_bearer_and_jwt():
    token = ".".join(("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJzdWIiOiIxIn0", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"))
    text = f"Authorization: Bearer {token} called"
    out = redact(text)
    assert "eyJ" not in out
    assert "SflKxwR" not in out
    assert "[REDACTED]" in out


def test_redact_masks_session_cookie():
    text = "cookie stockpilot_session=eyJzZXNzaW9uIjoiMTAyNGV4YW1wbGUifQ.abc.def"
    assert "stockpilot_session=" not in redact(text)
    assert "[REDACTED]" in redact(text)


def test_redact_masks_json_secret_pairs():
    cases = [
        '{"password": "StrongPass9!x", "email": "a@b.c"}',
        '{"access_token": "esr3eyJhbGciOiJIUzI1In0.xyz.abc", "active": true}',
        '{"api_key": "UPSTOK-9f8e7d6c5b4a3210", "n": 1}',
        '{"client_secret": "GOCSPX-a1b2c3d4e5f6g7h8i9j0", "scope": "openid"}',
    ]
    for text in cases:
        out = redact(text)
        assert "[REDACTED]" in out, out
        assert 'password' not in out or 'StrongPass9!x' not in out
        assert 'GOCSPX-' not in out


def test_redact_masks_assign_style_values():
    text = "PASSWORD=mysecret123 URL=http://x token=abc123def456"
    out = redact(text)
    assert "mysecret123" not in out
    assert "[REDACTED]" in out


def test_redact_masks_live_secret_env_values_verbatim(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "live-upstox-analytics-token-abc")
    monkeypatch.setenv("STOCKPILOT_JWT_SECRET", "jwt-secret-value-xyz")
    text = "probe token live-upstox-analytics-token-abc roundtrip jwt-secret-value-xyz ok"
    out = redact(text)
    assert "live-upstox-analytics-token-abc" not in out
    assert "jwt-secret-value-xyz" not in out


def test_redact_preserves_non_secret_text():
    text = '{"email": "a@b.c", "symbol": "RELIANCE", "shares": 10}'
    out = redact(text)
    assert out == text


def test_formatter_redacts_message_and_exception():
    record = logging.LogRecord(
        name="stockpilot.errors",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failed: token=%s",
        args=("abc123secretXYZ",),
        exc_info=(ValueError, ValueError("token=abc123secretXYZ rejected"), None),
    )
    payload = json.loads(JsonFormatter().format(record))
    assert "abc123secretXYZ" not in payload["message"]
    assert "[REDACTED]" in payload["message"]
    assert "abc123secretXYZ" not in payload["exception"]
    assert "[REDACTED]" in payload["exception"]


def test_redacting_filter_scrubs_args():
    logger, capture = _logger_with_redaction()
    logger.info("login ok password=%s", "supersecret99x")
    assert len(capture.lines) == 1
    assert "supersecret99x" not in capture.lines[0]
    assert "[REDACTED]" in capture.lines[0]


def test_configured_stockpilot_logger_has_redacting_handler():
    import utils.logging_config as lc

    logger = logging.getLogger("stockpilot")
    before = list(logger.handlers)
    try:
        configured = lc.configure_logging()
        assert configured is logger
        assert logger.handlers
        assert any(getattr(h, "filters", None) and any(
            isinstance(f, lc.RedactingFilter) for f in h.filters
        ) for h in logger.handlers)
    finally:
        for h in logger.handlers:
            if h not in before:
                logger.removeHandler(h)
                h.close()
