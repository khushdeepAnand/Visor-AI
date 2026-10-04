"""Tests for the descriptive morning brief.

These tests pin the two properties that matter: the arithmetic must be right,
and the brief must never quietly invent coverage it does not have.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.morning_brief import (  # noqa: E402
    MIN_SESSIONS,
    BriefUnavailable,
    build_morning_brief,
    symbol_snapshot,
)


def _frame(sessions: int, *, start: float = 100.0, step: float = 1.0, volume: float = 1000.0) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=sessions, freq="B")
    closes = [start + step * index for index in range(sessions)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": [value - 0.5 for value in closes],
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [volume] * sessions,
        }
    )


def _loader(mapping):
    def loader(symbol: str) -> pd.DataFrame:
        if symbol not in mapping:
            raise KeyError(symbol)
        return mapping[symbol]

    return loader


NOW = datetime(2026, 4, 10, 3, 30, tzinfo=timezone.utc)


def test_snapshot_reports_exact_one_session_change():
    frame = _frame(40, start=100.0, step=1.0)
    snapshot = symbol_snapshot("RELIANCE", frame, now=NOW)
    assert snapshot["included"] is True
    # Closes run 100..139, so the last two closes are 138 and 139.
    assert snapshot["last_close"] == 139.0
    assert snapshot["change_pct_1d"] == pytest.approx(round(1 / 138 * 100, 2), abs=0.01)
    assert snapshot["sessions_available"] == 40


def test_snapshot_excludes_short_history_with_a_reason():
    snapshot = symbol_snapshot("NEWLIST", _frame(MIN_SESSIONS - 1), now=NOW)
    assert snapshot["included"] is False
    assert snapshot["reason"] == "insufficient_history"
    assert snapshot["sessions_required"] == MIN_SESSIONS


def test_snapshot_excludes_missing_history():
    assert symbol_snapshot("NOTHING", None)["reason"] == "history_unavailable"
    assert symbol_snapshot("EMPTY", pd.DataFrame())["reason"] == "history_unavailable"


def test_volume_ratio_is_relative_to_its_own_baseline():
    frame = _frame(40, volume=1000.0)
    frame.loc[frame.index[-1], "volume"] = 3000.0
    snapshot = symbol_snapshot("SPIKE", frame, now=NOW)
    # 20-session mean = (19 * 1000 + 3000) / 20 = 1100 -> ratio 3000/1100.
    assert snapshot["volume_vs_20d_average"] == pytest.approx(3000 / 1100, abs=0.01)


def test_brief_ranks_movers_by_realized_change_only():
    up = _frame(40, start=100.0, step=2.0)
    down = _frame(40, start=200.0, step=-1.0)
    flat = _frame(40, start=50.0, step=0.0)
    brief = build_morning_brief(
        symbols=["UP", "DOWN", "FLAT"],
        history_loader=_loader({"UP": up, "DOWN": down, "FLAT": flat}),
        now=NOW,
    )
    assert [row["symbol"] for row in brief["movers"]["gainers"]][0] == "UP"
    assert [row["symbol"] for row in brief["movers"]["losers"]][0] == "DOWN"
    assert "not a ranking" in brief["movers"]["basis"].lower()


def test_brief_records_excluded_symbols_instead_of_defaulting():
    brief = build_morning_brief(
        symbols=["GOOD", "SHORT", "MISSING"],
        history_loader=_loader({"GOOD": _frame(40), "SHORT": _frame(5)}),
        now=NOW,
    )
    assert brief["coverage"]["included"] == 1
    reasons = {row["symbol"]: row["reason"] for row in brief["coverage"]["excluded"]}
    assert reasons["SHORT"] == "insufficient_history"
    assert reasons["MISSING"] == "history_unavailable"


def test_brief_is_withheld_when_nothing_can_be_described():
    with pytest.raises(BriefUnavailable) as excinfo:
        build_morning_brief(symbols=["A", "B"], history_loader=_loader({}), now=NOW)
    assert excinfo.value.code == "brief_history_unavailable"


def test_brief_requires_at_least_one_symbol():
    with pytest.raises(BriefUnavailable) as excinfo:
        build_morning_brief(symbols=[], history_loader=_loader({}), now=NOW)
    assert excinfo.value.code == "brief_symbols_required"


def test_symbol_cap_is_enforced_and_reported():
    symbols = [f"SYM{index}" for index in range(20)]
    mapping = {symbol: _frame(40) for symbol in symbols}
    brief = build_morning_brief(
        symbols=symbols,
        history_loader=_loader(mapping),
        now=NOW,
        max_symbols=5,
    )
    assert brief["coverage"]["considered"] == 5
    assert brief["coverage"]["truncated"] is True


def test_headlines_absent_source_is_stated_not_inferred():
    brief = build_morning_brief(
        symbols=["GOOD"],
        history_loader=_loader({"GOOD": _frame(40)}),
        news_loader=lambda symbol: {"status": "unavailable", "reason": "news_source_not_configured", "items": []},
        now=NOW,
    )
    assert brief["headlines"]["state"] == "unavailable"
    assert brief["headlines"]["items"] == []
    assert "nothing is inferred" in brief["headlines"]["note"].lower()


def test_news_loader_failure_does_not_break_the_brief():
    def exploding(symbol: str):
        raise RuntimeError("provider down")

    brief = build_morning_brief(
        symbols=["GOOD"],
        history_loader=_loader({"GOOD": _frame(40)}),
        news_loader=exploding,
        now=NOW,
    )
    assert brief["headlines"]["state"] == "unavailable"
    assert brief["coverage"]["included"] == 1


def test_headlines_are_attributed_when_available():
    payload = {
        "status": "available",
        "items": [
            {
                "title": "Company reports quarterly numbers",
                "url": "https://example.test/a",
                "publisher": "Example Wire",
                "published_at": "2026-04-09T10:00:00+05:30",
                "sentiment": {"score": 0.4, "label": "positive"},
            }
        ],
    }
    brief = build_morning_brief(
        symbols=["GOOD"],
        history_loader=_loader({"GOOD": _frame(40)}),
        news_loader=lambda symbol: payload,
        now=NOW,
    )
    assert brief["headlines"]["state"] == "available"
    item = brief["headlines"]["items"][0]
    assert item["publisher"] == "Example Wire"
    assert item["url"].startswith("https://")
    assert brief["headlines"]["mean_sentiment"] == pytest.approx(0.4)


def test_market_status_failure_is_surfaced_not_faked():
    def exploding():
        raise RuntimeError("calendar unavailable")

    brief = build_morning_brief(
        symbols=["GOOD"],
        history_loader=_loader({"GOOD": _frame(40)}),
        market_status_loader=exploding,
        now=NOW,
    )
    assert brief["session"]["state"] == "unavailable"


def test_brief_never_claims_to_be_a_forecast():
    brief = build_morning_brief(
        symbols=["GOOD"],
        history_loader=_loader({"GOOD": _frame(40)}),
        now=NOW,
    )
    assert brief["evidence"]["is_forecast"] is False
    assert brief["evidence"]["is_recommendation"] is False
    assert any("not a forecast" in text.lower() or "does not establish" in text.lower() for text in brief["disclosures"])
    snapshot = brief["symbols"][0]
    forbidden = {"target_price", "prediction", "forecast", "expected_return", "recommendation", "rating"}
    assert forbidden.isdisjoint(snapshot.keys())
