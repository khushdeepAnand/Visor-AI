"""SQLite implementation of DAO interfaces."""
from __future__ import annotations

import sqlite3
import math
from .update_fields import USER_FIELDS, SETTINGS_FIELDS, validate_fields
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

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

from database import get_connection, _open_connection, database_row


class SQLiteDatabase(DatabaseInterface):
    """SQLite database connection wrapper."""
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn or get_connection()
        self._conn.row_factory = database_row
    
    def get_connection(self) -> sqlite3.Connection:
        return self._conn

    def sql(self, query: str) -> str:
        return query

    def begin_write(self, lock_key: Optional[str] = None) -> None:
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
    
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._conn.execute(query, params)
    
    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[dict[str, Any]]:
        cursor = self._conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> Sequence[dict[str, Any]]:
        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def commit(self) -> None:
        self._conn.commit()
    
    def rollback(self) -> None:
        self._conn.rollback()
    
    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class SQLiteUserDAO(UserDAO):
    """SQLite user data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def create_user(self, name: str, email: str, password: str, auth_provider: str = "password",
                    google_id: Optional[str] = None, date_of_birth: Optional[str] = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.execute(
            """INSERT INTO users (name, email, password, auth_provider, google_id, date_of_birth, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, email, password, auth_provider, google_id, date_of_birth, now)
        )
        self.db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("Database did not return the inserted row ID.")
        return cursor.lastrowid
    
    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM users WHERE email = ?", (email,)
        )
    
    def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
    
    def update_user(self, user_id: int, **kwargs: Any) -> bool:
        validate_fields(kwargs, USER_FIELDS)
        if not kwargs:
            return False
        fields = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        cursor = self.db.execute(
            f"UPDATE users SET {fields} WHERE id = ?", tuple(values)  # nosec B608 - identifiers allowlisted above, values bound
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def delete_user(self, user_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.db.commit()
        return cursor.rowcount > 0
    
    def set_mfa_secret(self, user_id: int, encrypted_secret: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """INSERT OR REPLACE INTO user_mfa (user_id, encrypted_totp_secret, enabled, updated_at)
               VALUES (?, ?, 1, ?)""",
            (user_id, encrypted_secret, now)
        )
        self.db.commit()
    
    def get_mfa_secret(self, user_id: int) -> Optional[str]:
        row = self.db.fetchone(
            "SELECT encrypted_totp_secret FROM user_mfa WHERE user_id = ? AND enabled = 1",
            (user_id,)
        )
        return row["encrypted_totp_secret"] if row else None
    
    def enable_mfa(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE user_mfa SET enabled = 1, updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )
        self.db.commit()
    
    def disable_mfa(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE user_mfa SET enabled = 0, updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )
        self.db.commit()
    
    def add_recovery_code(self, user_id: int, code_hash: str) -> None:
        self.db.execute(
            "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (?, ?)",
            (user_id, code_hash)
        )
        self.db.commit()
    
    def use_recovery_code(self, user_id: int, code_hash: str) -> bool:
        cursor = self.db.execute(
            """UPDATE mfa_recovery_codes SET used_at = datetime('now')
               WHERE user_id = ? AND code_hash = ? AND used_at IS NULL""",
            (user_id, code_hash)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def get_recovery_codes(self, user_id: int) -> Sequence[str]:
        rows = self.db.fetchall(
            "SELECT code_hash FROM mfa_recovery_codes WHERE user_id = ? AND used_at IS NULL",
            (user_id,)
        )
        return [row["code_hash"] for row in rows]


class SQLitePortfolioDAO(PortfolioDAO):
    """SQLite portfolio data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def add_holding(self, user_id: int, symbol: str, company: str, shares: float, buy_price: float) -> int:
        cursor = self.db.execute(
            """INSERT INTO portfolio (user_id, symbol, company, shares, buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, symbol, company, shares, buy_price)
        )
        self.db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("Database did not return the inserted row ID.")
        return cursor.lastrowid
    
    def get_holdings(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, company, shares, buy_price, buy_date
               FROM portfolio WHERE user_id = ? ORDER BY buy_date DESC, id DESC""",
            (user_id,)
        )

    def buy_holding(self, user_id: int, symbol: str, company: str, shares: float, buy_price: float) -> int:
        try:
            cursor = self.db.execute(
                "INSERT INTO portfolio(user_id,symbol,company,shares,buy_price) VALUES(?,?,?,?,?)",
                (user_id, symbol, company, shares, buy_price),
            )
            row_id = int(cursor.lastrowid or 0)
            self.db.execute(
                "INSERT INTO transactions(user_id,symbol,transaction_type,shares,price) VALUES(?,?,'BUY',?,?)",
                (user_id, symbol, shares, buy_price),
            )
            self.db.commit()
            return row_id
        except Exception:
            self.db.rollback()
            raise
    
    def update_holding(self, holding_id: int, user_id: int, shares: float, buy_price: float) -> bool:
        cursor = self.db.execute(
            "UPDATE portfolio SET shares = ?, buy_price = ? WHERE id = ? AND user_id = ?",
            (shares, buy_price, holding_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def delete_holding(self, holding_id: int, user_id: int) -> bool:
        cursor = self.db.execute(
            "DELETE FROM portfolio WHERE id = ? AND user_id = ?",
            (holding_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0
    
    def sell_holding(self, holding_id: int, user_id: int, shares: float, sell_price: float) -> bool:
        if not math.isfinite(shares) or shares <= 0 or not math.isfinite(sell_price) or sell_price < 0:
            raise ValueError("Sale quantity must be finite and positive; price must be finite and nonnegative.")
        conn = self.db.get_connection()
        cursor = conn.cursor()
        try:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT symbol, shares FROM portfolio WHERE id = ? AND user_id = ?",
                (holding_id, user_id)
            )
            holding = cursor.fetchone()
            if not holding:
                self.db.rollback()
                return False
            
            symbol, available_shares = holding["symbol"], holding["shares"]
            if shares > available_shares + 1e-12:
                raise ValueError("Sale quantity cannot exceed the holding quantity.")
            
            conn.execute(
                "INSERT INTO transactions(user_id, symbol, transaction_type, shares, price) VALUES (?, ?, 'SELL', ?, ?)",
                (user_id, symbol, shares, sell_price)
            )
            
            remaining = available_shares - shares
            if remaining <= 1e-12:
                cursor.execute(
                    "DELETE FROM portfolio WHERE id = ? AND user_id = ?",
                    (holding_id, user_id)
                )
            else:
                cursor.execute(
                    "UPDATE portfolio SET shares = ? WHERE id = ? AND user_id = ?",
                    (remaining, holding_id, user_id)
                )
            
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise
        finally:
            cursor.close()


class SQLiteWatchlistDAO(WatchlistDAO):
    """SQLite watchlist data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def add_symbol(self, user_id: int, symbol: str) -> bool:
        cursor = self.db.execute(
            "SELECT id FROM watchlist WHERE user_id = ? AND UPPER(symbol) = ?",
            (user_id, symbol.upper())
        )
        if cursor.fetchone():
            return False
        
        self.db.execute(
            "INSERT INTO watchlist (user_id, symbol) VALUES (?, ?)",
            (user_id, symbol.upper())
        )
        self.db.commit()
        return True
    
    def get_watchlist(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, added_date FROM watchlist
               WHERE user_id = ? ORDER BY added_date DESC""",
            (user_id,)
        )
    
    def remove_symbol(self, watchlist_id: int, user_id: int) -> bool:
        cursor = self.db.execute(
            "DELETE FROM watchlist WHERE id = ? AND user_id = ?",
            (watchlist_id, user_id)
        )
        self.db.commit()
        return cursor.rowcount > 0


class SQLiteTransactionDAO(TransactionDAO):
    """SQLite transaction data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def add_transaction(self, user_id: int, symbol: str, transaction_type: str,
                        shares: float, price: float) -> int:
        cursor = self.db.execute(
            """INSERT INTO transactions (user_id, symbol, transaction_type, shares, price)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, symbol, transaction_type, shares, price)
        )
        self.db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("Database did not return the inserted row ID.")
        return cursor.lastrowid
    
    def get_transactions(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, transaction_type, shares, price, transaction_date
               FROM transactions WHERE user_id = ?
               ORDER BY transaction_date DESC, id DESC LIMIT ?""",
            (user_id, limit)
        )


class SQLitePredictionDAO(PredictionDAO):
    """SQLite prediction data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def save_prediction(self, user_id: int, symbol: str, **kwargs: Any) -> int:
        import json
        from database import _serialize_prediction_date
        payload = kwargs.get("payload")
        cursor = self.db.execute(
            """INSERT INTO prediction_history(user_id,symbol,linear_prediction,decision_tree_prediction,
               random_forest_prediction,prediction_date,consensus_prediction,best_model,model_version,payload_json)
               VALUES(?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),?,?,?,?)""",
            (user_id, symbol.strip().upper(), kwargs.get("linear_prediction"), kwargs.get("decision_tree_prediction"),
             kwargs.get("random_forest_prediction"), _serialize_prediction_date(kwargs.get("prediction_date")),
             kwargs.get("consensus_prediction"), kwargs.get("best_model"), kwargs.get("model_version"),
             json.dumps(payload, default=str) if isinstance(payload, dict) else None),
        )
        row_id = int(cursor.lastrowid or 0)
        self.db.commit()
        return row_id
    
    def get_prediction_history(self, user_id: int, limit: int = 50) -> Sequence[dict[str, Any]]:
        from database import get_prediction_history
        rows = get_prediction_history(user_id, limit)
        return [dict(zip(["id", "symbol", "linear", "dt", "rf", "date"], row)) for row in rows]
    
    def get_prediction_details(self, user_id: int, symbol: Optional[str] = None,
                                limit: int = 50) -> Sequence[dict[str, Any]]:
        from database import get_prediction_details
        return [dict(row) for row in get_prediction_details(user_id, symbol, limit)]
    
    def save_range_forecast(self, user_id: int, symbol: str, forecast_payload: dict[str, Any]) -> int:
        from database import range_forecast_values
        cursor = self.db.execute(
            """INSERT INTO prediction_history(
               user_id,symbol,consensus_prediction,model_version,payload_json,
               forecast_low,forecast_median,forecast_high,confidence_level,training_window,timeframe,
               prediction_date,created_at,origin_timestamp,target_timestamp,provider,data_timestamp,
               feature_timestamp,data_version,schema_version,forecast_evidence_json,forecast_status,
               snapshot_hash,outcome_status,official_outcome,horizon,horizon_sessions)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            range_forecast_values(user_id, symbol, forecast_payload),
        )
        row_id = int(cursor.lastrowid or 0)
        self.db.commit()
        return row_id
    
    def get_settled_forecasts(self, user_id: int, limit: int = 500) -> Sequence[dict[str, Any]]:
        from database import get_settled_range_forecasts
        rows = get_settled_range_forecasts(user_id, limit)
        return [dict(row) for row in rows]


class SQLitePaperTradingDAO(PaperTradingDAO):
    """SQLite paper trading data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def get_account(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM paper_accounts WHERE user_id = ?", (user_id,)
        )
    
    def create_account(self, user_id: int, initial_balance: float = 1000000) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """INSERT INTO paper_accounts (user_id, initial_balance, cash_balance, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, initial_balance, initial_balance, now, now)
        )
        self.db.commit()
        return {"user_id": user_id, "initial_balance": initial_balance, "cash_balance": initial_balance}
    
    def get_positions(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT symbol, quantity, average_price, updated_at, lot_size
               FROM paper_positions WHERE user_id = ?""",
            (user_id,)
        )
    
    def get_orders(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT * FROM paper_orders WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit)
        )


class SQLiteAuditDAO(AuditDAO):
    """SQLite audit log data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def record_event(self, user_id: Optional[int], action: str,
                     entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                     details: Optional[dict[str, Any]] = None) -> int:
        import json
        payload = json.dumps(details or {})
        cursor = self.db.execute(
            """INSERT INTO audit_log (user_id, action, entity_type, entity_id, details_json)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, action, entity_type, entity_id, json.dumps(details or {}))
        )
        self.db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("Database did not return the inserted row ID.")
        return cursor.lastrowid
    
    def get_events(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        import json
        rows = self.db.fetchall(
            """SELECT id, action, entity_type, entity_id, details_json, created_at
               FROM audit_log WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
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


class SQLiteSettingsDAO(SettingsDAO):
    """SQLite settings data access."""
    
    def __init__(self, db: SQLiteDatabase):
        self.db = db
    
    def get_settings(self, user_id: int) -> Optional[dict[str, Any]]:
        return self.db.fetchone(
            "SELECT * FROM settings WHERE user_id = ?", (user_id,)
        )
    
    def update_settings(self, user_id: int, **kwargs: Any) -> bool:
        validate_fields(kwargs, SETTINGS_FIELDS)
        if not kwargs:
            return False
        fields = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        cursor = self.db.execute(
            f"UPDATE settings SET {fields} WHERE user_id = ?", tuple(values)  # nosec B608 - identifiers allowlisted above, values bound
        )
        self.db.commit()
        return cursor.rowcount > 0


class SQLiteDAOFactory(DAOFactory):
    """SQLite DAO factory."""
    
    def __init__(self, db: Optional[SQLiteDatabase] = None):
        self._db = db or SQLiteDatabase()
    
    @property
    def db(self) -> SQLiteDatabase:
        return self._db
    
    def create_user_dao(self) -> UserDAO:
        return SQLiteUserDAO(self._db)
    
    def create_portfolio_dao(self) -> PortfolioDAO:
        return SQLitePortfolioDAO(self._db)
    
    def create_watchlist_dao(self) -> WatchlistDAO:
        return SQLiteWatchlistDAO(self._db)
    
    def create_transaction_dao(self) -> TransactionDAO:
        return SQLiteTransactionDAO(self._db)
    
    def create_prediction_dao(self) -> PredictionDAO:
        return SQLitePredictionDAO(self._db)
    
    def create_paper_trading_dao(self) -> PaperTradingDAO:
        return SQLitePaperTradingDAO(self._db)
    
    def create_audit_dao(self) -> AuditDAO:
        return SQLiteAuditDAO(self._db)
    
    def create_settings_dao(self) -> SettingsDAO:
        return SQLiteSettingsDAO(self._db)
