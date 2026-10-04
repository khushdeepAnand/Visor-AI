"""Instrument-master reconciliation report (Part L3).

The bundled NSE/BSE symbol universe is the *expected* set; ``instruments_india.json``
is the *resolved* master. A drift between the two silently surfaces as
``history_unavailable`` for names that do exist, because quote/history lookups
probe the master snapshot. This module produces a read-only report of that
drift so an operator can trigger a refresh before customers see gaps.
"""
from __future__ import annotations

from typing import Any

from .instruments import CATALOGUE, InstrumentCatalogue


def reconcile_instrument_master(catalogue: InstrumentCatalogue | None = None) -> dict[str, Any]:
    """Compare the bundled expected universe against the resolved master snapshot.

    The report never mutates data: it just says what a refresh would change, so
    the scheduled/on-demand ``refresh_instruments`` action (which does mutate the
    snapshot) has an accountable trigger.
    """
    store = catalogue or CATALOGUE
    bundled = store._load_bundled()
    resolved = store.load()

    expected: dict[str, dict[str, Any]] = {"NSE": {}, "BSE": {}}
    for item in bundled:
        if item.exchange in expected:
            expected[item.exchange][item.symbol] = item

    resolved_by_exchange: dict[str, dict[str, Any]] = {"NSE": {}, "BSE": {}}
    for item in resolved:
        if item.exchange in resolved_by_exchange and item.symbol:
            resolved_by_exchange[item.exchange][item.symbol] = {
                "instrument_key": item.instrument_key,
                "segment": item.segment,
            }

    exchanges: dict[str, Any] = {}
    totals = {"expected": 0, "resolved": 0, "missing": 0, "extra": 0}
    for exchange in ("NSE", "BSE"):
        expected_symbols = set(expected[exchange])
        resolved_symbols = set(resolved_by_exchange[exchange])
        missing = sorted(expected_symbols - resolved_symbols)
        extra = sorted(resolved_symbols - expected_symbols)
        keyed_resolved = sum(1 for entry in resolved_by_exchange[exchange].values() if entry["instrument_key"])
        coverage = round(len(missing) / len(expected_symbols) * 100, 2) if expected_symbols else 100.0
        exchanges[exchange] = {
            "expected": len(expected_symbols),
            "resolved": len(resolved_symbols),
            "missing": len(missing),
            "missing_sample": missing[:25],
            "extra": len(extra),
            "extra_sample": extra[:25],
            "resolved_with_key": keyed_resolved,
            "expected_members_covered_pct": round(100.0 - coverage, 2),
        }
        totals["expected"] += len(expected_symbols)
        totals["resolved"] += len(resolved_symbols)
        totals["missing"] += len(missing)
        totals["extra"] += len(extra)

    return {
        "expected": totals["expected"],
        "resolved": totals["resolved"],
        "missing": totals["missing"],
        "extra": totals["extra"],
        "exchanges": exchanges,
        "recommended_action": (
            "refresh"
            if totals["missing"] or totals["extra"]
            else "in_sync"
        ),
        "note": (
            "Expected universe is the bundled NSE/BSE CSV excerpt; a non-zero "
            "missing/extra count is normal when the master contains more symbols "
            "than the bundled excerpt, and signals a refresh is due."
            if totals["missing"] or totals["extra"]
            else "Bundled expected universe and resolved master are in sync."
        ),
    }