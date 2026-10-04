# Release Checklist

## Status

**CURRENT UPDATE BLOCKED PENDING COMPLETE 14-STAGE AND CLEAN-ARCHIVE VERIFICATION**

Current update date: 2026-09-05

The 2026-08-31 candidate completed the then-current 12-stage and clean-archive workflow. Security/governance changes made on 2026-09-05 passed focused local and allowlisted-input gates, but the new complete 14-stage workflow and clean extracted archive have not been rerun. Real Upstox connectivity and legal/regulatory approval are not verified.

**2026-09-14 addendum:** a hotfix pass changed `.env.example`'s default provider mode and added a light/dark theme (see `CHANGELOG.md`). These changes were statically reviewed (full-tree `py_compile`, import/hook/brace checks) but not build-verified - `npm ci`/`tsc`/`npm run build`/`vitest`/`pytest` could not be run in that environment. This does not change the BLOCKED status above; it adds one more unverified delta on top of it.

## Release Identity

| Item | Status | Evidence or action |
| --- | --- | --- |
| Product scope is NSE/BSE research and paper trading only | VERIFIED | Code/config inspection and fixture tests |
| Documentation uses v6.1 secure-release designation | VERIFIED | Root documentation set |
| API `APP_VERSION` | VERIFIED | `api/main.py` reports `6.1.0` |
| Frontend package and lock-file versions | VERIFIED | `frontend/package.json` and `frontend/package-lock.json` report `6.1.0` |
| Next.js version is documented as 16.3.3 | VERIFIED | `frontend/package.json` and passed build |
| Python requirement is documented as exactly 3.12 | VERIFIED | Launchers and `runtime.txt` |

## Documentation

- [x] `README.md`
- [x] `QUICK_START_WINDOWS.md`
- [x] `UPSTOX_CONFIGURATION.md`
- [x] `ARCHITECTURE.md`
- [x] `SECURITY.md`
- [x] `TESTING.md`
- [x] `RELEASE_CHECKLIST.md`
- [x] `CHANGELOG.md`
- [x] `VERIFICATION_RESULTS.md`
- [x] `SECURITY_REVIEW.md`
- [x] `ADMIN_OPERATIONS.md`
- [x] `REGULATORY_REVIEW_REQUIRED.md`
- [x] `UPGRADE_VERIFICATION.md`
- [x] `REFERENCE_RECONCILIATION.md`
- [x] Verified facts, fixture checks, external unavailable checks, and future work are separated.
- [x] No document in the authoritative Phase 8 set claims implicit demo mode, Python 3.13 support, Next.js 15, unconstrained frontend installation, bundled private credentials, or an old project folder name.
- [x] Older non-Phase-8 reports are identified as historical rather than current release instructions.

## Completed Verification

The table below is the dated 2026-08-31 baseline, not a complete rerun for the 2026-09-05 update.

| Check | Status | Result |
| --- | --- | --- |
| Windows 11-like `win32` environment | VERIFIED | 2026-08-31 |
| Toolchain | VERIFIED | Python 3.12.10; Node 24.18.0; npm 11.16.0 |
| Consolidated verifier | VERIFIED | `VERIFY_STOCKPILOT.bat -SkipBrowserInstall` passed all 12 stages |
| Launcher tests | VERIFIED | 9 passed |
| Normal non-launcher backend suite | VERIFIED | 260 passed, 2 credential-gated skipped, 0 failed in 71.36s |
| Randomized non-launcher backend suite | VERIFIED | 260 passed, 2 skipped; seed 20260831; 101.66s |
| Full suite equivalent | VERIFIED | 269 passed, 2 skipped |
| Python compilation | VERIFIED | `compileall` passed |
| Mypy | VERIFIED | 76 source files passed |
| Python dependency audit | VERIFIED | No known vulnerabilities after pip 26.2.1 pin |
| Frontend unit/component suite | VERIFIED | 6 files, 47 tests passed; no unhandled React warnings |
| TypeScript | VERIFIED | Passed |
| Next.js production build | VERIFIED | Next.js 16.3.3 build passed |
| npm production audit | VERIFIED | 0 vulnerabilities |
| Windows Playwright | VERIFIED | 3 passed in 57.5s; demo banner checked on every supported route |
| First-run setup | VERIFIED | Passed in OneDrive path; `.venv` and `.env` generated safely |
| Actual Windows startup/readiness | VERIFIED | Backend/frontend started and both readiness checks passed in OneDrive path |
| Backend restart persistence | VERIFIED | One paper order and one position retained |

## Completed Archive Gates

These gates describe the 2026-08-31 archive. A new archive containing the 2026-09-05 changes remains blocked until rebuilt and verified.

- [x] Release input, staging, ZIP, and extracted-tree secret scans passed.
- [x] Staged, ZIP, and extracted-tree hygiene and exact inventory checks passed.
- [x] Final ZIP was built from `release-allowlist.txt`.
- [x] ZIP contains exactly one top-level `StockPilot-AI-v6.1-SECURE` folder and no duplicate project folder.
- [x] ZIP excludes private environments, credentials, databases, caches, logs, virtual environments, dependencies, builds, reports, bytecode, model caches, and nested archives.
- [x] ZIP was safely extracted into a fresh temporary path containing spaces.
- [x] A new extracted `.venv` was restored from the exact Python lock; deterministic/randomized backend, warning, compile, mypy, pip-audit, and launcher gates passed.
- [x] Frontend `npm ci`, tests, TypeScript, production build, production audit, and Playwright passed from the extracted copy.
- [x] Final checksum and size are emitted by the release builder and reported with delivery.

## Current Security And Governance Update

| Check | Status | Result |
| --- | --- | --- |
| Focused artifact/export/release/launcher tests | VERIFIED | 33 passed in 2.36s on Python 3.12.10 |
| Existing prediction cache integration | VERIFIED | 1 passed in 9.55s |
| Scoped Python compilation | VERIFIED | Changed modules compiled successfully |
| Scoped mypy | VERIFIED | 5 changed modules passed |
| Required-document and allowlisted-input hygiene | VERIFIED | 251 files accepted |
| Allowlisted-input secret scan | VERIFIED | 251 files, 0 findings |
| Working-tree secret scan | BLOCKED AS EXPECTED | Local `.env`, active/unscannable generated Next files, and generated fixture bytecode findings; values were not printed |
| Working-tree release hygiene | BLOCKED AS EXPECTED | Runtime/development artifacts are present and must not be packaged directly |
| Complete updated Windows verifier | NOT RUN | Script now has 14 stages; prior 12-stage evidence is historical |
| New clean archive/extraction | NOT RUN | Required before delivery of this update |
| Legal/regulatory review | BLOCKED | No qualified external approval record |

## External Checks

| Check | Status | Reason |
| --- | --- | --- |
| Real Upstox read-only connectivity | NOT VERIFIED | No newly generated user token available |
| Upstox quote/history freshness and identity | NOT VERIFIED | Requires live diagnostic evidence |
| Upstox V3 stream authorization and ticks | NOT VERIFIED | Requires live token/network evidence |
| Upstox option-chain and margin response | NOT VERIFIED | Requires endpoint-specific live evidence |
| Angel One | NOT VERIFIED | No live credential evidence |
| Google OAuth, SMTP, Sentry | NOT VERIFIED | No configured external-service evidence |
| Redis multi-worker leadership | NOT VERIFIED | No deployed multi-process infrastructure evidence |

Upstox may be configured after delivery. The required operator sequence is:

```bat
CONFIGURE_UPSTOX.bat
VERIFY_UPSTOX_LIVE.bat
```

Do not approve a live-connectivity claim from a future encoded token expiry, fixture response, public instrument download alone, or stale cache.

## Security And Privacy

- [x] Default provider mode is `LIVE_ONLY`; demo requires explicit `OFFLINE_DEMO`.
- [x] Paper-only and NSE/BSE-only boundaries are documented.
- [x] Broker live-order route blocking is fixture-verified.
- [x] JWT secret generation and minimum production length are implemented and fixture-verified.
- [x] HttpOnly/SameSite cookie, HTTPS Secure attribute, CORS, CSRF Origin, rate-limit, and redaction controls are fixture-verified.
- [x] Release examples contain empty credentials.
- [x] Final release archive secret scan passed.
- [x] Final security review disposition updated from completed archive evidence.
- [x] Executable joblib loading is constrained to resolved allowlisted roots; optional SHA-256 validation checks the exact bytes loaded.
- [x] Spreadsheet-dangerous report text and safe CSV serialization are formula-neutralized and fixture-tested.
- [x] Release hygiene rejects caches, logs, private environments, databases and sidecars, fitted models, generated reports, builds, bytecode, archives, and temporary artifacts.
- [x] Secret scanning traverses runtime/generated caches, logs, databases, builds, reports, and bytecode; dependency and VCS trees remain outside release input.

## Approval Conditions

The prior dated candidate completed its recorded local workflow. Delivery approval for the 2026-09-05 update is blocked until the complete 14-stage verifier and a new clean extracted archive pass. External providers remain NOT VERIFIED, and legal/regulatory review remains BLOCKED; neither is implied by engineering tests.

## Future Work

- Retain a sanitized Upstox live diagnostic after a user supplies a newly generated token.
- Perform a separate threat model and deployment review before exposing the service beyond loopback.
- Reassess SQLite, local secret storage, in-memory rate limits, proxy trust, TLS, and Redis/database deployment controls for any multi-user or internet-facing use.

## 2026-09-09 - v8 upgrade pass (v7.1 -> v8)

### v8 pass - required before any release

- [ ] Run the full suite with real pytest and the scientific stack installed (scipy,
      scikit-learn, joblib); the v8 evidence came from an offline shim.
- [ ] `npm ci`, then `next build`, vitest and Playwright: no frontend verification was
      possible offline.
- [ ] Manually exercise the admin step-up flow for provider priority and `bootstrap_admins`,
      including a wrong password and an expired token.
- [ ] Call `GET /api/v1/brief`, the `/api/v1/screener/*` routes and
      `POST /api/v1/options/payoff` against a running API; they were never invoked.
- [ ] Confirm `REGULATORY_REVIEW_REQUIRED.md` still blocks live order placement and that
      `tests/test_broker_order_mutation_unreachable.py` passes.
- [ ] Fill in the newly documented `.env.example` keys this deployment needs.

## 2026-09-09 - v9 upgrade pass (v8 -> v9)

**This build is NOT approved for release.**

| Gate | State |
| --- | --- |
| Real pytest run with full dependencies | NOT RUN (no network in build sandbox) |
| `npm ci` / lint / `tsc --noEmit` / `next build` | NOT RUN |
| Vitest component tests | NOT RUN |
| Playwright end-to-end tests | NOT RUN |
| Request-level tests for the eleven new routes | NOT WRITTEN |
| SEBI regulatory review | **BLOCKED / NOT REVIEWED** |
| Offline unit checks for F1-F3 | PASSED (48/48) |

The offline harness result is evidence that the new modules behave as specified
in a dependency-free environment. It is not a substitute for any gate above.
