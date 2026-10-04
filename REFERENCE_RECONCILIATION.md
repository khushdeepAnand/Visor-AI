# Reference Reconciliation

## Authoritative Release References

For the v6.1 secure candidate, use the following as current release references: `README.md`, `QUICK_START_WINDOWS.md`, `UPSTOX_CONFIGURATION.md`, `ARCHITECTURE.md`, `SECURITY.md`, `SECURITY_REVIEW.md`, `TESTING.md`, `RELEASE_CHECKLIST.md`, `VERIFICATION_RESULTS.md`, `CHANGELOG.md`, `ADMIN_OPERATIONS.md`, `REGULATORY_REVIEW_REQUIRED.md`, `UPGRADE_VERIFICATION.md`, and this document.

Root continuation, implementation, activation, deployment, design, interview, and completed-work notes are historical or specialized references. They are not release approval or current verification evidence unless an authoritative document explicitly incorporates them.

## Reconciled Facts

| Topic | Current reference | Reconciled statement |
| --- | --- | --- |
| Product/version | API/package metadata and authoritative docs | v6.1 secure candidate; internal application metadata is 6.1.0 |
| Python | Windows launchers and `runtime.txt` | Exactly Python 3.12 for the supported launcher workflow |
| Frontend dependencies | `frontend/package.json` and lock | Next.js 16.3.3 with lock-based `npm ci` restoration |
| Deployment | launcher scripts | Single local workstation; backend and frontend bind to loopback |
| Trading | broker guard and paper engine | Research and paper simulation only; no broker order actions |
| Provider mode | runtime configuration | `LIVE_ONLY` default; demo requires explicit `OFFLINE_DEMO` |
| Administrators | `services/admin_registry.py` runtime constant | At most three configured identities; normal registration plus bootstrap is required |
| Secrets/data | release rules | `.env`, local databases/sidecars, caches, logs, fitted models, builds, and generated reports are not release assets |
| Verification | `UPGRADE_VERIFICATION.md` and `VERIFICATION_RESULTS.md` | Historical completed evidence is distinct from checks rerun for the current change |
| External systems | provider verification record | Not verified without current credential/network evidence |
| Legal status | `REGULATORY_REVIEW_REQUIRED.md` | Blocked pending qualified human review; no legal approval claimed |

## Known Reference Conflicts

- Historical files may mention Python 3.13, Next.js 15, older test totals, superseded folder names, or incomplete build status. Those statements are not current release instructions.
- Some source comments describe a two-administrator ceiling, while the enforced runtime constant and tests permit three. Operational documentation follows the enforced value of three; changing the policy requires a coordinated code, test, configuration-example, and documentation decision.
- Prior 12-stage verifier results remain valid only as dated historical evidence. The current script contains 14 stages after adding release-input and focused security gates; it has not passed until a new complete run is recorded.

## Reconciliation Rule

When references disagree, prefer executable metadata and enforced runtime behavior for technical facts, then update all authoritative documents in the same release. Never use precedence to manufacture evidence: tests not run remain not run, external checks without live evidence remain not verified, and legal review without a qualified sign-off remains blocked.

## v7.1 Additions and Conflict Resolutions

| Topic | Authoritative reference | Reconciled statement |
| --- | --- | --- |
| Administrator ceiling | `admin_registry.py` (`MAX_ADMINS = 3`), `ADMIN_OPERATIONS.md` | The ceiling is three administrators. Notes describing a two-administrator ceiling are historical and superseded. `/api/v1/admin/overview` reports `max_admins` from the constant, so code and docs cannot drift. |
| Quantile regression | `model_registry.py`, `FORECAST_CONTRACT.md`, `MODEL_CARD.md` | Registered as `experimental`, challenger-only. Its bounds are diagnostic and do not determine published bounds. No quantile fusion is implemented or claimed. |
| Motion values | `frontend/lib/motionTokens.ts`, `MOTION_SYSTEM.md` | `MOTION_TOKENS` in code is authoritative; the doc table mirrors it. Component-level hard-coded durations are defects. |
| Forecast suppression | `services/forecast_guardrails.py`, `FORECAST_CONTRACT.md` | `forecast_disabled_by_operator` is the only operator-suppression code, and suppression is always scoped, reasoned, and expiring. |
| Low-history coverage | `forecasting/pooled_cross_section.py`, `MODEL_CARD.md` | The pooled cross-sectional model is a flag-gated challenger for short-history instruments. It does not change the production interval path. |
| Verification evidence | `UPGRADE_VERIFICATION.md` | The v7.1 additions were verified by their own unit suites only. Live broker validation, frontend build, and Playwright runs remain NOT VERIFIED in this environment. |

## 2026-09-09 - v8 upgrade pass (v7.1 -> v8)

### Part D references, and what was actually built

| Reference | Taken | Deliberately not taken |
| --- | --- | --- |
| Screener.in, Chartink, Tickertape, StockEdge | Field catalogue with an explicit basis and window per metric, per-symbol exclusion reasons, saved screens | Pre-built "buy" screens, scores, star ratings |
| Moneycontrol, Trendlyne | Movers, volume leaders, attributed headlines, explicit session state | Sentiment-as-signal, target prices, analyst calls |
| Sensibull, AlgoTest, Stockmock, Streak | Multi-leg expiry payoff, breakevens, bounded/unbounded tails, optional non-predictive marks | Strategy recommendations, margin estimates, order placement |
| Stoxra, Jarvis Invest, Quantiply, IMATE | Nothing beyond the above | Advisory framing and automated execution, which require SEBI registration |

Everything above is descriptive. No reference product's advisory, execution or auto-trading
behaviour was copied, and the paper-only boundary is unchanged.

## 2026-09-09 - v9 upgrade pass (v8 -> v9)

### v9 reference reconciliation

The v9 prompt named four public repositories as references: `psatam21/alphaengine`,
`mirajgodha/options`, `NeuroTechh/OpenQuant`, `marketcalls/openengine`.

**None of them were fetched or read during this pass** - the build sandbox has
no network access. No code, naming, or structure was copied from them. The
strategy builder, backtester and forward tracker were written against this
repository's own conventions (`services/screener.py`, `derivatives/payoff.py`,
`services/paper_trading_v6.py`).

Where those projects would ordinarily supply a live order router, this build
deliberately stops at `services.paper_trading_v6.place_order`. That divergence
is intentional and permanent, not an unimplemented reference.
