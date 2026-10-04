# Testing

## Evidence Rules

Use these labels consistently:

- **VERIFIED**: the check ran and a concrete result is recorded.
- **FIXTURE-VERIFIED**: the check ran against mocks, synthetic/seeded data, temporary SQLite, or jsdom. It validates application behavior, not an external service.
- **NOT VERIFIED**: the external dependency could not be checked.
- **PENDING**: the check has not finished or its final evidence is not available.
- **FUTURE WORK**: outside the current release gate.

Passing source inspection or a fixture test is never evidence that a broker token, network endpoint, market price, or model prediction is live or accurate.

## Supported Environment

- Windows with PowerShell 5.1.
- Exactly Python 3.12 for the provided launchers and verification script.
- Node.js compatible with the engine range in `frontend/package.json`.
- Lock-file frontend restoration with `npm ci`.
- Exact Python 3.12 restoration with `requirements-dev.lock`.

The recorded run used Python 3.12.10, Node 24.18.0, and npm 11.16.0 in a Windows 11-like `win32` environment on 2026-08-31.

## Standard Verification

After setup, run from the root. `-SkipBrowserInstall` reuses the browser installed during setup:

```bat
VERIFY_STOCKPILOT.bat -SkipBrowserInstall
```

The current script runs these 14 stages in order:

1. Allowlisted release-input, required-document, and secret-hygiene gates.
2. Focused artifact-loading, spreadsheet-export, release-security, and launcher tests.
3. Windows launcher/static contract tests.
4. Normal backend tests with launcher tests excluded and random ordering disabled.
5. Backend tests in randomized order with seed 20260831.
6. Python source compilation with generated/dependency directories excluded.
7. Mypy with strict defaults and named legacy exceptions using `mypy.ini`.
8. `pip-audit --local` unless explicitly skipped.
9. `npm ci` from `frontend/package-lock.json`.
10. Vitest frontend tests.
11. TypeScript checking.
12. Next.js production build.
13. npm production dependency audit unless explicitly skipped.
14. Windows Playwright end-to-end tests.

The recorded `VERIFY_STOCKPILOT.bat -SkipBrowserInstall` run passed the prior 12-stage workflow on 2026-08-31. The updated 14-stage workflow has not been run completely and must not be recorded as passed.

## Individual Commands

Backend full suite:

```bat
.venv\Scripts\python.exe -m pytest -q
```

Focused security, export, release, and launcher gates:

```bat
.venv\Scripts\python.exe scripts\verify_release_inputs.py
.venv\Scripts\python.exe -m pytest -q -p no:randomly tests/test_model_runtime.py tests/test_reports.py tests/test_release_security.py tests/test_windows_launchers.py
```

On 2026-09-05 these commands passed: 251 allowlisted files with 0 secret findings and 33 tests passed in 2.36s. The existing prediction cache integration test separately passed in 9.55s. The broad working-tree secret/hygiene scans failed closed on local private/generated state as expected; the working directory is not a release payload.

Python compilation:

```bat
.venv\Scripts\python.exe -m compileall -q .
```

Type checking with strict defaults and named legacy exceptions:

```bat
.venv\Scripts\python.exe -m mypy --config-file mypy.ini .
```

Python dependency audit:

```bat
.venv\Scripts\python.exe -m pip_audit --local
```

Frontend reproducible restore and checks:

```bat
cd frontend
npm ci --no-fund --no-audit
npm run test
npm run typecheck
npm run build
npm run audit:prod
```

Playwright:

```bat
cd frontend
npm run test:e2e
```

Windows Playwright passed 3 tests. Coverage included the `OFFLINE_DEMO` banner on every supported route; register, logout, login, RELIANCE chart, forecast, technical indicators, paper order, portfolio, and logout; plus mocked invalid live credentials producing an unavailable state with no demo fallback.

Read-only Upstox diagnostics, only after configuring a newly generated user token:

```bat
CONFIGURE_UPSTOX.bat
VERIFY_UPSTOX_LIVE.bat
```

Do not run live checks with production account data in captured output. Do not claim success from local JWT expiry decoding.

## What Local Tests Cover

The backend suite covers authentication, SQLite persistence, API security, market-data orchestration, provider diagnostics, instrument restrictions, freshness/provenance, streaming fallback, indicators, forecasting, risk, backtesting, paper trading, alerts, audit behavior, derivatives, reports, workspaces, launchers, and application smoke behavior.

The frontend suite covers component and page behavior under jsdom, including market context, forecast states, timeframe consistency, workspace states, and virtualized order tables.

Many checks are fixture-based:

- External provider HTTP and WebSocket responses are mocked.
- Test users and trading records use temporary SQLite databases.
- Forecast, indicator, risk, and backtest inputs include synthetic or seeded frames.
- Browser component tests use jsdom rather than a real browser.
- Security tests use artificial credential-shaped strings, test JWTs, and endpoint fixtures.

These are valuable deterministic checks but are not external connectivity proof.

## Recorded Results

See [VERIFICATION_RESULTS.md](VERIFICATION_RESULTS.md) for the authoritative result record. Current completed evidence is:

- Consolidated verification: all 12 stages passed.
- Launcher tests: 9 passed.
- Normal non-launcher backend suite: 260 passed, 2 credential-gated skipped, 0 failed in 71.36s.
- Randomized non-launcher backend suite: 260 passed, 2 skipped with seed 20260831 in 101.66s.
- Full suite including launcher tests: 269 passed, 2 skipped.
- `compileall`: passed.
- Mypy: passed 76 source files under strict defaults with named legacy exceptions.
- pip-audit: no known vulnerabilities after the pip 26.2.1 pin.
- Frontend Vitest: 6 files, 47 tests passed with no unhandled React warnings.
- TypeScript: passed.
- Next.js 16.3.3 production build: passed.
- npm production audit: 0 vulnerabilities.
- Windows Playwright: 3 passed in 57.5s from the final isolated extraction.
- First-run setup in a OneDrive path with spaces: passed and safely generated `.venv` and `.env`.
- Actual `START_STOCKPILOT.bat`: backend/frontend started and both readiness checks passed in the OneDrive path.
- Backend restart persistence: one paper order and one position retained.

The two credential-gated backend skips must not be converted into passes. They preserve the real-provider external boundary.

## Release Archive Checks

- Final ZIP secret/hygiene/inventory and selected extracted-copy verification: passed.
- A complete deterministic/randomized/backend/frontend/Playwright rerun using a newly created virtual environment inside a fresh extraction passed.

## External Checks Not Verified

- Real Upstox instruments, quotes, history, option chain, margin, and V3 stream authorization with a newly generated user token.
- Angel One, supplementary NSE network behavior, and yfinance fallback against current services.
- Google OAuth, SMTP password reset delivery, Sentry, Redis leadership/pub-sub, and scheduler operation in deployed infrastructure.
- Forecast quality on future unseen market outcomes.

## Future Work

Retain exact command/timestamp evidence for a complete all-12-stage clean-extraction run. Separately, verify real external providers only when current user-owned credentials and suitable infrastructure are available.

## 2026-09-09 - v8 upgrade pass (v7.1 -> v8)

### New in the v8 pass

| File | Covers |
| --- | --- |
| `tests/test_morning_brief.py` | Snapshot arithmetic, symbol cap, exclusion reasons, market-status and news failure tolerance, disclosures. |
| `tests/test_screener.py` | Field catalogue, per-metric window requirements, filter validation, operators, sorting/limits, saved-screen CRUD and per-user scoping. |
| `tests/test_option_payoff.py` | Leg normalisation and rejection codes, breakevens, bounded/unbounded tails, extremes outside the plotted grid, optional Black-Scholes marks, margin unavailable. |
| `tests/test_low_history_forecast.py` | Flag gating, refusal codes, peer fan-out cap, peer-failure tolerance, provenance labelling, range/abstention consistency. Skips unless scipy and scikit-learn are installed. |
| `tests/test_broker_order_mutation_unreachable.py` | Static proof that no live broker order path or non-paper order route exists in shipped source. |

Run them with the project's normal invocation:

```
pytest tests/test_morning_brief.py tests/test_screener.py tests/test_option_payoff.py tests/test_low_history_forecast.py tests/test_broker_order_mutation_unreachable.py
```

The broker-boundary scan skips `tests/` on purpose: `tests/test_phase4_security.py` names
the forbidden Upstox order endpoints as fixtures for its own allowlist assertions.

## 2026-09-09 - v9 upgrade pass (v8 -> v9)

### New test modules

| File | Tests | Covers |
| --- | --- | --- |
| `tests/test_strategy_builder.py` | 21 | metric/operator validation, AND/OR groups, crossing vs level operators, stop/target attribution, caps, store CRUD |
| `tests/test_strategy_backtest.py` | 13 | gross/costs/net separation, adverse fills, open-position exclusion, model-priced multi-leg cycles, leg caps, validation codes |
| `tests/test_forward_test.py` | 14 | idempotent evaluation, no pre-start backfill, per-user scoping, stop/restart refusals, realised-only scorecard |
| `frontend/tests/brief.test.tsx` | 4 | session snapshot, stale and insufficient-history labelling, "not a forecast" disclosure |
| `frontend/tests/screener.test.tsx` | 4 | field catalogue, starter screens, saved screens, run controls |
| `frontend/tests/PayoffChart.test.tsx` | 7 | curve rendering, debit vs credit, unbounded profit, margin-unavailable wording, empty-curve fallback |

Backend: 48/48 pass offline. Frontend: **written, never executed** - Vitest and
Playwright need `npm ci`, which needs network.

### Running the offline harness

```
python3 /data/tools/make_stubs.py
python3 /data/tools/offline_runner.py /path/to/repo tests/test_strategy_builder.py
```

The harness resolves module fixtures plus built-in `tmp_path` and `monkeypatch`.
Anything it cannot resolve is printed as SKIP and counted as a skip, never as a
pass. It is a stopgap: real pytest remains the gate.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### v10 test additions

- `tests/test_conformal_calibration.py` (17 tests): no-lookahead guarantees (mutating the tail
  cannot change earlier walk-forward predictions), monotonic half-width in confidence, measured
  coverage near target, coverage holding as the price level rises, refusal codes for short history /
  bad horizon / bad confidence / missing close column / non-positive closes, the random-walk model
  never claiming skill over itself, and the methodology denying point predictions.
- `tests/test_setup_doctor.py` (12 tests): every missing variable named, complete environment
  reporting `ok`, redirect-URI warnings (localhost, trailing slash, wrong path), partial Upstox
  credentials naming only the missing one, blocked-capability propagation, and an explicit assertion
  that no secret value appears anywhere in the report.

Verified in this environment with the offline runner: **73 passed, 0 failed** across the suite it can
collect, plus `python3 -m py_compile` on every touched module and an aliased-import check at
`217 checked / 0 missing`.

Still not executable here (no network, no Node toolchain, no pytest/FastAPI/scipy): `npm ci`,
`next build`, `tsc`, vitest, Playwright, and any request-level API test through `fastapi.testclient`.
The frontend changes in this pass are therefore type-unverified.
