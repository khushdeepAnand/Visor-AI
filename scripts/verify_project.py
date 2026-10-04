"""Deterministic local verification for StockPilot AI v7."""
from __future__ import annotations

import compileall
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_runtime_imports() -> None:
    modules = [
        "pandas", "numpy", "sklearn", "joblib", "requests", "httpx", "bcrypt",
        "jwt", "reportlab", "authlib", "fastapi", "uvicorn", "pydantic",
        "statsmodels", "lightgbm", "catboost", "dotenv",
    ]
    failures: list[str] = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as error:
            failures.append(f"{module_name}: {error}")
    if failures:
        message = "Runtime dependency check failed:\n- " + "\n- ".join(failures)
        if os.environ.get("STOCKPILOT_STRICT_RUNTIME") == "1":
            raise RuntimeError(message)
        print("  Warning: some declared dependencies are not installed in this verification environment.")
        print(message)


def check_product_contract() -> None:
    sys.path.insert(0, str(ROOT))
    from api.main import APP_VERSION, app
    from forecasting.interval_forecast import TRAINING_WINDOWS
    from services.market_data.instruments import CATALOGUE
    from services.market_data.manager import MANAGER

    assert APP_VERSION.startswith("7.")
    assert {"1w", "1mo", "3mo", "1y", "5y"}.issubset(TRAINING_WINDOWS)
    assert len(CATALOGUE.load()) > 100
    assert CATALOGUE.resolve("RELIANCE") is not None
    for bad in ("AAPL", "BTC-USD", "GC=F"):
        try:
            MANAGER.normalize_symbol(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Non-Indian symbol was accepted: {bad}")

    paths = {getattr(route, "path", "") for route in app.routes}
    required_routes = {
        "/api/v1/market/quote/{symbol}",
        "/api/v1/market/history/{symbol}",
        "/api/v1/market/providers/health",
        "/api/v1/auth/sessions",
        "/api/v1/news/{symbol}",
        "/api/v1/predict/{symbol}",
        "/api/v1/compare",
        "/api/v1/reports/forecast/{symbol}.pdf",
        "/api/v1/reports/portfolio.pdf",
        "/api/v1/paper/orders",
        "/api/v1/derivatives/options/greeks",
        "/ws/quotes/{symbol}",
    }
    missing_routes = sorted(required_routes - paths)
    if missing_routes:
        raise RuntimeError("Missing required v7 API routes: " + ", ".join(missing_routes))

    required_files = [
        ROOT / "forecasting" / "interval_forecast.py",
        ROOT / "services" / "market_data" / "manager.py",
        ROOT / "services" / "market_data" / "upstox.py",
        ROOT / "frontend" / "app" / "page.tsx",
        ROOT / "frontend" / "components" / "TerminalChart.tsx",
        ROOT / "frontend" / "components" / "MarketWorkspace.tsx",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required_files if not path.exists()]
    if missing:
        raise RuntimeError("Required v7 modules are missing: " + ", ".join(missing))
    if (ROOT / "app.py").exists() or (ROOT / "ui").exists() or (ROOT / "charts").exists():
        raise RuntimeError("Retired Streamlit presentation files are still present.")


def check_release_hygiene() -> None:
    forbidden_parts = {"venv", ".venv", ".git", "node_modules", ".next", "__pycache__", ".pytest_cache"}
    forbidden_files = {"stockpilot.db", "market.db", ".env"}
    found: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in forbidden_parts for part in relative.parts):
            found.append(str(relative)); continue
        if path.is_file() and path.name in forbidden_files:
            found.append(str(relative))
    if found and os.environ.get("STOCKPILOT_RELEASE_CHECK") == "1":
        raise RuntimeError("Release-only artifacts are present: " + ", ".join(found[:30]))


def check_release_inputs() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.release_hygiene import collect_allowlisted, release_input_issues
    from scripts.scan_secrets import scan_paths

    issues = release_input_issues(ROOT)
    if issues:
        raise RuntimeError("Release input hygiene failed: " + "; ".join(issues))
    findings = scan_paths(collect_allowlisted(ROOT))
    if findings:
        raise RuntimeError(f"Release input secret scan failed with {len(findings)} finding(s).")


def _run_backend_tests() -> None:
    # The legacy prediction suite performs repeated ensemble fitting. Run it in
    # deterministic chunks so verification is reliable on low-resource machines.
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--ignore=tests/test_prediction.py"],
        cwd=ROOT,
        check=True,
    )
    prediction_groups = [
        [
            "test_evaluate_predictions_perfect_predictions_give_r2_of_1",
            "test_evaluate_predictions_known_mae",
            "test_evaluate_predictions_returns_all_expected_keys",
            "test_feature_columns_include_engineered_indicators",
            "test_prepare_data_raises_informative_error_on_short_history",
            "test_prepare_data_splits_chronologically_not_randomly",
            "test_baseline_prediction_equals_latest_close",
        ],
        [
            "test_predict_returns_registered_models_and_baseline",
            "test_predict_handles_insufficient_data_gracefully",
            "test_predict_reuses_cached_models_on_second_call",
            "test_build_backtest_results_returns_aligned_serializable_rows",
            "test_predict_response_contains_backtest_rows",
            "test_directional_accuracy_uses_feature_day_close_reference",
        ],
        [
            "test_backtest_dates_are_next_observed_trading_dates",
            "test_predict_reports_walk_forward_evaluation",
            "test_model_ranking_prioritizes_error_not_r2_alone",
            "test_consensus_weights_sum_to_one_and_favor_lower_rmse",
            "test_prediction_interval_contains_weighted_consensus",
            "test_walk_forward_exposes_fold_boundaries_and_rmse",
        ],
    ]
    for group in prediction_groups:
        node_ids = [f"tests/test_prediction.py::{name}" for name in group]
        subprocess.run([sys.executable, "-m", "pytest", "-q", *node_ids], cwd=ROOT, check=True)


def main() -> int:
    print("[1/6] Checking release hygiene policy...")
    check_release_hygiene()
    print("[2/6] Checking allowlisted release inputs, required documents, and secrets...")
    check_release_inputs()
    print("[3/6] Checking runtime dependencies...")
    check_runtime_imports()
    print("[4/6] Compiling Python sources...")
    excluded = {"venv", ".venv", ".git", "node_modules", ".next", "__pycache__", ".pytest_cache"}
    sources = [path for path in ROOT.rglob("*.py") if not any(part in excluded for part in path.relative_to(ROOT).parts)]
    if not all(compileall.compile_file(str(path), quiet=1) for path in sources):
        raise RuntimeError("Python compilation failed.")
    print("[5/6] Checking v7 product contracts...")
    check_product_contract()
    print("[6/6] Running backend tests in deterministic chunks...")
    _run_backend_tests()
    print("StockPilot AI v7 verification completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
