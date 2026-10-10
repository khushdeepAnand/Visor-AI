"""Versioned user-facing compliance statements used by API and UI flows."""

import json
from typing import Any

from services.db.factory import dao_session
from services.admin_registry import _ensure_settings_table

RESEARCH_ACKNOWLEDGMENT_VERSION = "2026-09-27"
RESEARCH_ONLY_DISCLAIMER = (
    "Not investment advice. For research and education only. "
    "Forecasts, screeners, alerts, and derivatives analytics are uncertain analytical outputs, "
    "not recommendations. Paper trades are simulations and no broker orders are placed."
)

REVIEW_ITEMS = {
    "regulatory_classification": "Qualified review of research/education product classification",
    "disclaimer_review": "Review research disclaimers and forecast presentation",
    "data_licensing": "Verify market-data licensing and redistribution rights",
    "privacy_retention": "Review privacy, account deletion and retention windows",
    "external_penetration_test": "Independent penetration test before scaling",
    "no_live_execution": "Verify the no-live-order-execution boundary",
}


def review_checklist() -> list[dict[str, Any]]:
    with dao_session() as factory:
        conn = factory.db
        _ensure_settings_table(conn)
        rows = {row["key"]: row["value"] for row in conn.fetchall(conn.sql("SELECT key,value FROM app_settings WHERE key LIKE 'regulatory.review.%'"))}
        return [{"id": key, "label": label, **json.loads(rows.get(f"regulatory.review.{key}", '{"status":"open","note":""}'))}
                for key, label in REVIEW_ITEMS.items()]


def update_review_item(item_id: str, *, status: str, note: str, actor_id: int, actor_email: str) -> None:
    """Persist review evidence and its audit record atomically; never implies approval."""
    if item_id not in REVIEW_ITEMS or status not in {"open", "in_progress", "complete", "blocked"}:
        raise ValueError("Unknown review item or status")
    note = note.strip()
    if not 12 <= len(note) <= 500:
        raise ValueError("Provide a review justification or evidence reference (12–500 characters)")
    with dao_session() as factory:
        conn = factory.db
        _ensure_settings_table(conn)
        conn.begin_write("compliance-review:" + item_id)
        conn.execute(conn.sql("""INSERT INTO app_settings(key,value,updated_by) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP,updated_by=excluded.updated_by"""),
            (f"regulatory.review.{item_id}", json.dumps({"status": status, "note": note}), actor_email))
        conn.execute(conn.sql("""INSERT INTO admin_audit_log(actor_user_id,actor_email,action,target,outcome,detail)
            VALUES(?,?,'compliance_review_update',?,'ok',?)"""), (actor_id, actor_email, item_id, json.dumps({"status": status, "reason": note})))
        conn.commit()


def acknowledgment_directory(limit: int = 100) -> list[dict[str, Any]]:
    with dao_session() as factory:
        conn = factory.db
        rows = conn.fetchall(conn.sql("""SELECT u.id,u.email,MAX(a.created_at) AS acknowledged_at FROM users u LEFT JOIN audit_log a
            ON a.user_id=u.id AND a.action='research_disclaimer_acknowledged' AND a.entity_id=?
            GROUP BY u.id,u.email ORDER BY u.id DESC LIMIT ?"""), (RESEARCH_ACKNOWLEDGMENT_VERSION, limit))
        return [{"user_id": row["id"], "email": row["email"], "acknowledged_at": row["acknowledged_at"], "required": row["acknowledged_at"] is None} for row in rows]


def research_acknowledgment_required(user_id: int) -> bool:
    """Return whether the user has accepted the current disclosure version."""
    with dao_session() as factory:
        connection = factory.db
        row = connection.fetchone(connection.sql(
            """
            SELECT 1 FROM audit_log
            WHERE user_id=? AND action='research_disclaimer_acknowledged' AND entity_id=?
            LIMIT 1
            """),
            (int(user_id), RESEARCH_ACKNOWLEDGMENT_VERSION),
        )
        return row is None
