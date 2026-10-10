"""Repair DAO defaults and case-insensitive watchlist uniqueness without deleting data.

Revision ID: 20261010_01
Revises: 8788046ff051
"""
from alembic import op
import sqlalchemy as sa

revision = "20261010_01"
down_revision = "8788046ff051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original revision used a quoted literal on text timestamp columns.
    # Correct future inserts only: existing values must be reconciled separately.
    for table, columns in {
        "user_mfa": ("created_at", "updated_at"),
        "mfa_recovery_codes": ("created_at",),
        "mfa_challenges": ("created_at",),
        "auth_login_attempts": ("updated_at",),
        "password_reset_tokens": ("created_at",),
        "model_health": ("evaluated_at",),
        "paper_accounts": ("created_at", "updated_at"),
        "paper_positions": ("updated_at",),
        "paper_orders": ("created_at",),
        "paper_trade_journal": ("created_at",),
        "paper_badges": ("earned_at",),
        "paper_challenge_entries": ("created_at",),
        "user_workspace_layouts": ("updated_at",),
        "price_alerts": ("created_at",),
        "chart_preferences": ("updated_at",),
        "chart_drawings": ("created_at", "updated_at"),
        "audit_log": ("created_at",),
    }.items():
        for column in columns:
            op.alter_column(table, column, server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::text"))
    for table, column in (("paper_trade_journal", "payload_json"),
                          ("user_workspace_layouts", "layout_json"),
                          ("chart_preferences", "layout_json")):
        op.alter_column(table, column, server_default=sa.text("'{}'"))
    # Fails atomically if legacy case variants collide; never discard a row.
    op.create_index("idx_watchlist_user_upper_symbol", "watchlist",
                    ["user_id", sa.text("upper(symbol)")], unique=True)


def downgrade() -> None:
    raise RuntimeError("Forward-only revision. Recover using a verified pre-migration backup; no data is deleted by upgrade.")
