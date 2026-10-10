import hashlib
import json
import math
import os
import sqlite3
import psycopg2
from datetime import datetime, timedelta, timezone
from typing import Any, cast, Optional


def postgres_selected():
    # Delay package initialization: services.db exports SQLite DAOs that import
    # this module's legacy helpers. The selector itself has one shared owner.
    from services.db.configuration import postgres_selected as selected
    return selected()

try:
    import sqlcipher3.dbapi2 as sqlcipher  # type: ignore[import-untyped]
    SQLCIPHER_AVAILABLE = True
except ImportError:
    sqlcipher = None
    SQLCIPHER_AVAILABLE = False

# Both drivers expose DB-API exceptions, but their class hierarchies are distinct.
DATABASE_ERRORS = (sqlite3.Error, psycopg2.Error) + ((sqlcipher.Error,) if sqlcipher else ())
INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError) + ((sqlcipher.IntegrityError,) if sqlcipher else ())
OPERATIONAL_ERRORS = (sqlite3.OperationalError, psycopg2.OperationalError) + ((sqlcipher.OperationalError,) if sqlcipher else ())


def database_row(cursor, values):
    """Use the row type belonging to the cursor's actual DB-API driver."""
    if isinstance(cursor, sqlite3.Cursor):
        return sqlite3.Row(cursor, values)
    return sqlcipher.Row(cursor, values)


# ==========================================================
# DATABASE CONFIGURATION
# ==========================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DATABASE_DIR = os.path.join(
    BASE_DIR,
    "database"
)

DATABASE = os.path.join(
    DATABASE_DIR,
    "stockpilot.db"
)

if os.getenv("STOCKPILOT_DATABASE_PATH"):
    DATABASE = os.path.abspath(os.environ["STOCKPILOT_DATABASE_PATH"])
    DATABASE_DIR = os.path.dirname(DATABASE)

# SQLCipher encryption settings
SQLCIPHER_KEY = os.getenv("STOCKPILOT_DB_ENCRYPTION_KEY")
SQLCIPHER_KEY_FILE = os.getenv("STOCKPILOT_DB_KEY_FILE")
SQLCIPHER_CIPHER = os.getenv("STOCKPILOT_DB_CIPHER", "aes-256-cbc")
SQLCIPHER_KDF_ITER = int(os.getenv("STOCKPILOT_DB_KDF_ITER", "256000"))
SQLCIPHER_PAGE_SIZE = int(os.getenv("STOCKPILOT_DB_PAGE_SIZE", "4096"))
SQLCIPHER_HMAC = os.getenv("STOCKPILOT_DB_HMAC", "hmac-sha512")


def _get_encryption_key() -> Optional[bytes]:
    """Get the encryption key from environment or key file."""
    if SQLCIPHER_KEY:
        return SQLCIPHER_KEY.encode('utf-8')
    if SQLCIPHER_KEY_FILE:
        try:
            with open(SQLCIPHER_KEY_FILE, 'rb') as f:
                key = f.read().strip()
        except OSError as exc:
            raise RuntimeError("Database encryption key file is unavailable.") from exc
        if not key:
            raise RuntimeError("Database encryption key file is empty.")
        return key
    return None


def _open_connection(database_path=None):
    """Open a database connection with optional SQLCipher encryption."""
    from services.db.configuration import postgres_selected
    path = os.path.abspath(os.fspath(database_path) if database_path is not None else DATABASE)
    if path == os.path.abspath(DATABASE) and postgres_selected():
        raise RuntimeError(
            "PostgreSQL application routing is not complete. Refusing to write the application SQLite database "
            "while PostgreSQL is selected; keep DB_BACKEND=sqlite until end-to-end adoption is verified."
        )
    os.makedirs(
        os.path.dirname(os.path.abspath(os.fspath(database_path) if database_path is not None else DATABASE)),
        exist_ok=True
    )

    encryption_key = _get_encryption_key()
    
    if encryption_key:
        if not SQLCIPHER_AVAILABLE:
            raise RuntimeError(
                "Database encryption requested (STOCKPILOT_DB_ENCRYPTION_KEY or "
                "STOCKPILOT_DB_KEY_FILE set) but sqlcipher3 is not installed. "
                "Install the required SQLCipher driver."
            )
        # Use SQLCipher for encrypted database
        conn = sqlcipher.connect(
            database_path if database_path is not None else DATABASE,
            timeout=30
        )
        
        # Configure SQLCipher PRAGMAs for encryption - key must be set first
        try:
            # PRAGMA key does not accept bound parameters; escape SQL literals.
            key_literal = encryption_key.decode('utf-8').replace("'", "''")
            conn.execute(f"PRAGMA key = '{key_literal}'")
            conn.execute(f"PRAGMA kdf_iter = {SQLCIPHER_KDF_ITER}")
            conn.execute(f"PRAGMA cipher_page_size = {SQLCIPHER_PAGE_SIZE}")
            if not conn.execute("PRAGMA cipher_version").fetchone():
                raise RuntimeError("Database driver does not provide SQLCipher encryption.")
            # Validate the key before returning a connection or touching journals.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            conn.close()
            raise
    else:
        # Standard SQLite connection
        conn = sqlite3.connect(
            database_path if database_path is not None else DATABASE,
            timeout=30
        )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")

    return conn


def get_connection():
    """Create the SQLite schema on first use, then return a connection."""

    if not os.path.exists(DATABASE):
        create_tables()
    return _open_connection()


# ==========================================================
# DATABASE TABLES
# ==========================================================

def create_tables():
    """
    Creates all required database tables.

    Existing tables and data are preserved.
    """

    if postgres_selected():
        _verify_postgres_schema()
        return

    conn = _open_connection()
    cursor = conn.cursor()

    # ======================================================
    # USERS
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL,

            email TEXT UNIQUE NOT NULL,

            password TEXT NOT NULL,

            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP

        )
        """
    )

    # Additive OAuth metadata migration. Existing password rows remain valid.
    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if "auth_provider" not in user_columns:
        cursor.execute(
            "ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'password'"
        )
    if "google_id" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
    if "last_login_at" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    # Additive role migration. Every existing and future account defaults to the
    # unprivileged "user" role; promotion happens only through the explicit
    # administrator bootstrap, never through registration.
    if "role" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "token_version" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    # Additive age-gate migration. The date of birth is captured at registration
    # so the server can enforce the 15+ account-age requirement for newly
    # created accounts; it is never returned by user-facing API responses.
    if "date_of_birth" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN date_of_birth TEXT")
    if "account_status" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'")
    cursor.execute("UPDATE users SET role='user' WHERE role IS NULL OR TRIM(role)=''")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            actor_email TEXT,
            action TEXT NOT NULL,
            target TEXT,
            outcome TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC)"
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id
        ON users(google_id)
        WHERE google_id IS NOT NULL
        """
    )

    # Provider identities are separate from the user row so additional OIDC
    # providers never rely on mutable email addresses as their primary key.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_identities(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL CHECK(provider IN ('google', 'apple')),
            provider_subject TEXT NOT NULL,
            verified_email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(provider, provider_subject),
            UNIQUE(user_id, provider)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_oauth_identities_user ON oauth_identities(user_id)"
    )

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS auth_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            jti_hash TEXT NOT NULL UNIQUE,
            token_version INTEGER NOT NULL,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )"""
    )
    cursor.execute("PRAGMA table_info(auth_sessions)")
    session_columns = {row[1] for row in cursor.fetchall()}
    if "device_label" not in session_columns:
        cursor.execute("ALTER TABLE auth_sessions ADD COLUMN device_label TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, revoked_at)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_mfa(
            user_id INTEGER PRIMARY KEY,
            encrypted_totp_secret TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
            last_totp_counter INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS mfa_recovery_codes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code_hash TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, code_hash)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mfa_recovery_user ON mfa_recovery_codes(user_id, used_at)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS mfa_challenges(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            jti_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            consumed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mfa_challenge_user ON mfa_challenges(user_id, consumed_at)"
    )

    # ======================================================
    # AUTHENTICATION SECURITY / PASSWORD RESET
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_login_attempts(
            identifier TEXT PRIMARY KEY,
            failed_count INTEGER NOT NULL DEFAULT 0,
            window_started TEXT,
            locked_until TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    # ======================================================
    # MARKET SYMBOL CATALOGUE
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT,
            country TEXT,
            sector TEXT
        )
        """
    )

    # ======================================================
    # PORTFOLIO
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            symbol TEXT NOT NULL,

            company TEXT,

            shares REAL NOT NULL,

            buy_price REAL NOT NULL,

            buy_date TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

        )
        """
    )

    # ======================================================
    # WATCHLIST
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            symbol TEXT NOT NULL,

            added_date TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

        )
        """
    )

    # ======================================================
    # TRANSACTIONS
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            symbol TEXT NOT NULL,

            transaction_type TEXT NOT NULL,

            shares REAL NOT NULL,

            price REAL NOT NULL,

            transaction_date TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

        )
        """
    )

    # ======================================================
    # PREDICTION HISTORY
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_history(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            symbol TEXT NOT NULL,

            linear_prediction REAL,

            decision_tree_prediction REAL,

            random_forest_prediction REAL,

            prediction_date TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

        )
        """
    )

    # ======================================================
    # SETTINGS
    # ======================================================

    cursor.execute("PRAGMA table_info(prediction_history)")
    prediction_columns = {row[1] for row in cursor.fetchall()}
    prediction_migrations = {
        "consensus_prediction": "REAL",
        "best_model": "TEXT",
        "model_version": "TEXT",
        "payload_json": "TEXT",
        "forecast_low": "REAL",
        "forecast_median": "REAL",
        "forecast_high": "REAL",
        "confidence_level": "REAL",
        "actual_price": "REAL",
        "coverage_hit": "INTEGER",
        "winkler_score": "REAL",
        "training_window": "TEXT",
        "timeframe": "TEXT",
        "created_at": "TEXT",
        "origin_timestamp": "TEXT",
        "target_timestamp": "TEXT",
        "provider": "TEXT",
        "data_timestamp": "TEXT",
        "feature_timestamp": "TEXT",
        "data_version": "TEXT",
        "schema_version": "TEXT",
        "forecast_evidence_json": "TEXT",
        "forecast_status": "TEXT",
        "snapshot_hash": "TEXT",
        "outcome_status": "TEXT",
        "outcome_evidence_json": "TEXT",
        "settlement_source": "TEXT",
        "settlement_provider": "TEXT",
        "settlement_data_timestamp": "TEXT",
        "settlement_is_stale": "INTEGER",
        "settlement_is_demo": "INTEGER",
        "official_outcome": "INTEGER DEFAULT 0",
        "settled_at": "TEXT",
        "result_hash": "TEXT",
        "horizon": "TEXT",
        "horizon_sessions": "INTEGER",
    }
    for column_name, column_type in prediction_migrations.items():
        if column_name not in prediction_columns:
            cursor.execute(
                f"ALTER TABLE prediction_history ADD COLUMN {column_name} {column_type}"
            )

    # Forecast snapshots and finalized outcomes are append-only. Settlement may
    # populate outcome columns once, but neither operation may rewrite evidence.
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prediction_history_forecast_immutable
        BEFORE UPDATE OF user_id, symbol, linear_prediction,
            decision_tree_prediction, random_forest_prediction, prediction_date,
            consensus_prediction, best_model, model_version, payload_json,
            forecast_low, forecast_median, forecast_high, confidence_level,
            training_window, timeframe, created_at, origin_timestamp,
            target_timestamp, provider, data_timestamp, feature_timestamp,
            data_version, schema_version, forecast_evidence_json,
            forecast_status, snapshot_hash
        ON prediction_history
        BEGIN
            SELECT RAISE(ABORT, 'forecast snapshot is immutable');
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS prediction_history_outcome_immutable
        BEFORE UPDATE OF actual_price, coverage_hit, winkler_score,
            outcome_status, outcome_evidence_json, settlement_source,
            settlement_provider, settlement_data_timestamp,
            settlement_is_stale, settlement_is_demo, official_outcome,
            settled_at, result_hash
        ON prediction_history
        WHEN OLD.actual_price IS NOT NULL
          OR OLD.outcome_status IN ('settled', 'unverifiable')
        BEGIN
            SELECT RAISE(ABORT, 'forecast outcome is immutable');
        END
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER UNIQUE NOT NULL,

            theme TEXT DEFAULT 'Dark',

            currency TEXT DEFAULT 'â‚¹',

            default_period TEXT DEFAULT '1y',

            FOREIGN KEY(user_id)
                REFERENCES users(id)
                ON DELETE CASCADE

        )
        """
    )

    # ======================================================
    # PAPER TRADING / ALERTS / AUDITABILITY
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_health(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            horizon INTEGER NOT NULL DEFAULT 1,
            settled_samples INTEGER NOT NULL DEFAULT 0,
            rolling_coverage REAL,
            nominal_coverage REAL,
            coverage_gap REAL,
            mase REAL,
            directional_accuracy REAL,
            naive_directional_accuracy REAL,
            model_mae REAL,
            naive_mae REAL,
            drift_detected INTEGER NOT NULL DEFAULT 0,
            drift_reasons_json TEXT,
            severity TEXT,
            action_taken TEXT,
            evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_health_symbol ON model_health(symbol, timeframe, horizon, evaluated_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_accounts(
            user_id INTEGER PRIMARY KEY,
            initial_balance REAL NOT NULL DEFAULT 1000000,
            cash_balance REAL NOT NULL DEFAULT 1000000,
            leaderboard_opt_in INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    # v6 privacy migration: leaderboard participation is opt-in, never implicit.
    cursor.execute("PRAGMA table_info(paper_accounts)")
    paper_account_columns = {row[1] for row in cursor.fetchall()}
    if "leaderboard_opt_in" not in paper_account_columns:
        cursor.execute("ALTER TABLE paper_accounts ADD COLUMN leaderboard_opt_in INTEGER NOT NULL DEFAULT 0")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_positions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            average_price REAL NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, symbol),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    # v6 derivative-position metadata (additive migration).
    cursor.execute("PRAGMA table_info(paper_positions)")
    paper_position_columns = {row[1] for row in cursor.fetchall()}
    if "lot_size" not in paper_position_columns:
        cursor.execute("ALTER TABLE paper_positions ADD COLUMN lot_size REAL NOT NULL DEFAULT 1")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            notional REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'FILLED',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    # v6 paper-trading order lifecycle columns (additive migration).
    cursor.execute("PRAGMA table_info(paper_orders)")
    paper_order_columns = {row[1] for row in cursor.fetchall()}
    v6_order_columns = {
        "order_type": "TEXT DEFAULT 'MARKET'",
        "limit_price": "REAL",
        "stop_price": "REAL",
        "trail_amount": "REAL",
        "spread_bps": "REAL DEFAULT 5",
        "slippage_bps": "REAL DEFAULT 2",
        "reasoning_notes": "TEXT",
        "linked_alert_id": "INTEGER",
        "updated_at": "TEXT",
        "filled_at": "TEXT",
        "cancelled_at": "TEXT",
        "instrument_type": "TEXT DEFAULT 'EQUITY'",
        "expiry": "TEXT",
        "strike": "REAL",
        "option_type": "TEXT",
        "margin_required": "REAL DEFAULT 0",
        "realized_pnl": "REAL DEFAULT 0",
        "target_price": "REAL",
        "parent_order_id": "INTEGER",
        "oco_group": "TEXT",
        "lot_size": "REAL DEFAULT 1"
    }
    for column_name, column_type in v6_order_columns.items():
        if column_name not in paper_order_columns:
            cursor.execute(f"ALTER TABLE paper_orders ADD COLUMN {column_name} {column_type}")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trade_journal(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id INTEGER,
            event_type TEXT NOT NULL,
            notes TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(order_id) REFERENCES paper_orders(id) ON DELETE SET NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_badges(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            badge_key TEXT NOT NULL,
            title TEXT NOT NULL,
            earned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, badge_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_challenge_entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            challenge_key TEXT NOT NULL,
            choice TEXT NOT NULL,
            score REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_workspace_layouts(
            user_id INTEGER NOT NULL,
            workspace TEXT NOT NULL,
            layout_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, workspace),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS price_alerts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            condition TEXT NOT NULL,
            threshold REAL NOT NULL,
            training_window TEXT NOT NULL DEFAULT '1mo',
            confidence_level REAL NOT NULL DEFAULT 0.80,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_triggered_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute("PRAGMA table_info(price_alerts)")
    price_alert_columns = {row[1] for row in cursor.fetchall()}
    if "training_window" not in price_alert_columns:
        cursor.execute("ALTER TABLE price_alerts ADD COLUMN training_window TEXT NOT NULL DEFAULT '1mo'")
    if "confidence_level" not in price_alert_columns:
        cursor.execute("ALTER TABLE price_alerts ADD COLUMN confidence_level REAL NOT NULL DEFAULT 0.80")
    if "email_enabled" not in price_alert_columns:
        cursor.execute("ALTER TABLE price_alerts ADD COLUMN email_enabled INTEGER NOT NULL DEFAULT 0")
    if "last_email_at" not in price_alert_columns:
        cursor.execute("ALTER TABLE price_alerts ADD COLUMN last_email_at TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chart_preferences(
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            layout_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, symbol),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chart_drawings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            drawing_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            details_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_acknowledgment ON audit_log(user_id, action, entity_id)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sentiment_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            snapshot_at TEXT NOT NULL,
            avg_score REAL,
            headline_count INTEGER NOT NULL DEFAULT 0,
            scored_count INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            UNIQUE(symbol, snapshot_at)
        )
        """
    )

    # ======================================================
    # INDEXES
    # ======================================================

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_symbols_symbol
        ON symbols(symbol)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_symbols_name
        ON symbols(name)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_portfolio_user_id
        ON portfolio(user_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_portfolio_symbol
        ON portfolio(symbol)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_watchlist_user_id
        ON watchlist(user_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_transactions_user_id
        ON transactions(user_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_prediction_user_id
        ON prediction_history(user_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prediction_outcome_due
        ON prediction_history(outcome_status, target_timestamp)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prediction_official_calibration
        ON prediction_history(user_id, official_outcome, outcome_status)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_password_reset_token
        ON password_reset_tokens(token_hash, expires_at)
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_symbol
        ON watchlist(user_id, symbol)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_orders_user
        ON paper_orders(user_id, created_at)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_price_alerts_user_symbol
        ON price_alerts(user_id, symbol, is_active)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_user_date
        ON audit_log(user_id, created_at)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_journal_user_date
        ON paper_trade_journal(user_id, created_at)
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sentiment_symbol_snapshot
        ON sentiment_snapshots(symbol, snapshot_at)
        """
    )

    conn.commit()
    conn.close()


# ==========================================================
# PORTFOLIO FUNCTIONS
# ==========================================================

def buy_stock(
    user_id,
    symbol,
    company,
    shares,
    buy_price
):
    """
    Adds a stock to the user's portfolio.
    """

    symbol = str(symbol).strip().upper()
    company = str(company).strip()

    shares = float(shares)
    buy_price = float(buy_price)

    if not symbol:

        raise ValueError(
            "Stock symbol is required."
        )

    if not math.isfinite(shares) or shares <= 0:

        raise ValueError(
            "Number of shares must be greater than zero."
        )

    if not math.isfinite(buy_price) or buy_price < 0:

        raise ValueError(
            "Buy price cannot be negative."
        )

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            factory.create_portfolio_dao().buy_holding(user_id, symbol, company, shares, buy_price)
        return

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO portfolio(

                user_id,

                symbol,

                company,

                shares,

                buy_price

            )

            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                symbol,
                company,
                shares,
                buy_price
            )
        )

        cursor.execute(
            """
            INSERT INTO transactions(

                user_id,

                symbol,

                transaction_type,

                shares,

                price

            )

            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                symbol,
                "BUY",
                shares,
                buy_price
            )
        )

        conn.commit()

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


def get_portfolio(user_id):
    """Returns all portfolio holdings for the selected user."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return [tuple(row[key] for key in ("id", "symbol", "company", "shares", "buy_price", "buy_date"))
                    for row in factory.create_portfolio_dao().get_holdings(user_id)]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT

            id,

            symbol,

            company,

            shares,

            buy_price,

            buy_date

        FROM portfolio

        WHERE user_id = ?

        ORDER BY

            buy_date DESC,

            id DESC
        """,
        (
            user_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def delete_stock(
    stock_id,
    user_id
):
    """Deletes a portfolio holding belonging to the selected user."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_portfolio_dao().delete_holding(stock_id, user_id)

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            DELETE FROM portfolio

            WHERE id = ?

            AND user_id = ?
            """,
            (
                stock_id,
                user_id
            )
        )

        conn.commit()

        return cursor.rowcount > 0

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


def update_stock(
    stock_id,
    user_id,
    shares,
    buy_price
):
    """
    Updates shares and buy price for an existing holding.
    """

    shares = float(shares)
    buy_price = float(buy_price)

    if not math.isfinite(shares) or shares <= 0:

        raise ValueError(
            "Number of shares must be greater than zero."
        )

    if not math.isfinite(buy_price) or buy_price < 0:

        raise ValueError(
            "Buy price cannot be negative."
        )

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_portfolio_dao().update_holding(stock_id, user_id, shares, buy_price)

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            UPDATE portfolio

            SET

                shares = ?,

                buy_price = ?

            WHERE id = ?

            AND user_id = ?
            """,
            (
                shares,
                buy_price,
                stock_id,
                user_id
            )
        )

        conn.commit()

        return cursor.rowcount > 0

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


def sell_stock(
    stock_id,
    user_id,
    shares,
    sell_price
):
    """Record a portfolio sale and update the selected holding atomically."""

    shares = float(shares)
    sell_price = float(sell_price)
    if not math.isfinite(shares) or shares <= 0:
        raise ValueError("Number of shares sold must be greater than zero.")
    if not math.isfinite(sell_price) or sell_price < 0:
        raise ValueError("Sale price cannot be negative.")

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_portfolio_dao().sell_holding(stock_id, user_id, shares, sell_price)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT symbol, shares FROM portfolio WHERE id = ? AND user_id = ?",
            (stock_id, user_id),
        )
        holding = cursor.fetchone()
        if holding is None:
            return False

        symbol, available_shares = holding
        available_shares = float(available_shares)
        if shares > available_shares + 1e-12:
            raise ValueError("Sale quantity cannot exceed the holding quantity.")

        cursor.execute(
            """
            INSERT INTO transactions(user_id, symbol, transaction_type, shares, price)
            VALUES (?, ?, 'SELL', ?, ?)
            """,
            (user_id, symbol, shares, sell_price),
        )

        remaining = available_shares - shares
        if remaining <= 1e-12:
            cursor.execute(
                "DELETE FROM portfolio WHERE id = ? AND user_id = ?",
                (stock_id, user_id),
            )
        else:
            cursor.execute(
                "UPDATE portfolio SET shares = ? WHERE id = ? AND user_id = ?",
                (remaining, stock_id, user_id),
            )

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ==========================================================
# WATCHLIST FUNCTIONS
# ==========================================================

def add_to_watchlist(
    user_id,
    symbol
):
    """
    Adds a symbol to the user's watchlist.
    """

    symbol = str(symbol).strip().upper()

    if not symbol:

        raise ValueError(
            "Stock symbol is required."
        )

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_watchlist_dao().add_symbol(user_id, symbol)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id

        FROM watchlist

        WHERE user_id = ?

        AND UPPER(symbol) = ?
        """,
        (
            user_id,
            symbol
        )
    )

    existing_stock = cursor.fetchone()

    if existing_stock:

        conn.close()

        return False

    cursor.execute(
        """
        INSERT INTO watchlist(

            user_id,

            symbol

        )

        VALUES (?, ?)
        """,
        (
            user_id,
            symbol
        )
    )

    conn.commit()
    conn.close()

    return True


def get_watchlist(user_id):
    """Returns all watchlist symbols for a user."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return [tuple(row[key] for key in ("id", "symbol", "added_date"))
                    for row in factory.create_watchlist_dao().get_watchlist(user_id)]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT

            id,

            symbol,

            added_date

        FROM watchlist

        WHERE user_id = ?

        ORDER BY added_date DESC
        """,
        (
            user_id,
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def remove_from_watchlist(
    watchlist_id,
    user_id
):
    """Removes a watchlist entry belonging to the user."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_watchlist_dao().remove_symbol(watchlist_id, user_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM watchlist

        WHERE id = ?

        AND user_id = ?
        """,
        (
            watchlist_id,
            user_id
        )
    )

    conn.commit()

    deleted = cursor.rowcount > 0

    conn.close()

    return deleted


# ==========================================================
# TRANSACTION FUNCTIONS
# ==========================================================

def get_transactions(
    user_id,
    limit=100
):
    """Returns recent portfolio transactions for a user."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return [tuple(row[key] for key in ("id", "symbol", "transaction_type", "shares", "price", "transaction_date"))
                    for row in factory.create_transaction_dao().get_transactions(user_id, limit)]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT

            id,

            symbol,

            transaction_type,

            shares,

            price,

            transaction_date

        FROM transactions

        WHERE user_id = ?

        ORDER BY transaction_date DESC, id DESC

        LIMIT ?
        """,
        (
            user_id,
            limit
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


# ==========================================================
# PREDICTION HISTORY FUNCTIONS
# ==========================================================

def _serialize_prediction_date(value):
    """Convert pandas/datetime values into SQLite-compatible text."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _canonical_hash(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_timestamp(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _forecast_target_timestamp(origin, timeframe):
    parsed = _parse_timestamp(origin)
    if parsed is None:
        return None
    increments = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1D": timedelta(days=1),
        "1W": timedelta(days=7),
    }
    target = parsed + increments.get(str(timeframe), timedelta(days=1))
    if str(timeframe) == "1D":
        while target.weekday() >= 5:
            target += timedelta(days=1)
    return target.isoformat()


def save_prediction(
    user_id,
    symbol,
    linear_prediction,
    decision_tree_prediction,
    random_forest_prediction,
    prediction_date=None,
    *,
    consensus_prediction=None,
    best_model=None,
    model_version=None,
    payload=None,
):
    """Persist a forecast while remaining compatible with the original schema API."""

    symbol = str(symbol).strip().upper()
    prediction_date = _serialize_prediction_date(prediction_date)
    payload_json = json.dumps(payload, default=str) if isinstance(payload, dict) else None

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            factory.create_prediction_dao().save_prediction(
                user_id, symbol, linear_prediction=linear_prediction,
                decision_tree_prediction=decision_tree_prediction, random_forest_prediction=random_forest_prediction,
                prediction_date=prediction_date, consensus_prediction=consensus_prediction,
                best_model=best_model, model_version=model_version, payload=payload,
            )
        return

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO prediction_history(
            user_id, symbol, linear_prediction,
            decision_tree_prediction, random_forest_prediction,
            prediction_date, consensus_prediction, best_model,
            model_version, payload_json
        )
        VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?)
        """,
        (
            user_id,
            symbol,
            linear_prediction,
            decision_tree_prediction,
            random_forest_prediction,
            prediction_date,
            consensus_prediction,
            best_model,
            model_version,
            payload_json,
        ),
    )
    conn.commit()
    conn.close()


def save_prediction_once(
    user_id,
    symbol,
    linear_prediction,
    decision_tree_prediction,
    random_forest_prediction,
    prediction_date,
    **extra,
):
    """Store at most one forecast per user, symbol, and market date."""

    symbol = str(symbol).strip().upper()
    prediction_date = _serialize_prediction_date(prediction_date)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM prediction_history
        WHERE user_id = ? AND UPPER(symbol) = ?
          AND DATE(prediction_date) = DATE(?)
        LIMIT 1
        """,
        (user_id, symbol, prediction_date),
    )
    existing = cursor.fetchone()
    conn.close()
    if existing:
        return False

    save_prediction(
        user_id,
        symbol,
        linear_prediction,
        decision_tree_prediction,
        random_forest_prediction,
        prediction_date=prediction_date,
        **extra,
    )
    return True


def get_prediction_history(user_id, limit=50):
    """Return recent forecasts in the original tuple layout for UI compatibility."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return [tuple(row[key] for key in ("id", "symbol", "linear", "dt", "rf", "date"))
                    for row in factory.create_prediction_dao().get_prediction_history(user_id, limit)]

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, symbol, linear_prediction, decision_tree_prediction,
               random_forest_prediction, prediction_date
        FROM prediction_history
        WHERE user_id = ?
        ORDER BY prediction_date DESC
        LIMIT ?
        """,
        (user_id, max(1, min(int(limit), 500))),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_prediction_details(user_id, symbol=None, limit=50):
    """Return expanded JSON-aware prediction history for professional reporting."""

    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return list(factory.create_prediction_dao().get_prediction_details(user_id, symbol, limit))

    conn = get_connection()
    conn.row_factory = database_row
    cursor = conn.cursor()
    params = [user_id]
    where = "WHERE user_id = ?"
    if symbol:
        where += " AND UPPER(symbol) = ?"
        params.append(str(symbol).strip().upper())
    params.append(max(1, min(int(limit), 500)))
    query = """
        SELECT id, symbol, prediction_date, consensus_prediction, best_model,
               model_version, payload_json, forecast_low, forecast_median, forecast_high,
               confidence_level, training_window, timeframe, actual_price, coverage_hit, winkler_score,
               origin_timestamp, target_timestamp, provider, data_timestamp, feature_timestamp,
               data_version, schema_version, forecast_evidence_json, forecast_status, snapshot_hash,
               outcome_status, outcome_evidence_json, settlement_source, settlement_provider,
               settlement_data_timestamp, settlement_is_stale, settlement_is_demo,
               official_outcome, settled_at, result_hash, horizon, horizon_sessions
        FROM prediction_history
        WHERE user_id = ?
        ORDER BY prediction_date DESC
        LIMIT ?
        """
    if symbol:
        query = query.replace("WHERE user_id = ?", "WHERE user_id = ? AND UPPER(symbol) = ?")
    cursor.execute(query, tuple(params))
    records = []
    for row in cursor.fetchall():
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        for source, target in (
            ("forecast_evidence_json", "forecast_evidence"),
            ("outcome_evidence_json", "outcome_evidence"),
        ):
            try:
                item[target] = json.loads(item.pop(source) or "{}")
            except json.JSONDecodeError:
                item[target] = {}
        records.append(item)
    conn.close()
    return records


def database_health_check():
    """Return a compact, non-sensitive database health report."""

    if postgres_selected():
        try:
            _verify_postgres_schema()
            return {"status": "Operational", "integrity": "schema_verified", "backend": "postgresql",
                    "encryption": "server-managed; at-rest state not inspected", "sqlcipher_available": SQLCIPHER_AVAILABLE}
        except Exception:
            return {"status": "Unavailable", "integrity": "unknown", "backend": "postgresql",
                    "encryption": "unknown", "sqlcipher_available": SQLCIPHER_AVAILABLE}

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        
        # Check encryption status
        encryption_status = "unencrypted"
        if SQLCIPHER_AVAILABLE:
            encryption_key = _get_encryption_key()
            if encryption_key:
                try:
                    cursor.execute("PRAGMA cipher_version")
                    cipher_version = cursor.fetchone()
                    if cipher_version:
                        encryption_status = f"encrypted (sqlcipher {cipher_version[0]})"
                except Exception:
                    encryption_status = "encrypted (sqlcipher)"
        
        conn.close()
        return {
            "status": "Operational" if integrity == "ok" else "Degraded",
            "integrity": "ok" if integrity == "ok" else "failed",
            "encryption": encryption_status,
            "sqlcipher_available": SQLCIPHER_AVAILABLE,
        }
    except sqlite3.Error:
        return {"status": "Unavailable", "integrity": "unknown", "encryption": "unknown", "sqlcipher_available": SQLCIPHER_AVAILABLE}


def _verify_postgres_schema():
    """Read-only application preflight. DDL belongs to direct-URL Alembic runs."""
    from services.db.factory import dao_session
    from services.db.base import APPLICATION_TABLES
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(os.path.join(BASE_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BASE_DIR, "alembic"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    with dao_session() as factory:
        db = factory.db
        revision = db.fetchone("SELECT version_num FROM alembic_version")
        if not revision or revision["version_num"] != expected:
            raise RuntimeError("PostgreSQL migrations are not at the required head")
        tables = {row["table_name"] for row in db.fetchall("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
        if not APPLICATION_TABLES <= tables:
            raise RuntimeError("PostgreSQL application schema is incomplete")


def encrypt_database(encryption_key: str) -> dict[str, Any]:
    """Offline compatibility entry point for the preservation-verified migration."""
    if not SQLCIPHER_AVAILABLE:
        return {"success": False, "error": "sqlcipher3 not installed"}
    if not os.path.exists(DATABASE):
        return {"success": False, "error": "Source database does not exist"}
    try:
        from pathlib import Path
        from scripts.migrate_encryption import migrate_in_place
        result = migrate_in_place(Path(DATABASE), encryption_key)
        return {"success": bool(result["verified"]), "backup_path": result.get("rollback_copy"),
                "message": "Database encryption preservation verified", **result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def verify_encryption() -> dict[str, Any]:
    """Verify that the database is properly encrypted."""
    if not SQLCIPHER_AVAILABLE:
        return {"encrypted": False, "error": "SQLCipher not available"}
    
    encryption_key = _get_encryption_key()
    if not encryption_key:
        return {"encrypted": False, "reason": "No encryption key configured"}
    
    try:
        from pathlib import Path
        from services.encrypted_storage import _cipher_connection
        conn = _cipher_connection(Path(DATABASE), encryption_key.decode('utf-8'))
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.execute("PRAGMA cipher_version")
        version = cursor.fetchone()
        conn.close()
        return {
            "encrypted": True,
            "cipher_version": version[0] if version else "unknown",
            "cipher": SQLCIPHER_CIPHER,
            "kdf_iter": SQLCIPHER_KDF_ITER,
            "page_size": SQLCIPHER_PAGE_SIZE,
        }
    except Exception as e:
        return {"encrypted": False, "error": str(e)}


# ==========================================================
# AUDIT LOG / USER DATA MANAGEMENT
# ==========================================================

def record_audit_event(
    user_id,
    action,
    entity_type=None,
    entity_id=None,
    details=None,
):
    """Persist a security/portfolio action without logging secrets."""

    action = str(action or "").strip()[:120]
    if not action:
        raise ValueError("Audit action is required.")
    payload = json.dumps(details or {}, ensure_ascii=True, default=str)
    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_audit_dao().record_event(
                int(user_id) if user_id is not None else None, action,
                str(entity_type or "")[:80] or None, str(entity_id or "")[:120] or None,
                json.loads(payload),
            )
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO audit_log(user_id, action, entity_type, entity_id, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(user_id) if user_id is not None else None,
            action,
            str(entity_type or "")[:80] or None,
            str(entity_id or "")[:120] or None,
            payload,
        ),
    )
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id


def get_audit_events(user_id, limit=100):
    limit = max(1, min(int(limit), 1000))
    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return list(factory.create_audit_dao().get_events(int(user_id), limit))
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, action, entity_type, entity_id, details_json, created_at
        FROM audit_log
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(user_id), limit),
    ).fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "action": row[1],
            "entity_type": row[2],
            "entity_id": row[3],
            "details": json.loads(row[4] or "{}"),
            "created_at": row[5],
        }
        for row in rows
    ]


def delete_user_data(user_id):
    """Purge all discoverable user-owned records and the account atomically."""

    user_id = int(user_id)
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            conn.rollback()
            return False
        email = str(user[0]).strip().lower()

        # Forward-test events are linked through a user-owned parent rather than
        # carrying user_id themselves, so remove them before the parent rows.
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not str(row[0]).startswith("sqlite_")
        }
        if {"forward_tests", "forward_test_events"} <= tables:
            conn.execute(
                "DELETE FROM forward_test_events WHERE forward_test_id IN "
                "(SELECT id FROM forward_tests WHERE user_id = ?)",
                (user_id,),
            )

        # Feature modules create some tables lazily and several legacy tables
        # predate foreign keys. Discover ownership columns so future user-owned
        # tables are included without relying solely on ON DELETE CASCADE.
        for table in sorted(tables - {"users"}):
            columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
            ownership_column = "user_id" if "user_id" in columns else "actor_user_id" if "actor_user_id" in columns else None
            if ownership_column:
                # Identifiers come exclusively from sqlite_master/PRAGMA metadata.
                conn.execute(f'DELETE FROM "{table}" WHERE "{ownership_column}" = ?', (user_id,))  # nosec B608

        # These security tables identify a subject by normalized email rather
        # than user_id. They still contain account-linked personal data.
        email_deletes = {
            "auth_login_attempts": ("identifier",),
            "admin_step_up_failures": ("actor_email",),
            "admin_audit_log": ("actor_email",),
        }
        for table, (column,) in email_deletes.items():
            if table in tables:
                # Both identifier values are constants above; only email is user data.
                conn.execute(f'DELETE FROM "{table}" WHERE LOWER("{column}") = ?', (email,))  # nosec B608

        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ==========================================================
# V6 RANGE FORECAST HISTORY
# ==========================================================

def range_forecast_values(user_id, symbol, forecast_payload):
    """Canonical immutable ledger values shared by both repository backends."""
    payload = cast(dict[str, Any], forecast_payload or {})
    forecast = cast(dict[str, Any], payload.get("forecast", {}))
    training = cast(dict[str, Any], payload.get("training", {}))
    context = cast(dict[str, Any], payload.get("context")) if isinstance(payload.get("context"), dict) else {}
    symbol = str(symbol).upper()
    timeframe = training.get("timeframe") or context.get("timeframe")
    origin_timestamp = _serialize_prediction_date(payload.get("generated_at")) or datetime.now(timezone.utc).isoformat()
    data_timestamp = _serialize_prediction_date(payload.get("data_timestamp") or context.get("as_of"))
    feature_timestamp = _serialize_prediction_date(payload.get("feature_timestamp"))
    target_timestamp = _serialize_prediction_date(payload.get("target_timestamp")) or _forecast_target_timestamp(
        feature_timestamp or data_timestamp or origin_timestamp,
        timeframe,
    )
    provider = str(context.get("provider") or payload.get("provider") or "unknown")
    model_version = str(payload.get("model_version") or "6.1")
    schema_version = str(payload.get("schema_version") or "forecast-outcome-ledger-v1")
    horizon = payload.get("horizon") or {}
    try:
        horizon_sessions = int(horizon.get("sessions") or horizon.get("bars") or 1)
    except (TypeError, ValueError):
        horizon_sessions = 1
    horizon_value = str(max(1, horizon_sessions))
    evidence = cast(dict[str, Any], payload.get("evidence")) if isinstance(payload.get("evidence"), dict) else {}
    forecast_status = str(payload.get("forecast_status") or "available")
    data_descriptor = {
        "provider": provider,
        "data_timestamp": data_timestamp,
        "feature_timestamp": feature_timestamp,
        "timeframe": timeframe,
        "training_window": training.get("training_window"),
        "rows": training.get("rows"),
    }
    data_version = str(payload.get("data_version") or _canonical_hash(data_descriptor))
    snapshot = {
        "symbol": symbol,
        "forecast": forecast,
        "current_price": payload.get("current_price"),
        "origin_timestamp": origin_timestamp,
        "target_timestamp": target_timestamp,
        "horizon": horizon_value,
        "provider": provider,
        "data_timestamp": data_timestamp,
        "feature_timestamp": feature_timestamp,
        "model_version": model_version,
        "data_version": data_version,
        "schema_version": schema_version,
        "evidence": evidence,
        "forecast_status": forecast_status,
        "training": training,
    }
    snapshot_hash = _canonical_hash(snapshot)
    return (
        int(user_id), symbol, forecast.get("median"), model_version,
        json.dumps(payload, ensure_ascii=True, default=str), forecast.get("low"),
        forecast.get("median"), forecast.get("high"), forecast.get("confidence_level"),
        training.get("training_window"), timeframe, origin_timestamp, origin_timestamp,
        origin_timestamp, target_timestamp, provider, data_timestamp, feature_timestamp,
        data_version, schema_version, json.dumps(evidence, ensure_ascii=True, default=str),
        forecast_status, snapshot_hash,
        "pending" if forecast_status in {"model_supported", "baseline_only", "low_evidence", "available"} else "excluded",
        0, horizon_value, int(horizon_sessions),
    )


def save_range_forecast(user_id, symbol, forecast_payload):
    """Persist one immutable canonical forecast snapshot and its provenance."""
    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return factory.create_prediction_dao().save_range_forecast(user_id, symbol, forecast_payload)
    create_tables()
    values = range_forecast_values(user_id, symbol, forecast_payload)
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO prediction_history(
            user_id,symbol,consensus_prediction,model_version,payload_json,
            forecast_low,forecast_median,forecast_high,confidence_level,training_window,timeframe,
            prediction_date,created_at,origin_timestamp,target_timestamp,provider,data_timestamp,
            feature_timestamp,data_version,schema_version,forecast_evidence_json,forecast_status,
            snapshot_hash,outcome_status,official_outcome,horizon,horizon_sessions
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    )
    row_id = int(cursor.lastrowid)
    conn.commit(); conn.close()
    return row_id

def settle_range_forecast(prediction_id, actual_price):
    """Legacy internal scorer; manual outcomes are never official calibration data."""
    return _finalize_range_forecast(
        prediction_id,
        actual_price,
        settlement_source="manual",
        provider="manual",
        data_timestamp=None,
        is_stale=False,
        is_demo=False,
        evidence={"reason": "legacy_manual_settlement", "official": False},
        official=False,
    )


def settle_range_forecast_automatically(
    prediction_id,
    actual_price,
    *,
    provider,
    data_timestamp,
    evidence=None,
):
    """Finalize a due forecast once from authoritative market data."""
    return _finalize_range_forecast(
        prediction_id,
        actual_price,
        settlement_source="automatic",
        provider=provider,
        data_timestamp=data_timestamp,
        is_stale=False,
        is_demo=False,
        evidence=evidence or {},
        official=True,
    )


def _finalize_range_forecast(
    prediction_id,
    actual_price,
    *,
    settlement_source,
    provider,
    data_timestamp,
    is_stale,
    is_demo,
    evidence,
    official,
):
    actual = float(actual_price)
    if not math.isfinite(actual) or actual <= 0:
        raise ValueError("Actual price must be positive.")
    conn = get_connection()
    row = conn.execute(
        """SELECT forecast_low,forecast_high,confidence_level,snapshot_hash,outcome_status,actual_price
           FROM prediction_history WHERE id=?""",
        (int(prediction_id),),
    ).fetchone()
    if not row:
        conn.close(); return False
    if row[4] in {"settled", "unverifiable"} or row[5] is not None:
        conn.close(); return False
    low, high, confidence = map(float, row[:3])
    hit = int(low <= actual <= high)
    alpha = max(1e-6, 1.0 - confidence)
    width = high - low
    winkler = width + ((2/alpha)*(low-actual) if actual < low else 0.0) + ((2/alpha)*(actual-high) if actual > high else 0.0)
    settled_at = datetime.now(timezone.utc).isoformat()
    outcome_evidence = {
        **(evidence or {}),
        "settlement_source": settlement_source,
        "provider": provider,
        "data_timestamp": _serialize_prediction_date(data_timestamp),
        "official": bool(official),
    }
    result_hash = _canonical_hash({
        "snapshot_hash": row[3],
        "actual_price": actual,
        "coverage_hit": hit,
        "winkler_score": winkler,
        "settlement_source": settlement_source,
        "provider": provider,
        "data_timestamp": _serialize_prediction_date(data_timestamp),
    })
    cursor = conn.execute(
        """UPDATE prediction_history
           SET actual_price=?,coverage_hit=?,winkler_score=?,outcome_status='settled',
               outcome_evidence_json=?,settlement_source=?,settlement_provider=?,
               settlement_data_timestamp=?,settlement_is_stale=?,settlement_is_demo=?,
               official_outcome=?,settled_at=?,result_hash=?
           WHERE id=? AND COALESCE(outcome_status,'pending')='pending' AND actual_price IS NULL""",
        (
            actual, hit, winkler, json.dumps(outcome_evidence, ensure_ascii=True, default=str),
            settlement_source, str(provider), _serialize_prediction_date(data_timestamp),
            int(bool(is_stale)), int(bool(is_demo)), int(bool(official)), settled_at,
            result_hash, int(prediction_id),
        ),
    )
    changed = cursor.rowcount == 1
    conn.commit(); conn.close(); return changed


def mark_range_forecast_unverifiable(prediction_id, *, reason, evidence=None):
    """Finalize a due forecast without a fabricated price when evidence is unusable."""
    evidence = evidence or {}
    conn = get_connection()
    row = conn.execute(
        "SELECT snapshot_hash,outcome_status,actual_price FROM prediction_history WHERE id=?",
        (int(prediction_id),),
    ).fetchone()
    if not row or row[1] in {"settled", "unverifiable"} or row[2] is not None:
        conn.close(); return False
    settled_at = datetime.now(timezone.utc).isoformat()
    provider = str(evidence.get("provider") or "unknown")
    data_timestamp = _serialize_prediction_date(evidence.get("data_timestamp"))
    is_stale = str(reason) == "stale_market_data"
    is_demo = str(reason) == "demo_market_data"
    outcome_evidence = {**evidence, "reason": str(reason), "official": False}
    result_hash = _canonical_hash({
        "snapshot_hash": row[0],
        "outcome_status": "unverifiable",
        "evidence": outcome_evidence,
    })
    cursor = conn.execute(
        """UPDATE prediction_history
           SET outcome_status='unverifiable',outcome_evidence_json=?,settlement_source='automatic',
               settlement_provider=?,settlement_data_timestamp=?,settlement_is_stale=?,
               settlement_is_demo=?,official_outcome=0,settled_at=?,result_hash=?
           WHERE id=? AND COALESCE(outcome_status,'pending')='pending' AND actual_price IS NULL""",
        (
            json.dumps(outcome_evidence, ensure_ascii=True, default=str), provider, data_timestamp,
            int(is_stale), int(is_demo), settled_at, result_hash, int(prediction_id),
        ),
    )
    changed = cursor.rowcount == 1
    conn.commit(); conn.close(); return changed


def get_pending_range_forecasts(limit=1000, user_id=None):
    """Return unsettled ledger rows for the automatic settlement worker."""
    conn = get_connection()
    conn.row_factory = database_row
    rows = conn.execute(
        """SELECT id,user_id,symbol,forecast_low,forecast_high,confidence_level,
                  training_window,timeframe,origin_timestamp,target_timestamp,snapshot_hash,
                  horizon,horizon_sessions
           FROM prediction_history
            WHERE forecast_low IS NOT NULL AND forecast_high IS NOT NULL
              AND actual_price IS NULL AND COALESCE(outcome_status,'pending')='pending'
               AND forecast_status IN ('model_supported','baseline_only','low_evidence','available')
               AND (? IS NULL OR user_id=?)
            ORDER BY target_timestamp ASC,id ASC LIMIT ?""",
        (user_id, user_id, max(1, min(int(limit), 10000))),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_settled_range_forecasts(user_id, limit=500):
    """Return only authoritative automatic outcomes for official calibration."""
    if postgres_selected():
        from services.db.factory import dao_session
        with dao_session() as factory:
            return list(factory.create_prediction_dao().get_settled_forecasts(user_id, limit))
    conn = get_connection()
    rows = conn.execute(
        """SELECT id,symbol,forecast_low,forecast_median,forecast_high,confidence_level,
                  training_window,timeframe,actual_price,coverage_hit,winkler_score,created_at,
                  horizon,horizon_sessions
           FROM prediction_history
           WHERE user_id=? AND actual_price IS NOT NULL AND coverage_hit IS NOT NULL AND winkler_score IS NOT NULL
             AND outcome_status='settled' AND settlement_source='automatic'
              AND official_outcome=1 AND COALESCE(settlement_is_stale,0)=0
              AND COALESCE(settlement_is_demo,0)=0
              AND forecast_status IN ('model_supported','baseline_only','low_evidence','available')
           ORDER BY id DESC LIMIT ?""",
        (int(user_id), max(1, min(int(limit), 5000))),
    ).fetchall()
    conn.close()
    keys = ["id","symbol","forecast_low","forecast_median","forecast_high","confidence_level","training_window","timeframe","actual_price","coverage_hit","winkler_score","created_at","horizon","horizon_sessions"]
    return [dict(zip(keys, row)) for row in rows]


def get_forecast_outcome_counts(user_id):
    """Expose official calibration numerator/denominator and exclusion counts."""
    conn = get_connection()
    row = conn.execute(
        """SELECT
               COUNT(*),
               SUM(CASE WHEN official_outcome=1 AND outcome_status='settled'
                             AND settlement_source='automatic' AND COALESCE(settlement_is_stale,0)=0
                             AND COALESCE(settlement_is_demo,0)=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN official_outcome=1 AND outcome_status='settled'
                             AND settlement_source='automatic' AND COALESCE(settlement_is_stale,0)=0
                             AND COALESCE(settlement_is_demo,0)=0 THEN coverage_hit ELSE 0 END),
               SUM(CASE WHEN outcome_status='pending' OR outcome_status IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome_status='unverifiable' THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome_status='settled' AND COALESCE(official_outcome,0)=0 THEN 1 ELSE 0 END)
           FROM prediction_history WHERE user_id=? AND forecast_low IS NOT NULL""",
        (int(user_id),),
    ).fetchone()
    conn.close()
    total, denominator, numerator, pending, unverifiable, excluded = row
    return {
        "total_forecasts": int(total or 0),
        "coverage_numerator": int(numerator or 0),
        "calibration_denominator": int(denominator or 0),
        "pending": int(pending or 0),
        "unverifiable": int(unverifiable or 0),
        "excluded_non_official": int(excluded or 0),
    }


def get_settled_rows_for_quality(limit=3000):
    """Authoritative settled outcomes across all users for model-health scoring.

    The drift monitor intentionally looks at every authoritative automatic
    settlement, not one user's ledger, because model quality is a property of
    the model, not of who asked for the range. Row order and timestamps make
    per-group rolling statistics honest.
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT id,symbol,forecast_low,forecast_median,forecast_high,confidence_level,
                  training_window,timeframe,actual_price,coverage_hit,winkler_score,
                   origin_timestamp,target_timestamp,created_at,horizon,horizon_sessions,payload_json
           FROM prediction_history
           WHERE actual_price IS NOT NULL AND coverage_hit IS NOT NULL AND winkler_score IS NOT NULL
             AND outcome_status='settled' AND settlement_source='automatic'
             AND official_outcome=1 AND COALESCE(settlement_is_stale,0)=0
             AND COALESCE(settlement_is_demo,0)=0
             AND forecast_status IN ('model_supported','baseline_only','low_evidence','available')
            ORDER BY target_timestamp DESC, created_at DESC, id DESC LIMIT ?""",
        (max(1, min(int(limit), 10000)),),
    ).fetchall()
    conn.close()
    columns = [
        "id", "symbol", "forecast_low", "forecast_median", "forecast_high", "confidence_level",
        "training_window", "timeframe", "actual_price", "coverage_hit", "winkler_score",
        "origin_timestamp", "target_timestamp", "created_at", "horizon", "horizon_sessions", "payload_json",
    ]
    return [dict(zip(columns, row)) for row in rows]


def record_model_health(
    *,
    symbol,
    timeframe,
    horizon=1,
    settled_samples=0,
    rolling_coverage=None,
    nominal_coverage=None,
    coverage_gap=None,
    mase=None,
    directional_accuracy=None,
    naive_directional_accuracy=None,
    model_mae=None,
    naive_mae=None,
    drift_detected=False,
    drift_reasons=None,
    severity=None,
    action_taken=None,
):
    """Append one immutable model-health observation for the audit trail."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO model_health(
               symbol,timeframe,horizon,settled_samples,rolling_coverage,nominal_coverage,
               coverage_gap,mase,directional_accuracy,naive_directional_accuracy,
               model_mae,naive_mae,drift_detected,drift_reasons_json,severity,action_taken)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(symbol).strip().upper(),
            str(timeframe or ""),
            max(1, int(horizon)),
            int(settled_samples or 0),
            rolling_coverage,
            nominal_coverage,
            coverage_gap,
            mase,
            directional_accuracy,
            naive_directional_accuracy,
            model_mae,
            naive_mae,
            1 if drift_detected else 0,
            json.dumps(list(drift_reasons or []), ensure_ascii=False),
            severity,
            action_taken,
        ),
    )
    health_id = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return health_id


def latest_model_health(limit=200):
    """Most recent model-health observations in evaluation order."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id,symbol,timeframe,horizon,settled_samples,rolling_coverage,nominal_coverage,
                  coverage_gap,mase,directional_accuracy,naive_directional_accuracy,
                  model_mae,naive_mae,drift_detected,drift_reasons_json,severity,action_taken,evaluated_at
           FROM model_health ORDER BY id DESC LIMIT ?""",
        (max(1, min(int(limit), 5000)),),
    ).fetchall()
    conn.close()
    columns = [
        "id", "symbol", "timeframe", "horizon", "settled_samples", "rolling_coverage", "nominal_coverage",
        "coverage_gap", "mase", "directional_accuracy", "naive_directional_accuracy",
        "model_mae", "naive_mae", "drift_detected", "drift_reasons_json", "severity", "action_taken", "evaluated_at",
    ]
    return [dict(zip(columns, row)) for row in rows]


def latest_model_health_by_group(limit=500):
    """Newest health record per (symbol,timeframe,horizon) group."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT mh.* FROM model_health mh
           JOIN (SELECT symbol,timeframe,horizon,MAX(id) AS max_id
                 FROM model_health GROUP BY symbol,timeframe,horizon) latest
             ON mh.id = latest.max_id
           ORDER BY mh.symbol ASC, mh.timeframe ASC, mh.horizon ASC LIMIT ?""",
        (max(1, min(int(limit), 5000)),),
    ).fetchall()
    conn.close()
    columns = [
        "id", "symbol", "timeframe", "horizon", "settled_samples", "rolling_coverage", "nominal_coverage",
        "coverage_gap", "mase", "directional_accuracy", "naive_directional_accuracy",
        "model_mae", "naive_mae", "drift_detected", "drift_reasons_json", "severity", "action_taken", "evaluated_at",
    ]
    return [dict(zip(columns, row)) for row in rows]
