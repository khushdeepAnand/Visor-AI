"""SHAP explainability block: present for admins, absent by default, graceful when shap is missing."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

import forecasting.explainability as explainability
from forecasting.interval_forecast import forecast_range


def _frame(rows: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    phase = np.arange(rows) * 2 * np.pi / 8
    close = 150 + 15 * np.sin(phase) + rng.normal(0, 0.1, rows)
    open_ = close + rng.normal(0, 0.05, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.3,
            "Low": np.minimum(open_, close) - 0.3,
            "Close": close,
            "Volume": 1_000_000 + np.sin(phase + 1) * 100_000 + rng.normal(0, 10_000, rows),
        },
        index=idx,
    )


def test_explain_default_omits_attribution():
    result = forecast_range("RELIANCE", _frame(), confidence_level=0.80, training_window="1y", timeframe="1D")
    assert "explainability" in result
    assert result["explainability"] is None


def test_explain_true_returns_tree_drivers():
    pytest.importorskip("shap")
    result = forecast_range(
        "RELIANCE",
        _frame(),
        confidence_level=0.80,
        training_window="1y",
        timeframe="1D",
        horizons=(1,),
        explain=True,
    )
    block = result["explainability"]
    assert block is not None
    assert block["available"] is True
    assert block["tree_models"], "expected at least one tree member in the stack"
    assert len(block["drivers"]) == 5
    for driver in block["drivers"]:
        assert driver["feature"]
        assert driver["direction"] in {"pushes_higher", "pushes_lower", "neutral"}
        assert "shap_value" in driver
        assert driver["magnitude_pct"] >= 0.0
    assert block["reference_price"] == round(result["current_price"], 2)


def test_explain_graceful_when_shap_unavailable():
    saved = sys.modules.get("shap", None)
    sys.modules["shap"] = None  # forces ImportError on `import shap`
    try:
        block = explainability.explain_stacked_tree_ensemble(
            {"gradient": object()},
            _frame().tail(1),
            ["Close", "Return_2D"],
            reference_point=150.0,
        )
    finally:
        if saved is None:
            sys.modules.pop("shap", None)
        else:
            sys.modules["shap"] = saved
    assert block["available"] is False
    assert block["reason"]


def test_no_tree_members_reports_reason():
    block = explainability.explain_stacked_tree_ensemble(
        {},
        _frame().tail(1),
        ["Close"],
        reference_point=150.0,
    )
    assert block["available"] is False
    assert "No tree" in block["reason"]
