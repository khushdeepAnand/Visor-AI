#!/usr/bin/env python3
"""CI/boot gate: published ranges must be calibrated and never worse than naive.

Replays the configured symbols through an expanding-window walk-forward harness
(no shuffling, next-bar-open fills with costs) and fails the build when any
symbol fails the trust contract:

* the interval's empirical coverage must land inside the nominal band
  (e.g. 8 of 10 actuals inside an 80% interval — and not so wide it becomes
  useless), and
* the midpoint must not be worse than naive persistence (default floor 0%).

Usage:
    python scripts/verify_forecast_gate.py [--horizon 1] [--floor 0.0]
                                           [--symbols "RELIANCE,TCS,INFY"]

Exit code 0 = gate passed; 1 = at least one symbol failed or data was unusable.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forecasting.interval_forecast import WINDOW_TIMEFRAME_DEFAULTS, forecast_range  # noqa: E402
from services.market_data.manager import MANAGER  # noqa: E402
from services.range_backtest import (  # noqa: E402
    DEFAULT_COVERAGE_TOLERANCE,
    DEFAULT_NAIVE_IMPROVEMENT_FLOOR,
    DEFAULT_NOMINAL_COVERAGE,
    range_model_gate_report,
)


def _symbols(explicit: str | None) -> list[str]:
    configured = explicit or os.getenv("STOCKPILOT_RETRAIN_SYMBOLS") or "RELIANCE,TCS,INFY,NIFTY 50"
    accepted: list[str] = []
    for raw in configured.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            accepted.append(MANAGER.normalize_symbol(value))
        except ValueError as error:
            print(f"  skip {value}: {error}", file=sys.stderr)
    return accepted


def _report(symbol: str, horizon: int, confidence: float, window: str, timeframe: str,
            floor: float, nominal_coverage: float, coverage_tolerance: float) -> dict[str, Any]:
    history = MANAGER.get_history(symbol, timeframe=timeframe, window=window)
    forecaster = lambda sym, frame: forecast_range(  # noqa: E731
        sym,
        frame,
        confidence_level=confidence,
        training_window=window,
        timeframe=timeframe,
        horizons=(horizon,),
    )
    return range_model_gate_report(
        symbol,
        history,
        forecaster,
        horizon=horizon,
        confidence=confidence,
        improvement_floor=floor,
        nominal_coverage=nominal_coverage,
        coverage_tolerance=coverage_tolerance,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=1, help="Forecast horizon in sessions (default 1).")
    parser.add_argument("--floor", type=float, default=DEFAULT_NAIVE_IMPROVEMENT_FLOOR,
                        help=f"Naive-beat improvement floor (default {DEFAULT_NAIVE_IMPROVEMENT_FLOOR:.0%}).")
    parser.add_argument("--confidence", type=float, default=0.80, help="Nominal coverage (default 0.80).")
    parser.add_argument("--coverage-tolerance", type=float, default=DEFAULT_COVERAGE_TOLERANCE,
                        help=f"Allowed coverage deviation from nominal (default {DEFAULT_COVERAGE_TOLERANCE:.0%}).")
    parser.add_argument("--window", default=os.getenv("STOCKPILOT_RETRAIN_WINDOW", "1y"))
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol list (overrides env).")
    args = parser.parse_args(argv)

    window = args.window.strip() or "1y"
    timeframe = WINDOW_TIMEFRAME_DEFAULTS.get(window, WINDOW_TIMEFRAME_DEFAULTS["1y"])

    print(f"StockPilot forecast gate: horizon={args.horizon} floor={args.floor:.0%} "
          f"coverage=nominal {args.confidence:.0%} +/- {args.coverage_tolerance:.0%} "
          f"window={window} timeframe={timeframe}")
    all_passed = True
    for symbol in _symbols(args.symbols):
        print(f"\n== {symbol} ==")
        try:
            report = _report(symbol, args.horizon, args.confidence, window, timeframe,
                             args.floor, args.confidence, args.coverage_tolerance)
        except Exception as error:  # noqa: BLE001 - gate must report and continue
            print(f"  ERROR: {type(error).__name__}: {error}", file=sys.stderr)
            all_passed = False
            continue
        accuracy = report["accuracy"]
        gate = report["gate"]
        trades = report["trades"]
        print(f"  replay origins: {report['origins']} usable: {report['usable_origins']}")
        print(f"  accuracy: coverage={accuracy['empirical_coverage']:.3f} direction={accuracy['directional_accuracy']} "  # noqa: E501
              f"mase={accuracy['mase']} improvement_vs_naive={accuracy['mae_improvement_vs_naive_pct']:.2f}%")
        print(f"  trades: {trades['count']} net_pnl={trades['net_pnl']:.4f} sharpe={trades['trade_sharpe']} "
              f"max_dd={trades['max_drawdown_pct']}% win_rate={trades['win_rate_pct']}")
        print(f"  gate: {'PASS' if gate['passed'] else 'FAIL'}" + (f" - {'; '.join(gate['reasons'])}" if gate["reasons"] else ""))
        if not gate["passed"]:
            all_passed = False

    print("\n" + ("GATE PASSED" if all_passed else "GATE FAILED"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
