# StockPilot AI — Data Pipeline

Where prices come from, what happens when a provider fails, how data quality
is scored, and the current operational state (provider mode, token status,
scheduler).

## 1. Provider architecture

`services/market_data/manager.py` sources OHLCV + quotes:

- **Upstox (primary)** — used when the configured access token is accepted.
- **NSE public quotes / yfinance history (fallback)** — used when Upstox
  rejects the token, so the UI keeps working with honest provenance instead of
  503s.

`.env` controls the behaviour:

| Key | Current | Meaning |
|---|---|---|
| `STOCKPILOT_PROVIDER_MODE` | `FALLBACK_ALLOWED` | fallbacks are legal when Upstox is down / token invalid |
| `STOCKPILOT_QUOTE_CACHE_SECONDS` | `15` | same quote cached for 15 s to avoid burst provider calls |
| `STOCKPILOT_ENABLE_SCHEDULER` | `true` | scheduler + drift monitor start with the app |

When a fallback serves a value, the response carries `provider=fallback` and
an `as_of` provenance marker that the UI shows as "As of …" / "Stale data"
pills — the user is never shown a fallback number presented as a live broker
quote.

## 2. Refresh (manual, needs your Upstox login/OTP)

`UPSTOX_ACCESS_TOKEN` in `.env` expired earlier and returns `UDAPI100050`
(HTTP 401). Refreshing it requires the Upstox login/OTP flow
(`CONFIGURE_UPSTOX.bat` / instructions at `UPSTOX_CONFIGURATION.md`). Until
then `FALLBACK_ALLOWED` keeps NSE public quotes flowing.

> Note: `.env` line 122 breaks a bare `source .env` in some shells — load it
> with python-dotenv (`python-dotenv` is a project dependency) or export the
> values explicitly.

## 3. Fit-and-score data hygiene

Inside the forecast engine (`forecasting/interval_forecast.py`):

- `_coerce_market_data` enforces numeric OHLCV on a clean DatetimeIndex;
  missing/zero rows are rejected and counted.
- Feature and data timestamps are aligned; a *raw trailing row* (not part of
  the cleaned frame) can never become the reference price or timestamp.
- The `data_quality` assessment block scores inputs 0–1:
  `1 − missingness − 1.5 × zero-volume ratio − duplicates − misalignment/
  corporate-action penalties`, carrying explicit `notes[]` for any defect
  above threshold. Levels: `strong` / `limited` / `blocked`.
- The daily drift monitor compares recent prediction error distribution
  against the historical baseline and caps confidence (`confidence ≤ 30`) or
  blocks the status (`drift_blocked`) when drift is detected.

## 4. Scheduler

Retraining runs daily at **18:30 IST** for the tracked universe
(RELIANCE, TCS, INFY, HDFCBANK). Forecasts and backtests are never computed on
data that leaked into the fit window (see `docs/model-evaluation.md`).

## 5. Known current state (operational)

| Item | State |
|---|---|
| Upstox token | expired → fallback mode serving NSE public quotes |
| LightGBM | inactive on this Mac (no `libomp`); CatBoost active |
| Ledger `database/stockpilot.db` `prediction_history` | empty — first scheduled retrain populates it |
| NIFTY 50 symbol via yfinance fallback | fails (symbol not found) → 503 persists for that one symbol; individual scrips work |
| CORS | restricted to `127.0.0.1:3000` |