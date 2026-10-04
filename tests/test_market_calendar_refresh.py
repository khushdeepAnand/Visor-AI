from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from services.market_calendar import (
    CalendarRefreshError,
    IST,
    market_status,
    refresh_calendar,
    resolve_year_calendar,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "nse_calendar_cache.json"
    monkeypatch.setenv("STOCKPILOT_NSE_CALENDAR_CACHE_PATH", str(cache_file))
    monkeypatch.delenv("STOCKPILOT_NSE_CALENDAR_URL", raising=False)
    return cache_file


def test_unknown_future_year_falls_back_safely_without_crashing(isolated_cache):
    # 27 Jan 2031 is a Monday and outside the bundled/cached calendar years.
    status = market_status(datetime(2031, 1, 27, 10, 0, tzinfo=IST))
    assert status["calendar_verified"] is False
    assert "not yet verified" in status["reason"]
    assert status["is_open"] is True  # weekday/hours logic still applies


def test_unknown_year_weekend_is_still_detected_as_closed(isolated_cache):
    # 26 Jan 2031 is a Sunday.
    status = market_status(datetime(2031, 1, 26, 10, 0, tzinfo=IST))
    assert status["is_open"] is False
    assert status["reason"].startswith("Weekend")


def test_refresh_calendar_requires_a_source(isolated_cache):
    with pytest.raises(CalendarRefreshError):
        refresh_calendar(2027)


def test_refresh_calendar_network_failure_is_wrapped_and_never_crashes_caller(isolated_cache):
    with patch("requests.get", side_effect=ConnectionError("no route to host")):
        with pytest.raises(CalendarRefreshError):
            refresh_calendar(2027, source_url="https://example.invalid/nse-calendar-2027.json")
    # A failed refresh must not have written a partial/corrupt cache.
    assert not isolated_cache.exists()


def test_refresh_calendar_persists_and_is_then_used_by_market_status(isolated_cache):
    fake_rows = [
        {"date": "2027-01-26", "description": "Republic Day", "type": "holiday"},
        {"date": "2027-02-01", "description": "Union Budget live session", "type": "special_session"},
    ]

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return fake_rows

    with patch("requests.get", return_value=_FakeResponse()):
        result = refresh_calendar(2027, source_url="https://example.invalid/nse-calendar-2027.json")

    assert result["verified"] is True
    assert result["holidays"][date(2027, 1, 26)] == "Republic Day"

    # And the cache file is real, on disk, and readable independently.
    cached = json.loads(isolated_cache.read_text())
    assert "2027" in cached

    # 26 Jan 2027 is a Tuesday -- a real trading day but for the refreshed holiday.
    status = market_status(datetime(2027, 1, 26, 10, 0, tzinfo=IST))
    assert status["is_open"] is False
    assert status["holiday"] == "Republic Day"
    assert status["calendar_verified"] is True
    assert status["calendar_source"] == "https://example.invalid/nse-calendar-2027.json"

    # 1 Feb 2027 special session overrides the weekend/holiday check.
    status = market_status(datetime(2027, 2, 1, 10, 0, tzinfo=IST))
    assert status["is_open"] is True
    assert status["special_session"] == "Union Budget live session"


def test_refresh_calendar_rejects_malformed_rows(isolated_cache):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"description": "missing date field"}]

    with patch("requests.get", return_value=_FakeResponse()):
        with pytest.raises(CalendarRefreshError):
            refresh_calendar(2028, source_url="https://example.invalid/bad.json")


def test_utc_input_is_converted_to_ist_before_evaluating_the_session():
    # 09:00 UTC on a 2026 weekday is 14:30 IST -- inside the regular session.
    status = market_status(datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc))
    assert status["is_open"] is True
    assert status["timezone"] == "Asia/Kolkata"

    # 03:00 UTC the same day is 08:30 IST -- before the open.
    status = market_status(datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc))
    assert status["is_open"] is False
    assert "Pre-market" in status["reason"]


def test_resolve_year_calendar_prefers_cache_over_bundled(isolated_cache):
    # 2026 is bundled; simulate an operator-refreshed override taking precedence.
    cache = {
        "2026": {
            "holidays": {"2026-06-01": "Ad-hoc exchange closure"},
            "special_sessions": {},
            "source": "https://example.invalid/override-2026.json",
        }
    }
    isolated_cache.write_text(json.dumps(cache))
    resolved = resolve_year_calendar(2026)
    assert resolved["source"] == "https://example.invalid/override-2026.json"
    assert date(2026, 6, 1) in resolved["holidays"]
    assert date(2026, 1, 26) not in resolved["holidays"]  # bundled Republic Day is gone once overridden
