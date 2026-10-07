# StockPilot AI v18 — continuation handoff

## Current delivery instruction — 2026-10-07

The resumed user request now authorizes committing and pushing verified work to
the existing GitHub feature branch after checks and archive refresh. This
supersedes the historical publication holds below. Scope of this resumption is
Supabase readiness only; SQLite remains the default, with no live cutover.

Read the newest executed continuation in `V18_IMPLEMENTATION_STATUS.md` for code
and consolidated evidence. The complete master prompt still has implementation
work outstanding. New code covers bulk accounts, compliance, queue operations,
travel detection, real passkey browser integration, logout and final cached
forecast-publication enforcement. Continue from this source, preserving prior work.

The newest user instruction supersedes ALL older archive instructions here:
keep exactly two parent-folder ZIPs: newest `StockPilot-AI-v18-WINDOWS.zip` and
unchanged old `StockPilot-AI-v18-CONTINUATION.zip`. Delete surplus REPAIRED ZIPs
only after replacement verification. Preserve the active source directory.
Publish only after the resumed request's verification and archive checks pass.

## Newest local continuation (2026-10-05)

Read the **Latest continuation** section of `V18_IMPLEMENTATION_STATUS.md` first.
The source now includes production policy hardening, transactional registry
exports, revoked-artifact rejection, signed persistent tier controls, shared
publication enforcement/cache invalidation, least-privilege operations routes and
UI, scheduled encrypted backup restore drills, durable-queue fail-closed behavior,
uncertainty fan/palette fixes, honesty lint and Prometheus exposition.

The older remaining-scope list below is still relevant where not explicitly
superseded. Several implementation requirements remain open, not merely external
deployment gates. Do not label the master prompt complete.

Use the current working tree or refreshed WINDOWS archives for these additions.
The original CONTINUATION ZIP is deliberately retained as the **old** checkpoint
under the user's newest request. The parent folder should have exactly three ZIPs:
two refreshed v18 WINDOWS archives and that old CONTINUATION archive. Do not
refresh the legacy v16 filename or delete the active source directory.

Continue implementation, not just planning or auditing. Use the supplied
`StockPilot-AI_v18_MASTER_Prompt.md` as the complete acceptance specification.
Skip requirements only when the code and execution evidence prove completion.
Write failing regression tests before changing behavior. Keep responses concise
(Caveman mode if available), but write normal English in code and documents.

## Workspace and publication rules

- Edit ONLY the active project: `C:\Users\khush\OneDrive\Desktop\stock\StockPilot-AI-v18`.
- Git branch: `feature/v18-comprehensive-updates`. Origin is
  `https://github.com/khushdeepAnand/Visor-AI.git`.
- Many tracked modifications and untracked new tests/modules are intentional.
  Inspect `git status` and `git diff`. Preserve all existing work.
- Do not commit, push, merge, or create a PR. The user explicitly wants local
  completion and review before publication, including before any feature-branch push.
- Shell is Windows PowerShell 5.1. Use tool working directories and quoted paths;
  do not use Bash-only `&&`, `head`, `tail`, or `ls -la`.
- If using the handoff ZIP on another machine, it contains source, not `.git`
  history. Prefer continuing in the original repository when available. Do not
  replace the local edited tree with a remote clone or an older ZIP.

## Read first

1. This handoff and the full supplied master prompt.
2. `V18_IMPLEMENTATION_STATUS.md`, especially its outstanding-requirements table.
3. `CHANGELOG.md`, `SECURITY.md`, `THREAT_MODEL.md`, `FORECAST_CONTRACT.md`.
4. `.github/workflows/ci.yml`, `scripts/build_release.py`, `release-allowlist.txt`.
5. The modules and tests for the next requirement before editing.

The master prompt is NOT complete. Do not call this release bug-free,
production-ready, or statistically superior to baselines without corresponding
evidence. No system can guarantee price direction. Do not inflate confidence.
Keep research/education disclosures, point-in-time correctness, CQR control arms,
and the no-live-broker-order boundary intact.

## Previously completed local checkpoint

- SQLCipher-aware authentication; both DB-API exception hierarchies and row
  factories fixed. Real encrypted registration/login/DAO/history tests added.
- E2E API uses disposable encrypted storage and independent credentials through
  `scripts/run_e2e_backend.py`; existing personal servers cannot be reused.
- Import checker detects entirely flattened package directories using the release
  inventory and does not treat function-local definitions as module exports.
- Release ZIPs are replaced atomically after clean extraction checks. Both failed
  verification and successful replacement have regression tests.
- Promotion metrics reject negative/nonfinite values, invalid conditional
  coverage, empty tier summaries, and DM evidence favouring the baseline.
- Nonconverged base members are excluded before evaluating the published blend.
- Tailwind 4.3.3 and updated undici removed frontend development audit findings;
  light/dark utility styling has browser coverage. Full npm audit is now in CI.
- Mandatory reference/doc checks, extracted `import main` and four smoke tests,
  daily security scans and targeted auth/IDOR/forecast mutation gates remain.

Execution evidence for that checkpoint: strict backend 1,049 passed / 2 skipped;
frontend 185 unit tests passed; E2E 18 passed; mypy 182 files passed; targeted
mutants 3/3 killed; Python/npm audits reported no known vulnerabilities. A later
release-replacement success regression also passed with its failure regression.
Do not present those full-suite numbers as validation of subsequent changes.

## Most recent work: signed promotion registry (continue here)

New/changed source:

- `forecasting/promotion_store.py`: configured SQLite/SQLCipher sidecar registry
  (`<manifest>.registry.db`), HMAC-signed manifests and chained audit entries,
  append-only SQL triggers, rejection logging, and current-decision verification.
- `forecasting/model_promotion.py`: gate version v2 / manifest schema 2, signing
  key requirement, partial policy-floor enforcement, future-dated receipt rejection,
  historical rollback preserving evidence timestamps, and audited revocation.
- `scripts/promote_model.py`: revocation now calls the audited service instead of
  writing an overwriteable `.revoked.json` file.
- `tests/test_promotion_registry_security.py`: nine tests covering tampering,
  missing signing keys, weakened gates, future evidence, history/rollback,
  append-only triggers, revoked-manifest replay, expired rollback evidence, and
  HMAC detection even after update triggers are deliberately removed.
- `tests/test_model_promotion.py`: isolated test signing key fixture added.

Latest focused run: these two test files passed **17 tests**. All new behavior
tests initially failed before implementation. Mypy retry passed **183 application
source files** after an earlier timeout. Full backend, E2E and mutation gates have
not yet been rerun for this signed-registry addition. The separate continuation
archive is built with mandatory extraction/import/smoke checks; broader clean-
environment archive verification remains pending.

Required key: `STOCKPILOT_AUDIT_SECRET`, at least 32 bytes. Windows provisioning
already knows this key. Do not use test material in deployment. Missing signing
configuration must fail closed. Legacy unsigned manifests intentionally stay
inactive until re-evaluated through the real gate.

Important limits and immediate follow-ups:

- SQL triggers/HMAC make application history append-only and detect edits.
  They do NOT prove resistance to a database owner truncating the whole history.
  An external WORM/independent signed-head anchor is still required for that claim.
- Review concurrent promotion/rollback/revocation, failed export recovery, sidecar
  backup/retention, malformed evidence, and registry/key rotation handling.
- Policy-floor enforcement is not yet comprehensive; audit all configurable
  thresholds and aggregate sample-count requirements for bypasses.
- Rollback is currently candidate-wide, not a finished per-tier model console.
- Production API/CLI integration, operational roles, UI controls, schema/client
  updates and comprehensive acceptance proof are still needed.
- Do not treat synthetic gate tests as real out-of-sample market superiority.

## Remaining implementation scope

Proceed in master-prompt order: foundation, security/infrastructure, prediction,
admin operations, UI, with tooling integrated throughout.

1. Execute broad mutmut, production-shape staging, and eventually hosted CI.
2. Complete PII/broker field encryption, confirmed login anomalies/passkeys,
   scheduled retention, encrypted backup automation and actual restore drills.
3. Finish shared PostgreSQL/SQLite repositories, async/pooling/migrations, Redis
   multi-user defaults/TTLs, durable scheduled Celery jobs/retries/dead letters,
   and stated-concurrency load/restart tests. Much code still calls SQLite directly.
4. Validate regime stacking and IPO peers against baselines on genuine held-out
   observations per tier; options benchmark at equal coverage; point-in-time
   event-aware widening; immutable promotion decisions; sustained tier decay must
   automatically affect published intervals or baseline routing, not just reports.
5. Public automatically updated scorecard and forecast-rendering honesty lint.
6. Support-ops/model-ops least privilege; model history/rollback/per-tier pause;
   feature flags; user directory/audited bulk actions; provider health/failover;
   job console; living compliance checklist and versioned acknowledgments.
7. Finish guided defaults, command palette, summaries/progressive disclosure,
   abstention, Framer Motion with reduced-motion support, fan chart, keyboard
   layout controls, measured contrast/axe checks, and mobile panels/navigation.
8. Complete runnable model registry, data validation, drift dashboards,
   Grafana/Prometheus/OpenTelemetry, SAST/secret/container scans, Storybook/Chromatic,
   load tooling and maintained architecture/changelog documentation.

Reuse working modules. Do not implement a second competing stack. Inspect
actual callers and data flows before selecting the smallest correct change.

## External blockers

- Docker was not installed/available on this workstation. Staging, real Postgres/
  Redis/Celery restart tests and load evidence cannot be marked passed without
  running equivalent infrastructure.
- The user withholds GitHub publication. Hosted CI for local changes must remain
  pending unless authorized runner access can execute the exact unpublished tree.
- Real prediction superiority requires licensed/verified market data, including
  genuine historical options/event inputs. Do not fabricate observations/results.
- External penetration testing and paid/provider services require real external
  engagements/credentials. Document exact blockers; continue independent code work.

## Verification and archive delivery

Use the project Python 3.12 environment consistently. Prior checks sometimes used
global Python while Playwright used `.venv`; verify dependency consistency.

Run appropriate targeted tests during edits, then final acceptance:

```text
pip install -r requirements.txt -r requirements-dev.txt
npm install                         (in frontend/)
python -m pytest tests/ -v -W error -p no:randomly
python -m mypy --config-file mypy.ini .
python scripts/check_dangling_refs.py
python scripts/check_required_docs.py
python scripts/check_critical_mutations.py
python -m pip_audit --local
python -m bandit -r api middleware services scripts forecasting analytics derivatives -q -ll
npm run verify                     (in frontend/)
npm run audit                      (in frontend/)
npm run test:e2e                    (in frontend/)
```

Run broad mutation/infrastructure/external gates on suitable runners. Record
passed, skipped, failed and blocked evidence separately. Repair failures rather
than suppressing checks or blindly increasing timeouts. Inspect skip reasons.

Use `scripts/build_release.py` for sanitized ZIPs. Preserve folder structure.
Never include `.env`, credentials, user DBs, DPAPI files, `.git`, `.venv`, caches,
node_modules or build output. Mandatory extraction/import/smoke checks cannot be
skipped even when full extracted environment verification is explicitly deferred.

At final completion refresh the original parent-folder targets:
`StockPilot-AI-v18-WINDOWS.zip`, `StockPilot-AI-v16-WINDOWS-REPAIRED.zip`, and the
additional `StockPilot-AI-v18-WINDOWS-REPAIRED.zip`. The legacy v16 filename was
explicitly refreshed with v18 content; explain that fact. The separate
`StockPilot-AI-v18-CONTINUATION.zip` is the handoff checkpoint, not final completion.

Finish by showing the user actual changes, acceptance evidence, remaining
external blockers and archive paths. Still do not commit or push.
