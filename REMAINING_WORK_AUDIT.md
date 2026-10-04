# Remaining Work Audit (current: v14 pass)

Date: 2026-09-30. Scope: `StockPilot-AI-v6.1-SECURE` working tree, checked against
`StockPilot-AI_v14_Continuation_Prompt.md`.

> **Supersession note.** The previous content of this file was a running log of the
> v6.1 -> v9 passes (flat-archive constraints, offline test harness gaps, and
> long-closed items). It no longer describes the repository and has been replaced
> by this audit. `V7_UPGRADE_STATUS.md` does not exist (nothing to refresh). The
> docs the old audit referenced are present and current: `MODEL_CARD.md`,
> `FORECAST_CONTRACT.md`, `DESIGN_SYSTEM.md`.

Status legend: **[done]** implemented and tested in this tree · **[partial]**
started, not complete · **[open]** not started · **[gated]** cannot be closed
without external resources.

---

## Part 1.1 - Security / platform

| Item | Status | Evidence |
| --- | --- | --- |
| Full-database encryption (SQLCipher) | **[done]** | `database.py` uses `pysqlcipher3` with key handling; falls back only when unavailable |
| PostgreSQL + Alembic migrations + shared DAO layer | **[done]** | `alembic/versions/8788046ff051_*.py` (29 tables, offline PG SQL verified), `scripts/run_migrations.py`, `services/db/{base,sqlite_impl,postgres_impl,factory}.py`; SQLite remains the single-user default |
| Task queue (Celery/RQ + Redis) with retries and DLQ | **[done]** | `services/task_queue.py` (Celery or inline executor, retry/backoff, Redis DLQ with replay), `tests/test_task_queue.py` |
| Recreate `DEPLOYMENT.md` (local, Compose, Postgres, TLS, secrets, backups) | **[done]** | `DEPLOYMENT.md` rewritten for the Compose stack in `docker-compose.yml` (Postgres, Redis, migrations, Celery, nightly `pg_dump`) |
| WebAuthn / passkeys as second MFA factor | **[done]** | `services/webauthn.py`, routes `POST /api/v1/auth/webauthn/{register,mfa}/...`, `tests/test_webauthn.py` |
| Login anomaly protection (new device, impossible travel) | **[done]** | `services/login_anomaly.py` (device fingerprints, pluggable geolocation, speed check), login response carries `anomalies`, `GET /api/v1/auth/login-anomalies` + ack, `tests/test_login_anomaly.py` |
| OpenTelemetry tracing across API -> forecast job -> data provider | **[partial]** | `services/telemetry.py` + FastAPI request spans wired in `api/main.py`, exporter via `STOCKPILOT_OTEL_*`; per-job and per-provider child spans still to be added |
| Generated TypeScript API client from OpenAPI (drift = build error) | **[done]** | `scripts/export_openapi.py`, `frontend/lib/generated/{openapi.json,routes.ts}` (161 routes), `frontend/lib/typedApi.ts`, gates: pytest `tests/test_openapi_sync.py`, `npm run check:api` (prebuild), CI steps |
| Refresh stale docs | **[done]** | this file; `V7_UPGRADE_STATUS.md` absent; `MODEL_CARD.md` / `FORECAST_CONTRACT.md` / `DESIGN_SYSTEM.md` present |
| Run everything for real (pytest, npm test, Playwright, CI) with network; live Upstox + multi-instance Redis rate limiting | **[gated]** | backend + vitest suites pass locally; the CI workflow (`.github/workflows/ci.yml`) has not been executed on a networked runner in this pass |
| External penetration test and legal/regulatory review | **[gated]** | `REGULATORY_REVIEW_REQUIRED.md`; requires qualified external reviewers |

## Part 1.2 - Options / futures product depth

| Item | Status | Notes |
| --- | --- | --- |
| IV Rank / IV Percentile history per underlying | **[open]** | no implementation found |
| Multi-leg strategy builder (iron condor, straddle/strangle, calendars, ratios) with combined payoff, breakevens, live P&L | **[partial]** | `services/strategy_builder.py`, `strategy_backtest.py`, `derivatives/payoff.py` cover legs/backtest/payoff; expiry-aware structures, breakeven reporting and live P&L tracking incomplete |
| OI-change heatmap across strikes; expiry-day pin-risk visualization | **[open]** | no OI heatmap implementation found |
| SPAN/margin estimates for paper multi-leg positions | **[open]** | `margin_required` column exists for single orders; no SPAN/multi-leg engine |

## Part 1.3 - Frontend / product

| Item | Status | Notes |
| --- | --- | --- |
| Storybook / living design system; accessibility audit | **[open]** | no `.storybook`; tokens live in `DESIGN_SYSTEM.md` + `globals.css` |
| Performance budgets / Core Web Vitals in CI | **[open]** | CI runs typecheck, vitest, build, audits only |
| Playwright E2E for full auth + MFA + session-revoke lifecycle | **[partial]** | `frontend/tests/e2e/{core,research,strategies,v9-surfaces}.spec.ts` exist; no dedicated MFA/revoke lifecycle suite |
| Mobile (PWA or React Native), community sharing, sandboxed strategy scripting | **[open]** | manifest exists (PWA basics); no sandboxed scripting surface |
| Second live data provider + provider-health panel | **[partial]** | `MANAGER` health/provider modes exist (`/api/v1/health`); second provider not added |

## Part 2 - Prediction upgrades (2.1-2.8)

| Section | Status | Notes |
| --- | --- | --- |
| 2.1 Data-tier router (T0-T4) with tier/evidence/reason surfaced | **[done]** | `DataSufficiencyReport`, evidence grades, tier fields in responses exist; `assign_tier` with per-tier tests in `tests/test_leakage_detection.py` |
| 2.2 Volatility first (HAR-RV, GARCH family, range estimators, realized variance, width-from-vol forecast) | **[partial]** | volatility forecast block published; HAR/GARCH/range-estimator members not added |
| 2.3 Sharper intervals (CQR default, ACI, Mondrian conformal, fan chart quantiles, tick/circuit-limit clipping) | **[done]** | CQR live when promoted (`cqr_canary_status` + `cqr_calibration`), ACI persisted (`read_aci_state`/`record_realized_outcome`), Mondrian per-group with persistence (`mondrian_conformal_half_width_persisted` + `save_mondrian_calibration`), fan chart quantiles, circuit-limit clipping all implemented |
| 2.4 Short history / new listings (panel model + embeddings, shrinkage, peer transfer, IPO features, foundation-model member) | **[partial]** | `pooled_cross_section.py`, `low_history_forecast.py`, `peer_transfer_prior`, `ipo_features` implemented; foundation-model member open |
| 2.5 Long history (regime-conditional routing, sample weighting, stacking, joint multi-horizon, stability checks) | **[partial]** | regime report + horizon-consistency flags exist; regime-conditional training/stacking not wired |
| 2.5b Data correctness (corporate actions, calendar, outliers, surveillance flags, leakage test, survivorship) | **[done]** | NSE calendar (`services/market_calendar.py`), `feature_timestamp` provenance, corporate-action adjustment (`forecasting/corporate_actions.py`), leakage-detection test (`tests/test_leakage_detection.py` with 7 tests) all implemented |
| 2.6 Feature expansion (market/sector context, VIX, positioning, flows, events, fundamentals, sentiment) | **[partial]** | expected-move, futures basis, sentiment surface exist; flows/events/fundamentals features open |
| 2.7 Per-tier evaluation with purging/embargo | **[done]** | `evaluation_harness.py` with `build_purged_group_folds`, `evaluate_tier`, `run_all_tier_gates` with Diebold-Mariano, conditional coverage, promotion gate |
| 2.8 Scheduling (live-decay, public scorecard, ACI updates) | **[done]** | `scheduler.py` with APScheduler jobs for settlement, drift evaluation, live decay computation, public scorecard rebuild, model refresh |

## Addendum, 2026-10-03 (v16 CI fix)

- **CI Storybook + Lighthouse steps removed** — the `npm run storybook:check` and `npm run performance:budget` scripts in `frontend/package.json` pointed to offline placeholder files that were deleted when real tooling was never installed. Rather than leave a CI pipeline that always fails on missing files, the two steps were removed from `.github/workflows/ci.yml` and the dangling npm scripts deleted. This is tracked as an open item below.

## Part 1.3 - Frontend / product (updated)

| Item | Status | Notes |
| --- | --- | --- |
| Storybook / living design system; accessibility audit | **[open]** | Removed broken CI references. Need to add `storybook` / `@storybook/nextjs` devDependencies, minimal config, and `axe-playwright` or `@storybook/test-runner` for a11y; re-add `npm run storybook:check` and CI step. |
| Performance budgets / Core Web Vitals in CI | **[open]** | Removed broken CI reference. Need to add `@lhci/cli` devDependency, `.lighthouserc.json` with budgets (LCP ≤ 2.5s, INP ≤ 200ms, CLS ≤ 0.1), and `npm run performance:budget` running `lhci autorun`; re-add CI step. |