# StockPilot AI v6.1 Windows Quick Start

StockPilot AI is for NSE/BSE research and paper trading only. It cannot place real broker orders.

## Prerequisites

- Windows with PowerShell 5.1 and `cmd.exe`.
- 64-bit Python 3.12 available through `py -3.12`, `python3.12`, or `python`.
- Node.js compatible with `frontend/package.json`: `^20.19.0`, `^22.12.0`, or `>=24.0.0`.
- npm. The verified version is npm 11.16.0.
- Internet access for first-run dependency and Playwright browser downloads.

Python 3.13 is not supported by the current Windows scripts. They require exactly Python 3.12.

## First Run

Extract the release to a normal user-writable directory. Paths containing spaces, including a OneDrive desktop path, are supported by the launchers and passed the recorded first-run setup check.

Open Command Prompt in the extracted project root and run:

```bat
RUN_STOCKPILOT.bat
```

`RUN_STOCKPILOT.bat` detects whether setup is required. On the first run it:

1. Creates a Python 3.12 `.venv`.
2. Installs `requirements-dev.txt` with the virtual-environment interpreter.
3. Creates `.env` from `.env.example` when needed.
4. Generates independent 48-byte JWT and MFA secrets in the current-user Windows DPAPI store, migrating and clearing an old plaintext JWT assignment when present.
5. Restores frontend packages from `package-lock.json` with `npm ci`.
6. Creates `frontend/.env.local` from its example when needed.
7. Installs Playwright Chromium.
8. Starts the backend and frontend and waits for readiness.

Do not substitute a non-lock-file package installation command. The supported frontend setup path is the provided launcher using `npm ci`.

## Separate Setup And Start

To perform setup without starting the application:

```bat
SETUP_STOCKPILOT.bat
```

To skip the Playwright browser download during setup:

```bat
SETUP_STOCKPILOT.bat -SkipBrowserInstall
```

After setup:

```bat
START_STOCKPILOT.bat
```

Open `http://127.0.0.1:3000`. API documentation is at `http://127.0.0.1:8000/docs`.

Keep the launcher window open. Press `Ctrl+C` to stop both managed process trees.

## Default Data Behavior

`.env.example` (and therefore a freshly-created `.env`) ships with
`STOCKPILOT_PROVIDER_MODE=OFFLINE_DEMO`, so the app is runnable immediately
with no broker credentials. Every quote, chart, indicator and forecast in
this mode is clearly labeled "demo" by the `DataSourceBadge` in the header -
synthetic demo values are never proof of market connectivity, and are never
served silently or mixed into a live session.

Once you have configured and verified a real broker (see "Configure Upstox"
below), set `STOCKPILOT_PROVIDER_MODE=LIVE_ONLY` in `.env` and restart. In
`LIVE_ONLY` mode, if no configured provider can return data, StockPilot
reports the data as unavailable rather than silently falling back to demo
data - this fail-closed behavior is unchanged and is exactly what protects
you from mistaking synthetic data for a live feed.

## Configure Upstox

Obtain a newly generated user token, then run:

```bat
CONFIGURE_UPSTOX.bat
VERIFY_UPSTOX_LIVE.bat
```

Real Upstox connectivity is currently **NOT VERIFIED** because no newly generated user token was available for the recorded release checks. See [UPSTOX_CONFIGURATION.md](UPSTOX_CONFIGURATION.md).

Backend secrets are stored outside the project at `%LOCALAPPDATA%\StockPilotAI\secrets.dpapi.json` and encrypted for the current Windows user. `.env` is retained for non-secret settings and plaintext fallback compatibility only.

## Verify The Project

Run the repository verification workflow with:

```bat
VERIFY_STOCKPILOT.bat
```

This workflow has 12 stages: launcher tests, normal backend tests, randomized backend tests, Python compilation, strict mypy, pip-audit, `npm ci`, frontend tests, TypeScript, a production build, npm production audit, and Windows Playwright.

The recorded command passed all 12 stages while reusing the setup-installed browser:

```bat
VERIFY_STOCKPILOT.bat -SkipBrowserInstall
```

`START_STOCKPILOT.bat` also passed actual backend/frontend startup and readiness checks in the OneDrive path. See [VERIFICATION_RESULTS.md](VERIFICATION_RESULTS.md) for exact counts and timings. Clean extraction and final ZIP verification remain Phase 9 work.

## Troubleshooting

- `Python 3.12 is required`: install 64-bit Python 3.12, enable the Python launcher, reopen the terminal, and rerun setup.
- `Port 8000 is already in use` or `Port 3000 is already in use`: run `netstat -ano | findstr :8000` or `netstat -ano | findstr :3000`, stop the conflicting process, and retry.
- `Setup is required`: run `SETUP_STOCKPILOT.bat`.
- Missing live market data: confirm `LIVE_ONLY` behavior, configure a provider, and inspect the visible provider/freshness status. Do not assume demo fallback.
- Upstox rejection: generate a new token, rerun `CONFIGURE_UPSTOX.bat`, then rerun `VERIFY_UPSTOX_LIVE.bat`.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### Step 0: run the configuration doctor

```
python scripts/doctor_setup.py --env-file .env --frontend-env-file frontend\.env.local
```

Exit code 0 means nothing is blocking. Exit code 1 means at least one check is `action_required`,
and the output names the variable to set. Do this before starting the API, and again if Google
sign-in or market data misbehaves.

Google redirect URI reminder: it must match the Google Cloud Console entry byte for byte, with no
trailing slash, and this project's defaults use `127.0.0.1` rather than `localhost` because Google
treats them as different origins.
