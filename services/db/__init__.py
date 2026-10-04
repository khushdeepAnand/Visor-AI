"""Database abstraction layer supporting SQLite and PostgreSQL with a unified DAO interface."""
from __future__ import annotations

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
from .sqlite_impl import SQLiteDAOFactory
from .postgres_impl import PostgresDAOFactory
from .factory import get_dao_factory

__all__ = [
    "DatabaseInterface",
    "UserDAO",
    "PortfolioDAO",
    "WatchlistDAO",
    "TransactionDAO",
    "PredictionDAO",
    "PaperTradingDAO",
    "AuditDAO",
    "SettingsDAO",
    "SQLiteDAOFactory",
    "PostgresDAOFactory",
    "get_dao_factory",
    "DAOFactory",
]
