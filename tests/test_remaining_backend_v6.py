from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from prediction_lstm import chronological_fold_boundaries, promotion_gate
from services.market_data.derivatives_live import LiveDerivativesService, MarginRequest
from services.market_data.stream_leadership import StreamLeadership


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry_at: dict[str, float] = {}

    def _expire(self, key: str) -> None:
        deadline = self.expiry_at.get(key)
        if deadline is not None and time.monotonic() >= deadline:
            self.values.pop(key, None)
            self.expiry_at.pop(key, None)

    async def set(self, key, value, nx=False, px=None):
        self._expire(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        if px is not None:
            self.expiry_at[key] = time.monotonic() + float(px) / 1000.0
        return True

    async def get(self, key):
        self._expire(key)
        return self.values.get(key)

    async def pexpire(self, key, ttl):
        self._expire(key)
        if key not in self.values:
            return False
        self.expiry_at[key] = time.monotonic() + float(ttl) / 1000.0
        return True

    async def delete(self, key):
        self.values.pop(key, None)
        self.expiry_at.pop(key, None)
        return 1

    async def publish(self, channel, payload):
        return 1


def test_lstm_promotion_requires_multiple_stable_folds():
    assert promotion_gate([{"rmse": 9, "baseline_rmse": 10}])["promoted"] is False
    passed = promotion_gate([
        {"rmse": 9.0, "baseline_rmse": 10.0},
        {"rmse": 18.0, "baseline_rmse": 20.0},
        {"rmse": 26.5, "baseline_rmse": 30.0},
    ])
    assert passed["promoted"] is True
    failed = promotion_gate([
        {"rmse": 9.0, "baseline_rmse": 10.0},
        {"rmse": 19.6, "baseline_rmse": 20.0},
        {"rmse": 26.5, "baseline_rmse": 30.0},
    ])
    assert failed["promoted"] is False


@pytest.mark.asyncio
async def test_stream_leader_is_single_and_failover_occurs_after_lease_expiry():
    redis = FakeRedis()
    first = StreamLeadership(client=redis, lease_seconds=2)
    second = StreamLeadership(client=redis, lease_seconds=2)
    # Shorten only the unit-test lease after construction. Production keeps the
    # two-second minimum, while Redis TTL semantics are preserved here.
    first.lease_seconds = second.lease_seconds = 0.05
    assert await first.try_acquire("RELIANCE") is True
    assert await second.try_acquire("RELIANCE") is False

    # Simulate a killed leader: no graceful release and therefore no delete.
    await asyncio.sleep(0.07)
    assert await second.try_acquire("RELIANCE") is True


def test_option_chain_fallback_never_fabricates_live_fields(monkeypatch):
    service = LiveDerivativesService()
    monkeypatch.setattr(service.provider, "is_configured", lambda: False)
    monkeypatch.setattr(service, "_master_only_rows", lambda *_: [{"strike": 25000, "ce": {"ltp": None}, "pe": {"ltp": None}}])
    result = service.live_chain("NIFTY 50", "2026-08-27")
    assert result["is_live"] is False
    assert result["source"] == "instrument_master_only"
    assert result["rows"][0]["ce"]["ltp"] is None


def test_margin_fallback_is_explicit_approximation(monkeypatch):
    service = LiveDerivativesService()
    monkeypatch.setattr(service.provider, "is_configured", lambda: False)
    result = service.margin(MarginRequest(symbol="RELIANCE", quantity=1, price=2500, instrument_type="FUTURE", lot_size=1))
    assert result["approximate_margin"] is True
    assert result["source"] == "StockPilot approximation"
    assert result["required_margin"] > 0


def test_upstox_instrument_option_expiry_and_type_are_normalized():
    from services.market_data.instruments import _from_upstox_row
    row = {
        "segment": "NSE_FO", "name": "NIFTY", "exchange": "NSE",
        "expiry": 1787788799000, "instrument_type": "CE", "underlying_symbol": "NIFTY 50",
        "instrument_key": "NSE_FO|123", "lot_size": 75, "trading_symbol": "NIFTY CE", "strike_price": 25000,
    }
    item = _from_upstox_row(row)
    assert item is not None
    assert item.option_type == "CE"
    assert item.expiry == "2026-08-26"
    assert item.strike == 25000


def test_option_chain_normalizes_official_oi_change_and_first_level_depth():
    service = LiveDerivativesService()
    side = service._normalize_side(
        {
            "instrument_key": "NSE_FO|51059",
            "market_data": {
                "ltp": 2449.9,
                "volume": 100,
                "oi": 750,
                "prev_oi": 1500,
                "bid_price": 1856.65,
                "bid_qty": 1125,
                "ask_price": 1941.65,
                "ask_qty": 1125,
            },
            "option_greeks": {"iv": 20.1, "delta": 0.743, "gamma": 0.0001, "theta": -4.2, "vega": 4.17},
        },
        "CE",
    )
    assert side is not None
    assert side["oi_change"] == -750.0
    assert side["depth"] == {"bids": [[1856.65, 1125]], "asks": [[1941.65, 1125]]}
    assert side["iv"] == 20.1


def test_lstm_chronological_fold_harness_has_no_lookahead():
    folds = chronological_fold_boundaries(120, folds=3, initial_train_fraction=0.55)
    assert len(folds) == 3
    previous_test_end = 0
    for train_end, test_start, test_end in folds:
        assert train_end == test_start
        assert test_start < test_end
        assert test_start >= previous_test_end
        previous_test_end = test_end
    assert folds[-1][2] == 120
