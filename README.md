# StockPilot AI

## Cross-platform release (Windows + macOS)

This package includes the original Windows launchers plus cross-platform Python/macOS launchers.
See `CROSS_PLATFORM_SETUP.md`.

- **Windows:** run `SETUP_STOCKPILOT.bat`, then `START_STOCKPILOT.bat`.
- **macOS:** run `./setup_stockpilot.sh`, then `./start_stockpilot.sh`.
- Do not copy `.venv` or `frontend/node_modules` between operating systems; each computer creates its own environment.


AI market research + paper-trading terminal (India). This folder is the merged
**v10 (hotfixed)** codebase.

> **Testers: start with [`TESTERS_START_HERE.md`](TESTERS_START_HERE.md).**
> It has the exact setup steps and a dated, honest list of what works and what
> does not. The full engineering audit is in `PROJECT_DOSSIER.md`.

## Setup guides
- **Google OAuth + Upstox + dual-API failover** → see `GOOGLE_OAUTH_UPSTOX_SETUP.md`
- **Upstox configuration & live verification** → see `UPSTOX_CONFIGURATION.md`
- **Config doctor** → `python scripts/doctor_setup.py --env-file .env`

## Last verified 2026-09-17 (this package)
- Backend: `592 passed, 2 skipped` (real deps, not stubs)
- Frontend: `tsc --noEmit` clean, `95/95` vitest, `next build` 21 routes
- Google OAuth redirect-origin bug fixed and reproduced/fixed (see section 0 of
  `GOOGLE_OAUTH_UPSTOX_SETUP.md`)

---

# Upstox V3 protobuf decoder

StockPilot refuses to guess broker protobuf field numbers. The native stream is enabled only when a real Upstox token and a generated `MarketDataFeedV3_pb2.py` are both present.

Recommended setup from the repository root:

```powershell
python -m pip install -r requirements-dev.txt
python scripts/generate_upstox_proto.py
```

The generator downloads the current official Upstox V3 schema from:
`https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`

After generation, restart the backend. `UpstoxStreamAdapter.is_configured()` will become `True` only if `UPSTOX_ACCESS_TOKEN` (or `UPSTOX_ANALYTICS_TOKEN`) is also configured.

Do not commit real tokens or captured account data. Sanitized fixtures may be committed after live verification.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### Sign-in or market data not working?

Run the configuration doctor before filing a bug:

```
python scripts/doctor_setup.py --env-file .env --frontend-env-file frontend/.env.local
```

It names the exact environment variables that are missing, what each one blocks, and the fix.
Administrators can see the same report in the UI at **Admin > Setup Doctor** (`/admin/setup`) or
from `GET /api/v1/admin/setup`. Secret values are never printed or returned; only presence and
derived metadata such as Upstox token expiry.

The two most common causes of "Google auth is not working" and "Upstox data is not working" on a
fresh clone are both configuration, not code:

- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` unset, so `services/google_oauth.py`
  refuses with "Google sign-in is not configured".
- `UPSTOX_ACCESS_TOKEN` absent or expired, so every quote and candle request fails with
  `provider_not_configured` or `provider_auth_expired`.

The Google redirect URI must match the Google Cloud Console entry byte for byte. Google treats
`localhost` and `127.0.0.1` as different origins, and this project's defaults use `127.0.0.1`.

### Design system

The interface uses the **Ledger** design system as of v10: warm paper, ink text, hairline rules,
square corners, serif display type against tabular monospace numerals. It replaces the previous
dark terminal theme. See `DESIGN_SYSTEM.md`.
# v18 release status

This folder is the latest v18 release candidate. See
[`V18_IMPLEMENTATION_STATUS.md`](V18_IMPLEMENTATION_STATUS.md) for implemented
upgrades, actual execution results and remaining master-prompt acceptance work.
The full v18 master prompt is **not yet complete**. Older version-specific
instructions below are retained as historical context.
