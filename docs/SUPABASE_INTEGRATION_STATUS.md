# Supabase integration — verified checkpoint, 2026-10-10

**Partial implementation. No production migration/cutover; PostgreSQL application
startup is not yet accepted.** This report does not declare the 12-phase request
complete. SQLite stays the permanent default/local store. One selected writable
application backend, custom JWT/MFA/WebAuthn/session/CSRF auth, and no dual-write
remain the target contract.

## A. Inspection and architectural findings

The existing DAO factory was not the runtime application's database boundary.
`api/deps.py`, domain services, routers, authentication and background jobs
primarily called `database.get_connection()`/`authentication.get_connection()`.
Those opened SQLite regardless of DAO backend. The original migration has 28
tables, not the older assessment's assumed 29. `settings` was already present;
14 lazy tables were missing. Shared canonical forecast writes already existed
in the user's working tree and were preserved/extended.

The synchronous driver is psycopg2. Actual migration execution exposed SQLAlchemy
2.1's psycopg default; explicit psycopg2 selection fixed it. No asyncpg/prepared
statement cache is used. PostgreSQL transactions now apply UTC locally, not as
a pooler-dependent session setting.

### Direct SQLite path classification

| Path/group | Classification and current action |
|---|---|
| `database.py` main database opening | Application data; PG selection refuses local main-file access until remaining adoption is complete. Core portfolio/watchlist/transactions/prediction/audit helpers now have explicit PG DAO branches. |
| `authentication.py:get_connection` | Application auth data; `_open_connection(DATABASE_PATH)` must be adopted. Current runtime blocker. |
| `services/auth_api.py`, `webauthn.py`, `login_anomaly.py` | Application sessions/MFA/passkeys/security events; still pending adoption. |
| `services/admin_registry.py`, `admin_step_up.py`, `compliance.py`, `security_center.py` | Shared application/admin/settings/security data; pending adoption, not legitimate retained side DBs. |
| `services/paper_trading.py`, `paper_trading_v6.py`, `historical_replay.py`, `alerts.py` | Shared simulated trading, journal, alerts/challenges; pending service-level transactions/locks. DAO purchase/sale is tested, not full order-engine concurrency. |
| `services/chart_layouts.py`, `screener.py`, `strategy_builder.py`, `forward_test.py` | User-owned saved content/strategies/results; pending routing. Their real tables/columns are now in Alembic. |
| `services/outcome_settlement.py`, `retention.py`, `scorecard.py`, `task_queue.py`, `api/scheduler.py` | Shared forecast outcomes/audit/maintenance jobs; remaining helpers/jobs still pending. |
| `services/sentiment_snapshot.py`, `sector_rotation.py`, `search_engine.py` | Use the main SQLite connection for shared observations/catalogue; do not silently reclassify them as local-only. Pending routing/scoping. |
| `api/routers/admin.py`, `forecasting.py`, `layouts.py` | Inline owned application queries; pending abstraction adoption. Other routers call the services/helpers above. |
| `services/db/sqlite_impl.py` | Legitimate selected SQLite DAO implementation, retained permanently. |
| `services/encrypted_storage.py` | Legitimate local consistent SQLCipher/SQLite backups, encrypted exports/restore checks; retained. WAL snapshot restoration was fixed and exercised. |
| `forecasting/promotion_store.py` | Explicit local signed/encrypted `.registry.db` sidecar: `model_decisions`, `model_controls`, `model_control_events`; retained independently of backend. This does not make shared user forecast provenance local. |
| `scripts/migrate_legacy_database.py`, encryption/transfer utilities | Explicit offline source/backup tooling, not a second live writable app backend. |
| SQLite connection calls under `tests/` | Disposable fixtures/tests; retained. Live parity additionally uses real disposable PostgreSQL. |

### Explicitly retained local files

- `%LOCALAPPDATA%\StockPilotAI\secrets.dpapi.json`: current-user DPAPI encrypted
  **JSON**, not an SQLite database. URL/password keys were added to its allowlist.
- Manifest-adjacent `<manifest-stem>.registry.db`, from
  `forecasting/promotion_store.py:registry_path`: signed append-only model
  decisions/controls, optionally SQLCipher-encrypted. Actual path follows the
  configured promotion manifest; no registry file was found in the workspace
  database scan. Provisioning/consistency across cloud instances is not proven.
- `cache/market_v6` and model-refresh/provider cache files: local JSON/file caches,
  not authoritative user portfolios/orders/outcomes.
- Authenticated encrypted local backups/export/restore snapshots managed by
  `services/encrypted_storage.py`; plaintext backup rehearsals stay in memory.
- Optional research-only MLflow SQLite tracking configured by its URI, with
  `local-history/mlflow-artifacts`; not production forecast/auth persistence.
- Disposable SQLite fixtures and demonstration databases. The application's
  main `database/stockpilot.db` is permanent in local mode, but its production
  application data is the future migration source, not a cloud-mode side DB.

## B. Changes in this integration checkpoint

| File | Change |
|---|---|
| `services/db/postgres_impl.py` | Thread-local pooled leases, cursor cleanup/error rollback/pool shutdown; UTC transaction settings; ownership-safe row-locked sales; conflict-safe watchlist insertion; MFA replacement/recovery duplicate semantics; real duplicate paper-account errors; decoded/normalized history; official-outcome filtering; atomic holding+BUY ledger operation. |
| `services/db/sqlite_impl.py`, `base.py` | Existing SQLite path preserved; explicit atomic purchase contract, transactional/validated sale, owning database property. Existing canonical snapshot work retained. |
| `services/db/factory.py` | URL auto-selection removed; reusable scoped DAO sessions with explicit release and shutdown. |
| `services/db/configuration.py` | Explicit selector, direct-endpoint rejection for migrations, explicit SQLAlchemy psycopg2 dialect. |
| `database.py` | Main-file PG fallback refusal and initial core helper DAO adoption. Existing encryption/snapshot work preserved. Remaining raw paths still block completion. |
| `alembic/env.py`, `scripts/run_migrations.py` | Correct driver, disposed migration engine, inspected revision, propagated failures with sanitized CLI result. |
| `alembic/versions/20261010_01_dao_defaults.py` | Forward-only future timestamp/JSON defaults and upper-symbol uniqueness. No existing row deletion or default-value rewriting. |
| `alembic/versions/20261010_02_runtime_tables.py` | 14 audited lazy tables plus required constraints; RLS and denied Data API table grants on the 42 known app tables. |
| `scripts/verify_backend_parity.py` | Existing static check retained; opt-in real backend/schema/ownership/provenance/eligibility/duplicate/concurrency/RLS/atomic purchase/rollback/transfer-retry probes. |
| `scripts/migrate_sqlite_to_postgres.py` | Read-only inventory and separately approved, backup-restored, empty-target transactional import/reconciliation; no cutover/sync. |
| `services/encrypted_storage.py` | Closed SQLite snapshot handles and normalized standalone WAL snapshot header according to SQLite deserialize requirements. |
| `services/secure_secrets.py`, `scripts/manage_secrets.py`, `.env.example` | Existing backend URL/password secret-store support; explicit SQLite default, direct/pooled variables and verified encrypted-backup migration of plaintext fallback credentials. |
| `.github/workflows/ci.yml` | Added real disposable PostgreSQL parity job while retaining existing gates. |
| `tests/test_supabase_readiness.py` | Selector, no-fallback, migration endpoint/failure semantics; historical URL-auto-selection expectation updated for the user-mandated explicit selector. |
| Two forecast/streaming test modules | Isolated optional market feeds and broker credentials so unit tests do not contact workstation-configured live providers; assertions retained. |
| `tests/test_evaluation_harness.py` | Local deterministic RNG for a statistical uniformity test; original thresholds retained. |
| `DEPLOYMENT.md`, this report, source inventory | Current Windows/configuration/security/migration/recovery guidance and explicit verification boundaries. |

Pre-existing uncommitted device-confirmation/encryption/research/UI work was
present before this task. The user authorized including reviewed changes in
eventual publication. It must not be represented as newly implemented by this
integration checkpoint or silently discarded.

## C/D. Schema and security evidence

Head is `20261010_02`; clean schema and all 42 runtime table/column sets matched
in a real PostgreSQL 16.4 disposable instance bound to loopback port 55432.
The original initial migration remains unchanged. The new revisions are
forward-only; recovery uses a separately restored verified backup.

Real probes denied a nonprivileged role by table grants. A deliberately granted
SELECT still returned zero user rows under policy-free RLS. The tested owner
role was confirmed superuser/BYPASSRLS; it is **not protected by RLS**. Actual
Supabase role/network/grant inspection remains unverified pending locally
provisioned connection URLs. Application ownership checks are still needed.
Existing auth was not replaced with Supabase Auth. Local auth/IDOR/passkey
regressions passed; their PostgreSQL runtime equivalents are not yet proven.

## E. Source and migration state

Observed workspace database scan found `database/stockpilot.db`; configuration
can override it with `STOCKPILOT_DATABASE_PATH`. Its read-only inventory passed
integrity and reported zero FK violations/unmapped tables. All actual per-table
counts/column names are in `docs/supabase-source-inventory.json`, without values.
This is the candidate source, not approval to migrate every existing account.
Externally configured files still require an operator inventory.

The real transfer rehearsal used **only generated disposable fixture data**.
Authenticated encrypted snapshot restore, full imported-content reconciliation,
serial-counter repair and matching retry passed against fresh PostgreSQL.
The actual source was not copied to PostgreSQL; production approval is still
required. An actual-source encrypted backup and authenticated restore rehearsal
also completed locally; its new backup secret was persisted in current-user
DPAPI without exposing the value. Snapshot, target backups, data-go/no-go and traffic-cutover decisions
are separate. WAL snapshot recovery is proven for the disposable rehearsal,
not a deployed Supabase backup/PITR/restore drill.

## F. Commands actually completed

- `python scripts/verify_backend_parity.py --live` with a loopback disposable
  `DATABASE_MIGRATION_URL`: passed signature check plus clean migration, 42-table
  comparison, eight-DAO behavior, core runtime helper routing, RLS/grant denial,
  eight-thread sale/watchlist/recovery, rollback and transfer/retry probes.
- `python -m pytest -q -W error -p no:randomly` on database/readiness/snapshot/
  encrypted-storage/backups/secrets/evaluation modules: **42 passed**.
- Auth/phase4-security/cross-user-IDOR/v11-IDOR/device/passkey focused modules
  with empty live broker tokens and demo-only provider order: **69 passed**.
- Forecast execution/stream fallback modules after dependency isolation:
  **11 passed**.
- `python -m mypy --config-file mypy.ini .`: **198 files passed**.
- `python scripts/check_dangling_refs.py`: passed.
- `python scripts/check_required_docs.py`: **22/22 passed**.
- `npm run verify`: **194 tests passed**, API generation/honesty/typecheck/build
  passed, production dependency audit found zero vulnerabilities.
- Focused existing research/quality/encryption/interval/paper/scorecard modules:
  **45 passed / 1 credential-dependent skipped**.
- Bandit Medium/High gate: passed (pre-existing nosec-comment diagnostics remain).
- PostgreSQL offline Alembic SQL generation: passed through head, **48,195 bytes**.
- Allowlisted-source secret scan: zero findings; `git diff --check`: passed.
- User-approved `python scripts/manage_secrets.py migrate-env --env-file .env`:
  encrypted fallback backup read-back verified; existing effective DPAPI values
  preserved; private environment policy now **zero findings**. No private file
  or backup is added to Git or the archive.
- Temporary candidate release build: **583 files**, secret/inventory/reference
  gates, extracted `import main`, and extracted SQLite smoke **4 passed**.
  Candidate is `C:\Users\khush\AppData\Local\Temp\opencode\StockPilot-Supabase-checkpoint.zip`.
  The user's final Windows ZIP is intentionally not replaced while PostgreSQL
  application smoke and remaining adoption are incomplete.

Full backend attempts were interrupted/timed out before final summaries; they
are **not a full-suite pass**. An unseeded statistical test failure and tests
contacting optional live feeds were investigated and their fixture randomness/
dependencies made explicit. No SQLite-only/static test is claimed as Supabase
correctness. Whole-working-tree secret scanning timed out because generated/
runtime artifacts are also scanned; the complete allowlisted source and sanitized
candidate archive scans did pass. Full backend, browser, both-backend extracted
application smoke and final ZIP publication remain incomplete.

## Checkpoint publication

Implementation checkpoint `826f222` was committed and successfully pushed to
`origin/feature/v18-comprehensive-updates` in `khushdeepAnand/Visor-AI`, including
the reviewed existing changes the owner authorized. This is a staged integration
checkpoint, not completion of the full request. The final supplied
`StockPilot-AI-v18-WINDOWS.zip` is held pending the remaining PostgreSQL runtime
and release gates; the temporary sanitized candidate passed SQLite extraction
checks. Subsequent documentation commits record these observed delivery facts.

## G/H. Windows continuation and release blockers

Use the hidden-input DPAPI commands in DEPLOYMENT.md to provision pooled and
direct URLs; keep `DB_BACKEND=sqlite`. Provisioning credentials is not data
migration/cutover approval. The live parity utility deliberately rejects remote
databases, so Supabase-specific read-only validation must be separate.

Remaining: auth/admin/layout/screen/strategy/forward-test/trading/outcome/
retention/scheduler routing and transaction contracts; end-to-end PG auth and
paper-order concurrency; deployed least-privilege role/grants validation;
load/exhaustion behavior; source/target relationship/timestamp/JSON review on
real approved data; Supabase backup/recovery rehearsal; Windows/Docker cloud
startup; complete backend/browser and extracted PG smoke; release ZIP and Git
publication. No production-ready or completed-migration claim is made.
