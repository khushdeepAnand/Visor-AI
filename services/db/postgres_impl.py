"""PostgreSQL implementation of DAO interfaces."""
from __future__ import annotations
from .update_fields import USER_FIELDS, SETTINGS_FIELDS, validate_fields

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .base import (
    DatabaseInterface,
    UserDAO,
    PortfolioDAO,
    WatchlistDAO,
    TransactionDAO,
    PredictionDAO,
    PaperTradingDAO,
    AuditDAO,
    SettingsDAO,
    DAOFactory,
)


class PostgresDatabase(DatabaseInterface):
    """PostgreSQL database connection wrapper with connection pooling."""
    
    def __init__(self, dsn: str, pool_size: int = 10):
        self._pool = ThreadedConnectionPool(1, pool_size, dsn)
        self._conn: Optional[psycopg2.extensions.connection] = None
    
    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = self._pool.getconn()
        return self._conn
    
    def get_connection(self) -> psycopg2.extensions.connection:
        return self._get_conn()
    
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> RealDictCursor:
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params)
        return cursor
    
    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[dict[str, Any]]:
        cursor = self.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> Sequence[dict[str, Any]]:
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def commit(self) -> None:
        self._get_conn().commit()
    
    def rollback(self) -> None:
        self._get_conn().rollback()
    
    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._pool.putconn(self._conn)
            self._conn = None


def _inserted_id(cursor: RealDictCursor) -> int:
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Database did not return the inserted row ID.")
    return int(row["id"])


class PostgresUserDAO(UserDAO):
    """PostgreSQL user data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def create_user(self, name: str, email: str, password: str, auth_provider: str = "password",
                    google_id: Optional[str] = None, date_of_birth: Optional[str] = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.execute(
            """INSERT INTO users (name, email, password, auth_provider, google_id, date_of_birth, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (name, email, password, auth_provider, google_id, date_of_birth, datetime.now(timezone.utc))
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM users WHERE email = %s", (email,)
        )
    
    def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        )
    
    def update_user(self, user_id: int, **kwargs: Any) -> bool:
        validate_fields(kwargs, USER_FIELDS)
        if not kwargs:
            return False
        fields = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        cursor = self.db.execute(
            f"UPDATE users SET {fields} WHERE id = %s", tuple(values)  # nosec B608 - identifiers allowlisted above, values bound
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def delete_user(self, user_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        self.db.commit()
        return cursor.rowcount > 0
    
    def set_mfa_secret(self, user_id: int, encrypted_secret: str) -> None:
        now = datetime.now(timezone.utc)
        self.db.execute(
            """INSERT INTO user_mfa (user_id, encrypted_totp_secret, enabled, created_at, updated_at)
               VALUES (%s, %s, 1, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET encrypted_totp_secret = EXCLUDED.encrypted_totp_secret,
               enabled = 1, updated_at = EXCLUDED.updated_at""",
            (user_id, encrypted_secret, datetime.now(timezone.utc), datetime.now(timezone.utc))
        )
        self.db.commit()
    
    def get_mfa_secret(self, user_id: int) -> Optional[str]:
        row = self.db.fetchone(
            "SELECT encrypted_totp_secret FROM user_mfa WHERE user_id = %s AND enabled = 1",
            (user_id,)
        )
        return row["encrypted_totp_secret"] if row else None
    
    def enable_mfa(self, user_id: int) -> None:
        self.db.execute(
            "UPDATE user_mfa SET enabled = 1, updated_at = %s WHERE user_id = %s",
            (datetime.now(timezone.utc), user_id)
        )
        self.db.commit()
    
    def disable_mfa(self, user_id: int) -> None:
        self.db.execute(
            "UPDATE user_mfa SET enabled = 0, updated_at = %s WHERE user_id = %s",
            (datetime.now(timezone.utc), user_id)
        )
        self.db.commit()
    
    def add_recovery_code(self, user_id: int, code_hash: str) -> None:
        self.db.execute(
            "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, code_hash)
        )
        self.db.commit()
    
    def use_recovery_code(self, user_id: int, code_hash: str) -> bool:
        cursor = self.db.execute(
            """UPDATE mfa_recovery_codes SET used_at = NOW()
               WHERE user_id = %s AND code_hash = %s AND used_at IS NULL""",
            (user_id, code_hash)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def get_recovery_codes(self, user_id: int) -> Sequence[str]:
        rows = self.db.fetchall(
            "SELECT code_hash FROM mfa_recovery_codes WHERE user_id = %s AND used_at IS NULL",
            (user_id,)
        )
        return [row["code_hash"] for row in rows]


class PostgresPortfolioDAO(PortfolioDAO):
    """PostgreSQL portfolio data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def add_holding(self, user_id: int, symbol: str, company: str, shares: float, buy_price: float) -> int:
        cursor = self.db.execute(
            """INSERT INTO portfolio (user_id, symbol, company, shares, buy_price)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, symbol, company, shares, buy_price)
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_holdings(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, company, shares, buy_price, buy_date
               FROM portfolio WHERE user_id = %s ORDER BY buy_date DESC, id DESC""",
            (user_id,)
        )
    
    def update_holding(self, holding_id: int, user_id: int, shares: float, buy_price: float) -> bool:
        cursor = self.db.execute(
            "UPDATE portfolio SET shares = %s, buy_price = %s WHERE id = %s AND user_id = %s",
            (shares, buy_price, holding_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def delete_holding(self, holding_id: int, user_id: int) -> bool:
        cursor = self.db.execute(
            "DELETE FROM portfolio WHERE id = %s AND user_id = %s",
            (holding_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def sell_holding(self, holding_id: int, user_id: int, shares: float, sell_price: float) -> bool:
        conn = self.db.get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(
                "SELECT symbol, shares FROM portfolio WHERE id = %s AND user_id = %s",
                (holding_id, user_id)
            )
            holding = cursor.fetchone()
            if not holding:
                return False
            
            symbol, available_shares = holding["symbol"], holding["shares"]
            if shares > float(available_shares) + 1e-12:
                raise ValueError("Sale quantity cannot exceed the holding quantity.")
            
            self.db.execute(
                "INSERT INTO transactions(user_id, symbol, transaction_type, shares, price) VALUES (%s, %s, 'SELL', %s, %s)",
                (user_id, symbol, shares, sell_price)
            )
            
            remaining = float(available_shares) - shares
            if remaining <= 1e-12:
                self.db.execute(
                    "DELETE FROM portfolio WHERE id = %s AND user_id = %s",
                    (holding_id, user_id)
                )
            else:
                self.db.execute(
                    "UPDATE portfolio SET shares = %s WHERE id = %s AND user_id = %s",
                    (remaining, holding_id, user_id)
                )
            
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise


class PostgresWatchlistDAO(WatchlistDAO):
    """PostgreSQL watchlist data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def add_symbol(self, user_id: int, symbol: str) -> bool:
        cursor = self.db.execute(
            "SELECT id FROM watchlist WHERE user_id = %s AND UPPER(symbol) = %s",
            (user_id, symbol.upper())
        )
        if cursor.fetchone():
            return False
        
        self.db.execute(
            "INSERT INTO watchlist (user_id, symbol) VALUES (%s, %s)",
            (user_id, symbol.upper())
        )
        self.db.commit()
        return True
    
    def get_watchlist(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, added_date FROM watchlist
               WHERE user_id = %s ORDER BY added_date DESC""",
            (user_id,)
        )
    
    def remove_symbol(self, watchlist_id: int, user_id: int) -> bool:
        cursor = self.db.execute(
            "DELETE FROM watchlist WHERE id = %s AND user_id = %s",
            (watchlist_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0


class PostgresTransactionDAO(TransactionDAO):
    """PostgreSQL transaction data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def add_transaction(self, user_id: int, symbol: str, transaction_type: str,
                        shares: float, price: float) -> int:
        cursor = self.db.execute(
            """INSERT INTO transactions (user_id, symbol, transaction_type, shares, price)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, symbol, transaction_type, shares, price)
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_transactions(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, transaction_type, shares, price, transaction_date
               FROM transactions WHERE user_id = %s
               ORDER BY transaction_date DESC, id DESC LIMIT %s""",
            (user_id, limit)
        )


class PostgresPredictionDAO(PredictionDAO):
    """PostgreSQL prediction data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def save_prediction(self, user_id: int, symbol: str, **kwargs: Any) -> int:
        cursor = self.db.execute(
            """INSERT INTO prediction_history (user_id, symbol, linear_prediction,
               decision_tree_prediction, random_forest_prediction, prediction_date,
               consensus_prediction, best_model, model_version, payload_json)
               VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()), %s, %s, %s, %s) RETURNING id""",
            (user_id, symbol, 
             kwargs.get("linear_prediction"), kwargs.get("decision_tree_prediction"),
             kwargs.get("random_forest_prediction"), kwargs.get("prediction_date"),
             kwargs.get("consensus_prediction"), kwargs.get("best_model"),
             kwargs.get("model_version"), kwargs.get("payload"))
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_prediction_history(self, user_id: int, limit: int = 50) -> Sequence[dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT id, symbol, linear_prediction, decision_tree_prediction,
               random_forest_prediction, prediction_date
               FROM prediction_history WHERE user_id = %s ORDER BY prediction_date DESC LIMIT %s""",
            (user_id, limit)
        )
        return rows
    
    def get_prediction_details(self, user_id: int, symbol: Optional[str] = None,
                               limit: int = 50) -> Sequence[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "WHERE user_id = %s"
        if symbol:
            where += " AND UPPER(symbol) = %s"
            params.append(symbol.upper())
        params.append(limit)
        # where contains only constant clauses; all values are bound.
        query = f"""
            SELECT id, symbol, prediction_date, consensus_prediction, best_model,
                   model_version, payload_json, forecast_low, forecast_median, forecast_high,
                   confidence_level, training_window, timeframe, actual_price, coverage_hit, winkler_score,
                   origin_timestamp, target_timestamp, provider, data_timestamp, feature_timestamp,
                   data_version, schema_version, forecast_evidence_json, forecast_status, snapshot_hash,
                   outcome_status, outcome_evidence_json, settlement_source, settlement_provider,
                   settlement_data_timestamp, settlement_is_stale, settlement_is_demo,
                   official_outcome, settled_at, result_hash, horizon, horizon_sessions
            FROM prediction_history {where} ORDER BY prediction_date DESC LIMIT %s
        """  # nosec B608
        return self.db.fetchall(query, tuple(params))
    
    def save_range_forecast(self, user_id: int, symbol: str, forecast_payload: dict[str, Any]) -> int:
        # Simplified - full implementation would mirror database.py logic
        cursor = self.db.execute(
            """INSERT INTO prediction_history (user_id, symbol, payload_json, forecast_status)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (user_id, symbol, json.dumps(forecast_payload), "pending")
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_settled_forecasts(self, user_id: int, limit: int = 500) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT * FROM prediction_history 
               WHERE user_id = %s AND outcome_status = 'settled' 
               ORDER BY settled_at DESC LIMIT %s""",
            (user_id, limit)
        )


class PostgresPaperTradingDAO(PaperTradingDAO):
    """PostgreSQL paper trading data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def get_account(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM paper_accounts WHERE user_id = %s", (user_id,)
        )
    
    def create_account(self, user_id: int, initial_balance: float = 1000000) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        self.db.execute(
            """INSERT INTO paper_accounts (user_id, initial_balance, cash_balance, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING""",
            (user_id, initial_balance, initial_balance, datetime.now(timezone.utc), datetime.now(timezone.utc))
        )
        self.db.commit()
        return {"user_id": user_id, "initial_balance": initial_balance, "cash_balance": initial_balance}
    
    def get_positions(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT symbol, quantity, average_price, updated_at, lot_size
               FROM paper_positions WHERE user_id = %s""",
            (user_id,)
        )
    
    def get_orders(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT * FROM paper_orders WHERE user_id = %s ORDER BY created_at DESC LIMIT %s""",
            (user_id, limit)
        )


class PostgresAuditDAO(AuditDAO):
    """PostgreSQL audit log data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def record_event(self, user_id: Optional[int], action: str,
                     entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                     details: Optional[dict[str, Any]] = None) -> int:
        cursor = self.db.execute(
            """INSERT INTO audit_log (user_id, action, entity_type, entity_id, details_json)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, action, entity_type, entity_id, json.dumps(details or {}))
        )
        self.db.commit()
        return _inserted_id(cursor)
    
    def get_events(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT id, action, entity_type, entity_id, details_json, created_at
               FROM audit_log WHERE user_id = %s ORDER BY id DESC LIMIT %s""",
            (user_id, limit)
        )
        return [
            {
                "id": row["id"],
                "action": row["action"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "details": json.loads(row["details_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


class PostgresSettingsDAO(SettingsDAO):
    """PostgreSQL settings data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def get_settings(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM settings WHERE user_id = %s", (user_id,)
        )
    
    def update_settings(self, user_id: int, **kwargs: Any) -> bool:
        validate_fields(kwargs, SETTINGS_FIELDS)
        if not kwargs:
            return False
        fields = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        cursor = self.db.execute(
            f"UPDATE settings SET {fields} WHERE user_id = %s", tuple(values)  # nosec B608 - identifiers allowlisted above, values bound
        )
        self.db.commit()
        return cursor.rowcount > 0


class PostgresDAOFactory(DAOFactory):
    """PostgreSQL DAO factory."""
    
    def __init__(self, dsn: str, pool_size: int = 10):
        self._db = PostgresDatabase(dsn, pool_size)
    
    @property
    def db(self) -> PostgresDatabase:
        return self._db
    
    def create_user_dao(self) -> UserDAO:
        return PostgresUserDAO(self._db)
    
    def create_portfolio_dao(self) -> PortfolioDAO:
        return PostgresPortfolioDAO(self._db)
    
    def create_watchlist_dao(self) -> WatchlistDAO:
        return PostgresWatchlistDAO(self._db)
    
    def create_transaction_dao(self) -> TransactionDAO:
        return PostgresTransactionDAO(self._db)
    
    def create_prediction_dao(self) -> PredictionDAO:
        return PostgresPredictionDAO(self._db)
    
    def create_paper_trading_dao(self) -> PaperTradingDAO:
        return PostgresPaperTradingDAO(self._db)
    
    def create_audit_dao(self) -> AuditDAO:
        return PostgresAuditDAO(self._db)
    
    def create_settings_dao(self) -> SettingsDAO:
        return PostgresSettingsDAO(self._db)
