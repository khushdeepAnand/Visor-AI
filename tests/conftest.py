# ==========================================================
# Shared pytest fixtures
# ==========================================================
#
# Tests must never touch the real database/stockpilot.db that the
# running app uses. These fixtures point authentication.py and
# database.py at a throwaway SQLite file for the duration of each
# test, then let pytest's tmp_path clean it up automatically.

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Tests explicitly select the local-only JWT policy. No credential is created.
os.environ["STOCKPILOT_ENV"] = "test"
os.environ["STOCKPILOT_PROVIDER_MODE"] = "LIVE_ONLY"
os.environ["STOCKPILOT_CORS_ORIGINS"] = "http://localhost:3000,http://127.0.0.1:3000"

import authentication
import database
from middleware.observability import RATE_LIMITER


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Route every test, including API lifespan tests, to temporary SQLite."""
    RATE_LIMITER.clear()
    db_path = tmp_path / "test_stockpilot.db"
    monkeypatch.setattr(authentication, "DATABASE_PATH", db_path)
    monkeypatch.setattr(database, "DATABASE", str(db_path))
    monkeypatch.setattr(database, "DATABASE_DIR", str(tmp_path))
    return db_path


@pytest.fixture
def temp_db(isolated_database):
    """Create a fresh schema in the current test's isolated database."""
    database.create_tables()
    return isolated_database
