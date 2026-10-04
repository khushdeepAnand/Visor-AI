"""Factory for creating DAO instances based on configuration."""
from __future__ import annotations

import os
from typing import Any, Optional

from .base import DAOFactory
from .sqlite_impl import SQLiteDAOFactory
from .postgres_impl import PostgresDAOFactory


def get_dao_factory(config: Optional[dict[str, Any]] = None) -> DAOFactory:
    """Get the appropriate DAO factory based on configuration.
    
    Args:
        config: Optional configuration dict. If None, reads from environment.
                Expected keys:
                - 'database_url': PostgreSQL DSN (if using Postgres)
                - 'database_type': 'postgresql' or 'sqlite' (default: sqlite)
                - 'pool_size': PostgreSQL connection pool size (default: 10)
    
    Returns:
        DAOFactory instance for the configured database
    """
    if config is None:
        config = {}
    
    # Read from environment if not in config
    db_type = str(config.get("database_type") or os.getenv("STOCKPILOT_DB_TYPE", "sqlite"))
    dsn = config.get("database_url") or os.getenv("STOCKPILOT_DATABASE_URL")
    pool_size = config.get("pool_size") or int(os.getenv("STOCKPILOT_DB_POOL_SIZE", "10"))
    
    if db_type.lower() == "postgresql" or (dsn and dsn.startswith("postgresql://")):
        if not dsn:
            raise ValueError("PostgreSQL DSN required when database_type is 'postgresql'")
        return PostgresDAOFactory(dsn, pool_size)
    
    return SQLiteDAOFactory()


def create_dao_factory(database_url: Optional[str] = None, 
                       pool_size: int = 10) -> DAOFactory:
    """Convenience function to create a DAO factory.
    
    Args:
        database_url: PostgreSQL DSN. If None, uses SQLite.
        pool_size: PostgreSQL connection pool size.
    
    Returns:
        DAOFactory instance
    """
    if database_url:
        return PostgresDAOFactory(database_url, pool_size)
    return SQLiteDAOFactory()
