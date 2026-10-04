"""Tests for persisted sentiment snapshots and the trend reader (K4)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.sentiment_snapshot import (  # noqa: E402
    capture_snapshot,
    history,
    label_for,
)


def _fresh_db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    path = handle.name

    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS sentiment_snapshots("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, snapshot_at TEXT NOT NULL, "
            "avg_score REAL, headline_count INTEGER NOT NULL DEFAULT 0, scored_count INTEGER NOT NULL DEFAULT 0, "
            "source TEXT, UNIQUE(symbol, snapshot_at))"
        )
        connection.commit()
        return connection

    return factory


def test_capture_averages_scored_headlines_and_reports_unscored():
    factory = _fresh_db()
    headlines = [
        {"title": "Profits beat estimates as orders surge", "sentiment": {"score": 0.6}},
        {"title": "Routine board meeting scheduled", "sentiment": {"score": 0.0}},
        {"title": "Class action lawsuit filed", "sentiment": {"score": -0.5}},
    ]
    captured = capture_snapshot("Reliance", headlines=headlines, connection_factory=factory)
    assert captured["symbol"] == "RELIANCE"
    assert captured["avg_score"] == round((0.6 + 0.0 - 0.5) / 3, 4)
    assert captured["label"] == "neutral"
    assert captured["headline_count"] == 3
    assert captured["scored_count"] == 3
    assert captured["source"] == "inline"


def test_capture_scores_headlines_without_prior_sentiment():
    factory = _fresh_db()
    headlines = [{"title": "Strong revenue growth and record deliveries"}]
    captured = capture_snapshot("TCS", headlines=headlines, connection_factory=factory)
    assert captured["avg_score"] is not None
    assert captured["label"] == "positive"
    assert captured["scored_count"] == 1


def test_capture_with_empty_news_produces_null_score():
    factory = _fresh_db()

    def loader(_symbol):
        return {"symbol": "INFY", "items": [], "source": "test-feed"}

    captured = capture_snapshot("INFY", news_loader=loader, connection_factory=factory)
    assert captured["avg_score"] is None
    assert captured["label"] == "unknown"
    assert captured["headline_count"] == 0


def test_history_returns_chronological_series_within_window():
    factory = _fresh_db()
    base = datetime.now(timezone.utc) - timedelta(days=2)
    for index in range(3):
        capture_snapshot(
            "RELIANCE",
            headlines=[{"title": "Neutral daily headline", "sentiment": {"score": 0.1}}],
            connection_factory=factory,
            snapshot_at=base + timedelta(days=index),
        )
    result = history("reliance", days=10, connection_factory=factory)
    assert result["symbol"] == "RELIANCE"
    assert result["snapshot_count"] == 3
    stamps = [item["snapshot_at"] for item in result["series"]]
    assert stamps == sorted(stamps)
    assert result["series"][0]["label"] == "neutral"


def test_history_respects_days_window_and_staleness():
    factory = _fresh_db()
    now = datetime.now(timezone.utc)
    capture_snapshot(
        "INFY",
        headlines=[{"title": "Older headline", "sentiment": {"score": -0.2}}],
        connection_factory=factory,
        snapshot_at=now - timedelta(days=25),
    )
    recent = capture_snapshot(
        "INFY",
        headlines=[{"title": "Fresh headline", "sentiment": {"score": 0.3}}],
        connection_factory=factory,
        snapshot_at=now - timedelta(days=7),
    )
    result = history("INFY", days=30, connection_factory=factory)
    assert result["snapshot_count"] == 2
    assert result["series"][-1]["snapshot_at"] == recent["snapshot_at"]
    short = history("INFY", days=10, connection_factory=factory)
    assert short["snapshot_count"] == 1

    old_only_factory = _fresh_db()
    capture_snapshot(
        "XYZ",
        headlines=[{"title": "Stale", "sentiment": {"score": 0.1}}],
        connection_factory=old_only_factory,
        snapshot_at=now - timedelta(days=60),
    )
    stale_result = history("XYZ", days=120, connection_factory=old_only_factory)
    assert stale_result["is_stale"] is True


def test_history_of_unknown_symbol_is_empty_and_stale():
    factory = _fresh_db()
    result = history("NONEXISTENT", days=30, connection_factory=factory)
    assert result["snapshot_count"] == 0
    assert result["series"] == []
    assert result["is_stale"] is True


def test_concurrent_snapshot_for_same_timestamp_upserts():
    factory = _fresh_db()
    stamp = datetime.now(timezone.utc) - timedelta(days=1)
    first = capture_snapshot(
        "TCS",
        headlines=[{"title": "Boom", "sentiment": {"score": 0.5}}],
        connection_factory=factory,
        snapshot_at=stamp,
    )
    second = capture_snapshot(
        "TCS",
        headlines=[{"title": "Second thought", "sentiment": {"score": -0.4}}],
        connection_factory=factory,
        snapshot_at=stamp,
    )
    assert first["snapshot_at"] == second["snapshot_at"]
    result = history("TCS", days=10, connection_factory=factory)
    assert result["snapshot_count"] == 1
    assert result["series"][0]["avg_score"] == -0.4


def test_label_for_thresholds():
    assert label_for(0.12) == "positive"
    assert label_for(0.11) == "neutral"
    assert label_for(-0.12) == "negative"
    assert label_for(-0.05) == "neutral"
    assert label_for(None) == "unknown"
