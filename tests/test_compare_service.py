import pandas as pd

from services.compare_service import build_symbol_comparison
from services.forecast_presentation import present_compare_item


def _market_data():
    dates = pd.date_range("2024-01-01", periods=260, freq="B")
    close = pd.Series(range(100, 360), index=dates, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 100_000,
        },
        index=dates,
    )


def test_build_symbol_comparison_reuses_existing_components():
    calls = []

    def fetch(symbol, period):
        calls.append((symbol, period))
        return _market_data()

    def add_indicators(data):
        result = data.copy()
        result["RSI"] = 55.0
        result["MACD"] = 1.2
        result["MACD_Signal"] = 1.0
        result["SMA_20"] = 340.0
        result["SMA_50"] = 320.0
        result["Volatility"] = 2.5
        return result

    def forecast(symbol, data):
        return {
            "Consensus Prediction": 361.0,
            "Best Model": "Random Forest",
            "Prediction Interval": {"lower": 355.0, "upper": 367.0},
            "Best Model Beats Baseline": True,
        }

    result = build_symbol_comparison(
        "RELIANCE",
        data_fetcher=fetch,
        info_fetcher=lambda symbol: {"longName": "Reliance Industries", "currency": "USD"},
        indicator_adder=add_indicators,
        predictor=forecast,
    )

    assert calls == [("RELIANCE", "1y")]
    assert result["company"] == "Reliance Industries"
    assert result["quote"]["price"] == 359.0
    assert result["indicators"]["rsi_14"] == 55.0
    assert result["forecast"] == {
        "low": 355.0,
        "median": 361.0,
        "high": 367.0,
        "confidence_level": 0.8,
        "currency": "INR",
        "direction": "bullish",
    }
    assert result["validation"]["beats_naive_baseline"] is True


def test_build_symbol_comparison_rejects_prediction_error():
    try:
        build_symbol_comparison(
            "BAD",
            data_fetcher=lambda symbol, period: _market_data(),
            info_fetcher=lambda symbol: {},
            indicator_adder=lambda data: data.assign(
                RSI=50, MACD=0, MACD_Signal=0, SMA_20=1, SMA_50=1, Volatility=1
            ),
            predictor=lambda symbol, data: {"error": "not enough data"},
        )
    except ValueError as error:
        assert "not enough data" in str(error)
    else:
        raise AssertionError("Expected comparison failure")


def test_blocked_comparison_does_not_publish_raw_bounds():
    item = {
        "symbol": "RELIANCE",
        "quote": {"price": 100.0},
        "forecast": {"low": 90.0, "median": 100.0, "high": 110.0, "confidence_level": 0.8},
        "forecast_status": "drift_blocked",
        "abstention_reason": "Recent error drift exceeded the release gate.",
    }
    public = present_compare_item(item)
    assert public["forecast"] is None
    assert public["abstained"] is True
