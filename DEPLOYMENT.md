# StockPilot AI — Deployment Guide

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
