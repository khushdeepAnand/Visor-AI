# StockPilot AI — Prediction Engine (reference)

How the next-day price-range forecast is actually produced, end to end: data,
features, model stack, interval, and the statistical *assessment* block that
turns raw model output into the numbers shown in the UI (probability up/down,
expected move, expected volatility, market regime, model agreement, confidence
score, data quality, and the "why this range?" explanation).

Every number below is computed from model output or indicator data observable
at the latest bar. Nothing in the assessment is hardcoded, and the code tests
enforce that (see `tests/test_forecast_assessment.py`).

---

## 1. Inputs

- **OHLCV history** for the symbol via `services/market_data/manager.py`
  (Upstox primary, NSE public / yfinance fallbacks when the token is invalid —
  see `docs/data-pipeline.md`).
- The engine cleans the frame (`_coerce_market_data`), rejects missing/zero
  rows, aligns feature timestamps against the canonical data timestamp, and
  rejects a "raw trailing row" that is not part of the cleaned frame.
- Indicator enrichment (SMA/EMA/RSI/MACD/BB/ATR/ADX/CMF/MFI/OBV/CCI/Donchian/
  volume stats and returns-skew — full column list in `forecasting/indicators.py`)
  computes the columns the supervised target and the regime report consume.

## 2. Model stack

`forecasting/interval_forecast.py` fits an ensemble under a standing constraint
**LightGBM + CatBoost only** (no XGBoost, no deep learning):

| Member | Status on this machine | Notes |
|---|---|---|
| CatBoost | active (1.2.10) | gradient-boosted trees, quantile-capable |
| LightGBM | inactive until `brew install libomp` | code fully wired; no code change needed, only the native dependency |

The engine additionally fits quantile-regression / ARIMA-style *baselines* so
that every published range is benchmarked against persistence rather than
claimed in a vacuum. (The original upgrade spec named XGBoost and an LSTM
variant; the earlier explicit model constraint removed XGBoost and TensorFlow
from this project, so those members are documented here as **excluded by
decision**, not forgotten.)

## 3. Interval construction

- The target is multi-horizon next-close prediction; the published range is
  the calibrated interval `[low, median, high]` at a nominal 80% confidence
  ("8 of 10" contract).
- Bands use conformal/quantile calibration on the **untouched test fold**;
  the walk-forward method, splits and nominal coverage are never computed on
  data the model was fit on.
- `forecast_range()` returns the primary horizon plus an optional
  `multi_horizon.horizons[]` ladder; each entry carries its own
  `assessment`.
- The ladder is compared after all direct models finish. The consistency check
  never changes a corridor: it flags material direction conflicts, median-path
  reversals, and a longer-horizon interval narrowing by more than 5%.

### Cross-horizon confidence

`multi_horizon.consistency` starts at 100 and applies transparent penalties:
40 points for opposing material directions, up to 30 for median-path reversals,
and up to 20 for unexpected interval narrowing. Median moves inside ±0.25% are
treated as neutral. Scores are labelled high (80–100), moderate (55–79), or low
(below 55). Fewer than two released horizons produce `unavailable`, not a
guessed score. The public API exposes this as `horizon_consistency`, and the UI
labels it as a confidence signal rather than a recommendation.

## 4. The assessment block

Built by `_build_assessment(...)` per horizon and attached as
`result["assessment"]` (and per-ladder-entry):

| Field | Definition | Gated? |
|---|---|---|
| `probability.up / .down` | share of ensemble members whose next-bar prediction sits above the reference price, **calibrated by measured directional (balanced) accuracy on the test fold**, clipped to `[0.05, 0.95]` | publishable only |
| `expected_return_pct` | `(median / current_price - 1) × 100` | publishable only |
| `expected_volatility_pct` | recent one-bar scale (`recent_scale`) as % of current price | publishable only |
| `market_regime` | rule-based: trend score from close-vs-SMA20/50, EMA20-vs-EMA50, MACD histogram sign; ADX ≥ 25 marks trending; volatility = ATR% percentile in its own recent history | always |
| `model_agreement` | `1 − dispersion / max(3 × expected vol, 0.05%)`, clipped to `[0, 1]`; dispersion = median absolute deviation of member next-bar predictions as % of price | publishable only |
| `confidence_score` | 0–100 weighted: evidence 40, calibration 25 (5 pts lost per pp of coverage gap), skill 15 (scaled MAE improvement over naive), agreement 10, data quality 10; **drift caps at 30, blocked status caps at 20** | always |
| `data_quality` | 0–1 from observable input defects (missingness −1×, zero-volume −1.5×, duplicates −0.5, misalignment/review −0.25 each); levels strong/limited/blocked | always |
| `explanation` | bullet list mapping the lean to indicators (RSI, MACD histogram, distance to 20-day average, regime label, expected move, agreement, liquidity) | always |

*Gating:* statistics derived from model output (probability, expected return,
expected volatility, agreement) are `null` unless the horizon's status is in
`ASSESSMENT_PUBLISHABLE` = {`model_supported`, `baseline_only`, `low_evidence`,
`available`}. Context (regime, data quality, confidence, explanation) is always
published so a blocked user still sees *why* — the same rule is re-applied in
`services/forecast_presentation.py::public_forecast` so the HTTP payload can
never leak model statistics past a blocked gate.

## 5. Presentation & frontend

- `public_forecast()` maps the engine result to the public contract
  (research range, confidence, uncertainty, scenarios, expected-move block,
  trust badges) plus the gated `assessment` block.
- `frontend/components/ForecastCard.tsx` renders a **Model assessment**
  section (probability bar, expected move/volatility, regime badge, agreement,
  confidence score, "Why this range?" bullets) below the corridor and before
  the session ladder. Types live in `frontend/lib/api.ts`
  (`ForecastAssessment`, `ProbabilityReport`, `RegimeReport`,
  `ModelAgreementReport`, `ConfidenceScoreReport`, `DataQualityReport`,
  `ExplanationBlock`).

## 6. Verification

- `tests/test_forecast_assessment.py` — unit tests for each helper (clipping,
  agreement thresholds, regime labels, quality penalties, confidence caps,
  blocked gating) plus end-to-end checks that `forecast_range` and
  `public_forecast` emit and gate the block correctly.
- `tests/test_multi_horizon_ranges.py` — independent target dates and ranges,
  graceful short-history degradation, plus supportive and conflicting
  cross-horizon consistency cases.
- `tests/test_range_backtest.py` — walk-forward harness, naive-beat + coverage
  gate, and the intraday **range-containment** metric (next-bar high/low inside
  the band), which is the sharpness-anchored counter to close coverage.
- Frontend: `frontend/tests/ForecastCard.test.tsx` renders the assessment
  section and verifies the no-assessment payload renders nothing extra;
  `npm run typecheck` is clean.
