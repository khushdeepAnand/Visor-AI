# Verification Results

## 2026-09-05 Security And Governance Addendum

This addendum supersedes the release decision below for the changed candidate; the remainder of this file preserves dated 2026-08-31 evidence.

| Check | Result | Classification |
| --- | --- | --- |
| Focused model/report/release/launcher tests | 33 passed in 2.36s on Python 3.12.10 | FIXTURE-VERIFIED |
| Existing prediction cache integration | 1 passed in 9.55s | VERIFIED compatibility check |
| Changed-module compilation | Passed | VERIFIED scoped check |
| Changed-module mypy | 5 modules, no issues | VERIFIED scoped check |
| Required docs and release input hygiene | 251 allowlisted files accepted | VERIFIED release-input check |
| Allowlisted-input secret scan | 251 files, 0 findings | VERIFIED release-input check |
| Working-tree secret scan | Failed closed on local `.env`, active/unscannable generated Next files, and generated fixture bytecode | BLOCKED local tree, values not printed |
| Working-tree hygiene | Failed closed on runtime/development artifacts | BLOCKED direct tree packaging |
| Updated 14-stage Windows verifier | Not run completely | NOT RUN |
| New clean archive and extraction | Not built or run | NOT RUN |
| External providers | No new live evidence | NOT VERIFIED |
| Legal/regulatory review | No qualified external sign-off | BLOCKED |

Current release decision: **BLOCKED FOR DELIVERY** until a new complete 14-stage run and clean extracted archive pass. The allowlisted release input is clean; the working tree must not be packaged directly. No legal approval or live-provider evidence is claimed.

## Record

- Product: StockPilot AI v6.1 secure release candidate.
- Evidence date: 2026-08-31.
- Environment: Windows 11-like `win32` environment.
- Python: 3.12.10.
- Node.js: 24.18.0.
- npm: 11.16.0.
- Verification scope: completed safety fixes, regression suites, dependency audits, and allowlisted clean-extraction release workflow.
- Final isolated build started: `2026-08-31T14:58:39.6695249+05:30`.
- Final isolated build finished: `2026-08-31T21:55:58.9129963+05:30`.
- Extraction path: `C:\Users\khush\AppData\Local\Temp\StockPilot release extraction vb3m315e\StockPilot-AI-v6.1-SECURE`.

Phases 7 and 9 are complete. The results below are the final local and clean-extraction verification evidence; external provider checks remain separate boundaries.

## Verified Results

| Check | Result | Classification |
| --- | --- | --- |
| Version metadata | API, package, and lock file report 6.1.0 | VERIFIED release identity |
| First-run setup | Passed in OneDrive path; `.venv` and `.env` generated safely | VERIFIED Windows setup |
| Consolidated verifier | `VERIFY_STOCKPILOT.bat -SkipBrowserInstall` passed all 12 stages | VERIFIED Phase 7 workflow |
| Launcher tests | 9 passed | VERIFIED launcher suite |
| Normal non-launcher backend suite | 260 passed, 2 credential-gated skipped, 0 failed, warning-as-error enabled in 71.36s | VERIFIED deterministic suite |
| Randomized non-launcher backend suite | 260 passed, 2 skipped; seed 20260831; warning-as-error enabled; 101.66s | VERIFIED randomized suite |
| Full suite including launchers | 269 passed, 2 skipped | VERIFIED full-suite total |
| Python compilation | `compileall` passed | VERIFIED compilation |
| Mypy | 76 source files passed | VERIFIED static check |
| Python dependency audit | No known vulnerabilities after pip 26.2.1 pin | VERIFIED pip-audit result |
| Frontend tests | 6 files, 47 tests passed; no unhandled React warnings | VERIFIED jsdom/component suite |
| TypeScript | Passed | VERIFIED static check |
| Frontend production build | Next.js 16.3.3 build passed | VERIFIED build |
| Frontend production dependency audit | 0 vulnerabilities | VERIFIED npm audit result |
| Windows Playwright | 3 tests passed in 57.5s | VERIFIED browser E2E |
| Actual Windows startup/readiness | Backend/frontend started; both readiness checks passed in OneDrive path | VERIFIED launcher runtime |
| Backend restart persistence | One paper order and one position retained | VERIFIED persistence |
| Release archive | Allowlist inventory, ZIP and extracted-tree hygiene/secret scans passed | VERIFIED Phase 9 archive |
| Clean extraction | New `.venv`, exact Python lock, deterministic/randomized backend, warnings, compile, mypy, pip-audit, `npm ci`, frontend tests/typecheck/build/audit, and Playwright passed | VERIFIED extracted release |

No skipped test is counted as passed. The two skips are credential-gated and do not establish live provider connectivity.

## Inspected Facts

- `setup_stockpilot.ps1` requires exactly Python 3.12 and restores `requirements-dev.txt` into `.venv`.
- `api/main.py`, `frontend/package.json`, and `frontend/package-lock.json` report `6.1.0`.
- `frontend/package.json` pins Next.js 16.3.3 and declares npm 11.16.0.
- Frontend restoration uses `npm ci` with `frontend/package-lock.json`.
- Setup creates `.env` from `.env.example` if absent and generates a 48-byte random JWT secret if needed.
- `STOCKPILOT_PROVIDER_MODE` defaults to `LIVE_ONLY`.
- `OFFLINE_DEMO` and `FALLBACK_ALLOWED` are explicit modes.
- Symbol normalization requires catalogue membership in the NSE/BSE universe and rejects known global/crypto/commodity forms.
- The Windows start script binds the backend to `127.0.0.1:8000` and the frontend to `127.0.0.1:3000`.
- Setup builds the Next.js production frontend and the normal launcher runs `next start` on loopback.
- Upstox order-action URL patterns are blocked, while read-only market-data GET requests remain permitted.
- The consolidated verifier includes deterministic and randomized backend runs, compilation, typing, both dependency audits, frontend tests/build, and Playwright.

Inspection confirms what the source is designed to do; it is not a substitute for runtime, external, penetration, or release-archive testing.

## Fixture-Based Checks

The completed Python and frontend counts include checks that use temporary SQLite databases, mocked provider HTTP/WebSocket behavior, synthetic or seeded market frames, artificial tokens/errors, and jsdom. Those checks validate contracts such as:

- JWT secret requirements, password handling, lockout, cookie attributes, CORS, CSRF Origin checks, rate limiting, and error redaction.
- Secret-scanner detection behavior and source-tree expectations.
- Upstox token metadata handling, retries, sanitized classifications, quote/history parsing, and future-expiry non-validation.
- Blocking of Upstox order endpoints and separation of paper orders from broker HTTP.
- NSE/BSE symbol restrictions, provider modes, provenance, stale/fallback state, and streaming fallback logic.
- SQLite first-run schema, user data, portfolios, watchlists, alerts, workspaces, paper trades, forecasts, reports, and audit behavior.
- Forecasting, indicator, risk, backtest, derivatives, and historical challenge behavior on controlled data.
- Frontend pages/components, data-source state, timeframe behavior, unavailable/stale states, and virtualized tables under jsdom.
- Playwright verified the explicit `OFFLINE_DEMO` persistent banner and a mocked invalid-live-credential unavailable state with no demo fallback.

Fixture-based checks do not establish real exchange prices, broker authorization, external-service availability, or future model quality. Playwright establishes browser behavior for its local explicit-demo and mocked-invalid-credential scenarios, not real broker connectivity.

## External Checks Not Verified

### Upstox

**Status: NOT VERIFIED.**

No newly generated user token was available. Therefore this release does not claim successful real Upstox instrument, quote, history, option-chain, margin, V3 authorization, or streaming responses.

Required commands after generating a user token:

```bat
CONFIGURE_UPSTOX.bat
VERIFY_UPSTOX_LIVE.bat
```

Decoded expiry metadata, fixture responses, source inspection, or a public instrument-master download alone must not be reported as token validity.

### Other External Systems

- Angel One: NOT VERIFIED.
- Supplementary NSE and yfinance current network behavior: NOT VERIFIED.
- Google OAuth: NOT VERIFIED.
- SMTP delivery: NOT VERIFIED.
- Sentry reporting: NOT VERIFIED.
- Redis multi-worker stream leadership/pub-sub: NOT VERIFIED in deployed infrastructure.
- Scheduler and external calendar refresh source: NOT VERIFIED against a configured external source.

## Release Archive Results

- Built by `scripts/build_release.py` from `release-allowlist.txt`.
- Exactly one top-level `StockPilot-AI-v6.1-SECURE` folder is present.
- Forbidden artifacts, unsafe ZIP paths, duplicate/case-colliding names, secrets, private environments, databases, caches, dependencies, builds, logs, reports, bytecode, and nested ZIP files are rejected.
- The archive was extracted to a fresh temporary path containing spaces and the complete selected verification workflow passed there.

## Exact Final Commands

The final isolated builder ran these commands from the extracted release:

```text
<source .venv Python> -m venv <extracted>\.venv
<extracted>\.venv\Scripts\python.exe -m pip install --disable-pip-version-check --no-input -r requirements-dev.lock
<extracted Python> -m pip check
<extracted Python> -m pytest -q -p no:randomly tests/test_windows_launchers.py
<extracted Python> -m pytest -q -W error -p no:randomly --ignore=tests/test_windows_launchers.py
<extracted Python> -m pytest -q -W error --randomly-seed=20260831 --ignore=tests/test_windows_launchers.py
<extracted Python> -m compileall -q api services scripts tests
<extracted Python> -m mypy --config-file mypy.ini .
<extracted Python> -m pip_audit --local
npm ci --no-fund --no-audit
npm run test
npm run typecheck
npm run build
npm audit --omit=dev --audit-level=high
npm run test:e2e
```

The builder then reran ZIP inventory, secret, path-safety, and extracted-tree hygiene gates. The final ZIP size and SHA-256 are emitted after documentation is frozen because embedding a ZIP's own hash inside that ZIP would change the hash. The lock pins exact transitive versions; artifact hashes remain an explicit follow-up because the PyPI hash-generation download timed out on 2026-08-31.

## Release Decision

Historical 2026-08-31 decision: **APPROVED FOR LOCAL WINDOWS RELEASE** for that dated archive.

Those dated local and archive gates passed. The changed 2026-09-05 candidate is governed by the addendum at the top of this file and is blocked pending its complete updated workflow. Upstox remains NOT VERIFIED because no newly generated user token was supplied.

## 2026-09-19 Full Regression Re-Confirmation (freshly measured this session)

Re-ran both suites in-tree today; these are new numbers, not inherited:

| Suite | Command (as run today) | Result | Elapsed |
| --- | --- | --- | --- |
| Backend | `.venv\Scripts\python.exe -m pytest -q -p no:randomly tests` | **638 passed / 2 skipped** (1 DeprecationWarning: `datetime.utcnow()` in `services/market_data/yfinance_fallback.py:51`) | 382.45s |
| Frontend (real) | `vitest run` from `frontend/` (local config, `vitest.setup.ts` resolved) | **19 files / 134 passed** | 165.73s |
| Frontend typecheck | `frontend/node_modules/.bin/tsc --noEmit` | **clean (no output)** | N/A |

Note: an earlier shell probe ran vitest against the project root config from the wrong cwd and reported `40 failed / 0 tests`; that was a miss-invocation (it scanned `frontend/node_modules/**` and could not resolve `vitest.setup.ts`), not a product regression, and is not counted. The accepted number is the in-`frontend/` run above, which matches the K5 doc's recorded `19 files / 134 passed`.

## Factual Uncertainty

- The environment is described only as Windows 11-like `win32`; the exact Windows edition and build were not supplied.
- Retained legacy reports outside the authoritative Phase 8 set contain historical versions, results, and procedures and must not be treated as current release evidence.
- Real Upstox status is unknown until a newly generated token is tested.
- Archive verification used the local audited toolchain; a different future package registry state may resolve differently unless lock files and Python constraints are retained.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### Verified in this pass

- `python3 -m py_compile` on `api/main.py`, `forecasting/conformal_calibration.py`,
  `services/setup_doctor.py`, `scripts/doctor_setup.py` - all compile.
- Offline test runner: **73 passed, 0 failed**, including the 29 new tests for calibration and the
  setup doctor.
- Aliased-import check: `217 checked / 0 missing`.
- Route presence: `/api/v1/admin/setup`, `/api/v1/admin/review`, `/api/v1/admin/calibration`,
  `/api/v1/admin/calibration/methodology` all present in `api/main.py`.
- Calibration behaviour measured, not assumed: coverage 0.834 against an 0.80 target and 0.911
  against a 0.90 target on the synthetic trending series; the random-walk estimator correctly
  reports zero skill against itself and is gated to `baseline_only`.

### Not verified (cannot be, in this environment)

- Frontend: no `npm ci`, `next build`, `tsc`, vitest or Playwright run. The Ledger token layer and
  the `/admin/setup` page are unexecuted and type-unchecked.
- Credentialed paths: Google OAuth and Upstox were never exercised against real credentials. The
  setup doctor reports configuration state; it cannot prove a present token is accepted upstream.
- Request-level API tests: `fastapi.testclient` is unavailable, so the new routes are verified by
  compilation and import only, not by an HTTP round trip.

## 2026-09-17 - merged tree re-verification (real toolchain, this machine)

Environment: Windows, Python 3.12.10 (`.venv`), Node v24.18.0 / npm 11.16.0. All commands below
were executed against the merged tree; raw output retained in the session. The 2026-09-11 rows above
that said "frontend never compiled" and "credentialed paths never exercised" are now superseded.

### Verified in this pass

| Check | Command | Result |
| --- | --- | --- |
| Backend suite, real deps (no stubs) | `.venv\Scripts\python.exe -m pytest -q -p no:randomly` | **592 passed, 2 skipped in 180s** |
| Skipped tests are credential-gated | `pytest -rs` / source review | `STOCKPILOT_LIVE_TEST_SYMBOL` model-quality gate + user-broker-token test; both require live credentials |
| Frontend type safety | `npm run typecheck` | `tsc --noEmit` exit 0 |
| Frontend unit tests | `npm test` | **95 passed / 95 (13 files)** |
| Frontend production build | `npm run build` | 21 routes built, exit 0 |
| Backend readiness | `GET http://127.0.0.1:8000/api/v1/ready` | `{"status":"ready","version":"7.0.0-rc.1"}` |
| Live quote via configured provider | `GET /api/v1/market/quote/RELIANCE` | served by Upstox (Analytics token) |
| Provider failover | Upstox disabled, chain re-evaluated | served by `yfinance` (Yahoo Finance fallback), provenance labelled |
| Google OAuth origin fix | start on `127.0.0.1:3000`, callback same origin with cookie jar | passes state validation (reaches Google code exchange) |
| Google OAuth failure mode | same cookie jar, callback on `localhost` | `oauth_error=oauth_state_invalid` (proves the host-match requirement) |

### Defects found and fixed in this pass

1. `.env` had `GOOGLE_REDIRECT_URI=http://localhost:8000/...` while the app runs at
   `127.0.0.1:3000`. Corrected (with `APP_BASE_URL` and `UPSTOX_REDIRECT_URI`) to the canonical
   loopback origin.
2. `frontend/components/OAuthButtons.tsx:30` dereferenced `status.data.google.configured`; an empty
   `{}` response crashed the component. Changed to optional chaining.
3. `frontend/tests/all-pages.test.tsx` omitted `ThemeProvider` (the real app wraps pages in it via
   `app/layout.tsx`), so 30 page-render tests failed with "useTheme must be used within
   ThemeProvider". Harness aligned with production.
4. `frontend/vitest.setup.ts` did not stub `window.matchMedia`, which jsdom lacks; added a
   deterministic non-matching stub.

### Still not verified (unchanged by this pass)

- Real Google sign-in end to end (needs the user's Google Console redirect URI + browser login).
- Upstox token lifecycle: no automatic refresh; expiry is reported, not healed.
- Playwright `npm run test:e2e`, load tests, accessibility audit, `pip-audit` / `npm audit`.
- Postgres path, Redis path, scheduler background jobs, Docker/compose (files exist; not run here).
- SEBI/legal review remains BLOCKED / NOT REVIEWED.

## 2026-09-17 - v11 upgrade verification (real toolchain, this machine)

Environment: Windows, Python 3.12.10 (`.venv`), Node v24.18.0 / npm 11.16.0, Next.js 16.3.3, recharts
3.10.1, Playwright browsers installed. All commands run against the v11 tree; raw output retained in
the session.

### Verified in this pass

| Check | Command | Result |
| --- | --- | --- |
| Backend, complete suite | `.venv\Scripts\python.exe -m pytest -q -p no:randomly` | **596 passed, 2 skipped, 1 flake (see below), 366.69s** |
| OAuth token decode diagnostics | `services/google_oauth.py` `leeway=120`, granular expiry split (`ExpiredSignatureError`/`ImmatureSignatureError` -> "identity token is expired") | 62 tests pass across `test_google_oauth`, `test_oauth_end_to_end` |
| OAuth callback error taxonomy | `api/main.py` Google callback maps 10 granular `oauth_*` codes; `frontend/app/login/page.tsx` surfaces each message | verified by reading both files + syntax check |
| Strategy-group `join` pass-through | `StrategyGroupPayload.join`; `_strategy_body.groups()` emits `{"join": "and"/"or", ...}`; frontend AND/OR select serializes per group | new `tests/test_api_strategy_body.py` 5/5 pass |
| Frontend type safety | `npm run typecheck` | exit 0 |
| Frontend unit tests | `npm test` | **109 passed / 109 (15 files)** |
| Frontend production build | `npm run build` | 23 routes built, exit 0 |
| New-pages E2E (feature-disabled state) | `npx playwright test tests/e2e/strategies.spec.ts` | 2 passed (19.0s), fresh user, OFFLINE_DEMO backend |
| Tree release hygiene | `python scripts/release_hygiene.py . --allowlisted-source` | "Release hygiene passed" |
| Bytecode decontamination | removed 15 `__pycache__` dirs; 0 `.pyc/.pyo` outside `.venv`/node_modules/.next/.git | pass |

### Important verification facts

1. Feature flags `strategy_builder`, `multi_leg_backtest`, `forward_test_tracking` default **OFF**
   (`services/forecast_guardrails.py` FEATURE_FLAGS). Both new pages implement an explicit, honest
   "turned off by an operator feature flag" surface, which is exactly what the E2E spec asserts.
2. The E2E spec registers through `page.request` (browser-context cookie jar). Using the separate
   `request` fixture made the page unauthenticated (401 instead of 503) and rendered the live
   surface - fixed by sharing the page cookie jar.
3. jsdom cannot measure layout, so recharts `ResponsiveContainer` logged `width(0)`/`height(0)`;
   `frontend/vitest.setup.ts` fails on any console output. The strategies component test mocks
   `ResponsiveContainer` to explicit 420x200 dimensions (test-only).
4. Google OAuth root cause from the earlier incident (support_id `98743da575fc`) is unrecoverable -
   registry entry cleared, no id in `backend.log`. The v11 path is strict, granular diagnostics so a
   repeat is self-explanatory; THE REAL CONSENT FLOW WAS NOT REPRODUCED HERE.

### Defects found and fixed in this pass

1. Edit-tool whitespace stripping silently de-indented code in `api/main.py` (x2) and
   `services/google_oauth.py`; caught via `python -c "import ast; ast.parse(...)"` and repaired with
   patch scripts.
2. `strategies/page.tsx` used a `Button` variant `secondary` that does not exist (only
   `ghost|default|danger`); changed to `default`.
3. `forward-tests/page.tsx` used `useSearchParams` outside `<Suspense>`, which Next dev/build flags;
   page wrapped.
4. Frontend flake: new-pages E2E initially asserted with a strict-mode-colliding locator
   (`getByText` matched the status pill + inline hint); narrowed to `[role="status"]` filtered by
   `hasText`.

### Pre-existing flake (NOT v11-related, NOT a regression)

`tests/test_live_quote_hub_streaming_fallback.py::test_stale_stream_falls_back_to_rest_polling`
fails only when run after its sibling tests in the same event loop (2.5s `wait_for` race; the unit
test performs a **real** api.upstox.com `authorize` reachability call because `.env` supplies live
Upstox tokens). Passes standalone (1.79s) and passes with `STOCKPILOT_PROVIDER_MODE=OFFLINE_DEMO`.
Not touched: fixing it would modify live market-data streaming behaviour outside v11 scope.

### Still not verified

- Real Google sign-in end to end (needs the user's Google Console redirect URI + browser login).
- Upstox token lifecycle: no automatic refresh; expiry is reported, not healed.
- Playwright across ALL existing specs, load tests, accessibility audit, `pip-audit` / `npm audit`.
- Postgres path, Redis path, scheduler background jobs, Docker/compose.
- SEBI/legal review remains BLOCKED / NOT REVIEWED.

## 2026-09-20 Complete Verified Release Build (previously noted as BLOCKED)

The 2026-09-05 decision required a complete 14-stage run and a clean extracted
archive pass. Both were produced this session by `scripts/build_release.py`
(no `--skip-*` flags). Every number below is retained command output, recorded
here after it was actually executed.

### Scope added since the 2026-09-17 entry

- Server-side **age gate** (min 15) on registration: DB migration
  (`users.date_of_birth`), backend validation, API + frontend register fields;
  `/auth/me` does not leak the DOB.
- **Feature flags** (`services/forecast_guardrails.py`) reverted to ON by
  default for all 12 flags so the shipped build matches v5/6.0 behaviour.
- **Security Center** backend (`services/security_center.py`, 10 read-only
  checks) + `GET /api/v1/admin/security` + admin frontend page at
  `/admin/security`.
- **IDOR/BOLA suite** `tests/test_idor_cross_user_access.py` (7 tests: two
  isolated users; cross-user watchlist/portfolio/paper/forecast isolation,
  body-ownership forgery ignored, forged/missing session rejection).

### Working-tree results (2026-09-20)

| Gate | Result |
| --- | --- |
| Backend `pytest -q` | `649 passed, 2 skipped` in 335.74s |
| Frontend `npm test` | 19 files / 137 tests passed |
| Frontend `npm run typecheck` | clean (tsc --noEmit) |
| Release input hygiene (`--allowlisted-source`) | passed |
| `tests/test_release_security.py` | 12 passed |

### Extracted-release results (run inside a clean extraction of the built ZIP)

The gate sequence below is executed by `build_release.py` on the staged ZIP,
in order, and the build fails closed on any failure:

| Gate | Result |
| --- | --- |
| `python -m venv` + `pip install -r requirements-dev.lock` + `pip check` | passed |
| Launcher tests (Python 3.12.10, no randomly) | passed |
| `pytest -W error -p no:randomly --ignore=tests/test_windows_launchers.py` | passed |
| `pytest -W error --randomly-seed=20260831 --ignore=tests/test_windows_launchers.py` | passed |
| `compileall api services scripts tests` | passed |
| `mypy --config-file mypy.ini .` | `Success: no issues found in 109 source files` |
| `pip_audit --local` | passed (no findings) |
| `npm ci` + `npm run test` | passed (137 unit tests) |
| `npm run typecheck` | passed |
| `npm run build` | passed |
| `npm audit --omit=dev --audit-level=high` | `found 0 vulnerabilities` |
| `npm run test:e2e` (Playwright, OFFLINE_DEMO backend) | 10 passed |
| Release JSON/hygiene/secret scans (source, staged, ZIP, extracted) | 0 findings |

### Release artifact

- `StockPilot-AI-v10-WINDOWS.zip` (346 allowlisted files)
- Size: 2,353,648 bytes
- SHA-256: `593491f62ead94eeee560b59d2a1b126b5097a6ff2e6d0d73d883ac2d14a3ebd`
- Location: `C:\Users\khush\OneDrive\Desktop\stock\StockPilot-AI-v10-WINDOWS.zip`

### Outstanding limitations (unchanged)

- Google OAuth live web flow, Upstox token auto-refresh, load/accessibility
  audits, Postgres/Redis/Docker paths, scheduler jobs, and SEBI/legal sign-off
  remain NOT VERIFIED as of this entry.
- The 2026-09-17 entry records a pre-existing `test_live_quote_hub_streaming_fallback.py`
  flake (rest-pooling race) that is unrelated to this pass.
- Four Playwright specs were stale against the DOB-required register API and
  the re-enabled feature-flag defaults; they were updated this session
  (`core.spec.ts`, `strategies.spec.ts`, `v9-surfaces.spec.ts`) to send
  `date_of_birth` and assert the active (flag-on) surfaces, and now pass.

## v12 Engineering Upgrade Verification - 2026-09-27

### Working tree

| Gate | Result |
| --- | --- |
| Backend warning-as-error suite | `775 passed, 3 skipped` |
| Windows launcher suite | `10 passed` |
| Full mypy | `Success: no issues found in 134 source files` |
| Bandit Medium-or-higher | passed with no findings |
| `pip-audit --local` | no known vulnerabilities |
| Frontend Vitest | 21 files / 155 tests passed |
| Frontend typecheck | passed |
| Next.js production build | passed; 27 routes generated |
| npm production audit | 0 vulnerabilities |
| Release input hygiene and secret scan | 399 allowlisted files / 0 findings |

### Clean extraction

- Fresh Python 3.12 virtual environment installation from
  `requirements-dev.lock` and `pip check` passed.
- Launcher tests passed: 10.
- Deterministic backend gate passed: 775 passed, 3 skipped.
- Randomized backend gate passed: 775 passed, 3 skipped with seed 20260831.
- `compileall` and strict mypy passed; mypy checked 134 source files.
- A duplicate extracted `pip-audit` request encountered a PyPI read timeout.
  The same exact locked environment passed the local audit immediately before
  the build; this is recorded as a network interruption, not as a vulnerability
  pass from the interrupted command.

### Boundaries

- The release is local/private, loopback-bound, research and paper simulation
  only. It contains no broker order execution path.
- PostgreSQL/Alembic, SQLCipher database encryption, and a durable distributed
  task queue are not shipped and are not claimed as production-ready.
- Regulatory/legal status remains BLOCKED / NOT REVIEWED. Engineering tests do
  not substitute for a qualified reviewer.
