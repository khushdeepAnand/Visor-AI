"""Readiness checks use SQLite and inspection only; never a Postgres server."""
import hashlib
import inspect
import json
import runpy
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.db import factory, sqlite_impl

ROOT = Path(__file__).resolve().parents[1]
DB_VARIABLES = ("DB_BACKEND", "STOCKPILOT_DB_TYPE", "STOCKPILOT_DATABASE_URL", "DATABASE_URL", "DATABASE_MIGRATION_URL")


@pytest.fixture(autouse=True)
def clear_db_configuration(monkeypatch):
    for name in DB_VARIABLES:
        monkeypatch.delenv(name, raising=False)


# The prepare-only checkpoint's whole-file SQLite byte freeze is superseded
# by the user-authorized repository adoption. Snapshot/connection behavior is
# now proved by test_repository_snapshot.py; URL-selection byte parity below
# remains a standing requirement.


def test_default_sqlite_results_sql_and_database_bytes_ignore_future_urls(temp_db, monkeypatch):
    seed = sqlite3.connect(str(temp_db))
    seed.execute("PRAGMA journal_mode=DELETE")
    initial = seed.serialize()
    seed.close()

    class FixedClock:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 10, 7, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(sqlite_impl, "datetime", FixedClock)
    monkeypatch.setattr(factory, "PostgresDAOFactory", lambda *a, **k: pytest.fail("SQLite selected a Postgres factory"))

    def snapshot():
        conn = sqlite3.connect(":memory:")
        conn.deserialize(initial)
        conn.create_function("current_timestamp", 0, lambda: "2026-10-07 12:00:00")
        sql = []
        conn.set_trace_callback(sql.append)
        monkeypatch.setattr(sqlite_impl, "get_connection", lambda: conn)
        dao = factory.get_dao_factory()
        assert type(dao) is sqlite_impl.SQLiteDAOFactory
        uid = dao.create_user_dao().create_user("Parity User", "parity@example.test", "hashed-value", date_of_birth="1990-01-01")
        dao.create_portfolio_dao().add_holding(uid, "TCS", "TCS", 2, 100)
        dao.create_watchlist_dao().add_symbol(uid, "tcs")
        result = json.dumps({"user": dao.create_user_dao().get_user_by_id(uid),
                             "holdings": dao.create_portfolio_dao().get_holdings(uid),
                             "watchlist": dao.create_watchlist_dao().get_watchlist(uid)}, sort_keys=True).encode()
        state = conn.serialize()
        dao.db.close()
        return result, sql, state

    baseline = snapshot()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/pooled")
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql://unused.invalid/direct")
    assert snapshot() == baseline
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    assert snapshot() == baseline


@pytest.mark.parametrize("selector", ["DB_BACKEND", "STOCKPILOT_DB_TYPE"])
def test_explicit_postgres_uses_pooled_url_only(monkeypatch, selector):
    monkeypatch.setenv(selector, "postgresql")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/pooled")
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql://unused.invalid/direct")
    monkeypatch.setattr(factory, "PostgresDAOFactory", lambda dsn, size: (dsn, size))
    assert factory.get_dao_factory() == ("postgresql://unused.invalid/pooled", 10)


def test_invalid_backend_never_silently_selects_sqlite(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgreql")
    with pytest.raises(ValueError, match="backend must be sqlite or postgresql"):
        factory.get_dao_factory()


def test_selector_whitespace_and_case_are_normalized(monkeypatch):
    from services.db.configuration import postgres_selected
    monkeypatch.setenv("DB_BACKEND", " PostgreSQL ")
    assert postgres_selected()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/pooled")
    monkeypatch.setattr(factory, "PostgresDAOFactory", lambda dsn, size: (dsn, size))
    assert factory.get_dao_factory() == ("postgresql://unused.invalid/pooled", 10)


@pytest.mark.parametrize("variable", ["DATABASE_URL", "DATABASE_MIGRATION_URL", "STOCKPILOT_DATABASE_URL"])
def test_single_postgres_url_falls_back_for_app_and_migrations(monkeypatch, variable):
    from services.db.configuration import postgres_url
    monkeypatch.setenv(variable, "postgresql://unused.invalid/single")
    assert postgres_url() == postgres_url(migration=True) == "postgresql://unused.invalid/single"


def test_alembic_prefers_direct_url_only_when_postgres_selected(monkeypatch):
    from alembic import context
    from alembic.config import Config
    configured = []
    monkeypatch.setattr(context, "config", Config(), raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    monkeypatch.setattr(context, "configure", lambda **kwargs: configured.append(kwargs["url"]))
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/pooled")
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql://unused.invalid/direct")
    runpy.run_path(str(ROOT / "alembic" / "env.py"))
    assert configured[-1] == "sqlite:///stockpilot.db"
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    runpy.run_path(str(ROOT / "alembic" / "env.py"))
    assert configured[-1] == "postgresql://unused.invalid/direct"


def test_migration_runner_passes_direct_url_and_respects_explicit_override(monkeypatch):
    from alembic import command
    from scripts.run_migrations import upgrade_to_head
    monkeypatch.setattr("scripts.run_migrations._current_revision", lambda url: "20261010_01")
    urls = []
    monkeypatch.setattr(command, "upgrade", lambda cfg, revision: urls.append(cfg.get_main_option("sqlalchemy.url")))
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/pooled")
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql://unused.invalid/direct")
    assert upgrade_to_head() == "20261010_01"
    assert urls[-1] == "postgresql://unused.invalid/direct"
    assert upgrade_to_head("postgresql://unused.invalid/explicit") == "20261010_01"
    assert urls[-1] == "postgresql://unused.invalid/explicit"


def test_explicit_postgres_migration_never_silently_uses_sqlite(monkeypatch):
    from services.db.configuration import migration_url
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    with pytest.raises(ValueError, match="PostgreSQL migration URL required"):
        migration_url(sqlite_fallback="sqlite:///stockpilot.db")


def test_legacy_url_never_selects_postgres_without_an_explicit_selector(temp_db, monkeypatch):
    monkeypatch.setenv("STOCKPILOT_DATABASE_URL", "postgresql://unused.invalid/legacy")
    monkeypatch.setattr(factory, "PostgresDAOFactory", lambda dsn, size: (dsn, size))
    result = factory.get_dao_factory()
    assert type(result) is sqlite_impl.SQLiteDAOFactory
    result.db.close()
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    assert factory.get_dao_factory() == ("postgresql://unused.invalid/legacy", 10)
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    result = factory.get_dao_factory()
    assert type(result) is sqlite_impl.SQLiteDAOFactory
    result.db.close()


@pytest.mark.parametrize("url", ["postgresql://unused.invalid:6543/db", "postgresql://test.pooler.supabase.com:5432/db"])
def test_migrations_reject_known_supabase_poolers(url):
    from services.db.configuration import migration_url
    with pytest.raises(ValueError, match="direct PostgreSQL endpoint"):
        migration_url(configured_url=url, sqlite_fallback="sqlite:///unused.db")


def test_migration_failure_is_not_reported_as_success(monkeypatch):
    from alembic import command
    from scripts.run_migrations import upgrade_to_head
    def fail(*args):
        raise RuntimeError("disposable failure")
    monkeypatch.setattr(command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="disposable failure"):
        upgrade_to_head("postgresql://unused.invalid/direct")


def test_incomplete_postgres_runtime_never_falls_back_to_application_sqlite(temp_db, monkeypatch):
    import database
    monkeypatch.setenv("DB_BACKEND", "postgresql")
    with pytest.raises(RuntimeError, match="Refusing to write the application SQLite"):
        database.get_connection()
    # An explicitly scoped local sidecar remains available under either selector.
    connection = database._open_connection(temp_db.parent / "local-sidecar.db")
    connection.close()


def test_parity_check_never_constructs_databases_and_detects_signature_drift(monkeypatch):
    from scripts.verify_backend_parity import check_parity, compare_classes
    from services.db import postgres_impl
    monkeypatch.setattr(postgres_impl, "ThreadedConnectionPool", lambda *a, **k: pytest.fail("Parity check connected"))
    monkeypatch.setattr(sqlite_impl, "get_connection", lambda: pytest.fail("Parity check opened SQLite"))
    assert check_parity() == []

    class Reference:
        def read(self, user_id: int, limit: int = 50) -> dict: pass

    class DifferentDefault:
        def read(self, user_id: int, limit: int = 100) -> dict: pass

    class Missing:
        pass

    assert any("read" in issue for issue in compare_classes(Reference, DifferentDefault))
    assert any("read" in issue for issue in compare_classes(Reference, Missing))
    assert inspect.isclass(postgres_impl.PostgresDAOFactory)
