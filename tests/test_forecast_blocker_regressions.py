"""Regressions for the three root causes of the v6.1 forecasting outage.

Each test targets one confirmed cause rather than the symptom, so a future
refactor that reintroduces the cause fails here instead of surfacing as a
generic "forecast could not be generated" error in the terminal.

1. Exchange index series publish no traded volume, which made every
   volume-weighted feature NaN and emptied the supervised training frame.
2. Flat or no-trade intraday bars made the final feature row incomplete, which
   blocked every one-minute request even with thousands of usable rows.
3. Upstox V3 rejects historical-candle requests wider than a per-interval date
   span (HTTP 400 ``UDAPI1148``), which broke 5m/1mo, 15m/3mo, 1h/1y and 4h/1y.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from indicators import add_indicators, has_traded_volume
from prediction import FEATURE_COLUMNS, InsufficientDataError, build_supervised_frame
from services.market_data.base import (
    UnsupportedHistoryRangeError,
    WINDOW_DAYS,
)
from services.market_data.upstox import UpstoxProvider

VOLUME_WEIGHTED_FEATURES = [
    "VWAP",
    "CMF",
    "Volume_Change",
    "Volume_Ratio20",
    "Volume_ZScore20",
    "Close_to_VWAP_Pct",
]


def _synthetic_daily(rows: int = 400, *, volume: float | None = 1_000_000.0) -> pd.DataFrame:
    """Deterministic OHLCV history; ``volume=None`` mimics an exchange index."""
    index = pd.date_range("2024-01-01", periods=rows, freq="B", tz="Asia/Kolkata")
    steps = np.sin(np.arange(rows) / 7.0) * 12.0 + np.arange(rows) * 0.35
    close = 1000.0 + steps
    frame = pd.DataFrame(
        {
            "Open": close - 1.5,
            "High": close + 4.0,
            "Low": close - 4.0,
            "Close": close,
            "Volume": 0.0 if volume is None else volume,
        },
        index=index,
    )
    return frame


class TestIndexSeriesWithoutTradedVolume:
    def test_has_traded_volume_reports_index_series(self) -> None:
        assert has_traded_volume(_synthetic_daily(volume=1_000_000.0)) is True
        assert has_traded_volume(_synthetic_daily(volume=None)) is False

    def test_volume_weighted_features_stay_finite_without_volume(self) -> None:
        enriched = add_indicators(_synthetic_daily(volume=None))
        assert enriched.attrs["has_traded_volume"] is False
        tail = enriched.iloc[50:]
        for column in VOLUME_WEIGHTED_FEATURES:
            assert tail[column].notna().all(), f"{column} is undefined without traded volume"

    def test_supervised_frame_survives_index_series(self) -> None:
        frame = build_supervised_frame(_synthetic_daily(volume=None))
        assert len(frame) >= 80
        assert frame[FEATURE_COLUMNS].notna().all().all()

    def test_vwap_degrades_to_typical_price_mean(self) -> None:
        data = _synthetic_daily(rows=120, volume=None)
        enriched = add_indicators(data)
        typical = (data["High"] + data["Low"] + data["Close"]) / 3.0
        expected = typical.expanding().mean()
        assert np.allclose(enriched["VWAP"].to_numpy(), expected.to_numpy())

    def test_volume_bearing_series_is_unchanged_by_the_fallback(self) -> None:
        """The fallback must never alter a normal, volume-bearing instrument."""
        data = _synthetic_daily(rows=200, volume=1_000_000.0)
        enriched = add_indicators(data)
        typical = (data["High"] + data["Low"] + data["Close"]) / 3.0
        weighted = (typical * data["Volume"]).cumsum() / data["Volume"].cumsum()
        assert np.allclose(enriched["VWAP"].to_numpy(), weighted.to_numpy())


class TestFlatAndNoTradeBars:
    @staticmethod
    def _with_flat_tail(rows: int = 300, flat: int = 3) -> pd.DataFrame:
        frame = _synthetic_daily(rows=rows)
        level = float(frame["Close"].iloc[-flat - 1])
        # A circuit-limited or auction-only stretch: one price, no trades.
        # Reproduces the observed tail: a short auction-only stretch at one
        # price with no trades, immediately before the requested forecast bar.
        frame.iloc[-flat:, frame.columns.get_indexer(["Open", "High", "Low", "Close"])] = level
        frame.iloc[-flat:, frame.columns.get_loc("Volume")] = 0.0
        return frame

    def test_close_location_is_midpoint_on_zero_range_bar(self) -> None:
        enriched = add_indicators(self._with_flat_tail())
        assert float(enriched["Close_Location"].iloc[-1]) == pytest.approx(0.5)

    def test_stochastic_is_midpoint_on_flat_window(self) -> None:
        # The oscillator only becomes undefined once the whole 14-bar window is
        # flat, so this case needs a longer no-trade stretch than the tail above.
        enriched = add_indicators(self._with_flat_tail(flat=16))
        assert float(enriched["Stochastic_K"].iloc[-1]) == pytest.approx(50.0)
        assert float(enriched["Stochastic_D"].iloc[-1]) == pytest.approx(50.0)

    def test_volume_change_is_zero_after_a_no_trade_bar(self) -> None:
        enriched = add_indicators(self._with_flat_tail())
        assert float(enriched["Volume_Change"].iloc[-1]) == pytest.approx(0.0)

    def test_stochastic_warm_up_rows_stay_missing(self) -> None:
        """The midpoint convention must not paper over an incomplete warm-up."""
        enriched = add_indicators(_synthetic_daily(rows=200))
        assert enriched["Stochastic_K"].iloc[:13].isna().all()

    def test_latest_feature_row_is_complete_after_a_flat_tail(self) -> None:
        enriched = add_indicators(self._with_flat_tail())
        latest = enriched.iloc[-1]
        incomplete = [column for column in FEATURE_COLUMNS if not np.isfinite(float(latest.get(column, np.nan)))]
        assert incomplete == []


class TestUpstoxHistorySegmentation:
    def test_per_interval_span_caps_match_the_provider_limits(self) -> None:
        assert UpstoxProvider._max_span_days("minutes", 1) == 30
        assert UpstoxProvider._max_span_days("minutes", 15) == 30
        assert UpstoxProvider._max_span_days("minutes", 30) == 90
        assert UpstoxProvider._max_span_days("hours", 1) == 90
        assert UpstoxProvider._max_span_days("days", 1) == 3650

    def test_every_segment_fits_inside_the_provider_cap(self) -> None:
        to_date = date(2026, 8, 31)
        for timeframe in ("1m", "5m", "15m", "1h", "4h", "1D", "1W"):
            unit, interval = UpstoxProvider._unit_interval(timeframe)
            cap = UpstoxProvider._max_span_days(unit, interval)
            for window, days in WINDOW_DAYS.items():
                if days > UpstoxProvider.max_supported_days(timeframe):
                    continue
                segments = UpstoxProvider._segments(unit, interval, to_date - timedelta(days=days), to_date)
                assert segments, f"{timeframe}/{window} produced no segments"
                for start, end in segments:
                    assert (end - start).days + 1 <= cap, f"{timeframe}/{window} segment exceeds {cap} days"

    def test_segments_are_contiguous_ascending_and_cover_the_range(self) -> None:
        from_date, to_date = date(2025, 9, 1), date(2026, 8, 31)
        segments = UpstoxProvider._segments("hours", 1, from_date, to_date)
        assert segments[0][0] == from_date
        assert segments[-1][1] == to_date
        for (_, previous_end), (next_start, _) in zip(segments, segments[1:]):
            assert next_start == previous_end + timedelta(days=1)

    def test_segment_count_is_bounded(self) -> None:
        segments = UpstoxProvider._segments("minutes", 1, date(2010, 1, 1), date(2026, 8, 31))
        assert len(segments) <= UpstoxProvider.MAX_HISTORY_SEGMENTS

    def test_default_window_timeframe_pairs_are_all_supported(self) -> None:
        from forecasting.interval_forecast import WINDOW_TIMEFRAME_DEFAULTS

        for window, timeframe in WINDOW_TIMEFRAME_DEFAULTS.items():
            assert WINDOW_DAYS[window] <= UpstoxProvider.max_supported_days(timeframe), f"{window}/{timeframe}"

    def test_unsupported_pair_is_rejected_before_any_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = UpstoxProvider(token="unit-test-token")
        monkeypatch.setattr(provider, "_instrument", lambda symbol: _stub_instrument())

        def _fail(**_kwargs: object) -> None:  # pragma: no cover - must not run
            raise AssertionError("A rejected combination must not call the provider.")

        monkeypatch.setattr("services.market_data.upstox.request_json", _fail)
        with pytest.raises(UnsupportedHistoryRangeError) as excinfo:
            provider.get_history("RELIANCE", "5m", "5y")
        assert "1w" in excinfo.value.supported_windows()
        assert "5y" not in excinfo.value.supported_windows()

    def test_supported_pair_issues_one_request_per_segment_and_dedupes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = UpstoxProvider(token="unit-test-token")
        monkeypatch.setattr(provider, "_instrument", lambda symbol: _stub_instrument())
        calls: list[str] = []

        def _fake_request_json(*, endpoint: str, url: str, token: str, timeout: float, **_extra: object):
            calls.append(url)
            return {"data": {"candles": _fake_candles(url)}}, {"endpoint": endpoint}

        monkeypatch.setattr("services.market_data.upstox.request_json", _fake_request_json)
        frame = provider.get_history("RELIANCE", "5m", "1mo")
        unit, interval = UpstoxProvider._unit_interval("5m")
        expected = UpstoxProvider._segments(
            unit, interval, date.today() - timedelta(days=WINDOW_DAYS["1mo"]), date.today()
        )
        assert len(calls) == len(expected) >= 2
        assert frame.index.is_monotonic_increasing
        assert not frame.index.duplicated().any()


def _stub_instrument():
    from services.market_data.base import Instrument

    return Instrument(
        symbol="RELIANCE",
        name="Reliance Industries",
        exchange="NSE",
        segment="NSE_EQ",
        instrument_type="EQ",
        instrument_key="NSE_EQ|INE002A01018",
    )


def _fake_candles(url: str) -> list[list[object]]:
    """Two candles per segment, with the boundary bar deliberately repeated."""
    to_date = date.fromisoformat(url.rstrip("/").split("/")[-2])
    base = datetime(to_date.year, to_date.month, to_date.day, 9, 15, tzinfo=timezone.utc)
    return [
        [(base + timedelta(minutes=offset)).isoformat(), 100.0, 101.0, 99.0, 100.5, 1000, 0]
        for offset in (0, 5)
    ] + [[base.isoformat(), 100.0, 101.0, 99.0, 100.5, 1000, 0]]


def test_insufficient_data_is_distinguishable_from_a_bad_request() -> None:
    """The API layer relies on this subclass to pick an actionable message."""
    assert issubclass(InsufficientDataError, ValueError)
    with pytest.raises(InsufficientDataError):
        build_supervised_frame(_synthetic_daily(rows=90))
