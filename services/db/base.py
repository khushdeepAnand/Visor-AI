"""Base interfaces for database operations (DAO pattern)."""
from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Any, Optional, Sequence


class DatabaseInterface(abc.ABC):
    """Abstract database connection interface."""
    
    @abc.abstractmethod
    def get_connection(self) -> Any:
        """Return a database connection."""
        pass
    
    @abc.abstractmethod
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        """Execute a query."""
        pass
    
    @abc.abstractmethod
    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[dict[str, Any]]:
        """Fetch one row."""
        pass
    
    @abc.abstractmethod
    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> Sequence[dict[str, Any]]:
        """Fetch all rows."""
        pass
    
    @abc.abstractmethod
    def commit(self) -> None:
        """Commit transaction."""
        pass
    
    @abc.abstractmethod
    def rollback(self) -> None:
        """Rollback transaction."""
        pass
    
    @abc.abstractmethod
    def close(self) -> None:
        """Close connection."""
        pass


class UserDAO(abc.ABC):
    """User data access interface."""
    
    @abc.abstractmethod
    def create_user(self, name: str, email: str, password: str, auth_provider: str = "password", 
                    google_id: Optional[str] = None, date_of_birth: Optional[str] = None) -> int:
        """Create a new user. Returns user_id."""
        pass
    
    @abc.abstractmethod
    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        """Get user by email."""
        pass
    
    @abc.abstractmethod
    def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        """Get user by ID."""
        pass
    
    @abc.abstractmethod
    def update_user(self, user_id: int, **kwargs: Any) -> bool:
        """Update user fields."""
        pass
    
    @abc.abstractmethod
    def delete_user(self, user_id: int) -> bool:
        """Delete user."""
        pass
    
    @abc.abstractmethod
    def set_mfa_secret(self, user_id: int, encrypted_secret: str) -> None:
        """Set encrypted TOTP secret."""
        pass
    
    @abc.abstractmethod
    def get_mfa_secret(self, user_id: int) -> Optional[str]:
        """Get encrypted TOTP secret."""
        pass
    
    @abc.abstractmethod
    def enable_mfa(self, user_id: int) -> None:
        """Enable MFA for user."""
        pass
    
    @abc.abstractmethod
    def disable_mfa(self, user_id: int) -> None:
        """Disable MFA for user."""
        pass
    
    @abc.abstractmethod
    def add_recovery_code(self, user_id: int, code_hash: str) -> None:
        """Add a recovery code hash."""
        pass
    
    @abc.abstractmethod
    def use_recovery_code(self, user_id: int, code_hash: str) -> bool:
        """Mark recovery code as used. Returns True if valid and unused."""
        pass
    
    @abc.abstractmethod
    def get_recovery_codes(self, user_id: int) -> Sequence[str]:
        """Get all unused recovery code hashes."""
        pass


class PortfolioDAO(abc.ABC):
    """Portfolio data access interface."""
    
    @abc.abstractmethod
    def add_holding(self, user_id: int, symbol: str, company: str, shares: float, buy_price: float) -> int:
        """Add a portfolio holding. Returns holding ID."""
        pass
    
    @abc.abstractmethod
    def get_holdings(self, user_id: int) -> Sequence[dict[str, Any]]:
        """Get all holdings for user."""
        pass
    
    @abc.abstractmethod
    def update_holding(self, holding_id: int, user_id: int, shares: float, buy_price: float) -> bool:
        """Update holding shares and price."""
        pass
    
    @abc.abstractmethod
    def delete_holding(self, holding_id: int, user_id: int) -> bool:
        """Delete a holding."""
        pass
    
    @abc.abstractmethod
    def sell_holding(self, holding_id: int, user_id: int, shares: float, sell_price: float) -> bool:
        """Sell shares from holding."""
        pass


class WatchlistDAO(abc.ABC):
    """Watchlist data access interface."""
    
    @abc.abstractmethod
    def add_symbol(self, user_id: int, symbol: str) -> bool:
        """Add symbol to watchlist. Returns True if added, False if duplicate."""
        pass
    
    @abc.abstractmethod
    def get_watchlist(self, user_id: int) -> Sequence[dict[str, Any]]:
        """Get user's watchlist."""
        pass
    
    @abc.abstractmethod
    def remove_symbol(self, watchlist_id: int, user_id: int) -> bool:
        """Remove symbol from watchlist."""
        pass


class TransactionDAO(abc.ABC):
    """Transaction data access interface."""
    
    @abc.abstractmethod
    def add_transaction(self, user_id: int, symbol: str, transaction_type: str, 
                        shares: float, price: float) -> int:
        """Add a transaction. Returns transaction ID."""
        pass
    
    @abc.abstractmethod
    def get_transactions(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        """Get recent transactions."""
        pass


class PredictionDAO(abc.ABC):
    """Prediction data access interface."""
    
    @abc.abstractmethod
    def save_prediction(self, user_id: int, symbol: str, **kwargs: Any) -> int:
        """Save a prediction. Returns prediction ID."""
        pass
    
    @abc.abstractmethod
    def get_prediction_history(self, user_id: int, limit: int = 50) -> Sequence[dict[str, Any]]:
        """Get prediction history."""
        pass
    
    @abc.abstractmethod
    def get_prediction_details(self, user_id: int, symbol: Optional[str] = None, 
                                limit: int = 50) -> Sequence[dict[str, Any]]:
        """Get detailed prediction history."""
        pass
    
    @abc.abstractmethod
    def save_range_forecast(self, user_id: int, symbol: str, forecast_payload: dict[str, Any]) -> int:
        """Save a range forecast with full provenance."""
        pass
    
    @abc.abstractmethod
    def get_settled_forecasts(self, user_id: int, limit: int = 500) -> Sequence[dict[str, Any]]:
        """Get settled forecasts for calibration."""
        pass


class PaperTradingDAO(abc.ABC):
    """Paper trading data access interface."""
    
    @abc.abstractmethod
    def get_account(self, user_id: int) -> Optional[dict[str, Any]]:
        """Get paper trading account."""
        pass
    
    @abc.abstractmethod
    def create_account(self, user_id: int, initial_balance: float = 1000000) -> dict[str, Any]:
        """Create paper trading account."""
        pass
    
    @abc.abstractmethod
    def get_positions(self, user_id: int) -> Sequence[dict[str, Any]]:
        """Get paper positions."""
        pass
    
    @abc.abstractmethod
    def get_orders(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        """Get paper orders."""
        pass


class AuditDAO(abc.ABC):
    """Audit log data access interface."""
    
    @abc.abstractmethod
    def record_event(self, user_id: Optional[int], action: str, 
                     entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                     details: Optional[dict[str, Any]] = None) -> int:
        """Record audit event. Returns event ID."""
        pass
    
    @abc.abstractmethod
    def get_events(self, user_id: int, limit: int = 100) -> Sequence[dict[str, Any]]:
        """Get audit events for user."""
        pass


class SettingsDAO(abc.ABC):
    """User settings data access interface."""
    
    @abc.abstractmethod
    def get_settings(self, user_id: int) -> Optional[dict[str, Any]]:
        """Get user settings."""
        pass
    
    @abc.abstractmethod
    def update_settings(self, user_id: int, **kwargs: Any) -> bool:
        """Update user settings."""
        pass


class DAOFactory(abc.ABC):
    """Factory for creating DAO instances."""
    
    @abc.abstractmethod
    def create_user_dao(self) -> UserDAO:
        pass
    
    @abc.abstractmethod
    def create_portfolio_dao(self) -> PortfolioDAO:
        pass
    
    @abc.abstractmethod
    def create_watchlist_dao(self) -> WatchlistDAO:
        pass
    
    @abc.abstractmethod
    def create_transaction_dao(self) -> TransactionDAO:
        pass
    
    @abc.abstractmethod
    def create_prediction_dao(self) -> PredictionDAO:
        pass
    
    @abc.abstractmethod
    def create_paper_trading_dao(self) -> PaperTradingDAO:
        pass
    
    @abc.abstractmethod
    def create_audit_dao(self) -> AuditDAO:
        pass
    
    @abc.abstractmethod
    def create_settings_dao(self) -> SettingsDAO:
        pass
