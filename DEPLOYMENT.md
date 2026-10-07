# StockPilot AI — Deployment Guide

**Current live/default storage remains SQLite.** The future Postgres/Supabase
readiness changes below do not switch the app, migrate data or make a database
server a startup/test/build prerequisite. Existing Postgres deployment examples
are opt-in; the shared repository path still has the gaps recorded below.

## Future: switching to Supabase (prepare only; do not switch yet)

Treat Supabase as hosted PostgreSQL for this preparation. Keep `DB_BACKEND` unset
(or `sqlite`); the existing `STOCKPILOT_DB_TYPE` default is also still `sqlite`.
No environment variables need to be added to a fresh/local installation.

When a later, separately validated cutover explicitly selects Postgres, configure
these optional **backend-only** variables through the existing secret manager:

| Variable | Future purpose | Optional fallback |
| --- | --- | --- |
| `DB_BACKEND=postgresql` | Opt-in DAO selector alias; `STOCKPILOT_DB_TYPE=postgresql` remains supported | Unset defaults to existing SQLite selection |
| `DATABASE_URL` | Pooled PostgreSQL connection string for application DAO traffic | `STOCKPILOT_DATABASE_URL`, then `DATABASE_MIGRATION_URL` |
| `DATABASE_MIGRATION_URL` | Direct PostgreSQL connection string for Alembic only | `DATABASE_URL`, then legacy `STOCKPILOT_DATABASE_URL` |
| `STOCKPILOT_DATABASE_URL` | Existing single-URL configuration, retained for compatibility | Existing SQLite fallback if no Postgres path is selected |

Use the project dashboard's actual PostgreSQL URLs with the appropriate TLS
settings (`sslmode=require` at minimum, and certificate validation where supported).
If only one URL is configured, both selected-Postgres paths can use it as before;
for Supabase, deliberately supply both pooled and direct URLs. Supabase's pooled
connections, especially transaction-pooler mode, do not support every session/DDL
operation needed by Alembic; migrations must use the direct endpoint. Obtain a
direct endpoint reachable by the migration runner (including its IPv4/IPv6 needs).
An explicit Postgres argument to `upgrade_to_head(url)` overrides environment
selection for that deliberate migration invocation. No such invocation was run.

`DATABASE_URL` and `DATABASE_MIGRATION_URL` alone do **not** select Postgres.
`DB_BACKEND=sqlite` also prevents legacy URL auto-selection in the DAO factory.
For backward compatibility only, an existing `STOCKPILOT_DATABASE_URL` beginning
with `postgresql://` still auto-selects the legacy DAO path when `DB_BACKEND` is
unset. Keep that legacy URL absent on SQLite-only installations as today.
Supplying the future selector does not convert the whole application: many auth,
admin, forecast and scheduler paths still call SQLite directly.

Supabase's `service_role` key is **not a PostgreSQL password** and is not needed
by this connection-only preparation. No new key variable or SDK is introduced.
If a later server-side Supabase integration needs that key, provision it in the
backend secret store using the existing `scripts/manage_secrets.py`/DPAPI or
deployment secret-manager pattern. Never put it in `frontend/.env.local`, a
`NEXT_PUBLIC_*` variable, frontend code, an archive or a browser request.

RLS policies, Supabase Auth, Realtime and Supabase-specific hardening are separate
future work once an actual instance is live and testable. SQLite/Postgres
dual-write/sync, data transfer and the live switch are also separate tasks.

### Non-connecting DAO/migration review — 2026-10-07

Read side by side: `services/db/sqlite_impl.py`, `postgres_impl.py`, `base.py`,
`database.py` persistence helpers, `alembic/env.py` and the only current revision
`alembic/versions/8788046ff051_initial_postgresql_schema.py`.

**No public DAO method names are missing:** database wrapper, eight DAO classes
and factory all have matching public signatures. There is nevertheless partial
implementation and behavioral divergence; none of these were silently repaired:

| Gap found by reading | Evidence and later acceptance needed |
| --- | --- |
| Partial range-forecast persistence | `PostgresPredictionDAO.save_range_forecast` stores only user/symbol/JSON/status, labels forecast status `pending`, and omits bounds, provenance, immutable hashes and outcome status produced by `database.save_range_forecast`. Not equivalent to SQLite. |
| History/result shape | Postgres history returns `linear_prediction`/`decision_tree_prediction`/`random_forest_prediction`/`prediction_date`; SQLite maps to `linear`/`dt`/`rf`/`date`. Postgres details return raw JSON columns; SQLite decodes them to `payload`, `forecast_evidence`, `outcome_evidence`. Postgres also omits SQLite's strip/limit normalization. |
| Official-outcome protection | Postgres settled query checks only `outcome_status='settled'`; SQLite requires automatic, official, non-demo, non-stale, publishable outcomes and actual/coverage/score evidence. Result fields/order/limit bounds differ as well. |
| Legacy SQLite prediction adapter defects | SQLite `save_prediction` reads `kwargs['symbol']` instead of its explicit symbol; both SQLite prediction save wrappers query `last_insert_rowid()` on a different connection from the helper's insert. These pre-existing SQLite behaviors are intentionally untouched. Postgres payload adaptation/serialization also needs live validation. |
| MFA/recovery semantics | SQLite secret replacement resets the row via `INSERT OR REPLACE`; Postgres upsert preserves fields such as prior TOTP counter/creation time. Postgres ignores duplicate recovery-code inserts; SQLite raises a uniqueness error. |
| Paper-account duplicate behavior | Postgres ignores duplicate creation and returns the newly requested balances even if a different stored account exists; SQLite duplicate insertion raises. |
| Connection/concurrency lifecycle | `PostgresDatabase` retains a shared `_conn` despite a threaded pool, leaves cursors open and has no pool shutdown method. Sale operations lack a row lock; watchlist check-then-insert can race. No concurrency or transaction proof is implied. |
| Runtime schema/adoption | The initial 29-table revision lacks newer runtime-created tables (e.g. passkeys/login anomalies/settings/guardrails/strategies); numerous routes still bypass the DAO. Completing repository/schema adoption is required before cutover. |
| Migration runner legacy behavior | The runner returns `unknown` on execution failure and `head` rather than an inspected revision. Default SQLite Alembic behavior is not repaired here; SQLite startup uses `database.create_tables()`. Explicit Postgres configuration now reaches Alembic rather than being silently ignored. |

The revision's executable DDL is PostgreSQL-standard SQLAlchemy table/index DDL
and PostgreSQL trigger/function syntax (`LANGUAGE plpgsql`, `IS DISTINCT FROM`,
`EXECUTE FUNCTION`, `DROP TRIGGER ... ON ...`). No SQLite `PRAGMA`, `AUTOINCREMENT`
keyword, `INSERT OR REPLACE`, `datetime('now')`, `last_insert_rowid()` or
`RAISE(ABORT)` is present in executable migration SQL (a comparison comment names
SQLite's trigger equivalent). The revision itself is byte-for-byte unchanged.
This is a read-only syntax review, **not live migration acceptance**. Several
string-column `server_default='CURRENT_TIMESTAMP'` values are quoted literals,
not timestamp expressions, and JSON defaults are overquoted strings; those
semantic/schema gaps remain flagged for a live-tested follow-up.

### Standing readiness checks

`python scripts/verify_backend_parity.py` inspects public method/property names,
parameter kinds/names/defaults/annotations and returns without constructing a
DAO or connecting to either database. Missing/inherited abstract methods or
signature drift fail CI. Backend constructors/private helpers and concrete
connection/cursor return types allowed by the shared interface differ by design.
Passing this gate proves API shape only, not SQL behavior or cutover readiness.

`tests/test_supabase_readiness.py` pins the unchanged SQLite implementation bytes
(both exact LF/CRLF checkouts permitted by Git)
and compares serialized result bytes, executed SQL and entire SQLite database
bytes for unset selectors versus future URLs/explicit SQLite. The full existing
suite must also pass with `DB_BACKEND` unset. No real Postgres/Supabase server is
contacted by these readiness checks.

StockPilot v13 is a multi-service application:

| Service    | Port | Purpose                                        |
|------------|------|------------------------------------------------|
| FastAPI    | 8000 | Backend API, websockets, scheduler             |
| Next.js    | 3000 | Terminal UI                                    |
| PostgreSQL | –    | Primary durable store (production)             |
| SQLite     | –    | Bundled store (local/demo, optional SQLCipher) |
| Redis      | 6379 | Cache, Celery broker, rate-limit/DLQ state     |
| Celery worker/beat | – | Async forecast/settlement/retention tasks |
| backup     | –    | Nightly `pg_dump` to `./backups` (14-day keep) |

Uvicorn loads `api.main:app`. Domain routes live in `api/routers/` and share
dependencies through `api/deps.py`.

## Quick start (local, SQLite)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                 # fallback only; inject secrets from a secret manager
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd frontend && npm install
cp .env.local.example .env.local
npm run build && npm start
```

## Production (Docker Compose + PostgreSQL)

1. Create `.env` from `.env.example` and set at minimum:

   ```env
   STOCKPILOT_JWT_SECRET=<32+ random chars>
   POSTGRES_PASSWORD=<strong random password>
   STOCKPILOT_DB_TYPE=postgresql
   STOCKPILOT_TASK_QUEUE_ENABLED=true
   STOCKPILOT_CORS_ORIGINS=https://app.example.com
   STOCKPILOT_COOKIE_SECURE=true
   ```

2. Start everything (Postgres → migrations → API → workers → UI):

   ```bash
   docker compose up --build -d
   docker compose logs -f api       # watch migration + boot
   ```

3. Verify:

   ```bash
   curl -fsS http://localhost:8000/api/v1/health
   curl -fsS http://localhost:8000/api/v1/ready
   ```

### Migrations

The API container runs `scripts/run_migrations.py` on boot (Alembic `upgrade
head`). To run manually:

```bash
docker compose exec api python scripts/run_migrations.py
# or against an explicit DSN
STOCKPILOT_DATABASE_URL=postgresql://... python scripts/run_migrations.py
```

Roll back one revision:

```bash
docker compose exec api alembic -c alembic.ini downgrade -1
```

> SQLite-only deployments keep using `database.py:create_tables()`; the Alembic
> migration mirrors that schema for PostgreSQL and is additive.

### Backups

The `backup` service runs `pg_dump -Fc` daily into `./backups/` and deletes
dumps older than 14 days. Restore:

```bash
docker compose stop api worker beat
docker compose exec -T postgres pg_restore -U stockpilot -d stockpilot \
  < backups/stockpilot-YYYYMMDD-HHMMSS.dump
docker compose up -d
```

For real SLAs, also schedule off-host backups (S3/Blob) of `./backups`.

## TLS

Terminate TLS at a reverse proxy in front of the compose stack.

Caddy (automatic Let's Encrypt):

```
app.example.com {
    reverse_proxy localhost:3000
}
api.example.com {
    reverse_proxy localhost:8000
}
```

Or with nginx/certbot. Inside the compose network everything speaks HTTP.
When running uvicorn directly on a host you can also pass
`--ssl-certfile/--ssl-keyfile`.

## Secrets

Never commit `.env`. Supported secret paths:

1. **Container orchestrator secrets** (Docker secrets / K8s secrets) mounted
   at `/run/secrets` — the compose file mounts `./secrets` read-only there.
2. **Windows DPAPI store** (local dev): `SETUP_STOCKPILOT.bat` creates
   `%LOCALAPPDATA%\StockPilotAI\secrets.dpapi.json`.

   ```bat
   .venv\Scripts\python.exe scripts\manage_secrets.py status
   .venv\Scripts\python.exe scripts\manage_secrets.py set SMTP_PASSWORD
   .venv\Scripts\python.exe scripts\manage_secrets.py rotate jwt
   ```

3. **`.env` fallback** (plaintext, local-only).

Required secrets: `STOCKPILOT_JWT_SECRET`, `POSTGRES_PASSWORD`,
`STOCKPILOT_DB_ENCRYPTION_KEY` (SQLCipher), broker/OAuth/SMTP credentials.

## Database encryption (SQLCipher, SQLite mode)

When running with the bundled SQLite file instead of PostgreSQL:

```bash
export STOCKPILOT_DB_ENCRYPTION_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
python -c "from database import encrypt_database, verify_encryption; \
           print(encrypt_database('$STOCKPILOT_DB_ENCRYPTION_KEY')); \
           print(verify_encryption())"
```

`pysqlcipher3` must be installed (see `requirements.txt`). Startup fails fast
with a clear error if an encryption key is set but the driver is missing.

## Task queue (Celery + Redis)

```bash
# inside compose (workers run automatically):
docker compose up -d worker beat

# manual worker
celery -A services.task_queue.celery_app worker -Q forecast,default -l info
```

* Retries use exponential backoff (`STOCKPILOT_TASK_MAX_RETRIES`,
  `STOCKPILOT_TASK_RETRY_BACKOFF`).
* Exhausted tasks are written to the Redis dead-letter list
  (`stockpilot:dlq`) — inspect/replay via `services.task_queue.get_dead_letters()`
  and `replay_dead_letter(id)`.
* Without Redis/Celery the app transparently executes tasks inline so local
  dev and CI need no broker.

## Observability (OpenTelemetry)

Set `STOCKPILOT_OTEL_ENABLED=true` plus the standard
`OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_SERVICE_NAME` variables to emit traces
for API → forecast → data-provider spans.

### OpenTelemetry configuration (production)

```env
STOCKPILOT_OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.example.com:4318/v1/traces
OTEL_SERVICE_NAME=stockpilot-api
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,service.version=16.0.0
```

The Python SDK is configured in `services/telemetry.py`. Traces are emitted for:
- FastAPI request/response spans (auto-instrumented)
- Forecast job execution (`stockpilot.forecast` tracer)
- Market data provider calls (`stockpilot.provider` / `stockpilot.market_data` tracers)
- Challenger reconciliation (`stockpilot.reconciliation` tracer)
- Authentication flows (`stockpilot.auth` tracer)

### Collector options

| Backend | Exporter | Notes |
|---------|----------|-------|
| Grafana Tempo | `otlphttp` | `OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo.example.com:4318/v1/traces` |
| Honeycomb | `otlphttp` | Requires `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=<key>` |
| Datadog | `datadog` | Use `ddtrace` instead of native OTel for native Datadog support |
| Jaeger | `otlphttp` | `http://jaeger:4318/v1/traces` |

### Local development

Use the in-memory exporter (no external collector):

```bash
export STOCKPILOT_OTEL_ENABLED=true
export STOCKPILOT_OTEL_EXPORTER=memory
uvicorn api.main:app --port 8000
```

The memory exporter is intended for test suites only (`tests/test_telemetry.py`).
It cannot be scraped; use a real OTLP endpoint for production observability.

### Span sampling

Default: always-on for error spans, probabilistic for success (1/1000). Override:

```env
OTEL_TRACES_SAMPLER=parentbased_always_on
OTEL_TRACES_SAMPLER_ARG=0.1   # 10% of traces
```

### Verifying traces

```bash
# Quick check that spans are emitted
STOCKPILOT_OTEL_ENABLED=true STOCKPILOT_OTEL_EXPORTER=console \
uvicorn api.main:app --port 8000
```

## Real market data

Provider chain configured through `STOCKPILOT_PROVIDER_ORDER`. Adapters exist
for Upstox, normalized TrueData/Global Datafeeds gateways, NSE public quotes,
demo data, and yfinance fallback. Credentials are backend-only configuration.

```env
STOCKPILOT_PROVIDER_ORDER=upstox,yfinance,nse,demo
UPSTOX_ACCESS_TOKEN=
```

## Health checks

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `GET /api/v1/system`
- `GET /api/v1/metrics`
- `GET /api/v1/scorecard` (public forecast scorecard)

## Verification before deployment

```bash
python scripts/verify_project.py
pytest -q                      # full backend suite
cd frontend
npm test                       # unit/component
npm run build
npm run test:e2e               # Playwright auth/MFA/session lifecycle
```

Live provider/model gates only after supplying credentials:

```bash
STOCKPILOT_LIVE_TEST_SYMBOL=RELIANCE pytest -m live -q
```

## Scaling notes

* API containers are stateless when `STOCKPILOT_DB_TYPE=postgresql`;
  SQLite mode confines you to a single instance.
* Redis handles cache, rate-limits and the Celery broker.
* The websocket fan-out coalesces subscriptions per API process; for
  multi-instance deployments add a Redis pub/sub fan-out layer.
* Connection pool size: `STOCKPILOT_DB_POOL_SIZE` (default 10).

## Compliance gate

Before public or multi-user deployment, complete the pending signatures in
`NO_LIVE_ORDER_EXECUTION_POLICY.md`, obtain the qualified review tracked by
`REGULATORY_REVIEW_REQUIRED.md`, and adopt a legally approved retention
schedule per `DATA_RETENTION_AND_DELETION.md`. These documents do not
constitute legal approval.
