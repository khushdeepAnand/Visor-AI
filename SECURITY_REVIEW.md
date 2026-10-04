# Security Review

## 2026-09-05 Addendum

Artifact loading is now constrained to resolved allowlisted roots with optional SHA-256 validation of the exact bytes loaded. Spreadsheet-dangerous report text is neutralized, and a safe CSV serializer is fixture-tested. Release hygiene and secret scanning now explicitly gate required governance documents and runtime/generated locations.

Focused result: 33 tests passed in 2.36s; the existing prediction cache integration test passed in 9.55s; scoped compilation passed; scoped mypy passed 5 modules; 251 allowlisted release files passed hygiene and secret scanning with 0 findings.

The broad working-tree scans failed closed on expected local private/generated state. The updated 14-stage Windows workflow and a new clean archive have not been run. Therefore the changed candidate is **BLOCKED FOR DELIVERY** pending those gates. External providers remain NOT VERIFIED, and legal/regulatory review remains BLOCKED. This addendum makes no legal approval, penetration-test, or live-provider claim.

## Review Summary

- Product: StockPilot AI v6.1 secure release candidate.
- Review date: 2026-08-31.
- Scope: documentation review plus inspection of current application configuration, Windows launchers, authentication, middleware, market-data orchestration, Upstox diagnostics/order guard, release scanner, tests, and frontend package metadata.
- Change scope: application safety, cache serialization, provider truth, diagnostics, launchers, release tooling, tests, and documentation.
- Intended deployment: local Windows workstation, loopback frontend/backend, NSE/BSE research and paper trading only.
- Disposition: **APPROVED FOR THE DOCUMENTED LOCAL WINDOWS MODEL**. Local, archive, and clean-extraction gates passed.

This was not a penetration test or external broker certification. The final release ZIP, local Python environment, and frontend production dependencies were reviewed by automated gates.

## Findings

### SR-01: Final Archive Evidence

- Severity: Medium assurance gap, not a demonstrated vulnerability.
- Status: Closed.
- Evidence: allowlist staging, path-safe ZIP inventory, secret/hygiene scans, extraction to a fresh path containing spaces, and extracted-copy verification passed.
- Residual risk: future changes require rebuilding through the same gated workflow.

### SR-02: Real Provider Connectivity Is Unverified

- Severity: Informational assurance boundary.
- Status: NOT VERIFIED.
- Evidence: no newly generated user Upstox token was available.
- Risk: token permissions, current endpoint compatibility, freshness, stream authorization, and broker-side behavior are unknown.
- Required action: after the user generates a token, run `CONFIGURE_UPSTOX.bat` followed by `VERIFY_UPSTOX_LIVE.bat`; retain only reviewed, sanitized evidence. Do not treat encoded expiry as validity.

### SR-03: Local Secrets And User Data Are Not Encrypted By The Application

- Severity: Low for the intended single-user local model; higher if the folder is shared or synchronized insecurely.
- Status: Accepted local-operational risk pending user controls.
- Evidence: credentials are stored in local `.env`; user and paper-trading data are stored in `database/stockpilot.db`.
- Risk: another process or account with filesystem access, an unsafe backup, or an unsafe synchronization policy can read these files.
- Required action: rely on Windows account isolation, device encryption, careful OneDrive/backup policy, and release exclusion. Reassess secret storage and database encryption before broader deployment.

### SR-04: Production Frontend Launcher

- Severity: Low in the documented loopback-only model.
- Status: Closed for local release.
- Evidence: setup executes the production build and `start_stockpilot.ps1` executes `npm run start` on `127.0.0.1:3000`.
- Residual risk: the app remains local-only and requires a separate review before remote exposure.

## Positive Control Observations

### Release And Secret Hygiene

- `.gitignore` and `.dockerignore` exclude local environments, credentials, databases, caches, dependencies, builds, logs, reports, and archives.
- Environment examples contain empty private values.
- Setup creates local environment files and generates a 48-byte JWT secret rather than bundling one.
- `SETUP_STOCKPILOT.bat` passed in the OneDrive path and safely generated `.venv` and `.env`.
- Upstox configuration uses secure prompts and does not echo secrets.
- The release scanner recognizes populated sensitive environment assignments and several credential formats without printing values.

### Authentication And Browser Boundary

- JWT configuration fails closed outside explicit local development unless the secret contains at least 32 bytes.
- Passwords use bcrypt cost 12 with a versioned scrypt fallback where needed.
- Login lockout, generic unknown-account errors, reset-token hashing/expiry, and stable dummy verification are implemented.
- Session cookies are HttpOnly and SameSite=Lax; Secure is enabled on HTTPS or by configuration.
- Credentialed CORS requires explicit origins and rejects wildcards and malformed origins.
- Cookie-authenticated state-changing methods require an approved Origin.
- Endpoint-scoped rate limits and optional trusted-proxy behavior are implemented.

### Broker And Data Truth Boundary

- Upstox order place, modify, cancel, GTT, and multi-order endpoint patterns fail closed.
- Paper trading writes simulated state and is fixture-tested not to call broker HTTP.
- `LIVE_ONLY` is the default; synthetic demo data is available only in explicit `OFFLINE_DEMO` mode.
- Redis and disk market-history caches use validated JSON rather than executable pickle payloads.
- Open paper orders are atomically claimed before fill processing.
- Symbol validation is restricted to the NSE/BSE catalogue.
- Market responses include provenance and stale/fallback indicators.
- Provider failures and operational endpoints redact raw exception, path, response-body, and account-count details.
- The Upstox verifier records zero orders attempted and emits sanitized classifications.

## Verification Evidence

### Verified Local Results

- Consolidated `VERIFY_STOCKPILOT.bat -SkipBrowserInstall`: all 12 stages passed.
- Launcher tests: 9 passed.
- Normal backend: 260 passed, 2 credential-gated skipped, 0 failed in 71.36s.
- Randomized backend: 260 passed, 2 skipped with seed 20260831 in 101.66s.
- Full suite including launchers: 269 passed, 2 skipped.
- `compileall` passed; mypy passed 76 source files.
- pip-audit found no known vulnerabilities after the pip 26.2.1 pin.
- Frontend: 6 files, 47 tests passed with no unhandled React warnings; TypeScript passed.
- Next.js 16.3.3 production build passed; npm production audit reported 0 vulnerabilities.
- Windows Playwright: 3 tests passed in 57.5s, including every-route demo-banner coverage.
- Actual Windows startup and both readiness checks passed in the OneDrive path.
- Backend restart persistence retained one paper order and one position.

### Fixture-Based Security Evidence

- JWT configuration and cookie attributes.
- Explicit CORS and CSRF Origin behavior.
- Endpoint rate limits and untrusted forwarding behavior.
- Provider, health, system, and metrics redaction.
- First-run schema creation in temporary SQLite.
- Upstox order-route blocking and paper/broker separation.
- Secret-scanner fixture detection.
- Token-expiry metadata is not represented as verification.
- Sanitized Upstox retry and failure classification.
- Playwright explicit `OFFLINE_DEMO` persistent labelling and mocked invalid-live-credential unavailable behavior with no demo fallback.

These tests validate application controls under controlled inputs. They do not prove an external service or final archive.

### External Checks Not Verified

- Real Upstox instruments, quotes, history, option chain, margin, and streaming.
- Other market providers, Google OAuth, SMTP, Sentry, Redis deployment, and scheduler integrations.
- Internet-facing TLS, reverse proxy, multi-user, and multi-instance operation.
- Independent penetration testing or broker security certification.

### Phase 9 Security Evidence

- Final allowlisted source, staging, ZIP, and extracted-tree scans passed.
- Exact inventory and unsafe/colliding archive-path checks passed.
- Fresh extraction created its own `.venv` from the exact Python lock; deterministic/randomized backend, warnings, compile, typing, audits, frontend, and Playwright passed.

## Deployment Conditions

For the current candidate:

- Keep both services on loopback.
- Use only NSE/BSE instruments.
- Use paper trading only.
- Never include `.env`, `frontend/.env.local`, databases, caches, logs, dependencies, builds, reports, or credentials in the release ZIP.
- Treat provider, stale, fallback, and demo labels as security-relevant truth indicators.
- Do not claim live Upstox operation without newly generated token evidence.

Before any remote or multi-user deployment, require TLS, Secure cookies, explicit deployed origins, trusted-proxy validation, managed secret storage, durable database design, distributed rate-limit/state review, access logging/privacy review, dependency audits, and penetration testing.

## Historical 2026-08-31 Disposition

No known critical or high-severity issue remains in the documented local release scope. This statement remains limited by the absence of penetration testing and live external checks.

The dated technical security disposition covered the documented local loopback deployment model. It was not legal/regulatory approval. SR-02 remains an explicit external boundary and SR-03 remains an accepted local-operational risk; the changed candidate is governed by the addendum above.

## Future Work

- Capture a sanitized Upstox diagnostic after a newly generated user token is available.
- Conduct a new security review for any architecture that exposes StockPilot beyond a single local workstation.
