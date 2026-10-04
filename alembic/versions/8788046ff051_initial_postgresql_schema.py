"""Initial PostgreSQL schema for StockPilot AI.

Mirrors the SQLite schema in database.py create_tables() with PostgreSQL
dialect equivalents (SERIAL, TIMESTAMPTZ, BOOLEAN, JSONB).

Revision ID: 8788046ff051
Revises:
Create Date: 2026-09-29 20:13:56.064594

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '8788046ff051'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ======================================================
    # USERS
    # ======================================================
    op.create_table(
        'users',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('email', sa.String, nullable=False, unique=True),
        sa.Column('password', sa.String, nullable=False),
        sa.Column('created_at', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('auth_provider', sa.String, server_default='password'),
        sa.Column('google_id', sa.String, nullable=True),
        sa.Column('last_login_at', sa.String, nullable=True),
        sa.Column('role', sa.String, nullable=False, server_default='user'),
        sa.Column('token_version', sa.Integer, nullable=False, server_default='0'),
        sa.Column('date_of_birth', sa.String, nullable=True),
        sa.Column('account_status', sa.String, nullable=False, server_default='active'),
    )
    op.create_index('idx_users_google_id', 'users', ['google_id'], unique=True,
                    postgresql_where=sa.text('google_id IS NOT NULL'))

    op.create_table(
        'admin_audit_log',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('actor_user_id', sa.Integer, nullable=True),
        sa.Column('actor_email', sa.String, nullable=True),
        sa.Column('action', sa.String, nullable=False),
        sa.Column('target', sa.String, nullable=True),
        sa.Column('outcome', sa.String, nullable=False),
        sa.Column('detail', sa.String, nullable=True),
        sa.Column('created_at', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_admin_audit_created', 'admin_audit_log', [sa.text('created_at DESC')])

    # ======================================================
    # OAUTH IDENTITIES
    # ======================================================
    op.create_table(
        'oauth_identities',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String, nullable=False),
        sa.Column('provider_subject', sa.String, nullable=False),
        sa.Column('verified_email', sa.String, nullable=True),
        sa.Column('created_at', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_login_at', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('provider', 'provider_subject'),
        sa.UniqueConstraint('user_id', 'provider'),
        sa.CheckConstraint("provider IN ('google', 'apple')", name='ck_oauth_provider'),
    )
    op.create_index('idx_oauth_identities_user', 'oauth_identities', ['user_id'])

    # ======================================================
    # AUTH SESSIONS
    # ======================================================
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('jti_hash', sa.String, nullable=False, unique=True),
        sa.Column('token_version', sa.Integer, nullable=False),
        sa.Column('issued_at', sa.String, nullable=False),
        sa.Column('expires_at', sa.String, nullable=False),
        sa.Column('revoked_at', sa.String, nullable=True),
        sa.Column('device_label', sa.String, nullable=True),
    )
    op.create_index('idx_auth_sessions_user', 'auth_sessions', ['user_id', 'revoked_at'])

    # ======================================================
    # MFA
    # ======================================================
    op.create_table(
        'user_mfa',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('encrypted_totp_secret', sa.String, nullable=False),
        sa.Column('enabled', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_totp_counter', sa.Integer, nullable=True),
        sa.Column('created_at', sa.String, nullable=False, server_default='CURRENT_TIMESTAMP'),
        sa.Column('updated_at', sa.String, nullable=False, server_default='CURRENT_TIMESTAMP'),
        sa.CheckConstraint('enabled IN (0, 1)', name='ck_mfa_enabled'),
    )
    op.create_table(
        'mfa_recovery_codes',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code_hash', sa.String, nullable=False),
        sa.Column('used_at', sa.String, nullable=True),
        sa.Column('created_at', sa.String, nullable=False, server_default='CURRENT_TIMESTAMP'),
        sa.UniqueConstraint('user_id', 'code_hash'),
    )
    op.create_index('idx_mfa_recovery_user', 'mfa_recovery_codes', ['user_id', 'used_at'])
    op.create_table(
        'mfa_challenges',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('jti_hash', sa.String, nullable=False, unique=True),
        sa.Column('expires_at', sa.String, nullable=False),
        sa.Column('failed_attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('consumed_at', sa.String, nullable=True),
        sa.Column('created_at', sa.String, nullable=False, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_index('idx_mfa_challenge_user', 'mfa_challenges', ['user_id', 'consumed_at'])

    # ======================================================
    # AUTH SECURITY / PASSWORD RESET
    # ======================================================
    op.create_table(
        'auth_login_attempts',
        sa.Column('identifier', sa.String, primary_key=True),
        sa.Column('failed_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('window_started', sa.String, nullable=True),
        sa.Column('locked_until', sa.String, nullable=True),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String, nullable=False, unique=True),
        sa.Column('expires_at', sa.String, nullable=False),
        sa.Column('used_at', sa.String, nullable=True),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_index('idx_password_reset_token', 'password_reset_tokens', ['token_hash', 'expires_at'])

    # ======================================================
    # MARKET SYMBOL CATALOGUE
    # ======================================================
    op.create_table(
        'symbols',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('exchange', sa.String, nullable=True),
        sa.Column('country', sa.String, nullable=True),
        sa.Column('sector', sa.String, nullable=True),
    )
    op.create_index('idx_symbols_symbol', 'symbols', ['symbol'])
    op.create_index('idx_symbols_name', 'symbols', ['name'])

    # ======================================================
    # PORTFOLIO / WATCHLIST / TRANSACTIONS
    # ======================================================
    op.create_table(
        'portfolio',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('company', sa.String, nullable=True),
        sa.Column('shares', sa.Float, nullable=False),
        sa.Column('buy_price', sa.Float, nullable=False),
        sa.Column('buy_date', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_portfolio_user_id', 'portfolio', ['user_id'])
    op.create_index('idx_portfolio_symbol', 'portfolio', ['symbol'])

    op.create_table(
        'watchlist',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('added_date', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_watchlist_user_id', 'watchlist', ['user_id'])
    op.create_index('idx_watchlist_user_symbol', 'watchlist', ['user_id', 'symbol'], unique=True)

    op.create_table(
        'transactions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('transaction_type', sa.String, nullable=False),
        sa.Column('shares', sa.Float, nullable=False),
        sa.Column('price', sa.Float, nullable=False),
        sa.Column('transaction_date', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_transactions_user_id', 'transactions', ['user_id'])

    # ======================================================
    # PREDICTION HISTORY
    # ======================================================
    op.create_table(
        'prediction_history',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('linear_prediction', sa.Float, nullable=True),
        sa.Column('decision_tree_prediction', sa.Float, nullable=True),
        sa.Column('random_forest_prediction', sa.Float, nullable=True),
        sa.Column('prediction_date', sa.TIMESTAMP, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('consensus_prediction', sa.Float, nullable=True),
        sa.Column('best_model', sa.String, nullable=True),
        sa.Column('model_version', sa.String, nullable=True),
        sa.Column('payload_json', sa.String, nullable=True),
        sa.Column('forecast_low', sa.Float, nullable=True),
        sa.Column('forecast_median', sa.Float, nullable=True),
        sa.Column('forecast_high', sa.Float, nullable=True),
        sa.Column('confidence_level', sa.Float, nullable=True),
        sa.Column('actual_price', sa.Float, nullable=True),
        sa.Column('coverage_hit', sa.Integer, nullable=True),
        sa.Column('winkler_score', sa.Float, nullable=True),
        sa.Column('training_window', sa.String, nullable=True),
        sa.Column('timeframe', sa.String, nullable=True),
        sa.Column('created_at', sa.String, nullable=True),
        sa.Column('origin_timestamp', sa.String, nullable=True),
        sa.Column('target_timestamp', sa.String, nullable=True),
        sa.Column('provider', sa.String, nullable=True),
        sa.Column('data_timestamp', sa.String, nullable=True),
        sa.Column('feature_timestamp', sa.String, nullable=True),
        sa.Column('data_version', sa.String, nullable=True),
        sa.Column('schema_version', sa.String, nullable=True),
        sa.Column('forecast_evidence_json', sa.String, nullable=True),
        sa.Column('forecast_status', sa.String, nullable=True),
        sa.Column('snapshot_hash', sa.String, nullable=True),
        sa.Column('outcome_status', sa.String, nullable=True),
        sa.Column('outcome_evidence_json', sa.String, nullable=True),
        sa.Column('settlement_source', sa.String, nullable=True),
        sa.Column('settlement_provider', sa.String, nullable=True),
        sa.Column('settlement_data_timestamp', sa.String, nullable=True),
        sa.Column('settlement_is_stale', sa.Integer, nullable=True),
        sa.Column('settlement_is_demo', sa.Integer, nullable=True),
        sa.Column('official_outcome', sa.Integer, server_default='0'),
        sa.Column('settled_at', sa.String, nullable=True),
        sa.Column('result_hash', sa.String, nullable=True),
        sa.Column('horizon', sa.String, nullable=True),
        sa.Column('horizon_sessions', sa.Integer, nullable=True),
    )
    op.create_index('idx_prediction_user_id', 'prediction_history', ['user_id'])
    op.create_index('idx_prediction_outcome_due', 'prediction_history', ['outcome_status', 'target_timestamp'])
    op.create_index('idx_prediction_official_calibration', 'prediction_history',
                    ['user_id', 'official_outcome', 'outcome_status'])

    # Immutability triggers (equivalent of SQLite RAISE(ABORT)).
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_forecast_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'forecast snapshot is immutable';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER prediction_history_forecast_immutable
        BEFORE UPDATE ON prediction_history
        FOR EACH ROW
        WHEN (
            OLD.user_id IS DISTINCT FROM NEW.user_id OR
            OLD.symbol IS DISTINCT FROM NEW.symbol OR
            OLD.linear_prediction IS DISTINCT FROM NEW.linear_prediction OR
            OLD.decision_tree_prediction IS DISTINCT FROM NEW.decision_tree_prediction OR
            OLD.random_forest_prediction IS DISTINCT FROM NEW.random_forest_prediction OR
            OLD.prediction_date IS DISTINCT FROM NEW.prediction_date OR
            OLD.consensus_prediction IS DISTINCT FROM NEW.consensus_prediction OR
            OLD.best_model IS DISTINCT FROM NEW.best_model OR
            OLD.model_version IS DISTINCT FROM NEW.model_version OR
            OLD.payload_json IS DISTINCT FROM NEW.payload_json OR
            OLD.forecast_low IS DISTINCT FROM NEW.forecast_low OR
            OLD.forecast_median IS DISTINCT FROM NEW.forecast_median OR
            OLD.forecast_high IS DISTINCT FROM NEW.forecast_high OR
            OLD.confidence_level IS DISTINCT FROM NEW.confidence_level OR
            OLD.training_window IS DISTINCT FROM NEW.training_window OR
            OLD.timeframe IS DISTINCT FROM NEW.timeframe OR
            OLD.created_at IS DISTINCT FROM NEW.created_at OR
            OLD.origin_timestamp IS DISTINCT FROM NEW.origin_timestamp OR
            OLD.target_timestamp IS DISTINCT FROM NEW.target_timestamp OR
            OLD.provider IS DISTINCT FROM NEW.provider OR
            OLD.data_timestamp IS DISTINCT FROM NEW.data_timestamp OR
            OLD.feature_timestamp IS DISTINCT FROM NEW.feature_timestamp OR
            OLD.data_version IS DISTINCT FROM NEW.data_version OR
            OLD.schema_version IS DISTINCT FROM NEW.schema_version OR
            OLD.forecast_evidence_json IS DISTINCT FROM NEW.forecast_evidence_json OR
            OLD.forecast_status IS DISTINCT FROM NEW.forecast_status OR
            OLD.snapshot_hash IS DISTINCT FROM NEW.snapshot_hash
        )
        EXECUTE FUNCTION prevent_forecast_mutation();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_outcome_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'forecast outcome is immutable';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER prediction_history_outcome_immutable
        BEFORE UPDATE ON prediction_history
        FOR EACH ROW
        WHEN (
            OLD.actual_price IS NOT NULL OR
            OLD.outcome_status IN ('settled', 'unverifiable')
        )
        EXECUTE FUNCTION prevent_outcome_mutation();
    """)

    # ======================================================
    # SETTINGS
    # ======================================================
    op.create_table(
        'settings',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('theme', sa.String, server_default='Dark'),
        sa.Column('currency', sa.String, server_default='₹'),
        sa.Column('default_period', sa.String, server_default='1y'),
    )

    # ======================================================
    # MODEL HEALTH
    # ======================================================
    op.create_table(
        'model_health',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('timeframe', sa.String, nullable=False),
        sa.Column('horizon', sa.Integer, nullable=False, server_default='1'),
        sa.Column('settled_samples', sa.Integer, nullable=False, server_default='0'),
        sa.Column('rolling_coverage', sa.Float, nullable=True),
        sa.Column('nominal_coverage', sa.Float, nullable=True),
        sa.Column('coverage_gap', sa.Float, nullable=True),
        sa.Column('mase', sa.Float, nullable=True),
        sa.Column('directional_accuracy', sa.Float, nullable=True),
        sa.Column('naive_directional_accuracy', sa.Float, nullable=True),
        sa.Column('model_mae', sa.Float, nullable=True),
        sa.Column('naive_mae', sa.Float, nullable=True),
        sa.Column('drift_detected', sa.Integer, nullable=False, server_default='0'),
        sa.Column('drift_reasons_json', sa.String, nullable=True),
        sa.Column('severity', sa.String, nullable=True),
        sa.Column('action_taken', sa.String, nullable=True),
        sa.Column('evaluated_at', sa.String, nullable=False, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_index('idx_model_health_symbol', 'model_health',
                    ['symbol', 'timeframe', 'horizon', 'evaluated_at'])

    # ======================================================
    # PAPER TRADING
    # ======================================================
    op.create_table(
        'paper_accounts',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('initial_balance', sa.Float, nullable=False, server_default='1000000'),
        sa.Column('cash_balance', sa.Float, nullable=False, server_default='1000000'),
        sa.Column('leaderboard_opt_in', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_table(
        'paper_positions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('quantity', sa.Float, nullable=False),
        sa.Column('average_price', sa.Float, nullable=False),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.Column('lot_size', sa.Float, nullable=False, server_default='1'),
        sa.UniqueConstraint('user_id', 'symbol'),
    )
    op.create_table(
        'paper_orders',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('side', sa.String, nullable=False),
        sa.Column('quantity', sa.Float, nullable=False),
        sa.Column('price', sa.Float, nullable=False),
        sa.Column('notional', sa.Float, nullable=False),
        sa.Column('status', sa.String, nullable=False, server_default='FILLED'),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.Column('order_type', sa.String, server_default='MARKET'),
        sa.Column('limit_price', sa.Float, nullable=True),
        sa.Column('stop_price', sa.Float, nullable=True),
        sa.Column('trail_amount', sa.Float, nullable=True),
        sa.Column('spread_bps', sa.Float, server_default='5'),
        sa.Column('slippage_bps', sa.Float, server_default='2'),
        sa.Column('reasoning_notes', sa.String, nullable=True),
        sa.Column('linked_alert_id', sa.Integer, nullable=True),
        sa.Column('updated_at', sa.String, nullable=True),
        sa.Column('filled_at', sa.String, nullable=True),
        sa.Column('cancelled_at', sa.String, nullable=True),
        sa.Column('instrument_type', sa.String, server_default='EQUITY'),
        sa.Column('expiry', sa.String, nullable=True),
        sa.Column('strike', sa.Float, nullable=True),
        sa.Column('option_type', sa.String, nullable=True),
        sa.Column('margin_required', sa.Float, server_default='0'),
        sa.Column('realized_pnl', sa.Float, server_default='0'),
        sa.Column('target_price', sa.Float, nullable=True),
        sa.Column('parent_order_id', sa.Integer, nullable=True),
        sa.Column('oco_group', sa.String, nullable=True),
        sa.Column('lot_size', sa.Float, server_default='1'),
    )
    op.create_index('idx_paper_orders_user', 'paper_orders', ['user_id', 'created_at'])

    op.create_table(
        'paper_trade_journal',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('order_id', sa.Integer, sa.ForeignKey('paper_orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_type', sa.String, nullable=False),
        sa.Column('notes', sa.String, nullable=True),
        sa.Column('payload_json', sa.String, nullable=False, server_default="'{}'"),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_index('idx_paper_journal_user_date', 'paper_trade_journal', ['user_id', 'created_at'])

    op.create_table(
        'paper_badges',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('badge_key', sa.String, nullable=False),
        sa.Column('title', sa.String, nullable=False),
        sa.Column('earned_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.UniqueConstraint('user_id', 'badge_key'),
    )
    op.create_table(
        'paper_challenge_entries',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('challenge_key', sa.String, nullable=False),
        sa.Column('choice', sa.String, nullable=False),
        sa.Column('score', sa.Float, nullable=True),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.UniqueConstraint('user_id', 'challenge_key'),
    )

    # ======================================================
    # LAYOUTS / ALERTS / CHARTS / AUDIT / SENTIMENT
    # ======================================================
    op.create_table(
        'user_workspace_layouts',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workspace', sa.String, nullable=False),
        sa.Column('layout_json', sa.String, nullable=False, server_default="'{}'"),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.PrimaryKeyConstraint('user_id', 'workspace'),
    )
    op.create_table(
        'price_alerts',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('condition', sa.String, nullable=False),
        sa.Column('threshold', sa.Float, nullable=False),
        sa.Column('training_window', sa.String, nullable=False, server_default='1mo'),
        sa.Column('confidence_level', sa.Float, nullable=False, server_default='0.80'),
        sa.Column('is_active', sa.Integer, nullable=False, server_default='1'),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.Column('last_triggered_at', sa.String, nullable=True),
        sa.Column('email_enabled', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_email_at', sa.String, nullable=True),
    )
    op.create_index('idx_price_alerts_user_symbol', 'price_alerts', ['user_id', 'symbol', 'is_active'])

    op.create_table(
        'chart_preferences',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('layout_json', sa.String, nullable=False, server_default="'{}'"),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.PrimaryKeyConstraint('user_id', 'symbol'),
    )
    op.create_table(
        'chart_drawings',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('drawing_type', sa.String, nullable=False),
        sa.Column('payload_json', sa.String, nullable=False),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
        sa.Column('updated_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String, nullable=False),
        sa.Column('entity_type', sa.String, nullable=True),
        sa.Column('entity_id', sa.String, nullable=True),
        sa.Column('details_json', sa.String, nullable=True),
        sa.Column('created_at', sa.String, server_default='CURRENT_TIMESTAMP'),
    )
    op.create_index('idx_audit_log_user_date', 'audit_log', ['user_id', 'created_at'])
    op.create_index('idx_audit_acknowledgment', 'audit_log', ['user_id', 'action', 'entity_id'])

    op.create_table(
        'sentiment_snapshots',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String, nullable=False),
        sa.Column('snapshot_at', sa.String, nullable=False),
        sa.Column('avg_score', sa.Float, nullable=True),
        sa.Column('headline_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('scored_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('source', sa.String, nullable=True),
        sa.UniqueConstraint('symbol', 'snapshot_at'),
    )


def downgrade() -> None:
    op.drop_table('sentiment_snapshots')
    op.drop_table('audit_log')
    op.drop_table('chart_drawings')
    op.drop_table('chart_preferences')
    op.drop_table('price_alerts')
    op.drop_table('user_workspace_layouts')
    op.drop_table('paper_challenge_entries')
    op.drop_table('paper_badges')
    op.drop_table('paper_trade_journal')
    op.drop_table('paper_orders')
    op.drop_table('paper_positions')
    op.drop_table('paper_accounts')
    op.drop_table('model_health')
    op.drop_table('settings')
    op.execute('DROP TRIGGER IF EXISTS prediction_history_outcome_immutable ON prediction_history')
    op.execute('DROP TRIGGER IF EXISTS prediction_history_forecast_immutable ON prediction_history')
    op.execute('DROP FUNCTION IF EXISTS prevent_outcome_mutation()')
    op.execute('DROP FUNCTION IF EXISTS prevent_forecast_mutation()')
    op.drop_table('prediction_history')
    op.drop_table('transactions')
    op.drop_table('watchlist')
    op.drop_table('portfolio')
    op.drop_table('symbols')
    op.drop_table('password_reset_tokens')
    op.drop_table('auth_login_attempts')
    op.drop_table('mfa_challenges')
    op.drop_table('mfa_recovery_codes')
    op.drop_table('user_mfa')
    op.drop_table('auth_sessions')
    op.drop_table('oauth_identities')
    op.drop_table('admin_audit_log')
    op.drop_table('users')
