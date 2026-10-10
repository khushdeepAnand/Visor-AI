# Next-day specialist and paper-trading correction

## Publication status

The specialist is **research-only and not promotion-eligible**. The 2026-10-09
run in `docs/next-day-held-out-report.json` evaluates real Upstox index history
from the row-level `docs/history-manifest.json`: NIFTY 50 (238 sessions) and
INDIA VIX (238 sessions), 20 paired untouched T2 test outcomes each. There is
no statistically supported win against the shared horizon=1 comparator. GBM
coverage is 70% / 65% at an 80% nominal target; one-sided DM p-values are
0.254884 / 0.135263 respectively. HAR also fails the existing gates.

Equities remain excluded until a complete sourced split/bonus/rights/dividend
review covers their cache interval. An empty registry, a clear jump detector or
historical test fixtures cannot establish that review. Demo/synthetic and unknown
sources are tagged and excluded. Indices are explicitly action-not-applicable;
the forecasting verifier is invoked with an explicit empty issuer-action set.
Session-close timestamps use verified exchange schedules and completed fetched
bars, not fabricated historical provider-delivery timestamps. This is retrospective
research with local provider provenance, not an independent authenticity or
historical delivery-latency audit. See `docs/V21_CONTINUATION_STATUS.md` for scope.

The existing pipeline already fits each direct horizon separately. The new
specialist differs in its next-session return/gap targets, short-window signals,
quantile objective and separately evaluated volatility/options candidates.
Production forecasts and CQR treatment/control allocation remain governed by
the existing signed promotion-receipt workflow. This research evaluator cannot
write a production receipt or change published bounds. A passing research
report still requires independently reviewed, artifact-scoped production
integration and a fresh signed receipt before replacement.

## Implemented research path

`forecasting/next_day.py` provides:

- Separate horizon=1 gradient-boosted quantiles (10/50/90 at 80% coverage), trained
  on next-session returns, not price levels or longer horizons.
- Explicit next-open gap regression, conditioned on historical gaps and any
  timestamped overnight index/futures return signals supplied in the manifest.
- One-, two- and three-session realized ranges/variance, recent gap dispersion,
  daily close location, intraday return and relative volume.
- Optional completed-session intraday realized variance, late momentum, late
  volume share and VWAP distance. No tick-level volume profile is fabricated.
- A separate HAR-RV widening challenger fitted on daily/weekly/monthly log
  variance. Its interval-width contribution is evaluated independently.
- Nearest-expiry ATM CE+PE premium expected-move proxy, freshness checked,
  square-root-time scaled, as an optional GBM feature and calibrated benchmark.
  This proxy is not advertised as an inherently calibrated probability interval.
- An optional 10-bar/16-unit LSTM research challenger using TensorFlow only if
  already installed; `--sequence-candidate` requests it. No dependency is added.
  It never becomes an ensemble member just because it can run.

Optional inputs are joined backward by availability timestamp with expiry of
old observations and explicit missingness flags. A next-morning global return
is not available at the previous close; it cannot be backfilled into that
close's forecast. Inputs require real availability timestamps. Intraday inputs
must use bar-end timestamps and complete NSE sessions.

Training and calibration labels are purged by one session at their boundary.
Gap, quantile and HAR models see train only. CQR sees calibration only. Test
origins are paired exactly with the existing direct horizon=1 pipeline's
untouched test fold. Coverage, exact quantile pinball loss, Winkler and
MASE-versus-persistence are reported only for next-day outputs. HAC
Diebold-Mariano uses candidate-minus-shared relative Winkler losses.

Existing tier and aggregate gates are called without relaxed thresholds.
Candidate-family DM significance is additionally Bonferroni adjusted. HAR,
options and LSTM members must also significantly beat the specialist GBM.
Event coverage is assessed on next-session large-gap, earnings, major-news
and circuit-limit outcomes; fewer than ten observations in any required bucket
blocks eligibility. Events are evaluation labels, not predictive inputs.
Options comparison requires sufficient overlapping observations, comparable
empirical coverage (within five percentage points), and no worse interval score.
Calibration is fitted before test; test coverage never tunes interval widths.

## Running sourced evaluation

```powershell
.\.venv\Scripts\python.exe scripts/build_history_manifest.py --output "research-data\manifest.json"
.\.venv\Scripts\python.exe scripts/evaluate_next_day.py --input "research-data\manifest.json" --output "research-data\next-day-report.json"
```

The builder inventories every cached bar without modifying the cache. It hashes
source files and exported daily CSVs, excludes duplicate/conflicting sessions,
merges overlapping real cache files by symbol and rejects unmatched evaluation
rows. Unknown/special timing gaps cannot be silently concatenated into one-session
labels. The NSE 2025 calendar was fetched and text-reviewed from NSE/CMTR/65587;
its source URL and SHA-256 are recorded beside the bundled calendar.

For equities, pass `--action-evidence path/to/review.json`. A reviewed input has
the following shape; placeholders are not usable evidence:

```json
{
  "datasets": [{
    "symbol": "RELIANCE",
    "cache_sha256": "ACTUAL_SHA256_OF_CACHE_FILE",
    "source": "Named exchange announcement/feed and review reference",
    "source_file": "reviewed-source.json",
    "source_sha256": "ACTUAL_SHA256_OF_REVIEWED_SOURCE",
    "reviewed_by": "Named reviewer",
    "complete_from": "2025-10-03",
    "complete_through": "2026-10-08",
    "covered_action_types": ["split", "bonus", "rights", "dividend"],
    "input_adjustment": "raw",
    "actions": []
  }]
}
```

An empty actions list is permissible only after a sourced, complete no-action
review; this workspace does not contain such equity evidence. Actions use the
existing `forecasting.corporate_actions.CorporateAction` fields. Adjustment and
numeric verification call that module, including fail-closed application warnings,
invalid terms and issuer mismatch. Already-adjusted inputs are not adjusted twice.
Provider/adjustment strings alone are no longer an accepted evaluation manifest.

The generated dataset entries may additionally name `signals_csv`, `intraday_csv`,
`options_csv` and `events_csv`. Optional paths may be omitted. Daily CSV columns:
`available_at,Open,High,Low,Close,Volume`, with daily close availability at
15:30 IST. Signals CSV uses `available_at` plus any of
`overnight_index_return,overnight_futures_return,atm_move,late_momentum,
late_volume_share,intraday_rv,vwap_distance`. Fractions are decimal returns,
not percentage points. Intraday uses the same OHLCV schema with actual bar-end
availability. Options CSV columns:
`available_at,expiry,strike,call_mid,put_mid`; CE/PE quotes must be synchronized
and expiry must include its actual time. Events CSV uses
`session_close,earnings,major_news,circuit_limit`, one 0/1 row per daily session.

Timestamps must include timezones. `--sequence-candidate` optionally evaluates
the LSTM. Exit code 2 means missing/invalid evidence and is not a passed gate.
The manifest is a provenance input, not an independent source-authenticity audit.
Per-symbol reports cannot authorize cross-universe promotion.

## Executed registry and price-quality integration (2026-10-09)

Pandera 0.27.1 validates normalized provider OHLCV and research inputs: finite
positive prices, nonnegative volume, OHLC containment and chronological unique
timestamps. Zero-volume indices are valid, with explicit missing volume features.
`next-day-quantile-v2` includes this real-data missingness correction.

Optional MLflow 3.17.0 stores actual fitted quantile estimators, signatures,
input examples and hashed manifest lineage in a persistent local SQLite registry:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-registry.txt
.\.venv\Scripts\python.exe scripts/evaluate_next_day.py --input docs/history-manifest.json --output docs/next-day-held-out-report.json --registry-uri "sqlite:///local-history/mlflow.db"
.\.venv\Scripts\python.exe scripts/verify_mlflow_registry.py --uri "sqlite:///local-history/mlflow.db" --output docs/mlflow-registry-report.json
```

Six registered model names have four executed versions each (early inventory/
continuity runs and the final merged-input run). All 24 versions were reloaded
and performed finite smoke inference. Smoke inputs are synthetic and prove
artifact restoration only. No deployment alias or signed promotion receipt was
created. Local registry storage is excluded from the sanitized Windows ZIP;
reproduction commands, source, market-input exports and execution reports ship.

## User-visible evidence

`/api/v1/scorecard` now includes `next_day`: authoritative, automatically settled
**1D + horizon=1 only** outcomes, coverage/nominal coverage, Winkler, grouped
persistence MASE, exact saved-bound pinball loss, data tiers and sample evidence
tier. Empty evidence remains null/insufficient. This card is visible on Track
Record and in the public scorecard component. The forecast payload separately
reports next-day untouched-fold calibration; intraday next-bar metrics are
explicitly excluded from next-day evidence. Sample sufficiency is not promotion.

## Paper order regression

Confirmation verifies the submitted review snapshot, closes the ticket after
resolution, and shows the recorded order ID/status even if returned context is
malformed. A mismatch has a separate explicit warning against resubmission.
Successful mutations await account/journal invalidation so balances and
positions refetch before completion. Submitted `.NS`/`.BO` symbol spelling is
preserved in market context while normalized symbols still own position math.

Playwright's core flow changes shared timeframe during review, checks the
original submitted timeframe, verifies BUY then SELL and visible cash/position
updates, and exercises an actually recorded order with mismatched response
context. Backend tests cover supplied/provider quotes, both suffixes, case and
whitespace, as well as BUY/SELL position accounting.

## Verification (2026-10-08)

- Focused backend/promotion/CQR regression run: 132 passed, 1 live-provider test
  skipped because its explicit live-test symbol was not configured.
- Separate evaluation/scorecard/promotion-gate run: 28 passed.
- After the final forecast-evidence/type corrections: 14 passed, 1 live test skipped.
- Whole-project mypy: 187 source files, no issues.
- Frontend `npm run verify`: API schema synchronization, forecast honesty checks,
  194 tests, TypeScript, production build and production dependency audit passed
  (zero reported vulnerabilities).
- Playwright core flow: passed, including reviewed-timeframe race, BUY/SELL
  account/position updates and committed-order/mismatched-context feedback.
- Real next-day evaluation: blocked, **not passed**. See the JSON report.
- Release packaging uses the existing allowlist, secret scans, inventory checks,
  atomic replacement and mandatory extracted import/smoke checks. Full clean-venv
  backend/frontend installation checks are not repeated during packaging; the
  source checks above are the verification scope for this update.
