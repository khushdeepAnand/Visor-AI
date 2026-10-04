"""Unit tests for futures support + the low-data branch (Phase B item 8)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from api.main import _forecast_asset_class
from forecasting.interval_forecast import LOW_DATA_WIDTH_FACTOR, MIN_SUPERVISED_ABSOLUTE, MIN_SUPERVISED_FULL, _fit_base_models, forecast_range
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER


def _frame(n: int, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06", periods=n, freq="B")
    noise = rng.normal(0.0, 0.5, n).cumsum()
    close = np.maximum(100.0 + noise, 1.0)
    open_ = np.r_[close[0] + rng.normal(0, 0.2), close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, n))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": np.full(n, 1_000.0)},
        index=idx,
    )


def _synthetic_features(n: int = 60) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(9)
    X = pd.DataFrame(
        {
            "Close": rng.normal(100.0, 2.0, n),
            "Volume": rng.normal(1_000.0, 100.0, n),
            "ROC_1": rng.normal(0.0, 0.3, n),
        }
    )
    y = pd.Series(rng.normal(100.0, 2.0, n))
    return X, y


def test_low_data_models_join_the_stack_only_when_flagged() -> None:
    X, y = _synthetic_features()
    low = _fit_base_models(X, y, low_data=True)
    assert {"Ridge", "ETS"} <= set(low)
    assert "Ridge" in low and "ETS" in low

    full = _fit_base_models(X, y, low_data=False)
    assert "Ridge" not in full and "ETS" not in full
    assert "Naive Persistence" in full


def test_low_data_branch_active_on_short_history() -> None:
    frame = _frame(85, seed=11)
    result = forecast_range(
        "TEST",
        frame,
        confidence_level=0.80,
        training_window="3mo",
        timeframe="1D",
        horizons=(1, 3),
    )
    assert result["low_data"] is True
    assert result["low_data_branch"] == "classical_ridge_ets"
    assert result["training"]["mode"] == "low-data"
    assert result["training"]["data_mode"] == "classical_ridge_ets"
    policy = result["methods"]["low_data_policy"]
    assert policy["active"] is True
    assert policy["published_width_factor"] == LOW_DATA_WIDTH_FACTOR
    assert set(policy["extra_challenger_models"]) == {"Ridge (alpha=5.0)", "ETS (damped additive trend)"}

    # h=1 must carry the flag into the public ladder strip.
    primary = next(entry for entry in result["multi_horizon"]["horizons"] if entry["sessions"] == 1)
    assert primary["low_data"] is True
    assert primary["low_data_branch"] == "classical_ridge_ets"


def test_full_pipeline_uses_boosted_stack() -> None:
    frame = _frame(300, seed=22)
    result = forecast_range(
        "TEST",
        frame,
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
        horizons=(1,),
    )
    assert result["low_data"] is False
    assert result["low_data_branch"] == "boosted_stack"
    assert result["training"]["mode"] == "full"
    assert result["methods"]["low_data_policy"]["active"] is False
    assert result["methods"]["low_data_policy"]["published_width_factor"] == 1.0
    primary = result["multi_horizon"]["horizons"][0]
    assert primary["low_data"] is False


def test_asset_class_mapping_covers_futures() -> None:
    assert _forecast_asset_class("FUT") == "futures"
    assert _forecast_asset_class("FUTIDX") == "futures"
    assert _forecast_asset_class("FUTSTK") == "futures"
    assert _forecast_asset_class("EQ") == "equity"
    assert _forecast_asset_class("INDEX") == "index"
    assert _forecast_asset_class("PE") == "options"
    assert _forecast_asset_class(None) == "equity"


def test_futures_trading_symbol_resolves_and_normalizes() -> None:
    futures = [item for item in CATALOGUE.load() if item.instrument_type == "FUT"]
    assert futures, "the bundled instrument master must contain FUT rows"
    sample = futures[0]
    resolved = CATALOGUE.resolve(sample.symbol)
    assert resolved is not None
    assert resolved.instrument_type == "FUT"
    assert MANAGER.normalize_symbol(sample.symbol) == sample.symbol


def test_thresholds_are_sane() -> None:
    assert 30 <= MIN_SUPERVISED_ABSOLUTE < MIN_SUPERVISED_FULL
    assert LOW_DATA_WIDTH_FACTOR > 1.0