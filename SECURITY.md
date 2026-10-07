# Security

## Security Boundary

StockPilot AI v7.0 RC is designed as a local Windows NSE/BSE research and paper-trading application. The supported launcher binds the frontend and backend to `127.0.0.1`. It is not a broker execution platform, custody system, or guarantee of market-data accuracy.

No StockPilot route should place, modify, or cancel a real broker order. All application trading records are simulated paper trades stored locally.

## Secrets

- Release files contain empty secret placeholders only.
- On Windows, backend secrets are encrypted with DPAPI for the current user in `%LOCALAPPDATA%\StockPilotAI\secrets.dpapi.json`. The ciphertext file is outside the project and cannot be decrypted by a different Windows account.
- Explicit process environment values have highest precedence, the DPAPI store is second, and root `.env` is fallback-only. Plaintext `.env` values are not encrypted and must not be used for secrets on the supported Windows path.
- `SETUP_STOCKPILOT.bat` generates independent 48-byte JWT and MFA secrets, migrates an existing plaintext JWT value when present, and clears sensitive `.env` assignments.
- `CONFIGURE_UPSTOX.bat` uses hidden prompts through `scripts/manage_secrets.py` and writes credentials to the DPAPI store without displaying them.
- Logs, screenshots, bug reports, diagnostic output, and test fixtures must not contain real tokens, passwords, API secrets, private keys, or user data.
- `scripts/scan_secrets.py` rejects populated sensitive environment assignments and recognized private-key, JWT, AWS, and GitHub token patterns without printing values.
- Secret scans include caches, logs, databases, build output, reports, bytecode, and other generated paths. VCS metadata and restored dependency trees are not release inputs and are excluded from that scanner.

If a credential may have been exposed, revoke it at the provider before investigating, remove it from local files and archives, and rotate it after the exposure path is closed.

### Credential Rotation Runbook

The general sequence for provider credentials is: **revoke first, investigate
second, rotate local configuration last**. A leaked token is only conclusively dead once revoked at
the provider, removed from files and archives, and replaced with a newly issued
value. After any rotation, restart the backend, rebuild a fresh release ZIP (see
`RELEASE_CHECKLIST.md`), re-run `scripts/verify_upstox_live.py`, and
`scripts/scan_secrets.py`.

1. **Upstox**
    1. Revoke the affected token at Upstox before changing local configuration.
   2. Revoke the pair at https://upstox.com/developer in the active sessions /
      tokens screen (analytics token) and the OAuth app settings. Name the
      sessions something identifiable (e.g. `stockpilot-old`).
   3. Generate a new long-lived analytics token in the Upstox developer
      console. Only the read-only option is required for quote/history data.
    4. Run `python scripts\manage_secrets.py rotate upstox` and enter replacements through hidden prompts.
   5. Validate with `python scripts\verify_upstox_live.py --env-file .env`:
      the report must name **Upstox** as the source for a quote, which proves the
      new token is accepted (a bare `200 OK` is not proof).
   6. If the daily OAuth access flow is used, obtain a fresh `UPSTOX_ACCESS_TOKEN`
      via `CONFIGURE_UPSTOX.bat`.
2. **Google OAuth**
   1. In Google Cloud Console, open **APIs & Services > Credentials**.
   2. Regenerate the OAuth client secret (revokes the old one automatically).
   3. If a grant was exposed (e.g. leaked refresh token), revoke it under
      **Google Account > Security > Third-party access** for the affected Google
      account.
    4. Run `python scripts\manage_secrets.py rotate google` and keep
       `GOOGLE_REDIRECT_URI` and `APP_BASE_URL` byte-for-byte unchanged.
3. **SMTP (notification mail)**
   1. For Gmail App Passwords: rotate in the Google Account security settings.
   2. For a dedicated mail service (e.g. SendGrid/PHP SMTP): rotate the password
      or API key in the provider dashboard.
    3. Run `python scripts\manage_secrets.py set SMTP_PASSWORD` and enter the replacement through the hidden prompt.
4. **Sentry**
   1. Create a new DSN in **Settings > Projects > [project] > Client Keys (DSN)**.
    2. Run `python scripts\manage_secrets.py set SENTRY_DSN`; keep it pointed at the same organisation
      and environment so error grouping is preserved.
   3. Delete the old DSN key from the Sentry console.

After completing the runbook, confirm no archive retains the old value:
`python scripts\build_release.py --output <path> --allow-env-secrets` from a
directory where `.env` holds the placeholder files (or delete the archive) and
scan the result with `scripts/scan_secrets.py`.

Rotate the JWT signing secret with `python scripts\manage_secrets.py rotate jwt`. This generates the replacement internally, never accepts it on the command line, and invalidates every existing JWT after backend restart. Keep `STOCKPILOT_MFA_SECRET` independent so JWT rotation does not make encrypted TOTP enrollment unreadable. A rotation command changes local configuration only; it cannot revoke a third-party credential at its provider.

## Authentication

- Passwords require 8 to 128 characters with upper-case, lower-case, and numeric content.
- Normal passwords use bcrypt cost 12. Inputs beyond bcrypt's 72-byte boundary or environments without bcrypt use the versioned scrypt implementation.
- Unknown-account login follows a stable expensive verification path and returns a generic error.
- Five failed attempts in a 15-minute window trigger a 15-minute lockout.
- Password-reset tokens are generated securely, stored as hashes, expire after 30 minutes, and do not reveal account existence through the request response.
- JWT sessions use HS256, fixed issuer/audience, random `jti`, server-side hashed session records, token-version revocation, and a default 12-hour lifetime.
- Opt-in RFC 6238 TOTP protects password and OAuth sign-in. TOTP secrets use authenticated encryption at rest; recovery codes are returned once, stored only as keyed hashes, and consumed atomically. Challenges are short-lived, attempt-bounded, server-tracked, and single use.
- Users can inspect coarse browser/OS session labels, revoke individual sessions, or revoke every session. Session records intentionally omit IP addresses, raw user agents, and device fingerprints.
- Outside explicit local development, `STOCKPILOT_JWT_SECRET` must contain at least 32 bytes. `OFFLINE_DEMO` also requires an explicit secret.

The setup-generated local secret is required for stable sessions. Changing it invalidates existing JWTs.

## Browser And API Controls

- Browser sessions use an HttpOnly, SameSite=Lax `stockpilot_session` cookie.
- The cookie is marked Secure on HTTPS requests or when `STOCKPILOT_COOKIE_SECURE=true`.
- Credentialed CORS accepts only explicit HTTP(S) origins. Wildcards, paths, embedded credentials, queries, and fragments are rejected.
- Cookie-authenticated POST, PUT, PATCH, and DELETE requests require an approved `Origin` header.
- Bearer-authenticated API writes do not use the browser-cookie Origin rule.
- Endpoint-scoped rate limits cover general requests and sensitive registration, login, password-reset, and forecast paths.
- Forwarded client IP headers are ignored unless `STOCKPILOT_TRUST_PROXY=true`; enable that only behind a correctly configured trusted proxy.
- Public health, system, metrics, and provider errors are sanitized to avoid returning paths, raw exception text, account counts, request path inventories, token-shaped details, or provider response bodies.
- FastAPI and Next.js set CSP/frame, MIME-sniffing, referrer, permissions, and opener policies. HSTS is enabled by the API only for HTTPS production/release requests.

Local HTTP intentionally does not set the Secure cookie attribute unless configured because the supported loopback quick start is not HTTPS. Any non-local deployment requires TLS, `STOCKPILOT_COOKIE_SECURE=true`, explicit CORS origins, a trusted reverse-proxy design, and a separate deployment security review.

## Broker Safety

`services/market_data/upstox_auth.py` blocks Upstox place, modify, cancel, GTT, and multi-order endpoint patterns. Read-only GET market-data requests remain allowed. Paper-order tests also assert that the paper engine does not invoke broker HTTP.

This control is defense in depth, not permission to supply trading-enabled credentials unnecessarily. Use least-privilege read-only credentials where the provider supports them.

The Upstox diagnostic attempts zero orders and retains only sanitized classifications and metadata. A future encoded token expiry does not establish validity. Live connectivity remains **NOT VERIFIED** until a newly generated user token succeeds against the checked read-only endpoints.

`NO_LIVE_ORDER_EXECUTION_POLICY.md` is the auditable change-control policy. Its approval record remains pending; no signature is implied. The static broker-order test fails if shipped source introduces known live-order endpoints, mutation calls, or non-paper order routes.

## Market-Data Truthfulness

- Default mode is `LIVE_ONLY`; synthetic demo data is not implicit.
- `OFFLINE_DEMO` must be selected explicitly and is visibly labelled.
- `FALLBACK_ALLOWED` must be selected explicitly and never permits the synthetic demo provider.
- Market responses carry provider, credential mode, instrument, timestamp, live/stale/fallback state, fallback reason, request ID, and mode metadata.
- Stale historical disk cache is returned only with stale and fallback context.
- Fixture success proves parser behavior only; it does not prove a live provider response.

## Local Data Protection

Runtime user data is stored in `database/stockpilot.db`. The zero-config SQLite default remains plaintext. v18 installs the `sqlcipher3` driver on Windows; setting `STOCKPILOT_DB_ENCRYPTION_KEY` or `STOCKPILOT_DB_KEY_FILE` enables SQLCipher on a new encrypted database. Encryption roundtrip, quoted passphrases, rejection by ordinary SQLite, and wrong-key rejection are execution-tested. Missing or empty configured key files fail closed. Existing plaintext databases are **not automatically converted**: back up and verify a SQLCipher export before switching files and setting a persistent key. Never lose the key or represent plaintext local data as encrypted. Windows secrets use the current-user DPAPI store described above.

Generated databases, WAL files, caches, model artifacts, logs, dependencies, build output, test reports, and local environment files must not enter the release ZIP.

Pickle and joblib formats are executable formats, not passive data. `model_runtime.load_trusted_joblib_artifact` resolves artifacts against explicit trusted roots, rejects non-allowlisted extensions, and can validate a caller-supplied SHA-256 digest against the exact bytes passed to joblib. Do not load downloaded, emailed, user-uploaded, or otherwise untrusted fitted models.

Spreadsheet applications may execute cells beginning with formula characters. Report text that can flow to tabular output is neutralized, and backend CSV generation must use `services.reports.dataframe_to_safe_csv` rather than calling `DataFrame.to_csv` directly on untrusted text.

Avoid testing against a copy of a real user database. Automated tests use temporary SQLite paths through `tests/conftest.py`.

Browser verification now launches `scripts/run_e2e_backend.py` with a disposable
encrypted account database and fresh test credentials. It refuses reuse of
already-running personal servers. `STOCKPILOT_DATABASE_PATH` selects an explicit
local database path shared by authentication and application storage; normal
startup keeps the existing default. Authentication and named row reads use the
same SQLCipher-aware driver policy, without plaintext fallback when encryption
is requested. Run migrations/backups before enabling a key on existing data.

The Account page provides an authenticated, confirmation-gated right-to-erasure flow. Deletion runs in one transaction and discovers user ownership columns across existing SQLite tables, removes indirect forward-test events and email-keyed security records, and deletes the identity last. The current retention scope and backup limitation are documented in `DATA_RETENTION_AND_DELETION.md`. This technical control is not a claim of GDPR or DPDP legal compliance.

The onboarding research disclaimer has a server-controlled version. Acceptance is persisted as a user audit event, while every terminal surface displays the research/education-only and no-recommendation notice. A qualified reviewer must still approve public-facing language before non-private use.

## Dependency And Release Hygiene

- Python dependencies are declared in requirements files and installed into `.venv` with the virtual-environment interpreter.
- Frontend dependencies are locked by `frontend/package-lock.json` and restored with `npm ci`.
- Changed release, API, and licensed-provider modules pass mypy. A 2026-09-25 full-repository run still reports 20 known typing errors in eight legacy non-API modules; it must not be represented as a passing gate.
- pip-audit found no known vulnerabilities in the local environment after the pip 26.2.1 pin.
- The npm production audit completed with 0 vulnerabilities on 2026-08-31.
- Windows Playwright passed 2 tests, including invalid live credentials producing an unavailable state with no silent demo fallback.
- Final release secret scans, allowlist inventory, clean extraction, and ZIP checks passed.
- A git pre-commit hook (`scripts/hooks/pre-commit`, installed by `scripts/install_git_hooks.py`) and a `.github/workflows/ci.yml` gate run `scripts/scan_secrets.py` and fail closed on any finding.
- `scripts/build_release.py` runs an environment secret policy check (`.env` / `frontend/.env.local` must be placeholder-only) and fails the ZIP build by default. `--ignore-local-env-policy` is the safe sanitized-build override: private environment files remain excluded and the staged tree, ZIP, and extraction are still scanned. `--allow-env-secrets` is a separate high-risk opt-in that packages only the root `.env`; never use it for a distributable artifact.
- The 2026-09-05 focused update passed 33 tests and scanned 251 allowlisted files with 0 secret findings. The updated complete Windows and clean-archive workflows remain not run; see `UPGRADE_VERIFICATION.md`.

See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) and [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

## Evidence Classification

- Verified facts: inspected controls, all 12 verification stages, actual startup/readiness, restart persistence, and the completed local evidence listed in `VERIFICATION_RESULTS.md`.
- Fixture-based checks: isolated SQLite, mocked HTTP/WebSocket/provider behavior, synthetic/seeded market frames, and jsdom component behavior.
- External checks not verified: Upstox and all other credential/network integrations, real streaming, SMTP, OAuth, Sentry, and Redis deployment behavior.
- Release work: Phase 9 clean-extraction and final archive gates completed.
- Future work: perform an external deployment review before any non-local use and verify real providers only with current user-owned credentials.

## Reporting A Security Issue

Provide the affected version, component, reproduction steps using synthetic data, expected and observed behavior, and sanitized logs. Never include credentials, `.env`, a real database, broker response bodies, or personal/account information.
