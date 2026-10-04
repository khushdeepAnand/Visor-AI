# Forecast Contract (v14)

## Published Output

`forecast_range` publishes a next-observed-bar research interval with `low`,
`median`, `high`, and the requested `confidence_level`. It also publishes:

- `current_price` from the final valid row of the canonical cleaned frame.
- `data_timestamp` for that final valid market row.
- `feature_timestamp` for the row used to generate model features.
- `evidence.grade` as `A`, `B`, `C`, or `none`.
- `low_utility` when total interval width exceeds 20% of current price.
- `forecast_status` as `model_supported`, `baseline_only`, `low_evidence`, `abstained`, `drift_blocked`, or `data_quality_blocked`.

An excessively wide interval is not narrowed. The width rule has only a 0.25%
of reference-price half-width floor (and a one-paisa numerical floor), with no
maximum cap.

## v13/v14 Enhancements (Wired to Live Data)

### Tier & Evidence
- `tier`: T0/T1/T2/T3/T4 with `usable_trading_days`, `evidence_grade`, `reason`, `primary_output`, `model_stack`, `supported_horizons`, `liquidity_bucket`, `corporate_action_review_required`, `surveillance_flag`.

### Volatility Forecast
- `volatility_forecast`: `expected_daily_vol_pct`, `expected_range_pct`, `model`, `components`, `confidence_level`
- `volatility_scorecard`: per-horizon (1,5,10,20) volatility forecasts
- `range_estimators`: Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang, Close-to-Close

### Regime Detection
- `market_regime`: `regime`, `trend`, `volatility`, `stress`, `probability`, `adx`, `atr_percentile`, `vix_level`, `nifty_drawdown_pct`, `basis`
- `regime_model_weights`: model weights per regime

### Conformal Methods
- `cqr`: `available`, `low_quantile`, `high_quantile`, `status` (`challenger_only` | `promoted_canary`), `determines_published_bounds`, `offsets_applied`
- `aci_state`: `schema_version`, `gamma`, `step`, `last_realized_at`, `update_policy` (authoritative_realized_outcomes_only)
- `mondrian_groups`: `tier`, `regime`, `liquidity`, `sector`
- `mondrian_adjustment`: comparison of current vs Mondrian half-width (reported, not auto-applied)

### Peer Transfer (T0/T1)
- `ipo_peer_blend`: `peers_used`, `peer_prior`, `blended_expected_return`, `blended_residual_scale`, `peer_weight`
- `hierarchical_shrinkage`: `total_weight`, `components`

### Circuit-Limit Clipping
- `circuit_clip`: `circuit_limit_pct`, `clipped_low`, `clipped_high`, `tick_floor_applied` on all intervals

### Distributional Output
- `fan_chart`: quantiles 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95
- `multi_horizon`: independent direct regressors per horizon (1, 3, 5, 10, 20 sessions)

### Context Lineage
- `context_inputs`: lineage of market_index, india_vix, fno_membership, ipo, peers
- `context_metrics`: provider metrics (attempted, available, degraded, timeouts, circuit_open)
- `lineage`: full forecast origin, feature timestamp, provider, lookahead policy

### Enhancement Status
- `enhancement_status`: `degraded` with `reason` if v13 block fails (never breaks core forecast)

## Statistical Separation

Supervised rows are ordered chronologically and assigned once to four distinct
folds: train, meta, calibration, and test. Base learners fit train; the stack and
persistence blend fit meta; conformal residual width fits calibration; reported
metrics and drift use test.

**CQR promotion**: Quantile regressors are challengers until promoted by gate
(≥100 forecasts, coverage within ±5%, Winkler improvement, DM significance).
When promoted, CQR offsets are applied to published bounds.

**ACI updates**: Only authoritative, automatic, non-stale, non-demo realized
outcomes mutate ACI state via `record_realized_outcome`. Forecast reads use
`read_aci_state` (read-only).

**Mondrian calibration**: Group-conditional conformal with persisted residuals
per (tier, regime, liquidity, sector). Cold-start falls back to global conformal.

MAE, RMSE, interval coverage, Winkler score, baseline comparison, and drift all
evaluate the published persistence-blended point or its published interval rule.
The interval rule is replayed at each test origin using only canonical prices
available at that origin.

## Presentation

Grade `C` receives low-confidence limited-history language and grade `none`
abstains. Derived observation and risk zones are unavailable for those grades.
Zones are also withheld when calibration is poor, the naive baseline is not
beaten, or the range is marked low utility.

Public payloads expose evidence grade and timestamps but not model names,
features, metrics, split row counts, or private operational details. Those
methodology details remain administrator-only.

## Derivatives Boundary

Futures/options are not passed through the equity forecast ledger. When both
realized and implied volatility are supplied, futures analysis can publish an
expiry-bounded volatility *scenario* whose horizon is capped at contract expiry.
It is explicitly non-predictive and must not be compared with an equity forecast
corridor or presented as a price target.

## Scheduled Maintenance

- **Outcome settlement**: every 15 min, finalizes due forecast ledger rows
- **Drift evaluation**: every 20 min, scores model health, auto-retrains drift groups
- **Live decay**: every 30 min, compares live vs backtest, auto-widens if degraded
- **Public scorecard**: daily 7 PM, rebuilds rolling scorecard from settled outcomes
- **Model refresh**: daily 6:30 PM, retrains configured symbols
- **Instrument master**: daily 8 AM, refreshes NSE/BSE BOD catalogue