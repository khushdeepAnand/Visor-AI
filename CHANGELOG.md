# Changelog

## StockPilot AI v12 Engineering Upgrade - 2026-09-27

- Added opt-in RFC 6238 MFA for regular users with encrypted TOTP secrets, single-use recovery codes, replay protection, bounded challenges, OAuth enforcement, and per-session revocation.
- Added Windows DPAPI secret storage and management commands; plaintext `.env` is fallback-only and remains excluded from sanitized releases.
- Added independent futures term-structure analysis with contango/backwardation classification, quoted rollover costs, fair-value comparison, open-interest concentration, API coverage, and responsive UI.
- Added a transparent cross-horizon confidence signal that reports direction conflicts, median-path reversals, and unexpected interval narrowing without changing forecast corridors.
- Added daily bounded retention enforcement for expired security artifacts, stale login attempts, audit records, and model-refresh JSON files.
- Added a STRIDE threat model, security governance gates, weekly dependency automation, itemized blocked regulatory-review tracking, and version-triggered research acknowledgment re-prompting.
- Separated forecast-job status polling from the compute-submission rate-limit bucket so bounded clients cannot lock out their own normal polling loop.
- Restored a clean strict-mypy gate across all source files and updated the reviewed OpenAPI surface fingerprint.
- Added a sanitized release-build override that ignores populated local environment files while continuing to exclude them and scan source, staged, ZIP, and extracted inventories.

## Security And Governance Update - 2026-09-05

- Constrained executable joblib artifact loading to resolved allowlisted roots and added optional SHA-256 validation against the exact bytes loaded.
- Neutralized spreadsheet formula prefixes in report data and added a safe CSV serializer.
- Expanded release hygiene for environment files, database sidecars, caches, logs, artifacts, reports, builds, bytecode, temporary output, and required governance documents.
- Expanded secret scanning across runtime and generated locations while preserving fail-closed unreadable/oversized handling.
- Added administrator operations, regulatory-review, upgrade-verification, and reference-reconciliation records.
- Added release-input and focused security stages to the Windows verifier, increasing the current workflow from 12 to 14 stages.
- Focused evidence: 33 tests passed; 251 allowlisted files passed release-input and secret gates with 0 findings. Full updated Windows and clean-archive verification remain not run; legal review is blocked and live providers remain not verified.

## StockPilot AI v6.1 Secure Release Candidate - 2026-08-31

### Documentation

- Replaced the root release documentation with a consistent v6.1 secure-release set.
- Corrected the frontend framework to Next.js 16.3.3 and React 19.2.8.
- Corrected the Windows requirement to exactly Python 3.12; the launchers do not accept Python 3.13.
- Corrected frontend setup to use the committed lock file through `npm ci`.
- Removed claims that private broker credentials are bundled.
- Documented setup-generated local JWT secrets and local-only credential handling.
- Corrected provider behavior: `LIVE_ONLY` is the default and demo mode requires explicit `OFFLINE_DEMO` configuration.
- Replaced obsolete project-folder examples with the current `StockPilot-AI-v6.1-SECURE` release identity or path-independent launcher instructions.
- Added explicit separation of verified facts, fixture-based checks, unavailable external checks, pending gates, and future work.
- Added `SECURITY_REVIEW.md` as a release-review artifact.

### Secure Release Behavior Represented By This Candidate

- Windows wrappers resolve paths relative to themselves and preserve exit codes, including paths containing spaces.
- First-run setup creates a Python 3.12 virtual environment, restores declared dependencies, creates local environment files, and generates a private JWT secret.
- Startup binds FastAPI and Next.js to loopback, checks ports, waits for readiness, monitors both processes, and performs bounded process-tree cleanup.
- Market-data responses expose provider, credential mode, symbol/instrument identity, timestamps, freshness, live/stale/fallback state, fallback reason, request ID, and provider mode.
- NSE/BSE catalogue validation rejects unsupported global, cryptocurrency, and commodity symbols.
- Browser authentication uses JWT HttpOnly cookies, explicit credentialed CORS origins, CSRF Origin checks for cookie-authenticated writes, endpoint rate limits, and sanitized operational errors.
- Upstox live-order place, modify, cancel, GTT, and multi-order routes are blocked. Paper trading does not call broker HTTP.
- Upstox configuration uses secure local prompts; the read-only diagnostic reports sanitized classifications and attempts zero orders.
- Release secret scanning rejects populated sensitive environment variables and recognized credential patterns without printing values.
- Synthetic demo fallback is prohibited outside explicit `OFFLINE_DEMO` mode.
- Upstox quote verification now requires broker identity, a positive price, and a broker timestamp; order blocking covers every Upstox subdomain.
- Redis and disk history caches use validated JSON instead of executable pickle deserialization.
- Paper trigger orders are claimed atomically before fill processing.
- Setup builds the production frontend and the normal launcher uses `next start`.
- Release ZIP validation rejects traversal, absolute, duplicate, case-colliding, symlink, nested-project, and forbidden artifact entries.
- Added an exact transitive Python 3.12 lock and isolated extracted-project virtual-environment verification.
- Added warning-as-error backend gates, full stream market context, ISO broker tick timestamps, and every-route demo-banner Playwright coverage.

### Verified Evidence

- Environment: Windows 11-like `win32`, 2026-08-31.
- Python 3.12.10, Node 24.18.0, npm 11.16.0.
- Internal API, frontend package, and lock-file versions are consistently `6.1.0`.
- `SETUP_STOCKPILOT.bat` passed in the OneDrive path and safely generated `.venv` and `.env`.
- `VERIFY_STOCKPILOT.bat -SkipBrowserInstall` passed all 12 stages.
- Launcher tests: 9 passed.
- Normal backend suite: 260 passed, 2 credential-gated skipped, 0 failed in 71.36s.
- Randomized backend suite: 260 passed, 2 skipped with seed 20260831 in 101.66s.
- Full suite including launchers: 269 passed, 2 skipped.
- `compileall` passed; mypy passed 76 source files.
- pip-audit found no known vulnerabilities after the pip 26.2.1 pin.
- Frontend: 6 files, 47 tests passed with no unhandled React warnings; TypeScript passed.
- Next.js 16.3.3 production build passed; npm production audit reported 0 vulnerabilities.
- Windows Playwright: 3 tests passed, including explicit demo labelling on every supported route and invalid-live-credential unavailable/no-demo-fallback behavior.
- Actual `START_STOCKPILOT.bat` startup and both readiness checks passed in the OneDrive path.
- Backend restart persistence retained one paper order and one position.

### Not Verified

- Real Upstox connectivity is NOT VERIFIED because no newly generated user token was available. A future encoded token expiry is not validation.
- Final allowlisted ZIP creation, secret/hygiene scans, inventory, path-with-spaces extraction, and extracted-copy verification passed.

## 6.0.0 - Full-Stack India Market Terminal Baseline

- Replaced the retired Streamlit presentation with the FastAPI and Next.js/React terminal architecture.
- Introduced India-only provider orchestration, interval forecasts, provenance-aware market data, risk and derivatives research, reports, workspaces, and paper trading.
- Added local SQLite persistence, optional Redis support, native/REST quote streaming paths, and Windows setup/start/verification launchers.
- Established the boundary of research and paper-trading simulation only, with no real broker orders.

## Earlier 5.x Baseline

- Introduced the original quantitative research, authentication, portfolio, watchlist, alert, forecasting, backtesting, derivatives, reporting, observability, and local persistence capabilities that were subsequently migrated into the full-stack terminal.

## 2026-09-09 - v8 upgrade pass (v7.1 -> v8)

### Added

- Morning brief service and `GET /api/v1/brief`.
- Screener service, 17 documented fields, saved screens, `/api/v1/screener/*` routes.
- Multi-leg option/future payoff engine and `POST /api/v1/options/payoff`.
- Pooled low-history forecast bridge used when per-symbol history is insufficient.
- Operator safety surface: status endpoint, step-up tokens, kill switches, feature flags,
  status banners, cache invalidation, admin operations panel.
- Static regulatory-boundary test proving live broker order mutation is unreachable.
- 23 previously undocumented environment variables listed in `.env.example`.

### Fixed

- Payoff engine: put legs had an inverted tail direction, so a naked short put was reported
  as unbounded profit and a long put as unbounded loss.
- Payoff engine: max profit/loss are now evaluated at the true boundaries (a price of zero,
  and beyond the highest strike) and flagged with `outside_plotted_range`, instead of being
  read off the plotted grid and understated.
- Pooled low-history path: a provider failure on the requested symbol now returns the
  documented `history_unavailable` refusal instead of leaking the loader's exception.
- Admin UI: provider priority and `bootstrap_admins` now mint and send a step-up token, so
  they no longer fail with `step_up_required`.

### Not implemented

- Part D4-D8, and any UI for the brief, screener or payoff endpoints.

## 2026-09-09 - v9 upgrade pass (v8 -> v9)

**Added**

- No-code strategy builder (`services/strategy_builder.py`) with 20 metrics,
  8 operators and AND/OR condition groups. Long-only, paper-only.
- Equity and multi-leg option backtesting (`services/strategy_backtest.py`).
  Costs are always reported separately from gross P&L.
- Forward-test tracking (`services/forward_test.py`) that records signals
  without ever placing an order.
- Eleven API routes for strategies, option backtests and forward tests, each
  behind a feature flag that returns HTTP 503 `feature_disabled` when off.
- Morning Brief (`/brief`) and Screener (`/screener`) pages, a Payoff tab on
  `/derivatives`, and the `PayoffChart` component.
- Four `.claude/skills/` working notes and their allowlist entry.

**Changed**

- `TerminalShell` navigation now lists Morning Brief and Screener.
- Multi-leg pricing reads `theoretical_price` from `black_scholes()` and
  defaults leg lot size to 1 before normalisation.

**Unchanged on purpose**

- No live broker order path. SEBI review remains BLOCKED / NOT REVIEWED.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### Added

- `services/setup_doctor.py`: eight-check configuration doctor (core, session, database, Google
  sign-in, Upstox credentials, Upstox token, market-data fallback, frontend origin). Reports the
  exact missing variable names, what each blocks, and the fix. Never returns secret values.
- `scripts/doctor_setup.py`: CLI wrapper with `--env-file`, `--frontend-env-file` and `--json`;
  exits 1 when any check is `action_required`.
- `forecasting/conformal_calibration.py`: split-conformal interval calibration on walk-forward
  residuals, with measured coverage, MAE/RMSE, skill against a random-walk baseline, and a support
  gate that abstains rather than publish an uncalibrated range.
- API: `GET /api/v1/admin/setup`, `GET /api/v1/admin/review`, `GET /api/v1/admin/calibration`,
  `GET /api/v1/admin/calibration/methodology`.
- Frontend: `/admin/setup` Setup Doctor screen, reachable from admin navigation.
- Tests: `tests/test_conformal_calibration.py` (17), `tests/test_setup_doctor.py` (12).

### Changed

- Design system replaced wholesale: `frontend/app/globals.css` redefines the CSS variable token
  layer that `tailwind.config.ts` maps every colour, font and shadow to, so all existing screens
  restyle at once. `frontend/app/layout.tsx` viewport switched to the paper theme colour.

### Not changed

- The forecast range contract, paper-trading-only execution, the India-only scope, and the
  unapproved state of `RELEASE_CHECKLIST.md` and the SEBI review.

## 2026-09-14 - v10 hotfix pass (runnability + theme)

### Fixed

- **Everything blocked on first run.** `.env.example` shipped `STOCKPILOT_PROVIDER_MODE=LIVE_ONLY`,
  so a fresh checkout with no broker credentials correctly (by design) refused to show quotes,
  charts, indicators or forecasts rather than fabricate data - but that left the app unusable out
  of the box. `.env.example` now ships `STOCKPILOT_PROVIDER_MODE=OFFLINE_DEMO` instead: still an
  explicit, non-silent choice (per the existing comment in the file and `services/market_data/demo.py`'s
  own docstring), routed through the already-implemented, already-tested `demo_india` provider and
  the existing demo banner in `MarketContext.tsx`. `QUICK_START_WINDOWS.md`'s "Default Data Behavior"
  section is updated to match. Switching to `LIVE_ONLY` once a real broker is configured is unchanged
  and still fail-closed.
- **No real dark theme existed.** `app/layout.tsx` hardcoded `className="dark"` on `<html>` left over
  from the pre-v10 dark-terminal design, but `globals.css`'s v10 rewrite defined only one ("paper")
  palette - the class did nothing. Added a full `html.dark` CSS-variable palette in `globals.css`
  (every token in `:root` now has a dark counterpart), a `ThemeProvider` (`components/ThemeProvider.tsx`,
  localStorage-persisted, OS-preference-aware), a `ThemeToggle` button wired into `TerminalShell`'s
  header, and a `beforeInteractive` boot script in `layout.tsx` so the correct theme paints on first
  load with no flash. `tests/theme.test.tsx` added.
- Removed 182 stray `__pycache__`/`.pyc` files that had been committed into the working tree, which
  `scripts/release_hygiene.py` would otherwise flag.

### Verified in this pass

- Full-tree `python3 -m py_compile` across every `.py` file: clean, before and after these changes.
- All `@/`-aliased imports across `app/` and `components/` resolve to real files (scripted check).
- Every `.tsx` file using React hooks has a `"use client"` directive (scripted check).
- Brace/paren/bracket balance verified by script for every file touched in this pass.

### NOT verified

- `npm ci`, `tsc --noEmit`, `npm run build`, `vitest run` (including the new `theme.test.tsx`), and
  Playwright were **not executed** - no network and no `node_modules` in this environment. Treat the
  theme and provider-mode changes as statically reviewed, not build-verified, until run on a networked
  machine per `.claude/skills/release-verification.md`.
- No FastAPI/pytest run - same constraint as every prior pass in this document.
- Live broker validation remains not verified, as always.
## Unreleased

### Features
- publish verified StockPilot v18 foundation

### Fixes
- E2E rate limit retry - return failed response after max retries, increase max retries to 5
- E2E test robustness - screener wait for mutation, URL redirect expectations, gitleaks config, rate limit retry
- flaky E2E tests - robust research/screener assertions
- CI failures - strict mode selector, system endpoint error handling, mypy Windows constants

### Maintenance
- update changelog
- fix gitleaks config - use .gitleaks.toml allowlist for path-based false positives
