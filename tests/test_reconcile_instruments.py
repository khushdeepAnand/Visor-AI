"""Instrument-master reconciliation report (Part L3)."""
from __future__ import annotations

from services.market_data.base import Instrument
from services.market_data.reconcile_instruments import reconcile_instrument_master


def make_item(symbol: str, exchange: str, instrument_key: str | None, segment: str = "EQ") -> Instrument:
    return Instrument(
        symbol=symbol,
        name=symbol,
        exchange=exchange,
        segment=f"{exchange}_{segment}",
        instrument_type="EQ" if segment == "EQ" else "INDEX",
        instrument_key=instrument_key,
        isin=None,
    )


class StubCatalogue:
    def __init__(self, bundled, resolved) -> None:
        self._bundled = bundled
        self._resolved = resolved

    def _load_bundled(self):
        return self._bundled

    def load(self):
        return self._resolved


def test_in_sync_when_master_covers_universe() -> None:
    bundled = [make_item("RELIANCE", "NSE", None), make_item("TCS", "NSE", None)]
    resolved = [make_item("RELIANCE", "NSE", "NSE_EQ|RELIANCE"), make_item("TCS", "NSE", "NSE_EQ|TCS")]
    report = reconcile_instrument_master(StubCatalogue(bundled, resolved))
    assert report["recommended_action"] == "in_sync"
    assert report["missing"] == 0 and report["extra"] == 0
    assert report["exchanges"]["NSE"]["expected_members_covered_pct"] == 100.0


def test_missing_symbols_are_listed_with_sample() -> None:
    bundled = [make_item("RELIANCE", "NSE", None), make_item("MISSING", "NSE", None), make_item("BOFA", "BSE", None)]
    resolved = [make_item("RELIANCE", "NSE", "NSE_EQ|RELIANCE")]
    report = reconcile_instrument_master(StubCatalogue(bundled, resolved))
    assert report["missing"] == 2
    assert report["exchanges"]["NSE"]["missing_sample"] == ["MISSING"]
    assert report["exchanges"]["BSE"]["missing_sample"] == ["BOFA"]
    assert report["exchanges"]["NSE"]["expected_members_covered_pct"] == 50.0
    assert report["recommended_action"] == "refresh"


def test_extra_master_values_are_listed() -> None:
    bundled = [make_item("RELIANCE", "NSE", None)]
    resolved = [make_item("RELIANCE", "NSE", "NSE_EQ|RELIANCE"), make_item("NEWCOMER", "NSE", "NSE_EQ|NEWCOMER")]
    report = reconcile_instrument_master(StubCatalogue(bundled, resolved))
    assert report["extra"] == 1
    assert report["exchanges"]["NSE"]["extra_sample"] == ["NEWCOMER"]
    assert report["recommended_action"] == "refresh"


def test_exchange_buckets_are_disjoint() -> None:
    bundled = [make_item("SAME", "NSE", None), make_item("SAME", "BSE", None)]
    resolved = [make_item("SAME", "NSE", "NSE_EQ|SAME")]
    report = reconcile_instrument_master(StubCatalogue(bundled, resolved))
    assert report["exchanges"]["NSE"]["missing"] == 0
    assert report["exchanges"]["BSE"]["missing"] == 1