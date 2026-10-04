# Data Retention and Account Deletion

Status: enforced technical policy for the local/private release. Regulatory and legal review remains required before public or multi-user deployment.

## Scope

StockPilot stores account identity, sessions, settings, portfolios, watchlists, alerts, saved research, paper activity, forecast outcomes, acknowledgment evidence, and audit events in local SQLite. Broker and application credentials are deployment configuration, not user records.

## Enforced Schedule

`services/retention.py` applies the following defaults. Operators may shorten or extend them only within the hard bounds enforced by that module.

| Record class | Default maximum | Configuration | Enforcement |
| --- | ---: | --- | --- |
| Expired sessions | At expiry | Session expiry | Deleted after `expires_at`; revoked rows are removed after the security-artifact window |
| Password-reset tokens and MFA challenges | 7 days after use/expiry | `STOCKPILOT_SECURITY_ARTIFACT_RETENTION_DAYS` | Daily bounded delete |
| Stale login-attempt records | 7 days after update, once unlocked | `STOCKPILOT_SECURITY_ARTIFACT_RETENTION_DAYS` | Daily bounded delete |
| User audit events | 365 days | `STOCKPILOT_AUDIT_RETENTION_DAYS` | Daily bounded delete |
| Administrator audit events | 730 days | `STOCKPILOT_ADMIN_AUDIT_RETENTION_DAYS` | Daily bounded delete |
| Model-refresh JSON artifacts | 30 days | `STOCKPILOT_FORECAST_ARTIFACT_RETENTION_DAYS` | Daily bounded filesystem delete |
| In-memory forecast jobs | 1 hour | `STOCKPILOT_FORECAST_JOB_RETENTION_SECONDS` | Enforced by `ForecastJobManager` during normal access/submission |

Each database class is capped by `STOCKPILOT_RETENTION_BATCH_SIZE` per run (default 1,000; hard range 100 to 10,000). Database deletions run in one transaction. Artifact deletion accepts only regular `*.json` files under the configured model-refresh directory, never follows symlinks, and reports counts rather than names or contents.

The dedicated APScheduler job `stockpilot-retention-enforcement` runs daily at 02:15 in `STOCKPILOT_SCHEDULER_TIMEZONE`. It is controlled by `STOCKPILOT_RETENTION_ENABLED` (default `true`) independently of provider/retraining jobs. This is a local in-process scheduler; it is not a durable distributed queue and does not claim restart catch-up beyond APScheduler's configured misfire grace period.

## Records Retained With An Active Account

Portfolio, watchlist, saved research, forecasts, paper-trading history, settings, and compliance acknowledgment records remain while the account is active, subject to the audit maximum above. Market-data cache TTLs are separate operational cache policy.

## Right To Erasure

An authenticated user can choose **Account → Delete account and data**, type `DELETE`, and submit the irreversible request. The API performs one database transaction that:

- removes directly user-owned rows from every existing table carrying `user_id` or `actor_user_id`;
- removes indirectly owned forward-test events before parent records;
- removes email-keyed login and administrator security records;
- removes the user identity last and invalidates the current session cookie.

Failure rolls back the transaction. Global instrument metadata and aggregate model-health records are not user records.

## Backups And Limitations

An application purge cannot erase external backups, filesystem snapshots, or OneDrive/cloud history. The deployment owner must apply an approved expiration and restore-time deletion procedure to those systems. The application does not claim GDPR, DPDP, SEBI, exchange, or broker compliance; qualified review remains tracked in `REGULATORY_REVIEW_REQUIRED.md`.
