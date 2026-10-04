"""NSE cash-market session status with an annually-refreshable holiday calendar.

Design:
  * ``BUNDLED_HOLIDAYS`` / ``BUNDLED_SPECIAL_SESSIONS`` ship the officially
    circulated 2026 NSE calendar (NSE Capital Market circular
    NSE/CMTR/71775, 12-Dec-2025) so the app is always correct for 2026 with
    zero network access.
  * ``refresh_calendar(year, ...)`` can pull a future year's calendar from a
    configured source (``STOCKPILOT_NSE_CALENDAR_URL``) and persist it to a
    small on-disk JSON cache (``STOCKPILOT_NSE_CALENDAR_CACHE_PATH``), so a
    freshly-circulated NSE calendar can be adopted without a code change or
    redeploy.
  * ``market_status`` always resolves a year through, in order: the on-disk
    cache, the bundled fallback, then an explicit "unverified" state --
    it never raises and never silently treats an unknown year as holiday-free
    without saying so.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)

# NSE Capital Market circular NSE/CMTR/71775, 12-Dec-2025.
BUNDLED_HOLIDAYS: dict[int, dict[date, str]] = {
    2026: {
        date(2026, 1, 15): "Municipal Corporation Elections in Maharashtra",
        date(2026, 1, 26): "Republic Day",
        date(2026, 3, 3): "Holi",
        date(2026, 3, 26): "Shri Ram Navami",
        date(2026, 3, 31): "Shri Mahavir Jayanti",
        date(2026, 4, 3): "Good Friday",
        date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
        date(2026, 5, 1): "Maharashtra Day",
        date(2026, 5, 28): "Bakri Id",
        date(2026, 6, 26): "Muharram",
        date(2026, 9, 14): "Ganesh Chaturthi",
        date(2026, 10, 2): "Mahatma Gandhi Jayanti",
        date(2026, 10, 20): "Dussehra",
        date(2026, 11, 10): "Diwali-Balipratipada",
        date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
        date(2026, 12, 25): "Christmas",
    },
}

BUNDLED_SPECIAL_SESSIONS: dict[int, dict[date, str]] = {
    2026: {date(2026, 2, 1): "Union Budget live trading session"},
}

# Kept for backwards compatibility with any existing import of the old name.
NSE_2026_HOLIDAYS = BUNDLED_HOLIDAYS[2026]

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "nse_calendar_cache.json"


class CalendarRefreshError(RuntimeError):
    """Raised when refresh_calendar cannot reach or parse an external source."""


def _cache_path() -> Path:
    override = os.getenv("STOCKPILOT_NSE_CALENDAR_CACHE_PATH")
    return Path(override) if override else _DEFAULT_CACHE_PATH


def _read_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt cache must never take the whole app down -- fall back
        # as if no cache existed.
        return {}


def _write_cache(data: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _year_from_cache(year: int) -> dict[str, Any] | None:
    cache = _read_cache()
    entry = cache.get(str(year))
    if not entry:
        return None
    return entry


def resolve_year_calendar(year: int) -> dict[str, Any]:
    """Return holidays/special-sessions for ``year`` plus provenance metadata.

    Resolution order: on-disk refreshed cache -> bundled fallback -> unverified.
    """
    cached = _year_from_cache(year)
    if cached is not None:
        return {
            "holidays": {date.fromisoformat(d): reason for d, reason in cached.get("holidays", {}).items()},
            "special_sessions": {
                date.fromisoformat(d): reason for d, reason in cached.get("special_sessions", {}).items()
            },
            "source": cached.get("source", "refreshed_cache"),
            "verified": True,
        }
    if year in BUNDLED_HOLIDAYS:
        return {
            "holidays": BUNDLED_HOLIDAYS[year],
            "special_sessions": BUNDLED_SPECIAL_SESSIONS.get(year, {}),
            "source": "bundled_nse_circular",
            "verified": True,
        }
    return {"holidays": {}, "special_sessions": {}, "source": "unverified_fallback", "verified": False}


def refresh_calendar(
    year: int,
    source_url: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch a year's NSE holiday calendar from a configured source and cache it.

    Expects the source to return a JSON list of objects shaped like
    ``{"date": "YYYY-MM-DD", "description": "...", "type": "holiday" | "special_session"}``,
    which matches the shape NSE's own published circular PDFs are commonly
    transcribed into by exchange-calendar aggregators. Never called
    automatically -- an operator or a scheduled job invokes this once NSE
    circulates next year's calendar, and the safe bundled/offline fallback in
    ``resolve_year_calendar`` is used until then.
    """
    url = source_url or os.getenv("STOCKPILOT_NSE_CALENDAR_URL")
    if not url:
        raise CalendarRefreshError(
            "No calendar source configured. Set STOCKPILOT_NSE_CALENDAR_URL or pass source_url."
        )
    try:
        import requests

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:  # network, timeout, non-2xx, bad JSON -- all non-fatal to the app
        raise CalendarRefreshError(f"Could not refresh {year} NSE calendar from {url}: {exc}") from exc

    holidays: dict[str, str] = {}
    special_sessions: dict[str, str] = {}
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError) as exc:
            raise CalendarRefreshError(f"Malformed calendar row from {url}: {row!r}") from exc
        if row_date.year != year:
            continue
        description = str(row.get("description", "")).strip() or "Exchange holiday"
        if str(row.get("type", "holiday")).lower() == "special_session":
            special_sessions[row_date.isoformat()] = description
        else:
            holidays[row_date.isoformat()] = description

    cache = _read_cache()
    cache[str(year)] = {
        "holidays": holidays,
        "special_sessions": special_sessions,
        "source": url,
        "refreshed_at": datetime.now(IST).isoformat(),
    }
    _write_cache(cache)
    return resolve_year_calendar(year)


def market_status(now: datetime | None = None) -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    calendar = resolve_year_calendar(now.year)
    holiday = calendar["holidays"].get(now.date())
    special_session = calendar["special_sessions"].get(now.date())
    weekend = now.weekday() >= 5
    regular_open = (special_session is not None or (not weekend and holiday is None)) and OPEN <= now.time() <= CLOSE
    if special_session:
        reason = special_session if regular_open else f"{special_session} — outside regular session"
    elif holiday:
        reason = holiday
    elif weekend:
        reason = "Weekend"
    elif now.time() < OPEN:
        reason = "Pre-market / before regular cash session"
    elif now.time() > CLOSE:
        reason = "Regular cash session closed"
    else:
        reason = "Regular cash session open"
    # 8 Nov 2026 is a Sunday Diwali holiday with Muhurat Trading whose timings are not hardcoded.
    muhurat_pending = now.date() == date(2026, 11, 8)
    if not calendar["verified"]:
        reason = f"{reason} (calendar not yet verified for {now.year} -- weekend/hours logic only)"
    return {
        "exchange": "NSE",
        "timezone": "Asia/Kolkata",
        "regular_session": "09:15-15:30 IST",
        "is_open": regular_open,
        "reason": "Muhurat Trading timing must be supplied dynamically" if muhurat_pending else reason,
        "timestamp": now.isoformat(),
        "holiday": holiday,
        "special_session": special_session,
        "muhurat_timing_dynamic": muhurat_pending,
        "calendar_source": calendar["source"],
        "calendar_verified": calendar["verified"],
    }
