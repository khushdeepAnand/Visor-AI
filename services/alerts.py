"""Persistent price and forecast-range alert rules with deterministic evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from database import get_connection, record_audit_event
from forecasting.interval_forecast import TRAINING_WINDOWS
from services.market_data.manager import MANAGER
from services.email_service import send_alert_email


def normalize_symbol(symbol: str) -> str:
    return MANAGER.normalize_symbol(symbol)


VALID_CONDITIONS = {"ABOVE", "BELOW", "FORECAST_HIGH_ABOVE", "FORECAST_LOW_BELOW"}
FORECAST_CONDITIONS = {"FORECAST_HIGH_ABOVE", "FORECAST_LOW_BELOW"}


def create_price_alert(
    user_id: int,
    symbol: str,
    condition: str,
    threshold: float,
    *,
    training_window: str = "1mo",
    confidence_level: float = 0.80,
    email_enabled: bool = False,
) -> int:
    symbol = normalize_symbol(symbol)
    condition = str(condition or "").strip().upper()
    threshold = float(threshold)
    training_window = str(training_window or "1mo").lower()
    confidence_level = float(confidence_level)
    if condition not in VALID_CONDITIONS:
        raise ValueError(f"Alert condition must be one of {sorted(VALID_CONDITIONS)}.")
    if threshold <= 0:
        raise ValueError("Alert threshold must be positive.")
    if training_window not in TRAINING_WINDOWS:
        raise ValueError(f"training_window must be one of {sorted(TRAINING_WINDOWS)}")
    if not 0.60 <= confidence_level <= 0.95:
        raise ValueError("confidence_level must be between 0.60 and 0.95.")
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO price_alerts(user_id, symbol, condition, threshold, training_window, confidence_level, email_enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(user_id), symbol, condition, threshold, training_window, confidence_level, int(email_enabled)),
    )
    alert_id = int(cursor.lastrowid)
    conn.commit(); conn.close()
    record_audit_event(
        user_id,
        "price_alert_created",
        "price_alert",
        alert_id,
        {"symbol": symbol, "condition": condition, "threshold": threshold, "training_window": training_window, "confidence_level": confidence_level},
    )
    return alert_id


def list_price_alerts(user_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT id, symbol, condition, threshold, training_window, confidence_level, is_active, created_at, last_triggered_at, email_enabled, last_email_at FROM price_alerts WHERE user_id = ?"
    params: list[Any] = [int(user_id)]
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY id DESC"
    conn = get_connection(); rows = conn.execute(sql, params).fetchall(); conn.close()
    return [
        {
            "id": row[0], "symbol": row[1], "condition": row[2], "threshold": float(row[3]),
            "training_window": row[4] or "1mo", "confidence_level": float(row[5] or 0.80),
            "is_active": bool(row[6]), "created_at": row[7], "last_triggered_at": row[8],
            "email_enabled": bool(row[9]), "last_email_at": row[10],
        }
        for row in rows
    ]


def set_alert_active(user_id: int, alert_id: int, active: bool) -> bool:
    conn = get_connection()
    cursor = conn.execute("UPDATE price_alerts SET is_active = ? WHERE id = ? AND user_id = ?", (1 if active else 0, int(alert_id), int(user_id)))
    conn.commit(); changed = cursor.rowcount > 0; conn.close()
    return changed


def delete_price_alert(user_id: int, alert_id: int) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (int(alert_id), int(user_id)))
    conn.commit(); changed = cursor.rowcount > 0; conn.close()
    if changed:
        record_audit_event(user_id, "price_alert_deleted", "price_alert", alert_id)
    return changed


def evaluate_price_alerts(
    user_id: int,
    prices: dict[str, float],
    forecast_ranges: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate spot and range-derived alerts.

    ``forecast_ranges`` maps normalized symbols to canonical forecast objects with
    low/median/high/confidence_level. A triggered range alert includes that entire
    band so no downstream alert surface falls back to a naked point prediction.
    """
    normalized_prices = {normalize_symbol(symbol): float(price) for symbol, price in prices.items() if float(price) > 0}
    normalized_ranges = {normalize_symbol(symbol): value for symbol, value in (forecast_ranges or {}).items()}
    alerts = list_price_alerts(user_id, active_only=True)
    triggered: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        for alert in alerts:
            last_triggered = alert.get("last_triggered_at")
            if last_triggered:
                try:
                    elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(str(last_triggered).replace("Z", "+00:00"))
                    if elapsed.total_seconds() < max(60, int(os.getenv("STOCKPILOT_ALERT_COOLDOWN_SECONDS", "900"))):
                        continue
                except ValueError:
                    pass
            condition = alert["condition"]
            current = normalized_prices.get(alert["symbol"])
            band = normalized_ranges.get(alert["symbol"])
            threshold = float(alert["threshold"])
            hit = False
            if condition == "ABOVE" and current is not None:
                hit = current >= threshold
            elif condition == "BELOW" and current is not None:
                hit = current <= threshold
            elif condition == "FORECAST_HIGH_ABOVE" and band is not None:
                hit = float(band["high"]) >= threshold
            elif condition == "FORECAST_LOW_BELOW" and band is not None:
                hit = float(band["low"]) <= threshold
            if not hit:
                continue
            conn.execute("UPDATE price_alerts SET last_triggered_at = ? WHERE id = ?", (now, alert["id"]))
            item = {**alert, "current_price": current, "triggered_at": now}
            if band is not None and condition in FORECAST_CONDITIONS:
                item["forecast"] = {
                    "low": float(band["low"]), "median": float(band["median"]), "high": float(band["high"]),
                    "confidence_level": float(band["confidence_level"]), "currency": str(band.get("currency") or "INR"),
                }
            triggered.append(item)
        conn.commit()
    finally:
        conn.close()
    return triggered


def deliver_triggered_alerts(user_id: int, email: str, triggered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deliver opted-in alerts once per trigger; evaluation remains successful on SMTP failure."""
    results: list[dict[str, Any]] = []
    for alert in triggered:
        if not alert.get("email_enabled"):
            results.append({"alert_id": alert["id"], "channel": "email", "status": "disabled"})
            continue
        try:
            status = send_alert_email(email, alert)
        except Exception:
            status = "failed"
        if status == "sent":
            conn = get_connection()
            try:
                conn.execute("UPDATE price_alerts SET last_email_at=? WHERE id=? AND user_id=?", (alert["triggered_at"], alert["id"], int(user_id)))
                conn.commit()
            finally:
                conn.close()
        results.append({"alert_id": alert["id"], "channel": "email", "status": status})
    return results
