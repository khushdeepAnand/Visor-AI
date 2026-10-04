"""Authoritative inventory of every forecasting model in the project.

The master requirement is honesty: a model appears here with the status it
actually has, and nothing is described as production unless it is genuinely
wired into the production forecast path. Normal users never see this registry;
it is exposed only to configured administrators.

Status values
-------------
``production``      Used by the live forecast path on every request.
``fallback_only``   Only used when a production model cannot be fitted.
``experimental``    Installed and importable, but not promoted to the forecast.
``retired``         Code retained for reference/tests only; no production path
                    calls it (legacy .pkl pipeline, v5 model metadata).
``not_implemented`` Named in documentation or research notes, no working code.
``unavailable``     Code exists but a required dependency is not installed.
"""
from __future__ import annotations

import importlib.util
from typing import Any

#: Concise label shown to normal users in place of any model internals.
PUBLIC_MODEL_LABEL = "calibrated interval ensemble"


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _entry(
    name: str,
    *,
    family: str,
    status: str,
    role: str,
    module: str | None = None,
    requires: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    dependency_installed = True if requires is None else _installed(requires)
    resolved = status
    if requires is not None and not dependency_installed and status != "not_implemented":
        resolved = "unavailable"
    return {
        "name": name,
        "family": family,
        "status": resolved,
        "declared_status": status,
        "role": role,
        "module": module,
        "requires": requires,
        "dependency_installed": dependency_installed,
        "notes": notes,
    }


def model_registry() -> list[dict[str, Any]]:
    return [
        _entry(
            "Stacked base ensemble",
            family="ensemble",
            status="production",
            role="Linear regression, elastic-net, random forest, gradient boosting, and the naive persistence anchor are base learners inside the stacked point forecast.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "Gradient boosting point base",
            family="gradient boosting",
            status="production",
            role="Base learner inside the stacked point forecast.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "LightGBM base learner",
            family="gradient boosting",
            status="production",
            role="Joins the stacked base ensemble on every request when the wheel can fit the train fold.",
            module="forecasting.interval_forecast",
            requires="lightgbm",
        ),
        _entry(
            "CatBoost base learner",
            family="gradient boosting",
            status="production",
            role="Joins the stacked base ensemble on every request when the wheel can fit the train fold.",
            module="forecasting.interval_forecast",
            requires="catboost",
        ),
        _entry(
            "Multi-horizon direct forecasters",
            family="ensemble",
            status="production",
            role="Independent direct regressors for 1/3/5/10 sessions ahead, each with its own chronological folds and untouched test fold.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "Linear regression meta-learner",
            family="linear",
            status="production",
            role="Combines base-learner predictions chronologically.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "Localised regime-adaptive conformal",
            family="conformal calibration",
            status="production",
            role="Turns held-out MAD-normalised residuals, weighted by recency and similarity to the current volatility regime, into a calibrated interval.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "SHAP TreeExplainer attribution",
            family="explainability",
            status="production",
            role="Post-hoc attribution of the point forecast to tree-stack drivers; diagnostic only, never feeds the published bounds.",
            module="forecasting.explainability",
            requires="shap",
        ),
        _entry(
            "Direct quantile regression",
            family="quantile regression",
            status="experimental",
            role="Challenger-only diagnostic; its bounds do not determine published bounds.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "ARIMA baseline",
            family="statistical",
            status="fallback_only",
            role="Reference point forecast recorded for comparison only.",
            module="forecasting.interval_forecast",
            requires="statsmodels",
        ),
        _entry(
            "Naive last-close baseline",
            family="baseline",
            status="production",
            role="Promotion gate; a model must beat it on unseen data.",
            module="forecasting.interval_forecast",
        ),
        _entry(
            "LightGBM quantile challenger",
            family="gradient boosting",
            status="experimental",
            role="Not promoted; no reviewed out-of-sample calibration evidence yet.",
            module="models.advanced_boosters",
            requires="lightgbm",
        ),
        _entry(
            "Legacy v5.2 .pkl pipeline",
            family="legacy",
            status="retired",
            role="Former production point forecast (train/save/load .pkl artifacts, MODEL_VERSION 5.2). No production path calls it; kept only for reference and legacy tests. Shared feature/TARGET constants it defines remain in use.",
            module="prediction",
            notes="The modern surface is the calibrated interval ensemble in forecasting.interval_forecast; this entry documents retirement, not availability.",
        ),
        _entry(
            "LSTM sequence challenger",
            family="neural",
            status="experimental",
            role="Legacy research code; requires PyTorch and is never used in production.",
            module="prediction_lstm",
            requires="torch",
        ),
        _entry(
            "Temporal fusion / transformer challenger",
            family="neural",
            status="not_implemented",
            role="Research direction only. No implementation exists in this build.",
        ),
        _entry(
            "Prophet seasonal challenger",
            family="statistical",
            status="not_implemented",
            role="Research direction only. No implementation exists in this build.",
        ),
         _entry(
            "Option-implied expected-move crossover",
            family="market calibration",
            status="production",
            role="Reads ATM implied volatility from the live NSE option chain and attaches IV-implied expected-move bands plus a model-vs-options crossover verdict to the public forecast payload. Best-effort: when no chain is available the block states that plainly instead of inventing volatility.",
            module="services.expected_move",
        ),
        _entry(
            "Walk-forward backtest honesty harness",
            family="validation",
            status="production",
            role="Replays the forecast path chronologically with next-bar-open fills and costs, then reports coverage, MASE, directional accuracy, trade Sharpe and max drawdown. The naive-beat gate (>=10% win over persistence on unseen folds) must pass before a range family is promoted.",
            module="services.range_backtest",
        ),
        _entry(
            "Naive-beat promotion gate CLI",
            family="validation",
            status="production",
            role="CI gate script; exits non-zero when the walk-forward harness does not beat the naive persistence baseline by the configured margin on the reviewed window.",
            module="scripts.verify_forecast_gate",
        ),
        _entry(
            "Drift monitor and auto-adaptation",
            family="validation",
            status="production",
            role="Judges model quality from authoritative settled outcomes across all users with rolling-coverage/MASE/directional metrics, persists model-health records, surfaces an admin quality dashboard, and schedules drift-triggered auto-retrains with artifact evidence.",
            module="forecasting.drift_monitor",
        ),
        _entry(
            "Ridge + ETS low-history fallback branch",
            family="statistical",
            status="production",
            role="Used when verified history is too short for the full ensemble: ridge (alpha=5) and exponential-smoothing challengers with an extra width safety factor (1.25x) applied identically to live and replay paths, flagged as low-data in the payload.",
            module="forecasting.interval_forecast",
        ),
    ]


def registry_summary() -> dict[str, Any]:
    entries = model_registry()
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "public_label": PUBLIC_MODEL_LABEL,
        "counts": counts,
        "production": [entry["name"] for entry in entries if entry["status"] == "production"],
        "models": entries,
    }
