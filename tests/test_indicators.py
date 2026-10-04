# ==========================================================
# Tests for indicators.py
# ==========================================================

import numpy as np
import pandas as pd
import pytest

import indicators as ind


def make_ohlcv(closes):
    """
    Build a minimal OHLCV DataFrame from a list of closing prices,
    for indicator math tests that only care about Close.
    """

    closes = pd.Series(closes, dtype=float)

    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 1,
            "Low": closes - 1,
            "Close": closes,
            "Volume": pd.Series([1000] * len(closes), dtype=float),
        }
    )


# ----------------------------------------------------------
# SMA
# ----------------------------------------------------------

def test_sma_matches_manual_average():
    data = make_ohlcv([10, 20, 30, 40, 50])

    sma = ind.calculate_sma(data, period=3)

    # First two rows have no full window yet.
    assert pd.isna(sma.iloc[0])
    assert pd.isna(sma.iloc[1])

    # 3rd row: average of 10, 20, 30
    assert sma.iloc[2] == pytest.approx(20.0)

    # last row: average of 30, 40, 50
    assert sma.iloc[4] == pytest.approx(40.0)


def test_sma_rejects_non_positive_period():
    data = make_ohlcv([10, 20, 30])

    with pytest.raises(ValueError):
        ind.calculate_sma(data, period=0)


# ----------------------------------------------------------
# RSI
# ----------------------------------------------------------

def test_rsi_is_100_when_prices_only_rise():
    closes = [10 + i for i in range(30)]  # strictly increasing
    data = make_ohlcv(closes)

    rsi = ind.calculate_rsi(data, period=14)

    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_when_prices_only_fall():
    closes = [100 - i for i in range(30)]  # strictly decreasing
    data = make_ohlcv(closes)

    rsi = ind.calculate_rsi(data, period=14)

    assert rsi.iloc[-1] == pytest.approx(0.0)


def test_rsi_stays_within_0_and_100():
    np.random.seed(0)
    closes = 100 + np.cumsum(np.random.normal(0, 1, 100))
    data = make_ohlcv(closes)

    rsi = ind.calculate_rsi(data, period=14).dropna()

    assert (rsi >= 0).all()
    assert (rsi <= 100).all()


# ----------------------------------------------------------
# MACD
# ----------------------------------------------------------

def test_macd_requires_fast_period_smaller_than_slow():
    data = make_ohlcv([10, 20, 30, 40, 50])

    with pytest.raises(ValueError):
        ind.calculate_macd(data, fast_period=26, slow_period=12)


def test_macd_histogram_equals_macd_minus_signal():
    np.random.seed(1)
    closes = 100 + np.cumsum(np.random.normal(0, 1, 60))
    data = make_ohlcv(closes)

    macd, signal, histogram = ind.calculate_macd(data)

    diff = (macd - signal - histogram).dropna()

    assert (diff.abs() < 1e-8).all()


# ----------------------------------------------------------
# Bollinger Bands
# ----------------------------------------------------------

def test_bollinger_upper_band_above_lower_band():
    np.random.seed(2)
    closes = 100 + np.cumsum(np.random.normal(0, 1, 60))
    data = make_ohlcv(closes)

    upper, middle, lower = ind.calculate_bollinger_bands(data, period=20)

    # Comparing NaN >= NaN evaluates to False (not NaN), so the
    # warm-up rows must be dropped BEFORE comparing, not after.
    combined = pd.concat(
        [upper.rename("upper"), lower.rename("lower")],
        axis=1,
    ).dropna()

    assert (combined["upper"] >= combined["lower"]).all()
    assert len(combined) > 0


def test_bollinger_rejects_non_positive_std_dev_multiplier():
    data = make_ohlcv([10, 20, 30, 40, 50] * 5)

    with pytest.raises(ValueError):
        ind.calculate_bollinger_bands(data, period=3, standard_deviations=0)


# ----------------------------------------------------------
# add_indicators end-to-end
# ----------------------------------------------------------

def test_add_indicators_produces_expected_columns():
    np.random.seed(3)
    closes = 100 + np.cumsum(np.random.normal(0, 1, 260))
    data = make_ohlcv(closes)

    enriched = ind.add_indicators(data)

    expected_columns = {
        "SMA_20", "SMA_50", "SMA_200", "EMA_20", "EMA_50",
        "RSI", "MACD", "MACD_Signal", "MACD_Histogram",
        "BB_Upper", "BB_Middle", "BB_Lower", "BB_Width",
        "ATR", "ROC", "OBV", "Stochastic_K", "Stochastic_D",
        "Daily_Return", "Volatility",
    }

    assert expected_columns.issubset(set(enriched.columns))


def test_add_indicators_rejects_missing_columns():
    bad_data = pd.DataFrame({"Close": [1, 2, 3]})

    with pytest.raises(ValueError):
        ind.add_indicators(bad_data)
