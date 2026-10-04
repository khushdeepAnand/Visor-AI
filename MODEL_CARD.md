# Model Card

## Intended Use

The model provides non-prescriptive, one-bar-ahead stock research ranges for
paper-trading support. It is not investment advice and does not provide trade
instructions, position sizing, entry prices, targets, or stops.

## Production Method (v14)

- **Point forecast**: base regressors stacked by Ridge and blended with naive
  last-close persistence using a weight fixed on the meta fold.
- **Bounds**: the maximum of chronological split-conformal residual width,
  z-scaled recent realized volatility, and a small numerical floor.
- **Validation**: untouched chronological test fold after train, meta, and
  calibration folds.
- **Baseline**: naive last-close persistence.
- **Challengers**: direct quantile regressors and optional quantile boosters.
  Their outputs do not determine published bounds unless promoted.

## v13/v14 Enhancements (wired to live data)

### Data-Tier Router (T0-T4)
| Tier | Usable Days | Primary Output | Model Stack |
|------|-------------|----------------|-------------|
| T0 | < 30 | Volatility band only; no directional claim | peer_prior, ipo_specific_prior, shrinkage |
| T1 | 30-120 | Calibrated range, wide; abstain on direction | pooled_cross_sectional, volatility_model, peer_transfer |
| T2 | 120-500 | Range + probability, limited direction | per_stock_gbm_quantiles, pooled_blend, volatility_model |
| T3 | 500-2500 | Full stack (CQR + pooled + regime + stacking) | per_stock_cqr, pooled, regime_routing, stacking |
| T4 | 2500+ | Full stack + options-implied + multi-regime | per_stock_cqr, pooled, regime_routing, stacking, options_implied, multi_regime |

Evidence grades (A/B/C/none) based on supervised rows and validation samples.

### Volatility-First Models
- HAR-RV (Heterogeneous Autoregressive Realized Volatility)
- GARCH-family (GJR-GARCH, EGARCH for leverage effects)
- Range-based estimators: Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang
- Realized variance from intraday bars (when available)
- Composite volatility forecast with volatility scorecard for multiple horizons

### Regime Detection & Routing
- Market regimes: bull/bear/sideways × low/high volatility × crisis
- Regime-specific model weights via `regime_router`
- Detected from indicators observable at forecast origin only (no lookahead)

### Conformal Methods
- **Split-conformal**: localised, recency/regime-weighted residual quantiles
- **CQR (Conformalized Quantile Regression)**: native quantile objectives, promoted via gate, shapes published bounds when `promoted_canary`
- **ACI (Adaptive Conformal Inference)**: online coverage self-correction, persisted per symbol/horizon/confidence via `read_aci_state` / `record_realized_outcome`
- **Mondrian (group-conditional)**: per (tier, regime, liquidity, sector) calibration with persistent residuals via `save_mondrian_calibration` / `mondrian_conformal_half_width_persisted`

### Peer Transfer for IPOs (T0/T1)
- IPO features: days since listing, issue price vs current, lock-in expiries, subscription level
- Peer transfer prior from sector/cap-bucket peers via `peer_transfer_prior`
- Blended forecast with hierarchical shrinkage

### Circuit-Limit Clipping & Tick-Size Floor
- NSE circuit limits (5%/10%/20%) applied to all published intervals
- Minimum width floor (0.25% of price or 1 paisa)

### Distributional Output
- Fan chart quantiles: 5/10/25/50/75/90/95
- Multi-horizon: 1, 3, 5, 10, 20 sessions (independent direct regressors per horizon)

## Evaluation & Promotion

- **Per-tier evaluation**: purged group folds with embargo, reported by tier/sector/liquidity/regime
- **Metrics**: coverage, Winkler score, pinball loss, CRPS, MASE, PIT calibration, Diebold-Mariano
- **Promotion gate**: blocks deployment if coverage outside ±5%, MASE > 1.0, Winkler not improved, or DM test not significant
- **Live decay tracking**: scheduled job compares live vs backtest coverage/Winkler/MASE, auto-widens intervals if degraded
- **Public scorecard**: rebuilt daily from settled outcomes, shows per-tier coverage, promotion gate status, model actions

## Evidence Grades

| Grade | Minimum supervised rows | Minimum unseen test samples |
| --- | ---: | ---: |
| A | 240 | 36 |
| B | 120 | 18 |
| C | 30 | 5 |
| none | Below C | Below C |

Both thresholds must be met. Grade C is limited evidence, not moderate or high
confidence. A good score on a small test fold does not override this limitation.

## Limitations

- Chronological holdouts reduce but do not eliminate regime and selection risk.
- A single instrument test fold can be small and serially dependent.
- Conformal coverage is empirical, not guaranteed under distribution shift.
- Optional model availability varies by environment.
- Wide intervals can be statistically honest but operationally low utility.
- Corporate actions, sparse trading, bad provider data, and market discontinuity
  can invalidate learned relationships.
- **T0/T1 abstention**: No directional forecast for < 120 usable days.
- **VIX-unavailable fallback**: Uses realized volatility proxy with disclosed flag.
- **CQR promotion required**: Quantile regression does not determine bounds until promotion gate passes with ≥100 forecasts, coverage within tolerance, and Winkler improvement.
- **Mondrian cold-start**: Falls back to global conformal when group has no persisted residuals.
- **ACI update policy**: Only authoritative, automatic, non-stale, non-demo outcomes update ACI state.