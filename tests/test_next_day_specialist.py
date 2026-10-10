"""Synthetic fixtures verify mechanics only; they are never promotion evidence."""
import numpy as np
import pandas as pd
import pytest

from forecasting.next_day import build_features, evaluate_next_day, intraday_summaries, options_implied_move


def daily_frame(n=700):
    rng = np.random.default_rng(72)
    index = (pd.bdate_range("2022-01-03", periods=n) + pd.Timedelta(hours=15, minutes=30)).tz_localize("Asia/Kolkata").tz_convert("UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, .01, n)))
    opening = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, .003, n))
    return pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) * 1.01,
                         "Low": np.minimum(opening, close) * .99, "Close": close,
                         "Volume": rng.integers(10000, 20000, n)}, index=index)


def test_features_never_see_future_signals_or_future_daily_bars():
    daily = daily_frame(60)
    signals = pd.DataFrame({"overnight_futures_return": [.03, .99]}, index=[daily.index[40], daily.index[-1] + pd.Timedelta(hours=1)])
    full = build_features(daily, signals)
    prefix = build_features(daily.iloc[:45], signals)
    pd.testing.assert_frame_equal(full.loc[prefix.index], prefix)
    assert full.loc[daily.index[39], "overnight_futures_return_available"] == 0
    assert full.loc[daily.index[40], "overnight_futures_return"] == .03
    assert not full.overnight_futures_return.eq(.99).any()
    assert full.loc[daily.index[44], "overnight_futures_return_available"] == 0


def test_options_nearest_expiry_atm_and_no_future_or_stale_quotes():
    now = pd.Timestamp("2026-03-02T10:00:00Z")
    chain = pd.DataFrame({"available_at": [now] * 3 + [now + pd.Timedelta(minutes=1)],
        "expiry": [now + pd.Timedelta(days=4)] * 2 + [now + pd.Timedelta(days=8)] * 2,
        "strike": [95, 100, 100, 100], "call_mid": [4, 2, 10, 50], "put_mid": [2, 2, 10, 50]})
    result = options_implied_move(chain, spot=101, as_of=now)
    assert result["strike"] == 100
    assert result["atm_move"] == pytest.approx(4 / 101 / 2)
    assert options_implied_move(chain, spot=101, as_of=now + pd.Timedelta(hours=1)) is None


def test_intraday_requires_completed_session_and_excludes_after_close():
    index = pd.date_range("2026-03-02T09:20:00+05:30", "2026-03-02T15:30:00+05:30", freq="5min")
    bars = pd.DataFrame({"Close": np.linspace(100, 101, len(index)), "High": 102, "Low": 99, "Volume": 100}, index=index)
    assert intraday_summaries(bars.iloc[:-1]).empty
    summary = intraday_summaries(bars)
    assert len(summary) == 1
    assert summary.index[0] == pd.Timestamp("2026-03-02T10:00:00Z")
    assert 0 < summary.iloc[0].late_volume_share < 1
    extended = pd.concat([bars, pd.DataFrame({"Close": [999], "High": [999], "Low": [999], "Volume": [99999]}, index=[index[-1] + pd.Timedelta(minutes=5)])])
    pd.testing.assert_frame_equal(summary, intraday_summaries(extended))


def test_real_pipeline_comparison_has_purged_fit_and_fail_closed_events(monkeypatch):
    from forecasting import interval_forecast
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor
    # Keep the shared pipeline runnable and portable; still use its actual
    # stacking, calibration, published-width replay and untouched test fold.
    def bases(X, y, **kwargs):
        return {"ridge": Ridge(alpha=1).fit(X, y), "persistence": interval_forecast._PersistenceRegressor().fit(X, y)}
    monkeypatch.setattr(interval_forecast, "_fit_base_models", bases)
    fitted = []
    original_fit = GradientBoostingRegressor.fit
    def tracked_fit(self, X, y, **kwargs):
        fitted.append(X.index.copy())
        return original_fit(self, X, y, **kwargs)
    monkeypatch.setattr(GradientBoostingRegressor, "fit", tracked_fit)
    report = evaluate_next_day("TCS", daily_frame())
    assert report["horizon_sessions"] == 1
    assert report["published"] is False
    assert set(report["candidates"]) == {"gbm", "gbm_har_rv"}
    for index in fitted:
        assert index.max() < pd.Timestamp(report["calibration_start"])
        assert len(index) == report["split"]["train"]
    assert pd.Timestamp(report["calibration_end"]) < pd.Timestamp(report["test_start"])
    for candidate in report["candidates"].values():
        assert candidate["samples"] == report["split"]["test"]
        assert candidate["dm_vs_shared"]["h"] == 1
        assert len(candidate["pinball"]) == 3
        assert not candidate["event_evidence_complete"]
        assert not candidate["eligible_for_promotion_review"]
    assert "dm_vs_specialist_gbm" in report["candidates"]["gbm_har_rv"]


def test_specialist_rejects_intraday_or_midnight_origins():
    daily = daily_frame(60)
    intraday = pd.concat([daily.iloc[:1], daily.iloc[:1].set_axis(daily.index[:1] + pd.Timedelta(minutes=5))])
    with pytest.raises(ValueError, match="one completed bar"):
        build_features(intraday)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_features(daily.set_axis(daily.index.tz_localize(None)))
