"""Tests for the pooled low-history forecast bridge.

These tests pin the guardrails around the bridge rather than a particular
numeric output: the flag must gate it, missing history must be reported with the
documented code, a fit must refuse to run without enough instruments, and any
published range must be internally consistent with its own evidence grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# forecasting/__init__.py imports the per-symbol interval engine, which depends
# on scipy and scikit-learn. The pooled model itself is numpy/pandas only, but
# the package import pulls the engine in, so skip instead of failing where the
# scientific stack is not installed.
pytest.importorskip("scipy", reason="scipy is required by forecasting/interval_forecast.py")
pytest.importorskip("sklearn", reason="scikit-learn is required by forecasting/interval_forecast.py")

from forecasting.pooled_cross_section import MIN_INSTRUMENTS, POOLED_MODEL_VERSION  # noqa: E402
from services.low_history_forecast import (  # noqa: E402
    DEFAULT_PEER_UNIVERSE,
    LOW_HISTORY_FLAG,
    MAX_PEERS,
    POOLED_MODEL_LABEL,
    LowHistoryUnavailable,
    pooled_low_history_forecast,
)

ENABLED = lambda: True  # noqa: E731 - test flag override
DISABLED = lambda: False  # noqa: E731 - test flag override


def _series(sessions: int, *, seed: int, start: float = 500.0) -> pd.DataFrame:
    """Deterministic pseudo-random walk with title-case provider columns."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.0004, scale=0.012, size=sessions)
    closes = start * np.exp(np.cumsum(steps))
    dates = pd.date_range("2024-01-01", periods=sessions, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "Open": closes * (1 - 0.002),
            "High": closes * (1 + 0.006),
            "Low": closes * (1 - 0.006),
            "Close": closes,
            "Volume": rng.integers(400_000, 900_000, size=sessions).astype(float),
        }
    )


def _universe(peer_count: int, *, target_sessions: int = 45, peer_sessions: int = 420) -> dict[str, pd.DataFrame]:
    panels = {"NEWCO": _series(target_sessions, seed=1, start=250.0)}
    for index, peer in enumerate(DEFAULT_PEER_UNIVERSE[:peer_count]):
        panels[peer] = _series(peer_sessions, seed=100 + index, start=400.0 + index * 25)
    return panels


def _loader(panels: dict[str, pd.DataFrame]):
    def loader(symbol: str) -> pd.DataFrame:
        key = str(symbol).strip().upper()
        if key not in panels:
            raise KeyError(f"no history for {key}")
        return panels[key]

    return loader


# -- gating ----------------------------------------------------------------


def test_flag_gates_the_entire_path():
    with pytest.raises(LowHistoryUnavailable) as excinfo:
        pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(_universe(8)),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:8],
            flag_override=DISABLED,
        )
    assert excinfo.value.code == "pooled_model_disabled"


def test_flag_name_matches_the_declared_feature_flag():
    assert LOW_HISTORY_FLAG == "pooled_low_history_model"
    assert MAX_PEERS >= MIN_INSTRUMENTS


# -- input failures --------------------------------------------------------


def test_missing_target_history_uses_the_documented_code():
    with pytest.raises(LowHistoryUnavailable) as excinfo:
        pooled_low_history_forecast(
            "GHOST",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(_universe(8)),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:8],
            flag_override=ENABLED,
        )
    assert excinfo.value.code == "history_unavailable"


def test_incomplete_columns_are_refused_not_patched():
    frame = _series(200, seed=5).drop(columns=["Volume"])
    with pytest.raises(LowHistoryUnavailable) as excinfo:
        pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader({"NEWCO": frame}),
            peer_symbols=[],
            flag_override=ENABLED,
        )
    assert excinfo.value.code == "history_unavailable"
    assert "volume" in excinfo.value.message.lower()


def test_too_few_instruments_abstains_with_pooled_peers_insufficient():
    panels = _universe(2)
    with pytest.raises(LowHistoryUnavailable) as excinfo:
        pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(panels),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:2],
            flag_override=ENABLED,
        )
    assert excinfo.value.code == "pooled_peers_insufficient"
    assert str(MIN_INSTRUMENTS) in excinfo.value.message


def test_unavailable_peers_are_skipped_not_fatal():
    panels = _universe(8)
    del panels[DEFAULT_PEER_UNIVERSE[3]]
    try:
        payload = pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(panels),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:8],
            flag_override=ENABLED,
        )
    except LowHistoryUnavailable as exc:
        # An honest abstention is acceptable; a crash on one missing peer is not.
        assert exc.code in {"pooled_model_unavailable", "pooled_peers_insufficient"}
        return
    assert DEFAULT_PEER_UNIVERSE[3] in payload["peers"]["unavailable"]


# -- successful and abstaining fits ---------------------------------------


def test_pooled_payload_is_labelled_with_its_own_provenance():
    panels = _universe(10)
    try:
        payload = pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(panels),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:10],
            flag_override=ENABLED,
        )
    except LowHistoryUnavailable as exc:
        assert exc.code == "pooled_model_unavailable"
        return

    assert payload["symbol"] == "NEWCO"
    assert payload["forecast_path"] == "pooled_cross_sectional"
    assert payload["model_label"] == POOLED_MODEL_LABEL
    assert payload["model_version"] == POOLED_MODEL_VERSION
    assert "not from this symbol" in payload["evidence"]["summary"].lower() or "cross-instrument" in payload["evidence"]["summary"].lower()
    assert payload["peers"]["count"] >= MIN_INSTRUMENTS
    assert payload["evidence"]["grade"] in {"A", "B", "C", "none"}


def test_range_and_abstention_are_mutually_exclusive_and_consistent():
    panels = _universe(10)
    try:
        payload = pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(panels),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:10],
            flag_override=ENABLED,
        )
    except LowHistoryUnavailable as exc:
        assert exc.code == "pooled_model_unavailable"
        return

    if payload["abstained"]:
        assert payload["research_range"] is None
        assert payload["code"]
        assert payload["message"]
        assert payload["support_state"] == "abstained"
    else:
        band = payload["research_range"]
        assert band["low"] < band["median_reference"] < band["high"]
        assert band["currency"] == "INR"
        assert 0 < band["confidence_level"] < 1
        assert payload["uncertainty"]["range_width"] > 0


def test_market_reference_failure_does_not_fail_the_request():
    panels = _universe(10)
    try:
        payload = pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=_loader(panels),
            peer_symbols=DEFAULT_PEER_UNIVERSE[:10],
            market_symbol="NIFTY 50",  # deliberately absent from the loader
            flag_override=ENABLED,
        )
    except LowHistoryUnavailable as exc:
        assert exc.code == "pooled_model_unavailable"
        return
    assert payload["support_state"]


def test_peer_fan_out_is_capped():
    panels = _universe(20)
    calls: list[str] = []
    base_loader = _loader(panels)

    def counting_loader(symbol: str) -> pd.DataFrame:
        calls.append(str(symbol).strip().upper())
        return base_loader(symbol)

    try:
        pooled_low_history_forecast(
            "NEWCO",
            timeframe="1D",
            training_window="1y",
            history_loader=counting_loader,
            peer_symbols=DEFAULT_PEER_UNIVERSE[:20],
            flag_override=ENABLED,
            max_peers=6,
        )
    except LowHistoryUnavailable:
        pass
    # Target plus at most max_peers + 1 loads; never the whole universe.
    assert len(calls) <= 8, calls
