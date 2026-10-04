# Upstox Configuration

StockPilot AI treats Upstox as an optional, best-effort read-only provider. It is not required for quotes or history. All trading in StockPilot is simulated paper trading. Upstox place, modify, cancel, GTT, and multi-order routes are blocked by the application boundary.

## Current Verification Status

**Real Upstox connectivity: NOT VERIFIED.**

No newly generated user token was available for the 2026-08-31 release evidence. Fixture-based tests of token classification, retries, quote/history parsing, order-route blocking, and sanitized diagnostics passed as part of the local test suites, but those checks do not prove that Upstox accepted a real credential or returned current market data.

A token is not valid merely because its encoded expiry is in the future. Only a successful request to Upstox with a newly generated user token can establish live acceptance for the checked endpoint and time.

## Credential Types

- `UPSTOX_ANALYTICS_TOKEN`: preferred credential for read-only quote, history, option-chain, and Market Data Feed V3 paths.
- `UPSTOX_ACCESS_TOKEN`: daily OAuth token for Upstox endpoints that specifically require OAuth, including the margin calculation integration.
- `UPSTOX_API_KEY` and `UPSTOX_API_SECRET`: optional OAuth application fields. They are not substitutes for a generated access or Analytics Token.
- `UPSTOX_REDIRECT_URI`: OAuth redirect configuration; default `http://localhost:3000/auth/upstox/callback`.

The release must contain empty placeholders only. Never put a real token in `.env.example`, documentation, source, tests, screenshots, logs, or the release ZIP.

## Configure Securely

Complete local setup first:

```bat
SETUP_STOCKPILOT.bat
```

Generate the required token in the user's Upstox account, then run from the project root:

```bat
CONFIGURE_UPSTOX.bat
```

The script prompts with hidden input and atomically saves non-empty replacements to the current-user Windows DPAPI store without displaying them. Pressing Enter preserves an existing value. The root `.env` is a plaintext fallback and is not the supported Windows credential path.

Restart StockPilot after changing provider credentials.

## Verify Live Read-Only Connectivity

Immediately after saving a newly generated token, run:

```bat
VERIFY_UPSTOX_LIVE.bat
```

The diagnostic uses the project virtual environment and requests an instrument-master refresh. It checks:

- Public instrument-master retrieval.
- Instrument resolution for `RELIANCE`, `INFY`, `NIFTY 50`, `NIFTY BANK`, and `SENSEX`.
- Read-only quote identity, price, source, timestamp, and freshness.
- Read-only V3 historical candles and freshness.
- Market Data Feed V3 authorization and protobuf decoder availability.
- That zero orders were attempted.

The JSON output intentionally records sanitized endpoint, credential mode, classification, HTTP status, safe error code, freshness, latency, and pass/fail fields. It does not print the token or retain response bodies. Do not publish diagnostic output until it has been reviewed for account-specific data.

A valid live evidence record must include the execution time, token type without the token value, checked endpoints and symbols, HTTP classifications, freshness, and zero orders attempted. Do not record broker response bodies containing account data.

## Failure Classifications

- `missing`: no usable token was configured.
- `expired`: a 401 was returned and local token metadata also indicates expiry.
- `revoked_or_invalid`: Upstox rejected a token that is not locally known to be expired.
- `permission_denied`: the credential lacks access to the endpoint.
- `rate_limited`: retry after the broker window resets.
- `network_error` or `timeout`: inspect internet, DNS, proxy, and firewall access.
- `endpoint_error`: inspect the sanitized status/error code against current Upstox documentation.
- `valid`: the specific endpoint returned a successful response during that diagnostic. This is not a permanent guarantee.

## Provider Modes

This working copy of the project is configured for automatic multi-provider
failover (see `GOOGLE_OAUTH_UPSTOX_SETUP.md`):

```env
STOCKPILOT_PROVIDER_MODE=FALLBACK_ALLOWED
STOCKPILOT_PROVIDER_ORDER=upstox,yfinance,nse,demo
STOCKPILOT_STREAM_PROVIDER=upstox
```

`FALLBACK_ALLOWED` walks every configured provider in order and falls back to
the free no-key `yfinance` backup before giving up, then to stale disk cache.
It never serves synthetic demo data. `LIVE_ONLY` (the stock release default)
tries only the first configured provider and fails closed, which looks
identical to a broken API when the primary credential is expired. The manager
automatically demotes a provider after three consecutive failures for the
current process, while preserving provenance and exposing configured/effective
order at `/api/v1/market/providers/health`.

Use `OFFLINE_DEMO` only when intentionally demonstrating synthetic NSE/BSE-labelled fixtures. Use `FALLBACK_ALLOWED` only when the operator explicitly accepts fallback behavior. Both modes must remain visibly labelled.

## Rotation And Removal

1. Generate a replacement token in Upstox.
2. Run `CONFIGURE_UPSTOX.bat` and enter the replacement.
3. Restart StockPilot.
4. Run `VERIFY_UPSTOX_LIVE.bat`.
5. Revoke the superseded credential in Upstox when appropriate.

To remove a credential, close StockPilot and run `.venv\Scripts\python.exe scripts\manage_secrets.py delete NAME`, replacing `NAME` with the exact allowlisted environment variable. The rotation prompt preserves blank input and therefore does not erase values.

If exposure is suspected, revoke the credential at Upstox first, remove it locally, inspect logs and archives without sharing values, and generate a replacement only after the exposure path is closed.

## Evidence Boundaries

- Verified locally: hidden prompting, DPAPI-store abstraction, migration/scrubbing, and launcher integration are covered by isolated tests. Provider acceptance remains external.
- Fixture-verified: parsing, retries, sanitized failures, future-expiry non-validation, broker-order blocking, and the Playwright invalid-live-credential unavailable state with no demo fallback.
- External check unavailable: real Upstox quote, history, instruments, stream authorization, option chain, and margin response with a newly generated user token.
- Release archive work: Phase 9 ZIP construction, scans, inventory, and selected extracted-copy checks passed.
- Future work: retain a sanitized, user-approved live evidence record after the external diagnostic succeeds.

## 2026-09-11 - v10 upgrade pass (v9 -> v10)
### Diagnosing "Upstox data is not working"

Run `python scripts/doctor_setup.py --env-file .env` first. The doctor separates the two distinct
failures that look identical from the browser:

1. **App credentials incomplete** - `UPSTOX_API_KEY`, `UPSTOX_API_SECRET` or `UPSTOX_REDIRECT_URI`
   unset. The provider cannot complete authorisation, so requests are refused before any network
   call. Fix in your Upstox developer app; the app's redirect URI must equal `UPSTOX_REDIRECT_URI`
   exactly.
2. **Token missing or expired** - `UPSTOX_ACCESS_TOKEN` absent, or present but expired. Upstox
   access tokens are short lived and are not derivable from the API key alone. The doctor reads the
   token's own unverified payload for its `exp` and reports `expires_at` plus `is_expired`, so an
   expired token is distinguishable from a missing one without any network call.

The doctor cannot validate that a present, unexpired token is *accepted* by Upstox; only a live
request can do that. Its job is to eliminate the configuration causes first.
