# StockPilot AI — v20 → v21 continuation evidence

Date: 2026-10-09. Working folder/archive names remain `StockPilot-AI-v18`.
**Partial continuation; no production-superiority or full-completion claim.**

## Real evaluation and review result

`history-manifest.json` records provenance, adjustment verification, per-bar
session/date/completion timestamps, received-at timestamps and exclusion reasons
for every bar in the 18-file cache inventory. Calendar-derived close times are
identified as such, not presented as observed historical provider delivery times.
The 2025 NSE calendar was independently downloaded and text-reviewed from
`https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf`:
SHA-256 `1e0f85615fbbcde9243dd6cafbbad4d9b581b8d2467fd9e27c0a4fe42a720c7d`.
The existing bundled 2026 calendar is reused; unknown Muhurat timing is excluded.

Real equities are excluded because complete sourced issuer-action reviews are
absent. Demo/synthetic/unknown sources, incomplete closes, conflicts, duplicate
rows and history preceding unverified session gaps cannot contribute evidence.
Overlapping caches are merged per symbol without manufacturing more samples.
The verified exported subset contains **238 NIFTY 50 + 238 INDIA VIX daily rows**.
These exchange indices do not take issuer-share split/dividend adjustments.
Explicit no-issuer-action verification uses `forecasting/corporate_actions.py`.

Final GBM results, evaluated against the actual shared model's horizon=1 test
outputs with the unrelaxed tier/aggregate gates and candidate-family correction:

| Symbol | Tier | Paired test n | Coverage (nominal 80%) | Winkler | Shared Winkler | MASE/persistence | One-sided DM p | Eligible |
|---|---|---:|---:|---:|---:|---:|---:|---|
| NIFTY 50 | T2 | 20 | 70% | 711.9470 | 734.3762 | 1.01321 | 0.254884 | No |
| INDIA VIX | T2 | 20 | 65% | 3.4285 | 3.6530 | 1.00693 | 0.135263 | No |

HAR coverage is 75% / 65%; DM p-values are 0.224599 / 0.114732, also not
significant. Event evidence is insufficient; earnings/news/circuit labels and
matched options observations were not supplied. Other tiers are not evaluated.
This is an executed retrospective real-price research result, not an independent
provider-authenticity/delivery-vintage audit or cross-universe promotion.

**Promotion review disposition: reject as ineligible.** The existing signed
receipt workflow remains mandatory for a future passing independently reviewed
production candidate. Nothing here issues a production receipt, changes published
bounds, or reallocates a CQR treatment/control arm. See `next-day-held-out-report.json`.

## Standing items advanced, and their actual limits

| Item | Executed work | Remaining acceptance |
|---|---|---|
| Shared repository | SQLite/Postgres range writers share canonical immutable snapshot values; SQLite insert IDs/symbols/connection ownership corrected; snapshot preservation test passed. | Application paths still directly use SQLite; full shared/async adoption and live PostgreSQL schema/behavior testing remain open. The historical prepare-only whole-file byte freeze was retired explicitly, not repinned to hide changes. |
| Load | Disposable actual SQLite database, 16 threads, 400 writes, all 400 IDs/rows preserved; integrity `ok`; 124.61 writes/s, median 131.34 ms, p95 211.43 ms. | Workload is synthetic, storage is real. This is not multi-process/API/Postgres/Redis or production-shape load/restart acceptance. Docker CLI is unavailable. See `repository-load-report.json`. |
| Confirmed devices | Random device-cookie possession bound into signed MFA challenges; TOTP/recovery/passkey proof confirms that device; wrong cookie/replay rejected; alert acknowledgment cannot trust it; strict detector outage fails closed. Real virtual passkey browser flow passed. | Strict mode requires provisioned, MFA-enrolled accounts; provision/enroll before enabling it. No new-device email approval mechanism is claimed. |
| PII/broker field encryption | Owner/field-bound, randomized authenticated AES-GCM helper with independent key, round-trip/cross-owner/cross-field/wrong-key/missing-key checks. Existing Windows broker secrets remain per-value DPAPI protected. | **Not complete**: helper is not an application-wide PII persistence/query migration. No claim that current user email/DOB/audit fields are field-encrypted, migrated, or rotation-tested end to end. |
| External penetration test | Concrete scope/request and booking inputs prepared in `EXTERNAL_PEN_TEST_ENGAGEMENT.md`. | **Not scheduled**: no selected vendor/contact, approved budget, staging URL, mail/calendar tool or vendor acceptance. Owner must provide these to book the engagement. |
| Pandera | Installed 0.27.1; normalized provider and research OHLCV boundary validation executed; malformed OHLC/nonfinite/negative-volume/duplicate inputs rejected, zero-volume indices accepted. | Historical source completeness is a separate requirement; schema success cannot establish corporate-action or market provenance. |
| MLflow | Installed 3.17.0; actual fitted quantile weights/signatures/examples/manifest-hash lineage registered in persistent local SQLite backend; six names, 24 versions, every version reloaded and performed finite smoke inference. | Research registry only. Zero-input inference is synthetic artifact restoration proof. Production model loading/receipt coupling, remote registry deployment/access control/backup remain open. Local storage is under `local-history` and excluded from ZIP. |

## Verification and delivery

- Final strict full backend: **1,146 passed / 3 skipped**, 582.70 s. Skips are optional
  SHAP and two credential/symbol-dependent live-provider tests.
- The final suite includes merged-cache/session-gap regressions and public
  encryption-compatibility checks. The last issuer/index metadata guard is also
  exercised by the final focused manifest checks.
- Frontend `npm run verify`: schema sync (178 routes/63 schemas), honesty gates,
  **194 tests**, typecheck/build, production audit passed (zero vulnerabilities).
- Isolated production browser build on 8011/3011, two workers: **20 passed**,
  including real virtual passkey enrollment/login and paper BUY/SELL regression.
- Whole-project mypy: **195 source files passed**. Bandit Medium/High gate,
  required docs (22/22), reference/import and non-connecting DAO signature gates
  passed. The legacy hand-written encryption copy triggered three Medium SQL
  findings; it was replaced by the existing verified exporter, with quoted-schema/
  passphrase, encrypted rollback and wrong-key proof in the final full suite.
- Installed-environment `pip check`: passed. Python vulnerability audit: no known
  vulnerabilities, including the installed optional registry dependencies.
- Both final exported series have **zero missing verified regular sessions**
  inside their retained intervals. This additional calendar check exposed a
  disconnected older-cache prefix during development; merged-series truncation
  now prevents its reintroduction. Earlier exploratory registry versions remain
  research-only and cannot be mistaken for the final frozen-input report.
- Packaging uses the existing allowlist, secret/inventory checks, atomic ZIP
  replacement and mandatory extracted import/reference/smoke verification. Private
  environment files, databases, caches, keys and local registry artifacts are excluded.
  Full clean-venv extracted backend/frontend installation tests are not repeated.

Commands are in `NEXT_DAY_SPECIALIST.md`. Repository load reproduction:

```powershell
.\.venv\Scripts\python.exe scripts/load_repository.py --workers 16 --operations 400 --output docs/repository-load-report.json
```

The requested `StockPilot-AI-v18-WINDOWS.zip` is refreshed only after the final
checks pass. Completion of the entire master prompt is not implied by this ZIP.
