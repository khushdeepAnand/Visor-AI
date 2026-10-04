"""Tests for live decay tracking."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.live_decay import (
    compute_live_vs_backtest_gap,
    detect_decay_signals,
    build_symbol_decay_status,
    decay_status_to_dict,
    get_user_facing_decay_card,
)


def _make_live_data(n: int = 50, coverage: float = 0.80, seed: int = 42) -> pd.DataFrame:
    """Create synthetic live data."""
    rng = np.random.default_rng(seed)
    actual = rng.normal(100, 5, n)
    # Create intervals that achieve target coverage
    half_width = 10.0
    low = actual - half_width * rng.uniform(0.5, 1.5, n)
    high = actual + half_width * rng.uniform(0.5, 1.5, n)
    median = (low + high) / 2

    # Adjust to hit target coverage
    if coverage < 1.0:
        n_miss = int(n * (1 - coverage))
        miss_idx = rng.choice(n, n_miss, replace=False)
        # Move some actuals outside intervals
        for idx in miss_idx:
            if rng.random() < 0.5:
                actual[idx] = low[idx] - rng.uniform(1, 5)
            else:
                actual[idx] = high[idx] + rng.uniform(1, 5)

    return pd.DataFrame({
        "actual": actual,
        "low": low,
        "high": high,
        "median": median,
        "confidence": 0.80,
    })


def test_compute_live_vs_backtest_gap():
    """Test live vs backtest gap computation."""
    # No decay
    result = compute_live_vs_backtest_gap(
        live_coverage=0.79, backtest_coverage=0.80,
        live_winkler=5.0, backtest_winkler=5.2,
        live_mase=0.85, backtest_mase=0.83,
    )
    assert result["decay_detected"] is False
    assert result["severity"] == "none"

    # Coverage decay
    result = compute_live_vs_backtest_gap(
        live_coverage=0.65, backtest_coverage=0.80,
        live_winkler=5.0, backtest_winkler=5.2,
        live_mase=0.85, backtest_mase=0.83,
    )
    assert result["decay_detected"] is True
    assert "Coverage gap" in str(result["flags"])

    # Winkler decay
    result = compute_live_vs_backtest_gap(
        live_coverage=0.79, backtest_coverage=0.80,
        live_winkler=8.0, backtest_winkler=5.0,
        live_mase=0.85, backtest_mase=0.83,
    )
    assert result["decay_detected"] is True
    assert any("Winkler" in f for f in result["flags"])

    # MASE decay
    result = compute_live_vs_backtest_gap(
        live_coverage=0.79, backtest_coverage=0.80,
        live_winkler=5.0, backtest_winkler=5.2,
        live_mase=1.5, backtest_mase=0.83,
    )
    assert result["decay_detected"] is True
    assert any("MASE" in f for f in result["flags"])


def test_detect_decay_signals_insufficient_data():
    """Test decay detection with insufficient live data."""
    live = pd.DataFrame({"actual": [100], "low": [95], "high": [105], "median": [100], "confidence": [0.8]})
    backtest = pd.DataFrame({"actual": [100]*100, "low": [95]*100, "high": [105]*100, "median": [100]*100, "confidence": [0.8]*100})

    signals = detect_decay_signals("TEST", 1, "T3", live, backtest, min_live_samples=20)
    assert len(signals) == 0


def test_detect_decay_signals_healthy():
    """Test decay detection with healthy live data."""
    live = _make_live_data(50, coverage=0.79, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    signals = detect_decay_signals("TEST", 1, "T3", live, backtest, min_live_samples=20)
    # Should have few or no signals for healthy data
    assert len(signals) <= 1


def test_detect_decay_signals_coverage_decay():
    """Test decay detection with coverage decay."""
    # Live data with poor coverage
    live = _make_live_data(50, coverage=0.60, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    signals = detect_decay_signals("TEST", 1, "T3", live, backtest, min_live_samples=20)
    assert len(signals) > 0
    assert any(s.signal_type == "coverage_decay" for s in signals)


def test_build_symbol_decay_status():
    """Test building complete decay status."""
    live = _make_live_data(50, coverage=0.79, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    status = build_symbol_decay_status("TEST", "T3", live, backtest)

    assert status.symbol == "TEST"
    assert status.tier == "T3"
    assert status.overall_status in ("healthy", "watch", "degraded")
    assert isinstance(status.signals, list)
    assert status.last_updated is not None


def test_decay_status_to_dict():
    """Test decay status conversion to dict."""
    live = _make_live_data(50, coverage=0.79, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    status = build_symbol_decay_status("TEST", "T3", live, backtest)
    d = decay_status_to_dict(status)

    assert "symbol" in d
    assert "tier" in d
    assert "overall_status" in d
    assert "live_coverage" in d
    assert "backtest_coverage" in d
    assert "auto_widened" in d
    assert "signals" in d
    assert "disclosure" in d


def test_get_user_facing_decay_card():
    """Test user-facing decay card."""
    live = _make_live_data(50, coverage=0.79, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    card = get_user_facing_decay_card("TEST", "T3", live, backtest)

    assert "summary" in card
    assert "disclosure" in card
    assert "signals" in card


def test_auto_widen():
    """Test auto-widen trigger."""
    # Live coverage much worse than backtest
    live = _make_live_data(50, coverage=0.60, seed=42)
    backtest = _make_live_data(200, coverage=0.80, seed=99)

    status = build_symbol_decay_status("TEST", "T3", live, backtest, auto_widen_threshold=0.15)

    assert status.auto_widened is True
    assert status.widen_factor > 1.0
    assert status.overall_status in ("watch", "degraded")  # Either watch or degraded


def test_thin_evidence_is_not_healthy_or_widened():
    live = _make_live_data(3, coverage=0.0)
    backtest = _make_live_data(50)
    status = build_symbol_decay_status("TEST", "T1", live, backtest, min_live_samples=20)
    assert status.overall_status == "insufficient_evidence"
    assert not status.auto_widened


def test_custom_tolerances_are_used():
    live = _make_live_data(50, coverage=0.60)
    backtest = _make_live_data(200, coverage=0.80, seed=99)
    signals = detect_decay_signals("TEST", 1, "T3", live, backtest, coverage_tolerance=0.9, winkler_tolerance=100, mase_tolerance=100)
    assert not signals


def test_decayed_card_does_not_claim_an_unexecuted_action():
    card = get_user_facing_decay_card("TEST", "T3", _make_live_data(50, coverage=0.0), _make_live_data(200))
    assert "recommended" in card["summary"].lower()
    assert "action_taken" not in card


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
