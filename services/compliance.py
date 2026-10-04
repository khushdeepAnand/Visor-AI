"""Versioned user-facing compliance statements used by API and UI flows."""

from database import get_connection

RESEARCH_ACKNOWLEDGMENT_VERSION = "2026-09-27"
RESEARCH_ONLY_DISCLAIMER = (
    "Not investment advice. For research and education only. "
    "Forecasts, screeners, alerts, and derivatives analytics are uncertain analytical outputs, "
    "not recommendations. Paper trades are simulations and no broker orders are placed."
)


def research_acknowledgment_required(user_id: int) -> bool:
    """Return whether the user has accepted the current disclosure version."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT 1 FROM audit_log
            WHERE user_id=? AND action='research_disclaimer_acknowledged' AND entity_id=?
            LIMIT 1
            """,
            (int(user_id), RESEARCH_ACKNOWLEDGMENT_VERSION),
        ).fetchone()
        return row is None
    finally:
        connection.close()
