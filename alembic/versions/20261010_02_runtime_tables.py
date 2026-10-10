"""Expand to the audited lazy runtime schema and deny Data API table access.

Revision ID: 20261010_02
Revises: 20261010_01
"""
from alembic import op
import sqlalchemy as sa
from typing import Any

revision = "20261010_02"
down_revision = "20261010_01"
branch_labels = None
depends_on = None


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.Integer, primary_key=True, autoincrement=True)


def _text(name: str, *, required: bool = False, default: Any = None) -> sa.Column[str]:
    return sa.Column(name, sa.Text, nullable=not required, server_default=default)


def _user() -> sa.Column[int]:
    return sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    stamp = sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::text")
    op.create_table("webauthn_credentials", _id(), _user(),
                    _text("credential_id", required=True), _text("public_key", required=True),
                    sa.Column("sign_count", sa.BigInteger, nullable=False, server_default="0"),
                    _text("transports"), _text("label"), _text("created_at", required=True, default=stamp),
                    _text("last_used_at"), sa.UniqueConstraint("credential_id"))
    op.create_index("idx_webauthn_user", "webauthn_credentials", ["user_id"])
    op.create_table("login_devices", _id(), _user(), _text("device_hash", required=True),
                    _text("device_label"), _text("ip_prefix"), sa.Column("lat", sa.Float), sa.Column("lon", sa.Float),
                    _text("first_seen", required=True, default=stamp), _text("last_seen", required=True, default=stamp),
                    sa.Column("seen_count", sa.Integer, nullable=False, server_default="1"), _text("confirmed_at"),
                    sa.UniqueConstraint("user_id", "device_hash"))
    op.create_index("idx_login_devices_user", "login_devices", ["user_id", "last_seen"])
    op.create_table("login_anomalies", _id(), _user(), _text("anomaly_type", required=True),
                    _text("severity", required=True, default="info"), _text("detail"), _text("ip_prefix"),
                    _text("created_at", required=True, default=stamp), _text("acknowledged_at"))
    op.create_index("idx_login_anomalies_user", "login_anomalies", ["user_id", "created_at"])
    op.create_table("app_settings", _text("key", required=True), _text("value", required=True),
                    sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
                    _text("updated_by"), sa.PrimaryKeyConstraint("key"))
    op.create_table("admin_step_up_tokens", _text("token_hash", required=True),
                    _text("actor_email", required=True), sa.Column("actor_user_id", sa.Integer),
                    _text("action", required=True), _text("target", required=True),
                    _text("created_at", required=True), _text("expires_at", required=True),
                    sa.PrimaryKeyConstraint("token_hash"))
    op.create_table("admin_step_up_failures", _id(), _text("actor_email", required=True),
                    _text("action", required=True), _text("occurred_at", required=True))
    op.create_table("forecast_kill_switches", _id(), _text("scope", required=True),
                    _text("target", required=True), _text("reason", required=True), _text("created_by"),
                    _text("created_at", required=True), _text("expires_at", required=True),
                    _text("revoked_at"), _text("revoked_by"), _text("revoke_reason"))
    op.create_table("feature_flag_state", _text("name", required=True),
                    sa.Column("enabled", sa.Integer, nullable=False, server_default="0"),
                    sa.Column("rollout_percent", sa.Integer, nullable=False, server_default="0"),
                    _text("updated_by"), _text("updated_at", required=True), _text("reason"),
                    _text("auto_rolled_back_at"), _text("auto_rollback_detail"), sa.PrimaryKeyConstraint("name"))
    op.create_table("status_banners", _id(), _text("level", required=True),
                    _text("headline", required=True), _text("body", required=True),
                    _text("starts_at", required=True), _text("ends_at", required=True),
                    sa.Column("published", sa.Integer, nullable=False, server_default="0"),
                    _text("created_by"), _text("created_at", required=True),
                    _text("published_by"), _text("published_at"), _text("withdrawn_at"))
    op.create_table("saved_chart_layouts", _id(), _user(), _text("name", required=True),
                    _text("symbol", required=True), _text("timeframe", required=True),
                    _text("overlays_json", required=True, default="{}"), _text("visible_range_json"),
                    _text("created_at", required=True), _text("updated_at", required=True),
                    sa.UniqueConstraint("user_id", "name"))
    op.create_table("screener_saved_screens", _id(), _user(), _text("name", required=True),
                    _text("filters", required=True), _text("sort_field"),
                    sa.Column("sort_descending", sa.Integer, nullable=False, server_default="1"),
                    _text("symbols"), _text("created_at", required=True), _text("updated_at", required=True),
                    sa.UniqueConstraint("user_id", "name"))
    op.create_table("strategy_definitions", _id(), _user(), _text("name", required=True),
                    _text("definition", required=True), _text("created_at", required=True),
                    _text("updated_at", required=True), sa.UniqueConstraint("user_id", "name"))
    op.create_table("forward_tests", _id(), _user(),
                    sa.Column("strategy_id", sa.Integer, sa.ForeignKey("strategy_definitions.id"), nullable=False),
                    _text("name", required=True), _text("symbols", required=True), _text("status", required=True),
                    _text("started_at", required=True), _text("stopped_at"), _text("last_evaluated_at"))
    op.create_table("forward_test_events", _id(),
                    sa.Column("forward_test_id", sa.Integer, sa.ForeignKey("forward_tests.id", ondelete="CASCADE"), nullable=False),
                    _text("symbol", required=True), _text("bar_at", required=True), _text("action", required=True),
                    sa.Column("price", sa.Float, nullable=False), _text("reason"), _text("recorded_at", required=True),
                    sa.UniqueConstraint("forward_test_id", "symbol", "bar_at", "action"))
    # Initial tables plus the audited additions only. Never alter unrelated
    # Supabase/public tables just because they share a schema.
    tables = """users admin_audit_log oauth_identities auth_sessions user_mfa mfa_recovery_codes
        mfa_challenges auth_login_attempts password_reset_tokens symbols portfolio watchlist transactions
        prediction_history settings model_health paper_accounts paper_positions paper_orders paper_trade_journal
        paper_badges paper_challenge_entries user_workspace_layouts price_alerts chart_preferences chart_drawings
        audit_log sentiment_snapshots webauthn_credentials login_devices login_anomalies app_settings
        admin_step_up_tokens admin_step_up_failures forecast_kill_switches feature_flag_state status_banners
        saved_chart_layouts screener_saved_screens strategy_definitions forward_tests forward_test_events""".split()
    bind = op.get_bind()
    preparer = bind.dialect.identifier_preparer
    for table in tables:
        identifier = preparer.quote_identifier(table)
        op.execute(sa.text(f"ALTER TABLE {identifier} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"REVOKE ALL ON TABLE {identifier} FROM PUBLIC"))
        for role in ("anon", "authenticated"):
            # Server-side conditional is safe on plain Postgres and compiles in
            # Alembic offline mode without pretending to query a live database.
            quoted_role = preparer.quote_identifier(role)
            op.execute(sa.text(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                    REVOKE ALL ON TABLE {identifier} FROM {quoted_role};
                END IF;
            END $$"""))
    # No public policies: custom JWTs remain application auth, not Supabase auth.
    # Table owners/superusers/BYPASSRLS roles bypass RLS. Backend ownership checks
    # are the authorization boundary for those trusted server connections.


def downgrade() -> None:
    raise RuntimeError("Forward-only expand migration. Restore a verified backup to a separate database for recovery.")
