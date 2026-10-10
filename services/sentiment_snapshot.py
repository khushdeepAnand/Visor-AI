"""Persisted per-session sentiment snapshots and a trend reader (K4).

A snapshot folds whatever headlines are available for a symbol at capture time
into a single [-1, +1] score. Snapshots are immutable rows keyed by
``(symbol, snapshot_at)`` so the trend chart reflects what was actually scored
each session --- it records the *ingestion* history, not a re-computed figure.

The trend view is honest about thin series: a symbol with fewer than two
snapshots has no meaningful trend, and the response always reports the snapshot
count and the window used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from services.db.factory import get_database
from services.db.sqlite_impl import SQLiteDatabase

try:
    from database import get_connection as _default_get_connection
except Exception:  # pragma: no cover - standalone/flat use
    _default_get_connection = None  # type: ignore[assignment]

ConnectionFactory = Callable[[], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def label_for(score: float | None) -> str:
    """Map a [-1, +1] score to a label.

    Ordering matters: the positive test runs first so a strong positive score is
    never misread as weak-negative by the lower threshold.
    """
    if score is None:
        return "unknown"
    if score >= 0.12:
        return "positive"
    if score <= -0.12:
        return "negative"
    return "neutral"


def capture_snapshot(
    symbol: str,
    *,
    headlines: list[dict[str, Any]] | None = None,
    news_loader: Callable[[str], dict[str, Any]] | None = None,
    connection_factory: ConnectionFactory | None = None,
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    """Fold a symbol's latest headlines into one stored [-1, +1] snapshot.

    ``headlines`` short-circuits the news loader (for callers who already have
    items); otherwise ``news_loader(symbol)`` (default ``services.news.get_news``)
    is consulted. Returns the stored row plus what was actually scored.
    """
    factory = connection_factory or _default_get_connection
    if factory is None:
        raise RuntimeError("No database connection factory is available.")

    if headlines is None:
        loader = news_loader or _default_news_loader()
        payload = loader(symbol)
        items = [item for item in payload.get("items", []) if isinstance(item, dict)]
        source = str(payload.get("source") or "")
    else:
        items = list(headlines)
        source = "inline"

    scored: list[float] = []
    for item in items:
        sentiment = item.get("sentiment")
        if isinstance(sentiment, dict):
            value = sentiment.get("score")
        else:
            value = _default_scorer()(item.get("title") or "").get("score")
        if isinstance(value, (int, float)):
            scored.append(float(value))

    avg = round(sum(scored) / len(scored), 4) if scored else None
    timestamp = (snapshot_at or _now()).isoformat()
    symbol_clean = str(symbol).strip().upper()
    connection = SQLiteDatabase(connection_factory()) if connection_factory is not None else get_database()
    try:
        connection.execute(connection.sql(
            """
            INSERT INTO sentiment_snapshots (symbol, snapshot_at, avg_score, headline_count, scored_count, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, snapshot_at) DO UPDATE SET
                avg_score = excluded.avg_score,
                headline_count = excluded.headline_count,
                scored_count = excluded.scored_count,
                source = excluded.source
            """),
            (symbol_clean, timestamp, avg, len(items), len(scored), source),
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "symbol": symbol_clean,
        "snapshot_at": timestamp,
        "avg_score": avg,
        "label": label_for(avg),
        "headline_count": len(items),
        "scored_count": len(scored),
        "source": source,
    }


def history(
    symbol: str,
    *,
    days: int = 30,
    connection_factory: ConnectionFactory | None = None,
) -> dict[str, Any]:
    """Chronological snapshot series for a symbol within ``days``."""
    factory = connection_factory or _default_get_connection
    if factory is None:
        raise RuntimeError("No database connection factory is available.")
    days = max(1, min(int(days), 365))
    cutoff = (_now() - timedelta(days=days)).isoformat()
    symbol_clean = str(symbol).strip().upper()
    connection = SQLiteDatabase(connection_factory()) if connection_factory is not None else get_database()
    try:
        rows = connection.fetchall(connection.sql(
            """
            SELECT snapshot_at, avg_score, headline_count, scored_count, source
            FROM sentiment_snapshots
            WHERE symbol = ? AND snapshot_at >= ?
            ORDER BY snapshot_at ASC
            """),
            (symbol_clean, cutoff),
        )
    finally:
        connection.close()

    series = [
        {
            "snapshot_at": str(row["snapshot_at"]),
            "avg_score": row["avg_score"],
            "label": label_for(row["avg_score"]),
            "headline_count": int(row["headline_count"]),
            "scored_count": int(row["scored_count"]),
            "source": str(row["source"] or ""),
        }
        for row in rows
    ]
    return {
        "symbol": symbol_clean,
        "window_days": days,
        "snapshot_count": len(series),
        "series": series,
        "is_stale": not series or _is_stale(series[-1]["snapshot_at"]),
    }


def _default_news_loader() -> Callable[[str], dict[str, Any]]:
    from services.news import get_news

    return get_news


def _default_scorer() -> Callable[[str], dict[str, Any]]:
    from services.sentiment import score_headline

    return score_headline


def _is_stale(latest_iso: str, days: int = 7) -> bool:
    try:
        latest = datetime.fromisoformat(latest_iso)
    except ValueError:
        return True
    return (_now() - latest) > timedelta(days=days)
