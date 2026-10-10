# Threat Model

Version: 1.1, reviewed 2026-10-07. Scope: local Windows research and paper-trading release.

## Security Objectives

- Prevent account takeover and cross-user access.
- Keep JWT, MFA, OAuth, broker, SMTP, and provider credentials out of responses, logs, source, and release archives.
- Preserve the research-only/no-live-order boundary.
- Keep market-data provenance and stale/fallback status visible.
- Prevent untrusted inputs or artifacts from becoming code execution.

## Trust Boundaries And Data Flows

1. Browser to Next.js/FastAPI: credentials and HttpOnly session cookies cross a loopback HTTP boundary. CORS, Origin checks, rate limits, secure-cookie policy, session revocation, and optional TOTP protect it.
2. FastAPI to SQLite: user PII, password hashes, sessions, paper activity, forecasts, and audit records enter local persistence. Foreign keys, ownership-derived queries, transactional erasure, and scheduled retention apply. SQLite is plaintext by default; configured SQLCipher databases encrypt pages. v18 validates update identifiers in both DAOs and fails closed for unavailable encryption key files. Plaintext-to-encrypted migration and encrypted backup/restore drills remain outstanding.
3. Backend to Windows DPAPI: local secrets are encrypted for the current Windows user outside the project tree. Process environment remains higher precedence; plaintext `.env` is fallback-only.
4. Backend to market/OAuth/mail providers: secrets leave only in provider-specific server requests. Responses are normalized and errors are sanitized before reaching users.
5. Backend to cache/model artifacts: provider history and model-refresh JSON are local operational artifacts. Release hygiene excludes them; retention removes stale refresh files. Joblib/pickle loading is restricted to trusted roots and optional digests.
6. Release pipeline: an explicit allowlist feeds secret scanning, archive construction, inventory verification, and clean extraction. Local databases, credentials, caches, logs, dependencies, and model artifacts are denied.

## STRIDE Review

| Threat | Representative abuse | Controls | Residual risk / next review trigger |
| --- | --- | --- | --- |
| Spoofing | Stolen password/session, OAuth callback forgery | bcrypt/scrypt, lockout, OIDC state/nonce, TOTP/recovery/passkeys, hashed JTI, token version | Confirmed-device anomaly enforcement remains open |
| Tampering | Forged user IDs, modified cookies, archive alteration | User ID derived from authenticated session, ownership predicates, signed JWT, CSRF Origin checks, release inventory and SHA-256 evidence | Local administrator/malware can alter unencrypted SQLite |
| Repudiation | User/admin denies sensitive action | User and admin audit logs, versioned acknowledgment, session records, bounded retention | Local logs are not immutable or externally timestamped |
| Information disclosure | Secret, raw IP, PII, provider body, TOTP leak | DPAPI, encrypted TOTP secret, keyed recovery hashes, coarse session labels, sanitized errors/logs, secret scanner | Memory/process inspection by same-account malware remains possible |
| Denial of service | Login guessing, forecast queue exhaustion, provider flood | Endpoint rate limits, login lockout, bounded worker queue/per-user quota, provider pacing/circuit behavior | In-process queues do not survive restart and are not distributed |
| Elevation of privilege | User becomes admin or accesses another account | Default user role, configured-email plus stored-role check, admin step-up, IDOR tests, no impersonation path | Host compromise bypasses application controls |

## High-Risk Feature Boundaries

- No broker order placement, modification, or cancellation is permitted. Static regression tests enforce known provider mutation routes.
- Shared strategy/layout content must remain serialized configuration; arbitrary user code and `eval` are prohibited.
- PostgreSQL/Alembic, SQLCipher, and Redis-backed task queues require separate migration threat models, rollback plans, and deployment evidence before activation.
- Any public hosting, mobile client, external API key, webhook, passkey, or live execution proposal requires updating this document before implementation approval.

## Review Process

Security review is required for authentication changes, new persisted sensitive fields, provider integrations, external network listeners, release-input changes, and retention-policy changes. Findings and acceptance owners are recorded in `SECURITY_GOVERNANCE.md`; legal/regulatory approval remains separate.
# Continuation data-flow review — 2026-10-05

New flows: authenticated model operators write signed tier-control state and
append-only control events in the promotion sidecar; the forecast publication
boundary reads verified state and abstains on unavailable integrity. Support
operators read bounded account metadata through a dedicated directory endpoint,
without portfolios, credentials or account mutation permissions. Both stored
roles and current deployment allowlists authorize these paths.

Encrypted scheduled backups now include the sidecar. Restore drills authenticate
and verify each snapshot before retention removes older copies. Backup and audit
keys must be provisioned persistently; loss/rotation and off-host recovery need
operator-managed procedures. HMAC and application triggers do not defeat a
database owner deleting the entire registry or control history. External WORM or
independently signed-head anchoring remains an open security requirement.

The public Prometheus endpoint exports aggregate counters/duration only, without
user paths, IDs, symbols, credentials or model artifacts. Restrict the monitoring
stack to a trusted network; its Compose ports bind to localhost. Monitoring
containers and independent penetration testing have not been executed here.

## Data-flow review — 2026-10-07

Passkey creation/assertion crosses the browser authenticator boundary. Challenge,
RP/origin, account ownership and signature verification precede session creation;
passkey-only accounts cannot bypass MFA. Challenge storage is still in-process
and needs shared persistence for multi-worker scale. Cookies cover both factors.

Bulk account controls require configured admin role, step-up and explicit reason;
state/audit writes are transactional. Support is read-only and model operators
cannot see account/compliance records. Queue views whitelist metadata, excluding
job arguments, traces and raw errors. Replay preserves original argument shapes.

Disclosure acknowledgments survive audit expiry but are erased with the account.
Checklist edits and audit records commit together; independent reviews still need
actual external evidence. Geolocation uses the resolved request client rather than
arbitrary forwarded headers; device identity remains a coarse UA/network heuristic.
Cached forecast publication rechecks signed controls without mutating cache data
or compounding widening.
