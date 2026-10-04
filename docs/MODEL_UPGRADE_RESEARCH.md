# Model upgrade research — making StockPilot ranges "accurate and trustable"

Research companion to `AUDIT_2026-09-23.md`. Goal: give users ranges that are
**measuredly 8-of-10 correct** (calibrated coverage) **and** wide enough to be
acted on, with an honest path to *better midpoints* where one exists.

---

## Where the system stands today (honest baseline — walk-forward, unseen replay)

- Coverage is calibrated: empirical coverage 0.80–0.94 on the live symbols
  (i.e. ~8–9 of 10 closes land inside the published 80% interval).
- MASE ≈ 1.0: the midpoint is roughly as good as naive persistence on daily 1D
  closes — for these liquid large-caps the *level* is essentially
  unforecastable at a 1-session horizon and no honest model claims otherwise.
- The gate now enforces both halves of trust: never worse than naive (floor 0%)
  and coverage inside 80% ± 15%.

Implication: on daily 1D data the battle is **not** "beat naive by 10%" — that
is unattainable and demanding it only forces dishonest output. The battle is
**(a) better calibrated-and-sharp intervals** and **(b) midpoints that beat
naive where data supports it**. The research below targets both.

---

## What the research says (2021–2026)

### 1. Conformalized Quantile Regression (CQR) — the top upgrade for the interval
Quantile regression (Koenker–Bassett pinball loss) alone gives adaptivity to
heteroscedasticity; *conformalizing* the quantile bands (split-conformal on the
pinball scores) restores finite-sample coverage guarantees. In the IEEE ICITEE
2025 benchmark of interval-forecast methods, **CQR delivered the best coverage
with gradient-boosted trees as base learners** — LSTM was sharper but
systematically under-covered (dangerous for trust).

**Why it fits us:** we already have the calibration machinery; the step is to
fit **native quantile models** (LightGBM `objective="quantile"`,
CatBoost `loss_function="Quantile:alpha=…"`) at α/2 and 1−α/2 and conformalize
those bands instead of (only) conformalizing a mean model. Bands then widen
automatically when the *model* sees high-volatility conditions, not just when
recent MAD says so.

### 2. Distributional / volatility-conditional conformal (DCP, PNAS 2021; TCP, arXiv 2025)
The decisive empirical result for your complaint ("a share moves more than my
range in a second"): with realized volatility as a predictor, **mean-based
conformal coverage collapsed from 90% to ~50% in high-vol regimes**, while
vol-conditioned conformal stayed near 90% across all regimes.

- **DCP** conditions calibration on the volatility state (windowing by
  vol-regime similarity — we already do regime-weighted calibration; extend it
  to condition the width on *predicted* vol).
- **Temporal Conformal Prediction (2025)** uses rolling-window quantile
  regression + a decaying learning rate for the calibration threshold — it
  re-calibrates online and reports *sharpness* alongside coverage. Directly
  maps to our walk-forward retrains.

### 3. Returns-space targets (biggest practical midpoint gain)
Predicting **log-returns** (or sign/size of the move) and converting back to
price is consistently better behaved than regressing price levels on a trending
series: the target is stationary, features scale, and the naive baseline is
restated as "0% move". For daily data this is what the honest edge looks like —
it is small, but it is *real* (not the artifact of fitting a level).

### 4. Volatility forecasting for the width (complement, not competitor)
Width can be driven by a proper vol forecast (simple EWMA realized-vol, or a
GARCH-family fit on the same series; `arch` package) instead of MAD of recent
closes. Expect: same calibrated coverage with *narrower* bands in calm regimes
and *reliably wider* bands in turbulent ones — the "big range exactly when the
chart moves big" behavior users intuitively want.

### 5. Cross-sectional context features
The single most under-used signal for Indian large-caps: index return
(NIFTY), sector peers' moves, and market breadth on the *same bar*. Adds signal
density at zero extra data cost and helps the low-history branch, where
univariate models (AIDAN / ETS / Theta-style) win on <100 observations.

### 6. Timeframe alignment ("make it big" — honestly)
A 1-session 80% band around a daily close is *supposed* to be small — the daily
close just doesn't move far. The honest "bigger" comes from the horizon ladder
(1/3/5/10 sessions — already published via `DEFAULT_HORIZONS`, 1W and 1M bands
are several × wider) and from an **intraday ladder** (1m/5m/15m/1h) where moves
are several × the spread. A single trustable metric to show with the ladder:
*walk-forward coverage per horizon*.

### 7. What to avoid (trust killers)
- **Wide-band inflation**: a band that covers 100% of outcomes is useless and
  is now *rejected by the gate* (over-coverage fails). This is the trap behind
  "Aion Veritas shows a big range".
- **NN/sequence models on daily panels**: the 2025 benchmarks showed sharper
  but *under-covered* intervals (worst failure mode for trust) and no alpha on
  short daily panels.
- **Any claim of directional edge**: gate shows 0 trades and no directional
  accuracy edge; labels must stay `baseline_only`/`model_supported` per the
  existing Phase-B rules.

---

## Prioritized roadmap (effort-ranked)

| # | Change | Expected effect | Effort |
|---|---|---|---|
| 1 | **Enable LightGBM** (`brew install libomp`) — the quantile objective and the base stack both activate immediately | Better midpoints + direct quantile candidates in the ensemble | 5 min |
| 2 | **Quantile families for the band** (LightGBM/CatBoost `alpha=q_low/q_high`), then conformalize on top (CQR) | Width adapts to predicted volatility; coverage stays calibrated | 1–2 days |
| 3 | **Returns-space targets** for the midpoint models, converted back to price | Honest small alpha; stationary targets; cleaner conformal scores | 2–3 days |
| 4 | **Vol forecast drives width** (EWMA realized-vol first, GARCH optional) | Same coverage, narrower in calm, wider in stress | 1 day |
| 5 | **Cross-sectional features** (index + peer returns/vol on same bar) | More signal per bar; helps low-history branch | 1–2 days |
| 6 | **Intraday horizon ladder + per-horizon coverage display** | "Big" ranges that are still calibrated; matches how people actually trade | 2–3 days |
| 7 | **TCP-style online recalibration** (rolling coverage re-check between retrains) | Long-run drift protection for published cards | 2 days |

Everything stays sklearn + LightGBM + CatBoost; XGBoost/TensorFlow remain out
of scope. Every item is measured with the same walk-forward gate — coverage
within 80% ± 15% and midpoint never worse than naive — before it can be labeled
`model_supported`.

## Honest expectation

You will not beat naive by 10% on daily 1D closes for liquid large-caps — no
model family does that honestly. What every item above buys you is
**genuinely calibrated, genuinely sharp intervals** (your "8 of 10" measured
live, not claimed) and small-but-real midpoint edge on the frames where it
exists (longer horizons, lower liquidity, intraday). That combination is what
makes a range product trustable.

Sources consulted: Koenker–Bassett quantile regression; Barber et al.
*Conformalized quantile regression* (CQR); Chernozhukov et al. *Distributional
Conformal Prediction* (PNAS 2021); *Temporal Conformal Prediction* (arXiv
2025); IEEE ICITEE 2025 interval-forecast benchmark; Fraunhofer "mind the
naive" (2025); AIDAN small-series study; SHAP tree-explainability docs.