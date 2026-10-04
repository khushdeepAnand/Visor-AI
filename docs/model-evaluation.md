# StockPilot AI — Model Evaluation

How we know a range is honest enough to publish: the walk-forward replay, the
metrics that come out of it (including the new intraday range-containment
coverage), the gate that decides publishability, and the "8 of 10" calibration
contract.

## 1. The replay (never shuffle, never look ahead)

`services/range_backtest.py::walk_forward_metrics` replays the forecaster:

- **Expanding chronological origins** — each origin fits only on bars up to
  itself, then forecasts the target session. No shuffling, no future bars.
- **Next-bar-open fills** for the trade sim with spread + slippage + charges
  applied on both fills (indicative, one share — not an order).
- Every origin produces one replay record with the forecast, target close,
  coverage flag, containment flag, and trade line (if directional).

`walk_forward_origins` enforces `MIN_TRAIN_BARS` (40) so no origin is fit on a
meaningless window.

## 2. Metrics produced

| Metric | Meaning |
|---|---|
| `empirical_coverage` | fraction of target closes inside the published band |
| `range_containment_coverage` | fraction of windows whose **highest high and lowest low** both stay inside the band — the intraday counterpart to close coverage |
| `directional_accuracy` / `naive_directional_accuracy` | sign agreement, model vs persistence |
| `model_mae` / `naive_mae` / `mase` | midpoint error vs the naive persistence benchmark |
| `mae_improvement_vs_naive_pct` | how much better than naive the midpoint is |
| `beats_naive_baseline` | boolean skill flag |
| trade block | fills, gross/net PnL, costs, win rate, Sharpe, max drawdown |

**Containment is strictly stronger than close coverage**: a band that hugs the
close can cover every close yet be blown out intraday. By construction
`containment ≤ coverage` for the same window (a close outside the band forces
a high or low outside it). `tests/test_range_backtest.py` pins this down with
a deliberately narrow band.

## 3. The gate (naive-beat by floor + calibration band)

`naive_beat_gate` decides whether a range may be **published** (deployment
gate) from the replay only:

- **Mind-the-naive:** midpoint improvement over naive persistence ≥ floor
  (*default floor 0%* — a tie passes; a positive floor can be configured only
  once genuine edge is demonstrated).
- **Calibration band:** empirical coverage inside `nominal ± 15pp` for the
  nominal 80% interval. Both **under-coverage** (too narrow — fabricated
  precision) *and* **over-coverage** (too wide — useless) fail.
- **Sample adequacy:** ≥ 8 usable origins and ≥ 60% of origins usable.

No number is invented: every gate input is measured on the replayed origins.

## 4. The "8 of 10" contract

- Nominal interval: 80%. The target is empirical coverage in
  `[0.65, 0.95]`, centered near 0.80 — the "8 of 10 actuals inside the range"
  behaviour users asked for, with the band wide enough to be *usable*.
- Calibration is re-checked on every backtest run; the assessment
  `confidence_score` independently digests the same calibration gap
  (`-5 pts per pp` of deviation) so the UI can never show high confidence
  while the interval is demonstrably off.
- The live/real-data quality gate (`tests/test_interval_forecast_v6.py`,
  `@pytest.mark.live`) still asserts `0.75 ≤ coverage ≤ 0.85` on configured
  broker data.

## 5. Honest-reporting rules

- Broken/expired providers (e.g. an expired Upstox token) cause explicit
  fallback provenance or blocked status — never silently-old numbers.
- Blocked statuses (`abstained`, `drift_blocked`, `data_quality_blocked`)
  withhold publishable statistics in both engine and presentation layers.
- The backtest is evidence, not a forecast: the report carries
  `is_forecast: false` and a full disclosure list.

## 6. Current state & known limits

- **CatBoost active; LightGBM dormant on this Mac** (native `libomp` library
  missing — `brew install libomp` enables it, no code change). The ensemble
  therefore runs with CatBoost + baselines until the dependency is installed.
- The model-upgrade roadmap (conformal quantile regression, volatility-
  conditional conformal, returns-space targets, intraday ladder) is documented
  separately in `docs/MODEL_UPGRADE_RESEARCH.md`; nothing there is yet
  implemented.