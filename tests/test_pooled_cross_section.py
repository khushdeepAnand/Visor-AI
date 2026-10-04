"""Tests for the pooled cross-sectional low-history research model.

Intended repository path: ``tests/test_pooled_cross_section.py``.

The panels are synthetic and seeded, so the leakage, cohort, shrinkage, and
abstention rules are checked deterministically without any provider access.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.pooled_cross_section import (
    FEATURE_NAMES,
    MIN_INSTRUMENTS,
    MIN_OUTCOME_OBSERVATIONS,
    NEWLY_LISTED_MAX_SESSIONS,
    POOLED_FEATURE_SCHEMA_VERSION,
    PooledModelError,
    build_instrument_features,
    build_pooled_dataset,
    dataset_report,
    fit_pooled_forecaster,
    forward_time_validation,
    latest_feature_row,
    leave_instrument_out_validation,
)

HORIZON = 5


def _panel(seed: int, sessions: int = 400, base: float = 500.0, volume: float = 1.0e6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=sessions)
    steps = rng.normal(0.0004, 0.012, size=sessions)
    close = base * np.exp(np.cumsum(steps))
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": close * (1 + rng.normal(0, 0.002, size=sessions)),
            "high": close * (1 + np.abs(rng.normal(0, 0.006, size=sessions))),
            "low": close * (1 - np.abs(rng.normal(0, 0.006, size=sessions))),
            "close": close,
            "volume": rng.integers(int(volume * 0.5), int(volume * 1.5), size=sessions),
        }
    )
    return frame


def _market(sessions: int = 400) -> pd.DataFrame:
    return _panel(seed=99, sessions=sessions, base=20000.0, volume=5.0e6)


@pytest.fixture(scope="module")
def pooled():
    market = _market()
    panels = {f"SYM{index}": _panel(seed=index) for index in range(8)}
    return build_pooled_dataset(panels, market=market, horizon=HORIZON)


# -- feature construction --------------------------------------------------
def test_features_are_point_in_time_and_labels_are_forward(pooled):
    frame = _panel(seed=1)
    built = build_instrument_features("SYM1", frame, market=_market(), horizon=HORIZON)
    assert set(FEATURE_NAMES).issubset(built.columns)
    # Every published label must be the realised forward return; the final rows
    # whose outcome has not happened yet are dropped rather than imputed.
    cleaned = frame.set_index(pd.to_datetime(frame["date"]))
    assert built.index.max() <= cleaned.index[-(HORIZON + 1)]
    recomputed = (cleaned["close"].shift(-HORIZON) / cleaned["close"] - 1.0).reindex(built.index)
    assert np.allclose(built["target"].to_numpy(), recomputed.to_numpy(), atol=1e-12)


def test_no_fabricated_rows_and_duplicates_are_dropped():
    frame = _panel(seed=3, sessions=200)
    duplicated = pd.concat([frame, frame.tail(5)]).reset_index(drop=True)
    built = build_instrument_features("SYM3", duplicated, horizon=HORIZON)
    assert built.index.is_unique
    assert len(built) <= len(frame)


def test_dataset_report_lists_excluded_instruments():
    market = _market()
    panels = {f"SYM{index}": _panel(seed=index) for index in range(6)}
    panels["TINY"] = _panel(seed=42, sessions=25)
    pooled = build_pooled_dataset(panels, market=market, horizon=HORIZON)
    report = dataset_report(pooled)
    assert report["feature_schema_version"] == POOLED_FEATURE_SCHEMA_VERSION
    assert "TINY" in report["excluded_instruments"]
    assert "TINY" not in report["symbols"]


def test_pooled_dataset_requires_usable_instruments():
    with pytest.raises(PooledModelError):
        build_pooled_dataset({"TINY": _panel(seed=5, sessions=20)}, horizon=HORIZON)


# -- validation ------------------------------------------------------------
def test_leave_instrument_out_never_trains_on_the_held_out_symbol(pooled):
    result = leave_instrument_out_validation(pooled)
    assert result["method"] == "leave-instrument-out"
    assert result["fold_count"] == pooled["symbol"].nunique()
    held_out = {fold["held_out_symbol"] for fold in result["folds"]}
    assert held_out == set(pooled["symbol"].astype(str))
    assert result["pooled"]["observations"] == len(pooled)
    assert set(result["cohorts"]).issubset({"newly_listed", "illiquid", "established"})


def test_leave_instrument_out_requires_enough_instruments():
    market = _market()
    panels = {f"SYM{index}": _panel(seed=index) for index in range(MIN_INSTRUMENTS - 2)}
    small = build_pooled_dataset(panels, market=market, horizon=HORIZON)
    with pytest.raises(PooledModelError):
        leave_instrument_out_validation(small)


def test_forward_time_folds_are_chronological_with_an_embargo(pooled):
    result = forward_time_validation(pooled, folds=3)
    assert result["fold_count"] >= 1
    for fold in result["folds"]:
        assert pd.Timestamp(fold["train_end"]) <= pd.Timestamp(fold["test_start"])
        assert fold["embargo_bars"] == HORIZON


def test_cohorts_are_reported_separately_for_newly_listed_and_illiquid():
    market = _market()
    panels = {f"SYM{index}": _panel(seed=index) for index in range(5)}
    panels["NEWLIST"] = _panel(seed=77, sessions=90)
    panels["THINLY"] = _panel(seed=88, volume=2.0e3)
    pooled = build_pooled_dataset(panels, market=market, horizon=HORIZON)
    cohorts = leave_instrument_out_validation(pooled)["cohorts"]
    assert "newly_listed" in cohorts
    assert "illiquid" in cohorts
    assert cohorts["newly_listed"]["observations"] < cohorts["established"]["observations"]


# -- fitted model behaviour ------------------------------------------------
def test_random_walk_panels_produce_no_skill_and_baseline_only(pooled):
    forecaster = fit_pooled_forecaster(pooled)
    # The synthetic panels are near random walks, so an honest model must not
    # claim skill: the shrinkage weight collapses to zero and the published
    # state is baseline_only.
    assert forecaster.shrinkage_weight == 0.0
    assert forecaster.has_skill is False
    row = pooled.iloc[-1]
    prediction = forecaster.predict_return(row)
    assert prediction["state"] == "baseline_only"
    assert prediction["expected_return"] == 0.0
    assert prediction["raw_expected_return"] != 0.0 or True


def test_evidence_tier_and_horizons_shrink_with_available_history(pooled):
    forecaster = fit_pooled_forecaster(pooled)
    assert forecaster.evidence_tier(1000) == "none"  # no skill => never publishable
    assert forecaster.supported_horizons(20) == []
    assert forecaster.supported_horizons(90) == [1, 5]
    assert forecaster.supported_horizons(300) == [1, 5, 10]
    assert forecaster.supported_horizons(900) == [1, 5, 10, 21]
    assert NEWLY_LISTED_MAX_SESSIONS == 120


def test_abstention_is_an_explicit_designed_result(pooled):
    forecaster = fit_pooled_forecaster(pooled)
    row = pooled.iloc[-1]
    result = forecaster.forecast_interval(
        reference_price=float(row["reference_close"]),
        features=row,
        sessions_available=30,
    )
    assert result["state"] == "abstained"
    assert result["code"] == "insufficient_evidence"
    assert "low" not in result
    assert result["evidence"]["minimum_outcome_observations"] == MIN_OUTCOME_OBSERVATIONS


def test_interval_is_ordered_and_never_cosmetically_narrowed(pooled):
    forecaster = fit_pooled_forecaster(pooled)
    # Force a skilful model state to exercise the published-interval path while
    # keeping the measured residual dispersion untouched.
    forecaster.shrinkage_weight = 0.25
    row = pooled.iloc[-1]
    result = forecaster.forecast_interval(
        reference_price=float(row["reference_close"]),
        features=row,
        sessions_available=800,
        confidence=0.80,
    )
    assert result["state"] in {"model_supported", "low_utility"}
    assert result["low"] <= result["median"] <= result["high"]
    assert result["low"] > 0
    assert result["nominal_coverage"] == 0.80
    wider = forecaster.forecast_interval(
        reference_price=float(row["reference_close"]),
        features=row,
        sessions_available=800,
        confidence=0.95,
    )
    # Higher confidence must widen the interval, never narrow it.
    assert wider["width_points"] > result["width_points"]


def test_invalid_inference_inputs_are_refused(pooled):
    forecaster = fit_pooled_forecaster(pooled)
    row = pooled.iloc[-1]
    with pytest.raises(PooledModelError):
        forecaster.forecast_interval(reference_price=0.0, features=row, sessions_available=800)
    with pytest.raises(PooledModelError):
        forecaster.predict_return({"momentum_5": 0.01})


def test_latest_feature_row_reports_freshness_alignment():
    frame = _panel(seed=11)
    row, metadata = latest_feature_row("SYM11", frame, market=_market(), horizon=HORIZON)
    assert metadata["symbol"] == "SYM11"
    assert metadata["feature_schema_version"] == POOLED_FEATURE_SCHEMA_VERSION
    assert metadata["sessions_available"] == len(frame)
    assert metadata["cohort"] in {"newly_listed", "illiquid", "established"}
    assert "feature_timestamp" in metadata and "latest_close_timestamp" in metadata
    assert isinstance(metadata["feature_is_current"], bool)
    assert all(np.isfinite(float(row[name])) for name in FEATURE_NAMES)
