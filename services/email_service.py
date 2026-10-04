"""Optional SMTP delivery for account and alert messages.

No credentials are stored in source code. Configure SMTP_HOST, SMTP_PORT,
SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, APP_BASE_URL, and SMTP_USE_TLS in
environment variables or a deployment secret manager.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote


def smtp_is_configured() -> bool:
    return all(os.getenv(name) for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"))


def _as_bool(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _send_message(message: EmailMessage) -> None:
    if not smtp_is_configured():
        raise RuntimeError("SMTP is not configured.")

    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ["SMTP_FROM"]
    context = ssl.create_default_context()
    timeout = 15
    if _as_bool("SMTP_USE_TLS", True):
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
            server.login(username, password)
            server.send_message(message)


def send_password_reset_email(email: str, token: str, valid_minutes: int) -> None:
    base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    reset_url = f"{base_url}/?reset_token={quote(token)}"
    message = EmailMessage()
    message["Subject"] = "Reset your StockPilot AI password"
    message["From"] = os.getenv("SMTP_FROM", "StockPilot AI")
    message["To"] = email
    message.set_content(
        "A password reset was requested for your StockPilot AI account.\n\n"
        f"Open this link within {valid_minutes} minutes:\n{reset_url}\n\n"
        "If you did not request this, ignore this email. The link can be used once."
    )
    _send_message(message)


def send_alert_email(email: str, alert: dict[str, object]) -> str:
    if not smtp_is_configured():
        return "not_configured"
    message = EmailMessage()
    message["Subject"] = f"StockPilot alert: {alert.get('symbol', 'market')}"
    message["From"] = os.getenv("SMTP_FROM", "StockPilot AI")
    message["To"] = email
    message.set_content(
        f"Your {alert.get('symbol')} research alert was triggered.\n\n"
        f"Condition: {alert.get('condition')} {alert.get('threshold')}\n"
        f"Observed at: {alert.get('triggered_at')}\n\n"
        "This is a research notification, not investment advice or a broker order."
    )
    _send_message(message)
    return "sent"
