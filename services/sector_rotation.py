"""Sector rotation view with an honest coverage report (K3).

The repo does NOT hold currency sector metadata: the runtime instrument master
(`instruments_india.json`) has no sector/industry field at all, and the search
catalogue (`symbols.sector`) is populated only for the handful of bundled BSE
rows. Per the v11 prompt, this feature must confirm coverage *before* exposing
the view, and must ship with an explicit "partial coverage" label while the
source data is incomplete --- which is the honest state today.

The exposure is therefore split in two parts:

* ``sector_coverage()`` reports real numbers (catalogue rows, sector-populated
  rows, distinct sectors, runtime EQ universe size) so operators can see how
  thin the source is;
* ``sector_rotation()`` groups whatever sector assignments DO exist and
  attaches a realized 5-session change per symbol, with a null aggregate when a
  sector cannot be measured. Every disclosure states the partial coverage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

try:  # package layout
    from database import get_connection as _default_get_connection
    from build_market_database import build as _build_catalogue
except Exception:  # pragma: no cover - standalone/flat use
    _default_get_connection = None  # type: ignore[assignment]
    _build_catalogue = None  # type: ignore[assignment]

ConnectionFactory = Callable[[], Any]

EQUALITY_SEGMENTS = ("NSE_EQ", "BSE_EQ")


def _ensure_symbols_catalogue(connection_factory: ConnectionFactory | None = None) -> None:
    factory = connection_factory or _default_get_connection
    if factory is None:
        raise RuntimeError("No database connection factory is available.")
    connection = factory()
    try:
        count = int(connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
    finally:
        connection.close()
    if count == 0 and _build_catalogue is not None:
        _build_catalogue()


def _catalogue_counts(connection_factory: ConnectionFactory | None = None) -> dict[str, Any]:
    factory = connection_factory or _default_get_connection
    if factory is None:
        raise RuntimeError("No database connection factory is available.")
    _ensure_symbols_catalogue(factory)
    connection = factory()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*),
                   COUNT(CASE WHEN sector IS NOT NULL AND TRIM(sector) <> '' THEN 1 END),
                   COUNT(DISTINCT CASE WHEN sector IS NOT NULL AND TRIM(sector) <> '' THEN TRIM(sector) END)
            FROM symbols
            """
        ).fetchone()
    finally:
        connection.close()
    total, populated, distinct = int(row[0]), int(row[1]), int(row[2])
    total = max(total, 0)
    populated = max(0, min(populated, total))
    return {
        "total": total,
        "sector_populated": populated,
        "sector_populated_pct": round((populated / total * 100) if total else 0.0, 2),
        "distinct_sectors": max(0, distinct),
    }


def _runtime_eq_total() -> int | None:
    try:
        from services.market_data.instruments import CATALOGUE

        instruments = CATALOGUE.load()
        return sum(1 for item in instruments if (item.segment or "").upper() in EQUALITY_SEGMENTS)
    except Exception:  # pragma: no cover - any provider/environment failure
        return None


def sector_coverage(connection_factory: ConnectionFactory | None = None) -> dict[str, Any]:
    catalogue = _catalogue_counts(connection_factory)
    eq_total = _runtime_eq_total()
    partial = bool(catalogue["total"] and catalogue["sector_populated"] < catalogue["total"])
    return {
        "catalogue": catalogue,
        "runtime_eq_total": eq_total,
        "partial_coverage": partial,
        "basis": (
            "Sector metadata is read from the search catalogue's `symbols.sector` column. "
            "The runtime broker instrument master carries no sector field, so coverage can "
            "never exceed what the bundled catalogue holds."
        ),
    }


def _sector_roster(connection_factory: ConnectionFactory | None = None) -> dict[str, list[str]]:
    factory = connection_factory or _default_get_connection
    if factory is None:
        return {}
    connection = factory()
    try:
        rows = connection.execute(
            """
            SELECT TRIM(sector), symbol FROM symbols
            WHERE sector IS NOT NULL AND TRIM(sector) <> ''
            ORDER BY TRIM(sector) COLLATE NOCASE, symbol ASC
            """
        ).fetchall()
    finally:
        connection.close()
    roster: dict[str, list[str]] = {}
    for sector, symbol in rows:
        roster.setdefault(str(sector), []).append(str(symbol))
    return roster


def _five_session_change(symbol: str, loader: Callable[[str], Any]) -> float | None:
    try:
        frame = loader(symbol)
    except Exception:
        return None
    if frame is None or len(frame) < 6:
        return None
    closes = frame["close"].reset_index(drop=True)
    if closes.iloc[-6] in (0, None):
        return None
    return round(float((closes.iloc[-1] / closes.iloc[-6] - 1.0) * 100), 3)


def _default_history_loader(symbol: str) -> Any:
    from services.market_data.manager import MANAGER

    return MANAGER.get_history(symbol, timeframe="1D", window="1mo")


def sector_rotation(
    *,
    connection_factory: ConnectionFactory | None = None,
    history_loader: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    coverage = sector_coverage(connection_factory)
    roster = _sector_roster(connection_factory)
    loader = history_loader or _default_history_loader

    sectors: list[dict[str, Any]] = []
    measured = 0
    for sector, symbols in sorted(roster.items()):
        entries = [
            {"symbol": symbol, "change_pct_5d": _five_session_change(symbol, loader)}
            for symbol in symbols
        ]
        values = []
        for entry in entries:
            raw = entry["change_pct_5d"]
            if raw is not None:
                values.append(float(raw))
        avg = round(sum(values) / len(values), 3) if values else None
        measured += len(values)
        sectors.append(
            {
                "sector": sector,
                "symbol_count": len(entries),
                "measured_count": len(values),
                "avg_change_pct_5d": avg,
                "symbols": entries,
            }
        )

    total = coverage["catalogue"]["sector_populated"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "partial_coverage": coverage["partial_coverage"],
        "sector_count": len(sectors),
        "sectors": sectors,
        "disclosures": [
            f"PARTIAL COVERAGE: sector metadata exists for {coverage['catalogue']['sector_populated']} of "
            f"{coverage['catalogue']['total']} catalogue symbols "
            f"({coverage['catalogue']['sector_populated_pct']}%); "
            f"{total - measured} of those with sector data could not be measured for 5-session change "
            f"(insufficient history). The runtime broker master carries no sector field, so this is the ceiling.",
            "Sector grouping is a display of realized returns only. Grouped averages are not forecasts, "
            "rankings, or recommendations.",
        ],
    }