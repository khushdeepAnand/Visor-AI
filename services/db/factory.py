"""Factory for creating DAO instances based on configuration."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Optional, Iterator

from .base import DAOFactory
from .sqlite_impl import SQLiteDAOFactory
from .postgres_impl import PostgresDAOFactory
from .configuration import postgres_url

_pools: dict[tuple[str, int], PostgresDAOFactory] = {}
_pool_lock = threading.Lock()


@contextmanager
def dao_session() -> Iterator[DAOFactory]:
    """Reuse process pools and release the calling thread's lease on every exit.

    Domain services own transaction boundaries; this scope never commits
    implicitly. Nested write operations must share this factory explicitly.
    """
    from .configuration import postgres_selected
    if postgres_selected():
        dsn = postgres_url()
        if not dsn:
            raise ValueError("PostgreSQL DSN required when Postgres is selected")
        size = int(os.getenv("STOCKPILOT_DB_POOL_SIZE", "10"))
        with _pool_lock:
            key = (dsn, size)
            if key not in _pools:
                _pools[key] = PostgresDAOFactory(dsn, size)
            factory: DAOFactory = _pools[key]
    else:
        factory = SQLiteDAOFactory()
    try:
        yield factory
    finally:
        factory.db.close()


def close_dao_pools() -> None:
    """Shutdown only after API/background users have stopped."""
    with _pool_lock:
        for factory in _pools.values():
            factory.db._shutdown_pool()
        _pools.clear()


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

    DB_BACKEND is an optional selector alias; the existing STOCKPILOT_DB_TYPE
    default remains sqlite. DATABASE_URL/DATABASE_MIGRATION_URL are read only
    when Postgres is explicitly selected. A URL never selects a backend.
    """
    if config is None:
        config = {}
    
    # Read from environment if not in config
    db_type = str(config.get("database_type") or os.getenv("DB_BACKEND") or os.getenv("STOCKPILOT_DB_TYPE", "sqlite"))
    dsn = config.get("database_url") or os.getenv("STOCKPILOT_DATABASE_URL")
    pool_size = config.get("pool_size") or int(os.getenv("STOCKPILOT_DB_POOL_SIZE", "10"))
    postgres = db_type.lower() in {"postgres", "postgresql"}
    if postgres:
        dsn = config.get("database_url") or postgres_url()
    
    if postgres:
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
