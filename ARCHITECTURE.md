# Architecture

## v18 operational continuation (2026-10-07)

- `/operations` → account directory/bulk API → existing users, MFA/session and
  admin-audit tables. Batch state/audit changes share one SQLite transaction.
- Compliance UI → allowlisted review API → existing app_settings and audit
  storage; versioned user acknowledgments remain until account erasure.
- Queue UI → sanitized operations API → existing Celery/Redis submission and
  dead-letter services; configured schedules do not imply observed worker health.
- Browser authenticator → WebAuthn registration/assertion API → credential
  signature verification → single-use pending MFA challenge → authenticated session.
- Cached forecast/comparison result → signed tier-control recheck → idempotent
  widening or abstention → public range with honest calibration wording.

## Scope

StockPilot AI v7.0 RC is a local-first Windows research and paper-trading application for NSE/BSE instruments. The supported release path is a single workstation process pair bound to loopback. Docker, Redis, and multi-worker capabilities exist in the repository but are not required by the Windows quick start.

The v7 release candidate is internally consistent: `api/main.py`, `frontend/package.json`, and `frontend/package-lock.json` report `7.0.0-rc.1`.

## Runtime Topology

```text
Browser at 127.0.0.1:3000
        |
        | REST with HttpOnly session cookie
        | WebSocket quote stream
        v
Next.js 16.3.3 / React 19.2.8 / TypeScript
        |
        v
FastAPI / Uvicorn at 127.0.0.1:8000
        |
        +-- Authentication and request middleware
        +-- Market-data orchestration and stream hub
        +-- Forecasting, indicators, risk, patterns, derivatives
        +-- Portfolio, watchlist, alerts, reports, workspaces
        +-- Paper-trading simulator
        |
        +-- SQLite: database/stockpilot.db
        +-- Local market cache: cache/market_v6
        +-- Optional Redis hot cache and stream leadership
        +-- Optional read-only external providers
```

`START_STOCKPILOT.bat` delegates to PowerShell, starts Uvicorn and the setup-built Next.js production server, waits for `/api/v1/ready` and the frontend root, monitors both processes, and terminates both process trees on exit. Ports 8000 and 3000 must be free.

## Frontend

The frontend is in `frontend/` and uses the Next.js App Router. Principal routes include:

- `/`, `/login`, and `/register`.
- `/markets/[symbol]`, `/compare`, and `/watchlist`.
- `/portfolio`, `/paper-trading`, and `/alerts`.
- `/risk`, `/derivatives`, and `/system`.

It uses TanStack Query for API state, Lightweight Charts for market charts, Recharts for analytics, React Mosaic for desktop workspaces, and TanStack Virtual for long order tables. Browser REST requests default to the same-origin Next.js proxy so host-only session cookies remain attached. WebSockets derive the backend URL using the page hostname, avoiding `localhost` versus `127.0.0.1` cookie mismatches.

Market-facing components consume provenance fields including provider, credential mode, resolved instrument, timeframe, timestamps, `is_live`, `is_stale`, `fallback_used`, fallback reason, request ID, and provider mode. Demo, fallback, stale, and unavailable states are represented separately.

## Backend

`api/main.py` is the application assembly point. It defines lifespan and middleware, keeps the four operational root/health/readiness/metrics routes and two WebSocket compatibility routes, and includes domain `APIRouter` instances. Startup validates JWT configuration, creates the SQLite schema, and optionally refreshes instruments or starts the scheduler. Shutdown stops the scheduler and live quote hub.

Shared request models, dependencies, service singletons, constants, and handler helpers live in `api/deps.py`. HTTP handlers are partitioned under `api/routers/` into auth, market, forecasting, analytics, screener, strategies/forward tests, trading, derivatives, layouts, research, system, admin, and admin-operations domains. Routers depend on `api.deps`; `api.deps` never imports routers or `api.main`, preventing application-import cycles. `api.main` intentionally re-exports the small legacy surface used by verification scripts and service checks (`app`, version/cookie constants, and selected strategy/forecast helpers).

Major API groups are:

- Operations: health, readiness, metrics, and system state.
- Authentication: register, login, logout, and current user.
- Market data: status, search, instruments, quote, history, stream health, and calendar refresh.
- Research: indicators, forecasts, saved predictions, calibration, compare, risk, strategy, and patterns.
- User data: watchlist, portfolio, reports, alerts, and workspace layouts.
- Paper trading: account, simulated orders, journal, badges, opt-in leaderboard, and historical challenges.
- Derivatives: options models, contract discovery, read-only chain data, margin calculation, expiries, and futures analysis.
- Streaming: `/ws/quotes/{symbol}` and compatibility `/ws/price/{symbol}`.

Interactive API documentation is exposed at `/docs` by FastAPI.

## Market Data

`services/market_data/manager.py` enforces the NSE/BSE catalogue and rejects unsupported global, cryptocurrency, and commodity symbols. Input `.NS` and `.BO` suffixes are normalized before exact catalogue resolution.

Available adapters are Upstox, TrueData and Global Datafeeds normalized licensed gateways, supplementary NSE quotes, explicit demo, and an India-only yfinance fallback. The configured default order is `upstox,yfinance,nse,demo`; unconfigured providers are skipped, and a licensed gateway can be placed first through `STOCKPILOT_PROVIDER_ORDER`.

Provider truth modes are:

- `LIVE_ONLY`: excludes demo and yfinance. It uses only the first usable configured provider in the ordered live/public list for each request.
- `OFFLINE_DEMO`: selects only the synthetic demo provider.
- `FALLBACK_ALLOWED`: permits the explicitly configured provider order and records fallback provenance.

Quotes use short memory/optional Redis caching. History also uses memory, optional Redis, and a validated JSON disk cache. When live history fails, a disk cache can be returned only with stale and fallback metadata. Quote failure does not silently synthesize a value.

Provider health exposes measured request, success, failure, fallback, latency, last-success age, data age, and stale-cache counters. The System page renders those runtime metrics alongside active readiness probes. Licensed gateway quotes and candles must include vendor timestamps; the REST adapters do not claim streaming support.

The live quote hub can use Upstox V3 streaming when configured, with REST polling fallback. Optional Redis leases and pub/sub coordinate stream leadership across workers. These external and multi-process paths are not proven by the current no-token release evidence.

## Forecasting And Trading Boundary

Forecasts expose ranges rather than guaranteed point outcomes. Historical and synthetic test data validate contracts, not future market accuracy.

All order entry under `/api/v1/paper/*` writes simulated state to SQLite. The paper engine does not call broker HTTP. Upstox live-order URL guards reject order place, modify, cancel, GTT, and multi-order actions. Margin and option-chain integrations are read-only research/calculation surfaces and are not order placement.

`NO_LIVE_ORDER_EXECUTION_POLICY.md` defines the change-control boundary. Any exception requires separate disabled-by-default code plus security, broker, deployment-owner, and qualified regulatory review; pending signature rows are not approvals.

## Authentication And Persistence

Local password accounts use bcrypt with cost 12 when applicable and a versioned scrypt fallback. Login lockout, password-reset token hashing, JWT session expiry, and unknown-account timing controls are implemented.

Browser authentication uses the `stockpilot_session` HttpOnly, SameSite=Lax cookie. The cookie is Secure for HTTPS requests or when `STOCKPILOT_COOKIE_SECURE=true`. Cookie-authenticated state-changing requests require an approved Origin. Bearer authentication is supported for API clients.

SQLite runs with foreign keys, WAL mode, a busy timeout, and schema creation on first use. `database/stockpilot.db` contains user-owned and simulated trading data. It is runtime state, not a release asset.

Research-only acknowledgment is versioned and recorded in the user audit log during onboarding. Authenticated users can invoke transactional account erasure from the Account page; schema-aware deletion covers legacy and lazily-created user tables rather than relying only on foreign-key cascades. See `DATA_RETENTION_AND_DELETION.md`.

## Configuration

Backend configuration precedence is explicit process environment, Windows DPAPI current-user store, then root `.env` as a plaintext fallback. Documented defaults live in `.env.example`. Frontend public endpoint configuration is loaded from `frontend/.env.local`; its empty-secret template is `frontend/.env.local.example`.

Important controls include:

- `STOCKPILOT_ENV`, `STOCKPILOT_JWT_SECRET`, and session/cookie settings.
- `STOCKPILOT_PROVIDER_MODE`, provider order, cache TTLs, and instrument refresh.
- Broker credentials and Upstox stream settings.
- Redis, scheduler, rate-limit, CORS, proxy-trust, logging, Sentry, OAuth, and SMTP settings.

No environment template contains a private credential. Windows setup generates independent JWT/MFA secrets in the DPAPI store and clears plaintext sensitive assignments. The SQLite database remains unencrypted; SQLCipher, PostgreSQL/Alembic, and distributed task-queue migrations are not shipped runtime paths.

## Verification Boundaries

- Verified facts: source/config topology, exact launcher behavior, all 12 consolidated verification stages, frontend build, actual startup/readiness, path-with-spaces first run, and backend restart persistence of one paper order and one position.
- Fixture-based checks: provider responses and failures, paper orders, security middleware, forecasting, browser components using isolated or synthetic data, and invalid-live-credential unavailable behavior without demo fallback.
- External checks not verified: real Upstox and other third-party connectivity, real broker streaming, SMTP, Google OAuth, Sentry, Redis deployment, and scheduler integrations.
- Release archive work: Phase 9 final ZIP construction, archive scans, inventory, path-safe extraction, and selected extracted-copy verification passed.
- Future work: complete real-provider diagnostics when suitable user-owned credentials and external infrastructure are available.
# v18 continuation flows — 2026-10-05

The existing forecast engine remains the implementation. Signed model decisions
use the SQLCipher-aware sidecar registry; manifest export occurs inside the
serialized decision transaction. Persistent signed tier controls are consumed by
the final primary/multi-horizon publication boundary. The cache key includes the
current promotion receipt and controls, so operational changes do not reuse an
old cached result. The decay scheduler consumes authoritative automatic outcomes,
matching artifact and nominal coverage against signed backtest evidence.

`/operations` uses separate support and model authorization dependencies. Scheduled
backup tasks snapshot the main database and registry, authenticate/restoration-test
the snapshots, then apply bounded retention. Celery-enabled submissions fail
closed when broker/worker integration is unavailable. `/metrics` exposes aggregate
Prometheus text counters; the optional monitoring Compose overlay provisions
Prometheus and a Grafana datasource. The existing OTEL instrumentation remains;
full dashboard/deployment evidence is still outstanding.
