"""PostgreSQL implementation of DAO interfaces."""
from __future__ import annotations
from .update_fields import USER_FIELDS, SETTINGS_FIELDS, validate_fields

import json
import math
import threading
import re
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
        try:
            self._pool = ThreadedConnectionPool(1, pool_size, dsn)
        except psycopg2.Error:
            # URI parse/auth failures can contain private DSN fragments. Keep
            # the failure observable without allowing vendor text into logs.
            raise RuntimeError("PostgreSQL connection failed; check backend credentials and endpoint.") from None
        self._local = threading.local()
    
    def _get_conn(self) -> psycopg2.extensions.connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._pool.getconn()
            self._local.conn = conn
            self._local.cursors = []
        if conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE:
            # Supavisor transaction mode preserves LOCAL settings only for this
            # transaction. Never depend on session-level state in pooled mode.
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        return conn
    
    def get_connection(self) -> psycopg2.extensions.connection:
        return self._get_conn()

    def sql(self, query: str) -> str:
        # Keep quoted literals/identifiers and SQL comments intact. Percent
        # escaping is for psycopg2's DB-API parameter parser, not SQL values.
        escaped = query.replace("%", "%%")
        return re.sub(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/)|\?",
                      lambda match: match.group(1) if match.group(1) is not None else "%s",
                      escaped, flags=re.DOTALL)

    def begin_write(self, lock_key: Optional[str] = None) -> None:
        self._get_conn()
        if lock_key is not None:
            # Transaction-scoped, stable across processes. Domain callers use
            # the same namespace/key for every writer of the protected subject.
            cursor = self.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
            cursor.close()
    
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> RealDictCursor:
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(query, params)
        except psycopg2.Error as exc:
            cursor.close()
            self.rollback()
            # Preserve exception classes for integrity/serialization handling,
            # but suppress vendor DETAIL/CONTEXT that can include entire rows.
            raise type(exc)("PostgreSQL database operation failed.") from None
        except Exception:
            cursor.close()
            self.rollback()
            raise
        self._local.cursors.append(cursor)
        return cursor
    
    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[dict[str, Any]]:
        cursor = self.execute(query, params)
        try:
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()
            self._local.cursors.remove(cursor)
    
    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> Sequence[dict[str, Any]]:
        cursor = self.execute(query, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            self._local.cursors.remove(cursor)

    def _close_cursors(self) -> None:
        for cursor in getattr(self._local, "cursors", []):
            cursor.close()
        self._local.cursors = []
    
    def commit(self) -> None:
        self._get_conn().commit()
        self._close_cursors()
    
    def rollback(self) -> None:
        self._get_conn().rollback()
        self._close_cursors()
    
    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            self._close_cursors()
            try:
                if not conn.closed:
                    conn.rollback()
            finally:
                self._pool.putconn(conn, close=bool(conn.closed))
                self._local.conn = None

    def _shutdown_pool(self) -> None:
        """Call after all users/worker threads have released their connections."""
        self.close()
        self._pool.closeall()


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
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
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
               enabled = 1, last_totp_counter = NULL,
               created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at""",
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
            "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (%s, %s)",
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
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
    def get_holdings(self, user_id: int) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id, symbol, company, shares, buy_price, buy_date
               FROM portfolio WHERE user_id = %s ORDER BY buy_date DESC, id DESC""",
            (user_id,)
        )

    def buy_holding(self, user_id: int, symbol: str, company: str, shares: float, buy_price: float) -> int:
        try:
            cursor = self.db.execute(
                "INSERT INTO portfolio(user_id,symbol,company,shares,buy_price) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (user_id, symbol, company, shares, buy_price),
            )
            row_id = _inserted_id(cursor)
            self.db.execute(
                "INSERT INTO transactions(user_id,symbol,transaction_type,shares,price) VALUES(%s,%s,'BUY',%s,%s)",
                (user_id, symbol, shares, buy_price),
            )
            self.db.commit()
            return row_id
        except Exception:
            self.db.rollback()
            raise
    
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
        if not math.isfinite(shares) or shares <= 0 or not math.isfinite(sell_price) or sell_price < 0:
            raise ValueError("Sale quantity must be finite and positive; price must be finite and nonnegative.")
        conn = self.db.get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(
                "SELECT symbol, shares FROM portfolio WHERE id = %s AND user_id = %s FOR UPDATE",
                (holding_id, user_id)
            )
            holding = cursor.fetchone()
            if not holding:
                self.db.rollback()
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
        finally:
            cursor.close()


class PostgresWatchlistDAO(WatchlistDAO):
    """PostgreSQL watchlist data access."""
    
    def __init__(self, db: PostgresDatabase):
        self.db = db
    
    def add_symbol(self, user_id: int, symbol: str) -> bool:
        cursor = self.db.execute(
            "INSERT INTO watchlist (user_id, symbol) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, symbol.upper())
        )
        self.db.commit()
        return cursor.rowcount > 0
    
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
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
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
        from database import _serialize_prediction_date
        payload = kwargs.get("payload")
        cursor = self.db.execute(
            """INSERT INTO prediction_history (user_id, symbol, linear_prediction,
               decision_tree_prediction, random_forest_prediction, prediction_date,
               consensus_prediction, best_model, model_version, payload_json)
               VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()), %s, %s, %s, %s) RETURNING id""",
            (user_id, symbol.strip().upper(),
             kwargs.get("linear_prediction"), kwargs.get("decision_tree_prediction"),
              kwargs.get("random_forest_prediction"), _serialize_prediction_date(kwargs.get("prediction_date")),
             kwargs.get("consensus_prediction"), kwargs.get("best_model"),
              kwargs.get("model_version"), json.dumps(payload, default=str) if isinstance(payload, dict) else None)
        )
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
    def get_prediction_history(self, user_id: int, limit: int = 50) -> Sequence[dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT id, symbol, linear_prediction AS linear, decision_tree_prediction AS dt,
               random_forest_prediction AS rf, prediction_date AS date
               FROM prediction_history WHERE user_id = %s ORDER BY prediction_date DESC LIMIT %s""",
            (user_id, max(1, min(int(limit), 500)))
        )
        return rows
    
    def get_prediction_details(self, user_id: int, symbol: Optional[str] = None,
                               limit: int = 50) -> Sequence[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "WHERE user_id = %s"
        if symbol:
            where += " AND UPPER(symbol) = %s"
            params.append(str(symbol).strip().upper())
        params.append(max(1, min(int(limit), 500)))
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
        rows = self.db.fetchall(query, tuple(params))
        for item in rows:
            for source, target in (("payload_json", "payload"),
                                   ("forecast_evidence_json", "forecast_evidence"),
                                   ("outcome_evidence_json", "outcome_evidence")):
                raw = item.pop(source)
                try:
                    item[target] = json.loads(raw or "{}") if isinstance(raw, (str, type(None))) else raw
                except json.JSONDecodeError:
                    item[target] = {}
        return rows
    
    def save_range_forecast(self, user_id: int, symbol: str, forecast_payload: dict[str, Any]) -> int:
        from database import range_forecast_values
        cursor = self.db.execute(
            """INSERT INTO prediction_history(
               user_id,symbol,consensus_prediction,model_version,payload_json,
               forecast_low,forecast_median,forecast_high,confidence_level,training_window,timeframe,
               prediction_date,created_at,origin_timestamp,target_timestamp,provider,data_timestamp,
               feature_timestamp,data_version,schema_version,forecast_evidence_json,forecast_status,
               snapshot_hash,outcome_status,official_outcome,horizon,horizon_sessions)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            range_forecast_values(user_id, symbol, forecast_payload),
        )
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
    def get_settled_forecasts(self, user_id: int, limit: int = 500) -> Sequence[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT id,symbol,forecast_low,forecast_median,forecast_high,confidence_level,
                      training_window,timeframe,actual_price,coverage_hit,winkler_score,created_at,
                      horizon,horizon_sessions
               FROM prediction_history
               WHERE user_id=%s AND actual_price IS NOT NULL AND coverage_hit IS NOT NULL AND winkler_score IS NOT NULL
                 AND outcome_status='settled' AND settlement_source='automatic'
                 AND official_outcome=1 AND COALESCE(settlement_is_stale,0)=0
                 AND COALESCE(settlement_is_demo,0)=0
                 AND forecast_status IN ('model_supported','baseline_only','low_evidence','available')
               ORDER BY id DESC LIMIT %s""",
            (int(user_id), max(1, min(int(limit), 5000)))
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
               VALUES (%s, %s, %s, %s, %s)""",
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
        row_id = _inserted_id(cursor)
        self.db.commit()
        return row_id
    
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
                "details": json.loads(row["details_json"] or "{}") if isinstance(row["details_json"], (str, type(None))) else row["details_json"],
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
