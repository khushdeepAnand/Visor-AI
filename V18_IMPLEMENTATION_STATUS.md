# StockPilot AI v18 RC — implementation and verification status

## Supabase integration checkpoint — 2026-10-10

This request is **not yet complete**. `docs/SUPABASE_INTEGRATION_STATUS.md`
records the real local PostgreSQL DAO/schema/RLS/concurrency/transfer evidence,
initial runtime helper adoption, read-only actual-source inventory, Windows
instructions and remaining blockers. SQLite remains permanent/default. URLs
alone cannot select PostgreSQL; incomplete PG runtime access refuses the main
SQLite file rather than silently falling back. There has been no production
data transfer or traffic cutover. Keep application startup on SQLite pending
remaining auth/trading/job adoption and both-backend release verification.

## v20 → v21 continuation — 2026-10-09

The requested Windows archive name remains v18. This continuation is **partial**;
it does not assert completion of every standing item. Detailed current evidence
and limitations are in `docs/V21_CONTINUATION_STATUS.md`.

- A row-level history manifest now inventories all 18 cache files, explicitly
  tags demo/unknown/unreviewed rows, checks corporate actions through the existing
  forecasting module and exports 476 unique completed real index sessions.
- Real next-day T2 evaluation executed: 20 paired test outcomes per index; neither
  GBM nor HAR passes promotion. No statistically supported win is claimed and
  no signed production receipt or production routing change was made.
- Pandera price validation runs in the provider pipeline and evaluator. MLflow
  stores actual research estimators; all 24 local registered versions reloaded
  and produced finite smoke predictions, without deployment aliases.
- Canonical forecast snapshot values are shared by SQLite/Postgres writers;
  SQLite writes use the owning connection and return the correct inserted IDs.
  A real disposable SQLite load preserved 400 writes with 16 threads. Full
  repository adoption, live Postgres execution and production-shape load remain open.
- Login MFA/recovery/passkey success can confirm possession of a random HttpOnly
  device cookie. Alert acknowledgment cannot confer trust. Strict-mode detection
  failures fail closed. Field-bound AES-GCM helpers are tested, but application-wide
  PII persistence/query migration and field-key rotation are **not complete**.
- The legacy database-encryption entry point now reuses the existing
  preservation-verified exporter instead of duplicating unsafe table-copy logic;
  encrypted rollback, quoted schema/passphrase preservation and wrong-key
  rejection were executed against disposable real databases.
- External penetration testing is **not booked**. A concrete engagement request
  exists in `docs/EXTERNAL_PEN_TEST_ENGAGEMENT.md`; vendor/contact, budget and
  staging URL are required from the owner. Docker CLI remains unavailable.

Executed: final strict full backend **1,146 passed / 3 skipped**, including
merged-cache continuity and encryption compatibility regressions. An additional
index/issuer identity guard is covered by the final focused manifest run.
Frontend verification **194 tests**, schema/honesty/type/build/audit passed;
production-browser suite **20 passed**, including actual virtual passkey sign-in.
Whole-project mypy **195 source files** passed; Bandit Medium/High,
dependency audit, document/reference and non-connecting parity gates passed.
The detailed status records the exact verification/delivery scope.

The prepare-only SQLite source-byte freeze is explicitly superseded by this
user-authorized adoption work; its URL-selection result/SQL/database-byte parity
checks remain, supplemented by canonical snapshot and bound-connection tests.

## Resumed Supabase-readiness verification — 2026-10-07

This resumption completes the supplied **prepare-only Supabase-readiness prompt**,
not the broader v18 master prompt. SQLite remains the default. The interrupted
URL split, non-connecting parity script/CI gate and deployment audit survived;
the source-byte regression now accepts both exact Git LF/CRLF representations.
The SQLite implementation and PostgreSQL migration revision were not edited in
this resumption. Existing encrypted-DAO changes predate this readiness checkpoint.

Executed against the resumed source:

- `DB_BACKEND` unset: full strict backend suite **1,110 passed / 3 skipped** in
  666.81 seconds. Skips: optional SHAP and two credential-dependent live checks.
- Readiness regression **12 passed**, including identical SQLite result bytes,
  SQL traces and serialized database bytes despite future pooled/direct URLs.
- Non-connecting DAO signature parity, required documents **22/22**, and dangling
  references/import inventory passed. Method names/signatures match; partial
  Postgres forecast persistence and behavioral/schema gaps are in `DEPLOYMENT.md`.
- Frontend `npm run verify`: **192 tests passed**, generated client/honesty checks,
  typecheck and production build passed; production and full npm audits: zero
  known vulnerabilities. Python dependency audit: zero known vulnerabilities.
- Mypy: **185 source files** passed. Bandit Medium/High gate passed. Critical
  mutation controls passed and **3/3 intentional mutants were killed**.
- Default development browser run: **19 passed / 1 failed** (passkey registration
  did not navigate). Production build on isolated ports 8011/3011 with two workers,
  matching the prior Windows verification setup: **20/20 passed**, including real
  virtual passkey enrollment and login. Development-mode intermittency is still
  recorded; no app behavior was changed to hide it.

No live PostgreSQL/Supabase connection or migration was executed. The existing
revision uses PostgreSQL-standard syntax, but flagged default-value/schema and
DAO semantic gaps still require live-tested follow-up before any cutover.

Delivery keeps exactly the refreshed `StockPilot-AI-v18-WINDOWS.zip` and unchanged
older `StockPilot-AI-v18-CONTINUATION.zip` in the parent folder. The latest user
request authorizes commit/push after local checks and release extraction checks,
superseding the historical publication holds below.

## Latest executed continuation — 2026-10-07

This section supersedes historical totals/archive instructions below. The master
prompt is **not fully complete**. This continuation closes specific gaps:

- Directory uses actual TOTP/passkey storage; search, MFA, account age and
  unacknowledged-login filters are exposed in `/operations`.
- Single/bulk account actions share transactional session revocation, suspension,
  reinstatement, lockout/MFA reset and per-account audit. Explicit nonblank reason
  and existing single-use admin step-up are required; a failed batch rolls back.
- Persistent regulatory checklist and version-specific acknowledgment directory
  are exposed in `/operations`. Support is read-only; model operators have no
  account/compliance access. Acknowledgments survive ordinary audit retention.
- Sanitized queue console supports allowlisted manual triggers and failed-job
  replay with step-up/justification. Celery replay restores args/kwargs correctly.
- Travel detection compares account history across networks; stale devices are
  flagged again, IPv6 prefixes canonicalized and arbitrary forwarded IPs ignored.
- Passkey-only accounts require MFA. Cookies reach both factor routes; browser
  enrollment/assertion and logout/login are verified with a virtual authenticator.
- Logout clears the observed session result and in-flight private queries, fixing
  immediate redirect back into a signed-in workspace.
- Cached primary/comparison publication rechecks signed controls. Widening is
  idempotent; adjusted coverage remains explicitly unverified, with low confidence
  and stale derived zones/fan data withheld.
- Browser tests have separate ports/build output. Production-browser execution
  avoids observed OneDrive dev-manifest locks. Source-map dependency patched.

Consolidated evidence: strict backend **1,098 passed / 3 skipped** (optional
SHAP/live-market evidence); frontend **192 unit tests**, API/honesty gates,
typecheck/build passed; full production browser suite **20 passed**, two workers,
disposable encrypted backend on ports 8011/3011. Mypy **183 files** passed,
doc/import/reference gates passed, critical mutations **3/3 killed** with passing
controls, Bandit Medium/High gate passed. Production npm check: zero findings.

Still outstanding: full shared PostgreSQL/SQLite/async repository adoption,
complete multi-user Redis caching and durable forecast/retraining integration,
complete PII/broker encryption and confirmed-device anomaly enforcement, all
forecast canary controls, full ML registry/data-quality/drift/monitoring and
Storybook integrations, broad mutation/full browser accessibility acceptance.
Real held-out superiority/options/event evaluation, production-shape load/restart
campaigns, hosted CI and independent penetration/regulatory review remain
unverified. Local/synthetic test success cannot establish these requirements.

Delivery: keep only newest `StockPilot-AI-v18-WINDOWS.zip` and unchanged old
`StockPilot-AI-v18-CONTINUATION.zip` in the parent folder. Delete surplus REPAIRED
archives only after replacement extraction checks pass. No commit or push until
the user has personally run and audited the app.

## Latest continuation — 2026-10-05

This section supersedes the older handoff below. The complete master prompt
remains **unfinished**. The Windows archives contain the updated local source;
they are not a claim that every master-prompt acceptance condition is complete.

Implemented in this continuation, with failing regression checks run first:

- Production policy now validates every numeric policy field, enforces aggregate
  sample minimums without double-counting an `overall` summary, fixes the coverage
  target, and enforces requested MASE/interval-score improvement floors.
- Revoked artifacts cannot be restored by rollback. Registry transactions
  serialize manifest exports across processes; a failed export rolls back the
  decision and preserves the previous active receipt. A crash between filesystem
  export and database commit still fails closed; independent recovery/key rotation
  and external signed-head anchoring remain open.
- Sustained live-decay enforcement reads the actual automatic-settlement ledger,
  uses signed tier backtest coverage and matching artifact/nominal metadata, and
  widens after three disjoint 20-outcome windows below a ten-percentage-point
  tolerance. Repeated evaluation of the same IDs cannot advance the counter.
  Widening and per-tier operator pauses are persistent, signed and audited.
  Primary and multi-horizon publication share the enforcement boundary. Control
  changes invalidate forecast cache keys. Pauses publish abstention, not zero bounds.
- Added configured `support-ops` and `model-ops` roles. Stored role and current
  allowlist must agree. Model operators cannot access the account directory or
  general admin mutations; support operators cannot modify models. The
  `/operations` screen exposes verified history, rollback, tier pause/resume and
  a read-only account directory with search/MFA filtering. Model mutations require
  justification. Existing admin step-up controls are preserved.
- Encrypted scheduled backups include the main database and promotion/control
  sidecar. Every snapshot runs a hash/integrity restore drill; plaintext SQLite
  drills restore only in memory. Retention runs only after successful drills.
  APScheduler and Celery beat have backup entries. Queue-enabled deployments refuse
  inline fallback when Celery/broker submission is unavailable.
- Reused the existing command palette, adding keyboard focus trapping, a close
  button and complete destination results. Added an accessible Motion/Framer Motion
  uncertainty fan using only published horizon bounds, respecting reduced motion.
  Diagnostic scores and cross-horizon agreement are no longer labeled calibrated
  confidence. A standing lexical forecast-honesty gate supplements rendering tests.
- Added aggregate Prometheus text exposition and a Compose monitoring overlay with
  Prometheus/Grafana datasource provisioning. These configuration files have not
  been executed here because Docker is unavailable. They do not complete OTEL/drift
  dashboards or production infrastructure acceptance.

### Current executed checks

- Broad strict backend run: 1,082 passed, 3 skipped, with one reviewed API-surface
  snapshot mismatch. After reviewing the six new routes and updating the snapshot,
  63 focused contract/admin/role/decay/metrics tests passed. A final clean full run
  is still required before claiming consolidated acceptance.
- Frontend verify: generated client in sync, forecast-honesty gate, 191 unit tests,
  TypeScript and production build passed. Production dependency audit: zero findings.
- Mypy: 183 application files passed. Required documents and dangling references
  passed. Critical mutations: 3/3 killed with passing unmutated controls.
- Python installed-environment vulnerability audit: no known findings. Bandit
  Medium/High gate passed with existing annotation-parsing notices.
- Light/dark theme token contrast audit passed all declared pairings. The new fan
  passes its component axe audit. This is not a full browser accessibility audit.
- Browser suite: 18 passed, one session-loading failure under parallel load. The
  isolated failing lifecycle test then passed. Consolidated browser rerun pending.

### Still open, including implementation work

Full shared PostgreSQL/SQLite repository adoption and async pooling, Redis coverage
for all multi-user caches, comprehensive durable forecasting/retraining and queue
operations, complete PII/broker field encryption and confirmed login anomalies,
bulk account actions/filtering, feature-flag/compliance consoles, full monitoring,
data-quality/ML registry integrations, Storybook/Chromatic, load/restart campaigns,
broad mutmut, and full browser accessibility/mobile acceptance are not completed
by these changes. Real per-tier market superiority/options/event benchmarks,
external penetration testing and hosted CI require external evidence/access.

Docker availability was checked directly: `docker` is not installed on this host.
No commit, push, merge, publication or PR was performed.

Delivery selection: refresh `StockPilot-AI-v18-WINDOWS.zip` and
`StockPilot-AI-v18-WINDOWS-REPAIRED.zip`; retain the original
`StockPilot-AI-v18-CONTINUATION.zip` as the old checkpoint. Remove only the surplus
legacy ZIP after validating replacements. Preserve the active source directory.

Date: 2026-10-04. Based on the latest `StockPilot-AI-v16` working copy and
`C:\Users\khush\Downloads\StockPilot-AI_v18_MASTER_Prompt.md`.

**This is a tested foundation/security release, not completion of the entire
master prompt. No bug-free, production-ready or statistically superior forecast
claim is implied.** Earlier verification documents contain historical claims;
this report supersedes them for this release.

## Changes implemented

- Windows dependency installation repaired using the prebuilt `sqlcipher3`
  driver. SQLCipher connections validate the actual cipher, safely handle quoted
  passphrases and reject unavailable/empty configured key files. Roundtrip,
  unreadability by normal SQLite and wrong-key rejection have executable tests.
- Static AST-based validation of project-local absolute and relative Python
  imports now joins the permanent dangling-reference gate. Optional external
  imports are left to dependency/runtime checks. It catches missing package files
  without executing application modules.
- Every release build executes the required-doc and dangling-reference gates,
  safely extracts its ZIP, imports `main`, and runs actual smoke tests in the
  extracted tree even when full-suite flags are skipped. A failed check removes
  the new ZIP.
- E2E configuration now explicitly supplies test CORS, HTTP-cookie settings and
  a random test signing key. It starts the API/browser servers in CI too.
- Public scorecard no longer queries a nonexistent `settled_forecasts` table.
  It reads authoritative automatic settlements, excludes stale/demo/manual
  evidence, uses the most recent ledger window and recorded tier metadata,
  preserves zero-valued metrics, reflects saved nominal coverage, and reports
  promotion only when a real active promotion receipt supports it.
- Missing baseline intervals or Diebold-Mariano evidence now block promotion.
  The evaluation harness computes baseline interval scores from aligned actual
  baseline bounds rather than claiming missing evidence is a pass.
- Live-decay diagnostics honor supplied tolerances/minimum evidence and do not
  call thin evidence healthy or claim widening was executed by a diagnostic.
- SQLite/Postgres DAO updates validate identifiers against shared allowlists
  before executing SQL; hostile identifier tests cover both backends.
- Celery uses late acknowledgment and worker-loss rejection, has daily beat
  entries for retention/instrument refresh, records exhausted retries, and keeps
  dead letters when resubmission fails. Scheduler artifacts stay in the project.
- PyJWT and urllib3 upgraded to audited versions and lock pins refreshed.
- CI dependency/secret/security jobs now run daily. Extracted release integrity,
  E2E and targeted auth/IDOR/forecast mutation checks are standing CI steps.
- Container build installs SQLCipher build libraries; API launch no longer masks
  a migration runner process failure.

## Verification

### Continuation execution — 2026-10-04

- Strict full suite (`pytest tests/ -q -W error -p no:randomly`): **1,026 passed,
  3 skipped**, no warnings, 355 seconds.
- Repository-wide mypy: **181 application source files passed**. Historical
  local backups are excluded from active source checks.
- Import validation now checks named local exports as well as package/module
  paths. A missing symbol fails the gate without executing imported code.
- Nonconvergent ARIMA fits are explicitly unavailable instead of emitting a
  publishable result or hiding numerical warnings.
- SQLCipher migration verifies each table's row content and retains an encrypted
  rollback copy. DPAPI bootstrap provisions independent DB, field, backup and
  audit keys; existing plaintext databases are exported before activation.
- Authenticated database backups snapshot consistently; plaintext snapshots stay
  in memory. Restore rejects wrong keys, corrupt data and existing destinations.
  Migration/backup/restore checks pass against actual databases.
- Broad mutmut campaign and staging Compose overlay added. Their deployment/
  runner outcomes remain pending until actually executed.
- GitHub repository: https://github.com/khushdeepAnand/Visor-AI. Initial repository
  is empty; publication uses the sanitized release inventory only.

- Baseline backend run: **1,005 passed, 3 skipped**, 22 model convergence/startup
  warnings, 406 seconds. Skips require optional SHAP/live provider evidence.
- Frontend: TypeScript and production build passed; **185 unit tests passed**.
- Browser suite after fixing CORS/test authentication: **17 passed**.
- Python installed-environment audit: **no known vulnerabilities** after updating
  PyJWT/urllib3. Frontend production audit: **0 vulnerabilities**.
- New security/integrity/scorecard/decay regression checks are execution-tested.
- Final full backend run from the renamed v18 folder: **1,021 passed, 3 skipped**,
  22 model warnings, 438 seconds. The stale v7 smoke assertion was corrected.
- Targeted mutation campaign: **3/3 mutants killed** by assertion failures;
  each corresponding unmutated test passed first. Mutations cover password
  verification bypass, cross-user portfolio leakage and coverage-gate bypass.
- Bandit Medium/High gate passed after repairing identifier validation and
  documenting reviewed parameterized-query false positives. Low findings remain.
- Sanitized release: **504 files**. The extracted archive passed the static
  reference gate, `import main` and **4 actual smoke tests** in a clean temporary
  directory containing spaces. Broader extracted frontend/backend verification
  is deliberately not claimed.

## Master-prompt items still outstanding

### Current local continuation

The active working folder is `StockPilot-AI-v18`, on
`feature/v18-comprehensive-updates`. No continuation changes have been committed
or pushed. Existing implemented features are retained; the master prompt remains
**in progress**, not complete.

New regression tests were executed and failed before fixing:

- Authentication ignored SQLCipher, and encrypted DAO/history reads used the
  incompatible stdlib SQLite row factory. Connections and row factories now use
  the actual driver. Registration, login, duplicate rejection, forecast-history
  reads and pending-ledger reads are exercised against real encrypted storage.
- Browser verification used personal account storage and could reuse running
  servers. It now uses disposable encrypted databases and fresh credentials,
  and refuses server reuse. This does not erase accounts created by older runs.
- The import gate did not recognize a whole package lost to flattening and
  accepted function-local definitions as module exports. Expected package roots
  now come from the release inventory; installed Alembic remains external to its
  migration workspace.
- Failed extracted verification deleted the previous release ZIP. Candidate ZIPs
  now replace the previous archive only after mandatory extraction checks pass.
- Promotion accepted negative/infinite scores, invalid conditional coverage,
  empty summaries and significant DM results in the wrong direction. Those
  inputs now fail closed; the CLI carries the DM statistic through to the gate.
- A nonconverged base member could still contribute to the forecast. It is now
  excluded before evaluating the exact surviving blend with its baseline.

The Tailwind 3 development chain and undici had audit findings. The CSS toolchain
is now Tailwind 4.3.3, using the existing theme configuration. Light/dark utility
styles have browser regression coverage. Full npm audits are mandatory in CI.
The previously added GitPython runtime requirement was removed: that finding
was from an unrelated globally installed Streamlit dependency, not this app.

Executed evidence before final continuation checks: full strict backend run
**1,031 passed / 2 skipped**; frontend **185 unit tests passed**, typecheck/build
passed; upgraded browser suite **18 passed**, including theme checks;
full npm audit **zero vulnerabilities**.

Final consolidated local verification of these code changes:

- Backend, warnings treated as errors: **1,049 passed, 2 skipped**, 401 seconds.
- Browser tests on a fresh encrypted database: **18 passed**, with six workers,
  including the CSS migration checks. No base-member convergence warning was
  emitted in this final browser run.
- Frontend unit tests: **185 passed**; generated API client, typecheck and
  production build passed after the dependency migration.
- Mypy: **182 application source files passed**.
- Targeted mutation checks: **3/3 killed**, with unmutated controls passing.
  This does not replace the unexecuted broad mutmut campaign.
- Python installed-environment audit and full npm audit: **zero known findings**.
- Bandit Medium/High gate, dangling references, required docs and diff whitespace
  checks passed. Bandit emits existing annotation-parsing warnings.

Release rebuilding uses mandatory inventory/secret checks, safe clean extraction,
`import main` and four extracted smoke tests. Full extracted environment installs,
frontend/backend suites, staging and hosted CI are not represented as completed.

Docker is unavailable on this workstation, so production-shape staging,
restart-durability and load evidence are still outstanding. Hosted CI on these
local changes awaits later publication authorization. Real per-tier forecast
superiority requires held-out market observations; synthetic tests cannot supply
that evidence. Independent penetration testing requires an external engagement.

### Signed registry handoff checkpoint

The newer `forecasting/promotion_store.py` and `model_promotion.py` changes add
signed manifests, a chained append-only decision registry, candidate rollback
without evidence-date renewal, future-date rejection and audited CLI revocation.
Focused security/promotion tests passed **17 tests**; mypy passed **183 files**.
The full suite and release
acceptance for this addition are not implied by the earlier checkpoint numbers.
Read `CONTINUATION_PROMPT.md` for exact files, remaining integration work,
verification status and the external WORM-anchor limitation.

| Section | Remaining acceptance work |
| --- | --- |
| 1 — foundation | Full-repository mypy and strict warning-as-error local tests pass. An actual GitHub runner, the broad mutmut campaign and production-shape staging still require executed evidence. |
| 2 — prediction | Real per-tier out-of-sample regime/IPO superiority, equal-coverage options benchmark, event-aware widening, tamper-resistant promotion/demotion audit, sustained live tier auto-reaction and forecast-rendering honesty lint remain incomplete. A synthetic unit test is not market evidence. |
| 3 — UX | Existing calm/pro surfaces and mobile flows are tested. Complete guided defaults, command palette, animated fan chart, measured contrast/axe CI audit and standardized Framer Motion adoption still need implementation/validation. |
| 4 — admin | Existing admin controls/flags/guardrails remain; complete support-ops/model-ops separation and the requested model, users, provider, queue and living compliance consoles are not complete. |
| 5 — security | Persistent DPAPI key provisioning and preservation-verified migration/rollback are implemented and tested. Local activation and complete PII field encryption/anomaly confirmation still need validation; independent penetration testing requires an external reviewer. |
| 6 — infra | DAO/Postgres/Alembic/Redis/Celery code exists, but the application still directly uses SQLite in many paths. Do not claim full Postgres support, async repository adoption, concurrency/load validation, restart durability or encrypted backup/restore drills without real infrastructure evidence. |
| 7 — tooling | Full MLflow, Pandera/Great Expectations, Evidently, Grafana/Prometheus, Semgrep/Gitleaks/Trivy, Storybook/Chromatic, Framer Motion and Locust/k6 adoption remains open. Existing dependency declarations/configuration do not prove running integrations. |

No live broker order execution has been added. Research/education disclosures,
CQR control-arm boundaries and point-in-time requirements remain mandatory.

## Local files and archives

The active project is `StockPilot-AI-v18`. Requested archive refresh targets in
the parent `stock` folder are `StockPilot-AI-v18-WINDOWS.zip` and the legacy-named
`StockPilot-AI-v16-WINDOWS-REPAIRED.zip`. The additional
`StockPilot-AI-v18-WINDOWS-REPAIRED.zip` created during the prior continuation is
also refreshed. All three targets package the current **v18** folder; the v16
filename is retained only to match the user's existing archive target and is
not a claim that the content remains v16. These are sanitized work-in-progress
snapshots, not completion of the master prompt. Private configurations, keys and
user databases remain excluded. Failed verification preserves the previous ZIP.
