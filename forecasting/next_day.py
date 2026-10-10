"""Daily-only next-session research challengers. No production routing side effects.

All optional signals are joined by their *availability* timestamp, not trading
date. Tomorrow's overnight futures return cannot be used at today's close.
Model fitting, HAR fitting and gap fitting use train only; CQR uses a disjoint
calibration fold; all comparisons use exactly the existing pipeline's test origins.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import importlib
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from forecasting.evaluation_harness import PinballLossResult, diebold_mariano_test, evaluate_tier, pinball_loss
from forecasting.promotion_gate import run_all_tier_gates, promotion_gate_summary
from forecasting.data_tier_router import assign_tier

VERSION = "next-day-quantile-v2"
OPTIONAL = ("overnight_index_return", "overnight_futures_return", "atm_move", "late_momentum", "late_volume_share", "intraday_rv", "vwap_distance")
EVENTS = ("earnings", "major_news", "circuit_limit")


def options_implied_move(chain: pd.DataFrame, *, spot: float, as_of: Any) -> dict[str, Any] | None:
    """Nearest unexpired ATM straddle, synchronized CE/PE, scaled by sqrt(T).

    This is a premium-based expected-move proxy, NOT a probability-calibrated
    interval. Coverage-matched evaluation must calibrate its correction on past
    data. Expiry is the actual expiry timestamp, not midnight on expiry day.
    """
    if spot <= 0 or chain.empty:
        return None
    required = {"available_at", "expiry", "strike", "call_mid", "put_mid"}
    if not required.issubset(chain):
        raise ValueError(f"Options require columns {sorted(required)}")
    now = pd.Timestamp(as_of)
    if now.tzinfo is None:
        raise ValueError("Options as_of must be timezone-aware")
    now = now.tz_convert("UTC")
    rows = chain.copy()
    rows["available_at"] = pd.to_datetime(rows["available_at"], utc=True)
    rows["expiry"] = pd.to_datetime(rows["expiry"], utc=True)
    age = now - rows["available_at"]
    rows = rows[(age >= pd.Timedelta(0)) & (age <= pd.Timedelta(minutes=15)) & (rows["expiry"] > now)]
    for name in ("strike", "call_mid", "put_mid"):
        rows[name] = pd.to_numeric(rows[name], errors="coerce")
    rows = rows.dropna(subset=["strike", "call_mid", "put_mid"])
    rows = rows[(rows["call_mid"] > 0) & (rows["put_mid"] > 0) & (rows["strike"] > 0)]
    if rows.empty:
        return None
    rows = rows[rows["expiry"] == rows["expiry"].min()]
    rows = rows.sort_values("available_at").drop_duplicates("strike", keep="last")
    row = rows.loc[(rows["strike"] - spot).abs().idxmin()]
    days = max((row["expiry"] - now).total_seconds() / 86400, 1.0)
    return {"atm_move": float((row["call_mid"] + row["put_mid"]) / spot / np.sqrt(days)),
            "available_at": row["available_at"], "expiry": row["expiry"], "strike": float(row["strike"]),
            "basis": "ATM straddle / spot / sqrt(calendar days); coverage correction fitted on calibration only"}


def intraday_summaries(bars: pd.DataFrame) -> pd.DataFrame:
    """Completed NSE sessions only, available at 15:30 IST, no partial days.

    Volume profile here means the late-session share and VWAP distance; it is
    not a fabricated tick-level volume-at-price profile from daily OHLC.
    """
    if bars.empty:
        return pd.DataFrame()
    frame = bars.copy().sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Intraday bars require timezone-aware bar-end timestamps")
    frame.index = frame.index.tz_convert("Asia/Kolkata")
    rows = []
    for day, session in frame.groupby(frame.index.normalize()):
        opening = day + pd.Timedelta(hours=9, minutes=15)
        closing = day + pd.Timedelta(hours=15, minutes=30)
        session = session[(session.index > opening) & (session.index <= closing)]
        if len(session) < 10 or session.index[-1] != closing or session.index[0] > opening + pd.Timedelta(minutes=15):
            continue
        late = session[session.index >= closing - pd.Timedelta(minutes=30)]
        volume = float(session.Volume.sum())
        if volume <= 0 or len(late) < 2:
            continue
        close = float(session.Close.iloc[-1])
        vwap = float(((session.High + session.Low + session.Close) / 3 * session.Volume).sum() / volume)
        rows.append({"available_at": closing.tz_convert("UTC"), "late_momentum": close / float(late.Close.iloc[0]) - 1,
                     "late_volume_share": float(late.Volume.sum() / volume),
                     "intraday_rv": float(np.square(np.log(session.Close).diff()).sum()),
                     "vwap_distance": close / vwap - 1})
    return pd.DataFrame(rows).set_index("available_at") if rows else pd.DataFrame()


def build_features(daily: pd.DataFrame, signals: pd.DataFrame | None = None) -> pd.DataFrame:
    """Daily bars indexed by their actual close availability timestamp (UTC)."""
    if not isinstance(daily.index, pd.DatetimeIndex) or daily.index.tz is None:
        raise ValueError("Daily data requires timezone-aware close timestamps")
    if not daily.index.is_monotonic_increasing or daily.index.has_duplicates:
        raise ValueError("Daily data must be strictly chronological")
    if pd.Index(daily.index.tz_convert("Asia/Kolkata").date).has_duplicates:
        raise ValueError("Next-day specialist accepts one completed bar per session, not intraday bars")
    close = daily.Close.astype(float)
    gap = daily.Open / close.shift(1) - 1
    returns = close.pct_change()
    rv = returns ** 2
    out = pd.DataFrame(index=daily.index)
    out["return_1"] = returns
    out["return_3"] = close.pct_change(3)
    out["gap_last"] = gap
    out["gap_mean_20"] = gap.rolling(20).mean()
    out["gap_std_20"] = gap.rolling(20).std()
    for window in (1, 2, 3):
        out[f"range_{window}"] = ((daily.High - daily.Low) / close).rolling(window).mean()
        out[f"rv_{window}"] = rv.rolling(window).mean()
    out["rv_5"] = rv.rolling(5).mean()
    out["rv_22"] = rv.rolling(22).mean()
    out["intraday_return"] = close / daily.Open - 1
    out["close_location"] = (close - daily.Low) / (daily.High - daily.Low).replace(0, np.nan)
    out["volume_ratio"] = daily.Volume / daily.Volume.rolling(20).mean().replace(0, np.nan)
    # Exchange indices have no traded volume. Preserve missingness rather than
    # dropping every index origin or inventing a volume signal.
    out["volume_ratio_available"] = out["volume_ratio"].notna().astype(float)
    out["volume_ratio"] = out["volume_ratio"].fillna(0)
    out["close_location_available"] = out["close_location"].notna().astype(float)
    out["close_location"] = out["close_location"].fillna(.5)
    if signals is not None and not signals.empty:
        if not isinstance(signals.index, pd.DatetimeIndex) or signals.index.tz is None or signals.index.has_duplicates:
            raise ValueError("Signals require unique timezone-aware availability timestamps")
        unknown = set(signals.columns) - set(OPTIONAL)
        if unknown:
            raise ValueError(f"Unknown signals: {sorted(unknown)}")
        for name in signals:
            aligned = pd.merge_asof(pd.DataFrame(index=out.index), signals[[name]].dropna().sort_index(),
                                    left_index=True, right_index=True, direction="backward", tolerance=pd.Timedelta(hours=24))
            values = aligned[name].replace([np.inf, -np.inf], np.nan)
            out[f"{name}_available"] = values.notna().astype(float)
            out[name] = values.fillna(0)
    return out.replace([np.inf, -np.inf], np.nan)


def _scores(actual: np.ndarray, prediction: np.ndarray, confidence: float) -> np.ndarray:
    low, _, high = prediction.T
    return np.asarray(high - low + 2 / (1 - confidence) * (np.maximum(low - actual, 0) + np.maximum(actual - high, 0)), dtype=float)


def _calibrate(raw: np.ndarray, actual: np.ndarray, confidence: float) -> float:
    scores = np.maximum(raw[:, 0] - actual, actual - raw[:, 2])
    rank = int(np.ceil((len(scores) + 1) * confidence))
    if rank > len(scores):
        raise ValueError("Insufficient calibration observations for finite-sample CQR")
    return max(0.0, float(np.sort(scores)[rank - 1]))


def evaluate_next_day(symbol: str, daily: pd.DataFrame, *, signals: pd.DataFrame | None = None,
                      events: pd.DataFrame | None = None, confidence: float = .80,
                      sequence_candidate: bool = False, registry_uri: str | None = None,
                      evidence_sha256: str = "") -> dict[str, Any]:
    """Fit fixed candidates before test, compare to actual shared h=1 output.

    Test results cannot select an ensemble weight. Every candidate gets a
    multiplicity-adjusted DM check in addition to the unmodified tier gates.
    No receipt is written and no CQR control assignment is changed here.
    """
    from forecasting.interval_forecast import _coerce_market_data, _retained_features, _run_horizon, MIN_SUPERVISED_FULL
    from indicators import add_indicators

    if not .60 <= confidence <= .95:
        raise ValueError("confidence must be between .60 and .95")
    features = build_features(daily, signals)
    canonical = _coerce_market_data(daily)
    if not canonical.index.equals(daily.index):
        raise ValueError("Invalid daily OHLCV rows must be resolved before research evaluation")
    local = pd.DatetimeIndex(daily.index).tz_convert("Asia/Kolkata")
    if not ((local.hour == 15) & (local.minute == 30)).all():
        raise ValueError("Daily origins must be actual completed session closes at 15:30 IST")
    enriched = add_indicators(canonical)
    shared = _run_horizon(symbol, canonical, enriched, daily,
                          _retained_features(enriched, MIN_SUPERVISED_FULL + 1), 1, confidence, "5y", "1D")
    # Match the comparator's exact origins. Purge labels at each fold boundary.
    target = daily.Close.shift(-1) / daily.Close - 1
    target_gap = daily.Open.shift(-1) / daily.Close - 1
    target_rv = daily.Close.pct_change().pow(2).shift(-1)
    valid = features.dropna().index.intersection(target.dropna().index)
    cal_index = pd.Index(shared["X_cal_index"]).intersection(valid)
    test_index = pd.Index(shared["test_index"]).intersection(valid)
    if len(cal_index) < 20 or len(test_index) < 10:
        raise ValueError("Insufficient aligned calibration/test evidence")
    train_index = valid[valid < cal_index[0]]
    train_index = train_index[:-1]  # label at last train origin must not overlap calibration
    cal_index = cal_index[cal_index < test_index[0]][:-1]
    if len(train_index) < 100:
        raise ValueError("Next-day specialist requires at least 100 purged training origins")
    assignment = assign_tier(usable_days=len(canonical.loc[:test_index[0]]), validation_samples=len(test_index))
    X = features.copy()
    gap_columns = [str(c) for c in X if str(c).startswith("gap_") or str(c).startswith("overnight_")]
    gap_model = make_pipeline(StandardScaler(), Ridge(alpha=10))
    gap_model.fit(X.loc[train_index, gap_columns], target_gap.loc[train_index])
    X["predicted_next_gap"] = gap_model.predict(X[gap_columns].fillna(0))
    # HAR-RV fitted only on train; short daily/weekly/monthly log variance.
    har_columns = ["rv_1", "rv_5", "rv_22"]
    log_rv = X[har_columns].clip(lower=1e-10).apply(np.log)
    har = make_pipeline(StandardScaler(), Ridge(alpha=10))
    har.fit(log_rv.loc[train_index], np.log(target_rv.loc[train_index].clip(lower=1e-10)))
    har_sigma = np.sqrt(np.exp(np.clip(har.predict(log_rv.fillna(np.log(1e-10))), -25, 0)))
    alpha = 1 - confidence
    quantiles = (alpha / 2, .5, 1 - alpha / 2)
    option_columns = [str(c) for c in X if str(c).startswith("atm_move")]
    base_columns = [str(c) for c in X if c not in option_columns]
    candidates: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    registry_artifacts = []

    def gbm(columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
        predictions = []
        for q in quantiles:
            model = GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=120,
                        max_depth=2, min_samples_leaf=15, learning_rate=.04, random_state=42)
            model.fit(X.loc[train_index, columns], target.loc[train_index])
            if registry_uri:
                from forecasting.mlflow_registry import register_candidate
                registry_artifacts.append(register_candidate(model, X.loc[train_index, columns], uri=registry_uri,
                    symbol=symbol, candidate=f"gbm-q{q:.2f}-{'options' if columns != base_columns else 'base'}",
                    evidence_sha256=evidence_sha256))
            predictions.append(model.predict(X.loc[cal_index.append(test_index), columns]))
        raw = np.sort(np.column_stack(predictions), axis=1)
        return raw[:len(cal_index)], raw[len(cal_index):]

    cal, test = gbm(base_columns)
    candidates["gbm"] = (cal, test)
    # Separate volatility-width challenger, never silently inserted in GBM.
    for_cal = pd.Series(har_sigma, index=X.index).loc[cal_index].to_numpy()
    for_test = pd.Series(har_sigma, index=X.index).loc[test_index].to_numpy()
    from scipy.stats import norm
    z = float(norm.ppf(1 - alpha / 2))
    def widen(raw: np.ndarray, sigma: np.ndarray) -> np.ndarray:
        result = raw.copy()
        result[:, 0] = np.minimum(result[:, 0], result[:, 1] - z * sigma)
        result[:, 2] = np.maximum(result[:, 2], result[:, 1] + z * sigma)
        return result
    candidates["gbm_har_rv"] = (widen(cal, for_cal), widen(test, for_test))
    if option_columns and X.loc[train_index, "atm_move_available"].sum() >= 100:
        candidates["gbm_options"] = gbm([str(c) for c in X.columns])
    sequence_status = "not_requested"
    if sequence_candidate:
        # Optional installed research dependency only. Training windows end at
        # each origin and cannot cross into the next fold's labels.
        try:
            tf = importlib.import_module("tensorflow")
        except ImportError:
            sequence_status = "unavailable_tensorflow_not_installed"
        else:
            tf.keras.utils.set_random_seed(42)
            scaler = StandardScaler().fit(X.loc[train_index, base_columns])
            scaled = scaler.transform(X[base_columns].fillna(0))
            def sequences(index: pd.Index) -> np.ndarray:
                return np.stack([scaled[int(i) - 9:int(i) + 1] for i in X.index.get_indexer(index)])
            train_seq = train_index[X.index.get_indexer(train_index) >= 9]
            model = tf.keras.Sequential([tf.keras.layers.Input((10, len(base_columns))), tf.keras.layers.LSTM(16), tf.keras.layers.Dense(1)])
            model.compile(optimizer="adam", loss="mae")
            model.fit(sequences(train_seq), target.loc[train_seq].to_numpy(), epochs=10, batch_size=32, shuffle=False, verbose=0)
            med_cal = model.predict(sequences(cal_index), verbose=0).ravel()
            med_test = model.predict(sequences(test_index), verbose=0).ravel()
            # Residual interval calibrated on the same fold, no test tuning.
            candidates["lstm"] = (np.column_stack([med_cal] * 3), np.column_stack([med_test] * 3))
            sequence_status = "evaluated_not_automatically_ensemble_member"

    shared_frame = pd.DataFrame({"actual": shared["test_actual"], "median": shared["test_median"],
                                  "low": shared["test_low"], "high": shared["test_high"]}, index=shared["test_index"]).loc[test_index]
    actual_price = shared_frame.actual.to_numpy()
    references = daily.Close.loc[test_index].to_numpy()
    baseline = shared_frame[["low", "median", "high"]].to_numpy()
    baseline_loss = _scores(actual_price, baseline, confidence) / references
    event_masks: dict[str, np.ndarray] = {}
    realized_gap = (daily.Open.shift(-1) / daily.Close - 1).loc[test_index].abs()
    threshold = features.loc[test_index, "gap_std_20"].to_numpy() * 2
    event_masks["large_overnight_gap"] = realized_gap.to_numpy() > np.maximum(.02, threshold)
    if events is not None:
        for name in EVENTS:
            if name in events:
                # Events are evaluation labels for the target session, never model inputs.
                event_masks[name] = events[name].shift(-1).reindex(test_index).eq(1).to_numpy()
    results: dict[str, dict[str, Any]] = {}
    losses = {}
    for name, (cal_raw, test_raw) in candidates.items():
        q = _calibrate(cal_raw, target.loc[cal_index].to_numpy(), confidence)
        predicted = test_raw.copy()
        predicted[:, 0] -= q
        predicted[:, 2] += q
        predicted = np.maximum(.01, references[:, None] * (1 + predicted))
        forecasts = [{"actual": float(y), "low": float(p[0]), "median": float(p[1]), "high": float(p[2])} for y, p in zip(actual_price, predicted)]
        naive = [{"actual": float(y), "median": float(r)} for y, r in zip(actual_price, references)]
        # Reuse harness metrics, replacing its approximate pinball interpolation
        # and naive DM with exact quantile losses and paired shared-model scores.
        metrics = evaluate_tier("overall", [symbol], forecasts, confidence, baseline_forecasts=naive)
        dm = diebold_mariano_test(_scores(actual_price, predicted, confidence) / references, baseline_loss,
                                  h=1, alternative="less", loss_function="relative_winkler")
        losses[name] = _scores(actual_price, predicted, confidence) / references
        hits = (actual_price >= predicted[:, 0]) & (actual_price <= predicted[:, 2])
        conditional = {"overall": float(hits.mean())}
        counts = {}
        for event, mask in event_masks.items():
            counts[event] = int(mask.sum())
            if mask.sum() >= 10:
                conditional[event] = float(hits[mask].mean())
        metrics = replace(metrics, baseline_winkler=float(_scores(actual_price, baseline, confidence).mean()),
                          diebold_mariano=dm, conditional_coverage=conditional,
                          pinball_losses=[PinballLossResult(qt, pinball_loss(actual_price, predicted[:, k], qt), len(predicted)) for k, qt in enumerate(quantiles)])
        gates = promotion_gate_summary(run_all_tier_gates({assignment.tier.value: replace(metrics, tier=assignment.tier.value), "overall": metrics}))
        option_benchmark = None
        if "atm_move" in X:
            cal_move = X.loc[cal_index, "atm_move"].to_numpy()
            test_move = X.loc[test_index, "atm_move"].to_numpy()
            cal_mask = cal_move > 0
            mask = test_move > 0
            if cal_mask.sum() >= 20 and mask.sum() >= 10:
                correction = _calibrate(np.column_stack([-cal_move[cal_mask], np.zeros(cal_mask.sum()), cal_move[cal_mask]]),
                    target.loc[cal_index].to_numpy()[cal_mask], confidence)
                # Additive conformal correction in return units, same nominal coverage.
                widths = test_move[mask] + correction
                option_pred = references[mask, None] * np.column_stack([1 - widths, np.ones(mask.sum()), 1 + widths])
                option_hits = (actual_price[mask] >= option_pred[:, 0]) & (actual_price[mask] <= option_pred[:, 2])
                model_cov = float(hits[mask].mean())
                market_cov = float(option_hits.mean())
                model_score = float(_scores(actual_price[mask], predicted[mask], confidence).mean())
                market_score = float(_scores(actual_price[mask], option_pred, confidence).mean())
                option_benchmark = {"samples": int(mask.sum()), "coverage": market_cov,
                    "candidate_coverage": model_cov, "winkler": market_score, "candidate_winkler": model_score,
                    "coverage_matched": abs(model_cov - market_cov) <= .05,
                    "passed": abs(model_cov - market_cov) <= .05 and model_score <= market_score}
        event_evidence_complete = all(counts.get(event, 0) >= 10 for event in ("large_overnight_gap", *EVENTS))
        eligible = bool(gates["overall_passed"] and dm.p_value < .05 / len(candidates) and event_evidence_complete
                        and (not option_columns or option_benchmark is not None and option_benchmark["passed"]))
        results[name] = {"samples": len(test_index), "coverage": metrics.coverage, "winkler": metrics.winkler_score,
            "baseline_winkler": metrics.baseline_winkler, "mase_vs_naive": metrics.mase,
            "pinball": [asdict(item) for item in metrics.pinball_losses], "dm_vs_shared": asdict(dm),
            "conditional_coverage": conditional, "event_samples": counts, "event_evidence_complete": event_evidence_complete,
            "options_benchmark": option_benchmark, "existing_gates": gates, "eligible_for_promotion_review": eligible}
    for name, result in results.items():
        if name != "gbm":
            member_dm = diebold_mariano_test(losses[name], losses["gbm"], h=1, alternative="less", loss_function="relative_winkler")
            result["dm_vs_specialist_gbm"] = asdict(member_dm)
            result["adds_value_over_gbm"] = bool(member_dm.dm_statistic < 0 and member_dm.p_value < .05 / len(candidates))
            result["eligible_for_promotion_review"] = bool(result["eligible_for_promotion_review"] and result["adds_value_over_gbm"])
    return {"version": VERSION, "symbol": symbol, "horizon_sessions": 1, "timeframe": "1D",
            "tier": assignment.tier.value, "evidence_grade": assignment.evidence_grade,
            "split": {"train": len(train_index), "calibration": len(cal_index), "test": len(test_index), "purge_sessions": 1},
            "train_end": str(train_index[-1]), "calibration_start": str(cal_index[0]), "calibration_end": str(cal_index[-1]),
            "test_start": str(test_index[0]), "test_end": str(test_index[-1]),
            "signals": list(features.columns), "sequence_candidate": sequence_status,
            "candidates": results, "registry_artifacts": registry_artifacts, "published": False,
            "promotion": "Research report only; signed production receipt and independent promotion review required"}
