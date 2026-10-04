"""Unit tests for the options-implied expected-move crossover (Phase B item 6)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from services.expected_move import (
    atm_iv_from_chain,
    crossover_vs_forecast,
    expected_move_bands,
    expected_move_snapshot,
    model_crossover,
)


def _fake_chain(spot: float = 500.0, iv: float = 0.20) -> dict[str, Any]:
    return {
        "is_live": True,
        "is_stale": False,
        "source": "Upstox",
        "fetched_at": "2026-09-22T10:00:00+00:00",
        "rows": [
            {"expiry": "2026-10-27", "strike": round(spot * 0.98, 2), "underlying_spot": spot,
             "ce": {"iv": iv, "ltp": 9.0}, "pe": {"iv": iv * 1.05, "ltp": 8.0}},
            {"expiry": "2026-10-27", "strike": spot, "underlying_spot": spot,
             "ce": {"iv": iv, "ltp": 12.0}, "pe": {"iv": iv, "ltp": 11.0}},
            {"expiry": "2026-10-27", "strike": round(spot * 1.02, 2), "underlying_spot": spot,
             "ce": {"iv": iv * 0.98, "ltp": 15.0}, "pe": {"iv": iv, "ltp": 14.0}},
            {"expiry": "2026-10-27", "strike": round(spot * 1.15, 2), "underlying_spot": spot,
             "ce": {"iv": iv, "ltp": 3.0}, "pe": {"iv": iv, "ltp": 3.0}},
        ],
    }


def test_bands_scale_with_sigma_and_sqrt_time() -> None:
    spot = 20_000.0
    iv = 0.15
    days = 30.0
    bands = expected_move_bands(spot, iv, days)
    assert bands is not None
    assert bands["horizon_days"] == 30
    scale = math.sqrt(30.0 / 252.0)
    levels = {float(level["sigma"]): level for level in bands["expected_moves"]}
    one = levels[1.0]
    two = levels[2.0]
    move1 = spot * 1.0 * iv * scale
    assert one["low"] == round(spot - move1, 2)
    assert one["high"] == round(spot + move1, 2)
    assert abs(one["move_pct"] - (1.0 * iv * scale * 100.0)) < 1e-4
    assert abs(two["move_pct"] - 2.0 * one["move_pct"]) < 1e-6
    assert two["low"] < one["low"] < one["high"] < two["high"]


def test_bands_reject_invalid_inputs() -> None:
    assert expected_move_bands(0.0, 0.2, 30) is None
    assert expected_move_bands(100, 0.0, 30) is None
    assert expected_move_bands(100, 6.0, 30) is None
    assert expected_move_bands(100, float("nan"), 30) is None


def test_atm_iv_averages_nearest_usable_strike() -> None:
    chain = _fake_chain(spot=500.0, iv=0.20)
    atm = atm_iv_from_chain(chain)
    assert atm["iv"] is not None
    # The strike at spot carries CE 0.20 and PE 0.20 (nearest row).
    assert atm["strike"] == 500.0
    assert abs(atm["iv"] - 0.20) < 1e-9
    # Strikes >5% away are never used.
    assert atm["count"] == 2


def test_atm_iv_prefers_spot_from_chain() -> None:
    chain = _fake_chain(spot=482.0, iv=0.18)
    atm = atm_iv_from_chain(chain)
    assert atm["spot_price"] == 482.0
    assert abs(atm["iv"] - 0.18) < 1e-9


def test_atm_iv_missing_when_no_usable_rows() -> None:
    chain = _fake_chain(iv=0.20)
    for row in chain["rows"]:
        row["ce"] = {"iv": None}
        row["pe"] = None
    atm = atm_iv_from_chain(chain)
    assert atm["iv"] is None
    assert atm["reason"] == "no_atm_iv_in_chain"


def test_model_crossover_aligned_and_flags() -> None:
    spot = 500.0
    bands = expected_move_bands(spot, 0.20, 30)
    assert bands is not None
    one = {float(level["sigma"]): level for level in bands["expected_moves"]}[1.0]
    two = {float(level["sigma"]): level for level in bands["expected_moves"]}[2.0]

    aligned = model_crossover(one["low"], one["high"], bands)
    assert aligned is not None
    assert aligned["flag"] == "aligned"

    tight = model_crossover(spot - 1.0, spot + 1.0, bands)
    assert tight is not None
    assert tight["flag"] == "model_tighter_than_implied"

    outer = model_crossover(two["low"] * 0.9, two["high"] * 1.1, bands)
    assert outer is not None
    assert outer["flag"] == "model_wider_than_implied"


def test_crossover_none_without_bands() -> None:
    assert model_crossover(10.0, 12.0, None) is None


def test_snapshot_unavailable_when_chain_not_live() -> None:
    snapshot = expected_move_snapshot(
        "NIFTY 50",
        expiry="2026-10-27",
        chain_provider=lambda underlying, expiry: {"is_live": False, "message": "no token configured"},
        expiry_provider=lambda underlying: ["2026-10-27"],
    )
    assert snapshot["available"] is False
    assert snapshot["reason"] == "chain_unavailable"


def test_snapshot_unavailable_without_expiry() -> None:
    snapshot = expected_move_snapshot(
        "NIFTY 50",
        expiry_provider=lambda underlying: [],
        chain_provider=lambda underlying, expiry: {},
    )
    assert snapshot["available"] is False
    assert snapshot["reason"] == "no_expiry_available"


def test_snapshot_pipeline_available() -> None:
    spot = 500.0
    chain = _fake_chain(spot=spot, iv=0.20)
    snapshot = expected_move_snapshot(
        "ACME",
        expiry="2026-10-27",
        spot_price=spot,
        chain_provider=lambda underlying, expiry: chain,
        expiry_provider=lambda underlying: ["2026-11-24", "2026-10-27"],
        now=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert snapshot["available"] is True
    assert snapshot["expiry"] == "2026-10-27"
    assert snapshot["days_to_expiry"] == 35.0
    assert abs(float(snapshot["atm_iv"]) - 0.20) < 1e-9
    assert snapshot["spot_price"] == spot
    levels = {float(level["sigma"]): level for level in snapshot["expected_moves"]}
    assert 1.0 in levels and 2.0 in levels
    assert levels[1.0]["low"] < spot < levels[1.0]["high"]
    assert levels[2.0]["low"] < levels[1.0]["low"]


def test_crossover_vs_forecast_wiring() -> None:
    spot = 500.0
    snapshot = expected_move_snapshot(
        "ACME",
        expiry="2026-10-27",
        spot_price=spot,
        chain_provider=lambda underlying, expiry: _fake_chain(spot=spot, iv=0.20),
        expiry_provider=lambda underlying: ["2026-10-27"],
        now=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    cross = crossover_vs_forecast(snapshot, spot - 1.0, spot + 1.0)
    assert cross is not None
    assert cross["flag"] == "model_tighter_than_implied"
    assert crossover_vs_forecast({**snapshot, "available": False}, spot - 1.0, spot + 1.0) is None
    assert crossover_vs_forecast(None, spot - 1.0, spot + 1.0) is None