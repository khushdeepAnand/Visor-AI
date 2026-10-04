# Administrator Operations

The v7 RC adds audited runtime provider reordering and account lifecycle actions
(`suspend`, `reinstate`, `force_logout`, and lockout reset). These controls expose
only an account ID and action result. They do not expose portfolios, watchlists,
orders, alerts, reports, journals, credentials, or session tokens.

## Scope

This runbook describes the constrained administrator surface for the supported local Windows deployment. Administrator access does not authorize broker trading, access to another user's records, filesystem browsing, SQL execution, environment dumping, or secret retrieval.

## Provisioning

1. Protect the workstation and local `.env` before making any privilege change.
2. Add no more than three distinct account email addresses to `STOCKPILOT_ADMIN_EMAILS` in the local `.env`. Do not place those addresses in release artifacts or diagnostic captures.
3. Each administrator must register through the normal account flow with their own password. Configuration does not create an account.
4. Restart the backend, sign in as an existing configured account, and invoke the allowlisted `bootstrap_admins` maintenance action. An initial bootstrap may also occur through the application's startup path.
5. Confirm `/api/v1/admin/overview` reports the expected configured and pending state. A configured address remains unprivileged until its database role is promoted; a stale database role is refused when its address is no longer configured.

Invalid addresses or more than three distinct configured identities fail closed. Removing an address from the allowlist and running bootstrap demotes that account.

## Allowed Operations

- Read aggregate application health, counts, model registry metadata, provider readiness, cache/job diagnostics, sanitized error groups, bounded settings, and administrator audit events.
- Update only the server-side allowlisted settings within their declared bounds.
- Trigger only `refresh_instruments`, `refresh_calendar`, `clear_error_groups`, and `bootstrap_admins`.

The instrument and calendar refreshes can depend on external network sources. A successful fixture test is not evidence that a live refresh source is available.

## Change Procedure

1. Record the reason, operator, time, intended setting or maintenance action, and rollback value outside any secret-bearing file.
2. Check readiness and current diagnostics before the change.
3. Make one bounded change or maintenance action at a time.
4. Confirm the response, readiness, and relevant diagnostics.
5. Review `/api/v1/admin/audit`; audit details are truncated and omit secret-like keys.
6. Revert the setting if behavior degrades. Restart only when required by the affected runtime configuration.

An audit write failure does not cancel the underlying operation. If the expected audit event is absent, treat the operation as needing manual reconciliation rather than as unperformed.

## Incident Handling

- Revoke provider credentials first if exposure is suspected; do not paste credentials into tickets, logs, screenshots, or chat.
- Stop both loopback services before preserving local evidence.
- Preserve only sanitized timestamps, support IDs, action names, outcomes, and version information.
- Do not distribute `.env`, database files or sidecars, caches, logs, fitted models, browser state, or raw provider responses.
- Run `VERIFY_STOCKPILOT.bat -SkipBrowserInstall` after remediation. Run live-provider verification separately only with current user-owned credentials.

## Evidence Boundary

Administrator authorization and redaction behavior are fixture-tested locally. This document is an operational runbook, not legal approval, penetration-test evidence, or proof of live-provider operation.

## Operational Guardrails (v7.1)

Implemented in `services/forecast_guardrails.py` (`forecast_guardrails.py` in this
flat archive) and covered by `tests/test_forecast_guardrails.py`.

### Forecast kill switch

- Scopes: `symbol`, `asset_class` (`equity`, `index`, `futures`, `options`), `timeframe`, `model_version`, `global`.
- Every switch requires a reason of 12-240 characters and an explicit expiry; the maximum lifetime is 14 days. There is no permanent switch.
- While a switch matches a request, the forecast path returns the blocked state with error code `forecast_disabled_by_operator` and the operator reason. Quotes, history, and reports remain available.
- Rollback path: `revoke_kill_switch(id, reason)`. Revoking twice is refused, so audit history stays unambiguous.
- At most 50 switches may be active at once; expired switches stop blocking automatically without an operator action.
- Create, revoke, and auto-expiry events are written to the admin audit log with actor, scope, target, reason, and expiry.

### Feature flags and staged rollout

- Flags are a fixed allowlist: `pooled_low_history_model`, `forecast_corridor_v2`, `scenario_studio`, `daily_brief_digest`. Unknown names are rejected rather than created on demand.
- Every flag defaults to off. Enabling requires a rollout percentage of 1-100 and a reason.
- Bucketing is deterministic (`sha256(flag + subject) % 100`), so a given user's experience does not flicker between requests.
- Each flag declares a failure budget (`max_failure_rate`) and a minimum sample count. `evaluate_auto_rollback` returns `insufficient_samples`, `within_budget`, or `rolled_back`; a rollback disables the flag, records the measured failure rate, and is audited.

### Status banners

- Levels: `info`, `maintenance`, `degraded`, `outage`. Body text is capped at 400 characters.
- Banners are drafted first and are invisible to users until `publish_banner(id, confirmed_preview=True)`. Publishing without confirming the preview is refused.
- Every banner is time-bounded (maximum 7 days) and can be withdrawn immediately. Expired banners disappear without operator action.
- `public_status_payload()` is the only banner surface exposed to end users; it never includes operator identities or internal detail.

## Step-Up Authentication (v7.1)

Implemented in `services/admin_step_up.py` (`admin_step_up.py` here) and covered by
`tests/test_admin_step_up.py`.

- Protected actions: `provider_mode_change`, `model_promotion`, `model_rollback`, `cache_invalidation`, `backup_restore`, `admin_change`, and forecast kill-switch changes. Read-only admin views are not gated.
- The operator re-enters their account password. Verification reuses `authentication.verify_password`; the password is never logged and no new credential store is introduced.
- A successful challenge mints a single-use, TTL-bounded token bound to actor, action, and target. Replay, cross-action reuse, cross-actor reuse, and target substitution are all refused.
- Only a hash of the token is persisted; the plaintext token exists solely in the response to the challenging operator.
- Repeated failures inside the rate-limit window lock the actor out of step-up, including with the correct password, until the window elapses.
- Every challenge, denial, lockout, and consumption is audited through `record_admin_action`.

### Admin ceiling

`admin_registry.MAX_ADMINS` is **3** and is the single source of truth; `/api/v1/admin/overview`
reports it as `max_admins`. Earlier notes that described a two-administrator ceiling are
historical and are superseded by this document.

## 2026-09-09 - v8 upgrade pass (v7.1 -> v8)

### Step-up from the admin UI

Two admin surfaces are gated by a single-use step-up token:

- **Provider priority** (`PUT /api/v1/admin/diagnostics/providers/order`): action
  `provider_mode_change`, target `provider_order`.
- **`bootstrap_admins` maintenance**: action `admin_change`, target
  `maintenance:bootstrap_admins`. The other maintenance actions (`refresh_instruments`,
  `refresh_calendar`, `clear_error_groups`) are audited but need no re-confirmation and are
  shown without an asterisk.

The UI exchanges a re-entered password for one token per action via
`POST /api/v1/admin/step-up`, sends it as `X-Step-Up-Token`, and clears the password on
success. Tokens expire after 5 minutes, are single use, and repeated failures are rate
limited. `STEP_UP_MAINTENANCE_ACTIONS` in `api/deps.py` is the authority; the mirrored
constant in `frontend/app/admin/page.tsx` must be kept in step.

### Operations endpoints

`GET /api/v1/status` (public), `GET /api/v1/admin/operations`, kill switch
create/list/revoke, `PUT /api/v1/admin/operations/flags/{name}`, banner
draft/list/publish/withdraw, `POST /api/v1/admin/operations/cache/invalidate`.

## 2026-09-09 - v9 upgrade pass (v8 -> v9)

### New feature flags

| Flag | Default | Controls | Auto-rollback |
| --- | --- | --- | --- |
| `strategy_builder` | off | `/api/v1/strategies*` and the equity backtester | 5% failure rate over 50 samples |
| `multi_leg_backtest` | off | `/api/v1/options/backtest` | 5% over 50 |
| `forward_test_tracking` | off | `/api/v1/forward-tests*` | 5% over 50 |

All three default to off, so a fresh deployment exposes none of them until an
operator enables the flag with a reason. When a flag is off the route answers
HTTP 503 with `{"code": "feature_disabled"}` - it does not fall back to a
partial result.

Operational note: forward tests never place orders. If a forward test appears to
have produced a broker order, treat it as a security incident, not a bug.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### New review and control surfaces

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/admin/setup` | Per-check configuration status with the exact missing variable names, what each blocks, and the fix. Includes provider readiness and database health. |
| `GET /api/v1/admin/review` | Consolidated operator view, leading with configuration blockers, then database, providers, feature flags, counts, failure groups, forecast cache and the calibration methodology. |
| `GET /api/v1/admin/calibration?symbols=...` | Measured interval quality per symbol: coverage vs target, MAE/RMSE, skill against a random walk, support state. Up to 25 symbols. |
| `GET /api/v1/admin/calibration/methodology` | What the range claim is based on and what the system refuses to claim. |

All four require an administrator session through `require_admin`. They are read-only: none of them
writes credentials, enables a feature flag, or touches an order path. The UI entry point is
**Admin > Setup Doctor** (`/admin/setup`).

Operational note: a symbol whose `support_state` is `baseline_only` or `abstained` on the
calibration endpoint is not a bug to be fixed by tuning. It is the honest result of that symbol's
own history, and suppressing it would break the range contract.
