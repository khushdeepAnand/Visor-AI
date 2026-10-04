# ==========================================================
# StockPilot AI
# Technical Indicators Engine
# ==========================================================

import numpy as np
import pandas as pd


# ==========================================================
# CONFIGURATION
# ==========================================================

REQUIRED_COLUMNS = {
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
}


# ==========================================================
# VALIDATION
# ==========================================================

def _validate_data(data):
    """
    Validate and normalize stock-market data.

    Returns:
        pandas.DataFrame:
            A cleaned copy of the input DataFrame.
    """

    if data is None:
        raise ValueError(
            "Stock data is required."
        )

    if not isinstance(data, pd.DataFrame):
        raise TypeError(
            "Stock data must be a pandas DataFrame."
        )

    if data.empty:
        raise ValueError(
            "Stock data is empty."
        )

    missing_columns = REQUIRED_COLUMNS.difference(
        data.columns
    )

    if missing_columns:
        raise ValueError(
            "Missing required columns: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    cleaned_data = data.copy()

    numeric_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    for column in numeric_columns:
        cleaned_data[column] = pd.to_numeric(
            cleaned_data[column],
            errors="coerce"
        )

    cleaned_data.replace(
        [np.inf, -np.inf],
        np.nan,
        inplace=True
    )

    cleaned_data.sort_index(
        inplace=True
    )

    cleaned_data = cleaned_data.loc[
        ~cleaned_data.index.duplicated(
            keep="last"
        )
    ]

    return cleaned_data


def _validate_period(period):
    """
    Validate an indicator period.
    """

    try:
        period = int(period)

    except (
        TypeError,
        ValueError
    ) as error:
        raise ValueError(
            "Indicator period must be an integer."
        ) from error

    if period <= 0:
        raise ValueError(
            "Indicator period must be greater than zero."
        )

    return period




def _annualization_factor(data):
    """Infer periods/year from a DatetimeIndex so volatility works on intraday bars."""
    index = getattr(data, "index", None)
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return 252.0
    deltas = pd.Series(index).sort_values().diff().dropna().dt.total_seconds() / 60.0
    if deltas.empty:
        return 252.0
    minutes = float(deltas.median())
    if minutes <= 2:
        return 252.0 * 375.0
    if minutes <= 7:
        return 252.0 * 75.0
    if minutes <= 20:
        return 252.0 * 25.0
    if minutes <= 90:
        return 252.0 * 6.25
    if minutes <= 300:
        return 252.0 * 1.55
    if minutes <= 60 * 30:
        return 252.0
    return 52.0


# ==========================================================
# SIMPLE MOVING AVERAGE
# ==========================================================

def calculate_sma(
    data,
    period
):
    """
    Calculate Simple Moving Average.

    SMA represents the average closing price over a selected
    number of trading sessions.
    """

    period = _validate_period(
        period
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    return close.rolling(
        window=period,
        min_periods=period
    ).mean()


# ==========================================================
# EXPONENTIAL MOVING AVERAGE
# ==========================================================

def calculate_ema(
    data,
    period
):
    """
    Calculate Exponential Moving Average.

    EMA gives greater weight to recent prices.
    """

    period = _validate_period(
        period
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    return close.ewm(
        span=period,
        adjust=False,
        min_periods=period
    ).mean()


# ==========================================================
# RELATIVE STRENGTH INDEX
# ==========================================================

def calculate_rsi(
    data,
    period=14
):
    """
    Calculate Relative Strength Index using Wilder's
    smoothing method.

    RSI values:
        Above 70: potentially overbought
        Below 30: potentially oversold
    """

    period = _validate_period(
        period
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    price_change = close.diff()

    gains = price_change.clip(
        lower=0
    )

    losses = -price_change.clip(
        upper=0
    )

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    relative_strength = (
        average_gain
        / average_loss.replace(
            0,
            np.nan
        )
    )

    rsi = 100 - (
        100
        / (
            1 + relative_strength
        )
    )

    # Handle special cases where there are no losses or gains.
    rsi = rsi.where(
        average_loss != 0,
        100
    )

    rsi = rsi.where(
        average_gain != 0,
        0
    )

    both_zero = (
        average_gain.eq(0)
        & average_loss.eq(0)
    )

    rsi = rsi.where(
        ~both_zero,
        50
    )

    return rsi.clip(
        lower=0,
        upper=100
    )


# ==========================================================
# MACD
# ==========================================================

def calculate_macd(
    data,
    fast_period=12,
    slow_period=26,
    signal_period=9
):
    """
    Calculate MACD, signal line, and histogram.
    """

    fast_period = _validate_period(
        fast_period
    )

    slow_period = _validate_period(
        slow_period
    )

    signal_period = _validate_period(
        signal_period
    )

    if fast_period >= slow_period:
        raise ValueError(
            "MACD fast period must be smaller than slow period."
        )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    fast_ema = close.ewm(
        span=fast_period,
        adjust=False,
        min_periods=fast_period
    ).mean()

    slow_ema = close.ewm(
        span=slow_period,
        adjust=False,
        min_periods=slow_period
    ).mean()

    macd = fast_ema - slow_ema

    signal = macd.ewm(
        span=signal_period,
        adjust=False,
        min_periods=signal_period
    ).mean()

    histogram = macd - signal

    return (
        macd,
        signal,
        histogram
    )


# ==========================================================
# BOLLINGER BANDS
# ==========================================================

def calculate_bollinger_bands(
    data,
    period=20,
    standard_deviations=2
):
    """
    Calculate upper, middle, and lower Bollinger Bands.
    """

    period = _validate_period(
        period
    )

    try:
        standard_deviations = float(
            standard_deviations
        )

    except (
        TypeError,
        ValueError
    ) as error:
        raise ValueError(
            "Standard-deviation multiplier must be numeric."
        ) from error

    if standard_deviations <= 0:
        raise ValueError(
            "Standard-deviation multiplier must be positive."
        )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    middle_band = close.rolling(
        window=period,
        min_periods=period
    ).mean()

    standard_deviation = close.rolling(
        window=period,
        min_periods=period
    ).std(
        ddof=0
    )

    upper_band = (
        middle_band
        + (
            standard_deviations
            * standard_deviation
        )
    )

    lower_band = (
        middle_band
        - (
            standard_deviations
            * standard_deviation
        )
    )

    return (
        upper_band,
        middle_band,
        lower_band
    )


# ==========================================================
# TRUE RANGE
# ==========================================================

def calculate_true_range(data):
    """
    Calculate True Range for each trading session.
    """

    high = pd.to_numeric(
        data["High"],
        errors="coerce"
    )

    low = pd.to_numeric(
        data["Low"],
        errors="coerce"
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    previous_close = close.shift(1)

    ranges = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1
    )

    return ranges.max(
        axis=1
    )


# ==========================================================
# AVERAGE TRUE RANGE
# ==========================================================

def calculate_atr(
    data,
    period=14
):
    """
    Calculate Average True Range.

    ATR measures market volatility.
    """

    period = _validate_period(
        period
    )

    true_range = calculate_true_range(
        data
    )

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


# ==========================================================
# RATE OF CHANGE
# ==========================================================

def calculate_roc(
    data,
    period=12
):
    """
    Calculate Rate of Change percentage.
    """

    period = _validate_period(
        period
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    previous_price = close.shift(
        period
    )

    return (
        (
            close - previous_price
        )
        / previous_price.replace(
            0,
            np.nan
        )
    ) * 100


# ==========================================================
# ON-BALANCE VOLUME
# ==========================================================

def calculate_obv(data):
    """
    Calculate On-Balance Volume.

    OBV combines price direction with trading volume.
    """

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    volume = pd.to_numeric(
        data["Volume"],
        errors="coerce"
    ).fillna(0)

    direction = np.sign(
        close.diff()
    ).fillna(0)

    return (
        direction
        * volume
    ).cumsum()


# ==========================================================
# STOCHASTIC OSCILLATOR
# ==========================================================

def calculate_stochastic(
    data,
    period=14,
    signal_period=3
):
    """
    Calculate Stochastic %K and %D.
    """

    period = _validate_period(
        period
    )

    signal_period = _validate_period(
        signal_period
    )

    high = pd.to_numeric(
        data["High"],
        errors="coerce"
    )

    low = pd.to_numeric(
        data["Low"],
        errors="coerce"
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    lowest_low = low.rolling(
        window=period,
        min_periods=period
    ).min()

    highest_high = high.rolling(
        window=period,
        min_periods=period
    ).max()

    price_range = (
        highest_high
        - lowest_low
    )

    # A flat window (every high equal to every low) happens on illiquid or
    # circuit-limited intraday bars. The oscillator is then undefined rather
    # than missing, so the standard midpoint reading of 50 is used once the
    # rolling warm-up has completed. Warm-up rows stay NaN.
    flat_window = price_range == 0

    stochastic_k = (
        (
            close - lowest_low
        )
        / price_range.replace(
            0,
            np.nan
        )
    ) * 100

    stochastic_k = stochastic_k.mask(
        flat_window,
        50.0
    )

    stochastic_d = stochastic_k.rolling(
        window=signal_period,
        min_periods=signal_period
    ).mean()

    return (
        stochastic_k,
        stochastic_d
    )


# ==========================================================
# DAILY RETURNS
# ==========================================================

def calculate_returns(data):
    """
    Calculate daily percentage returns.
    """

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    return close.pct_change() * 100


# ==========================================================
# VOLATILITY
# ==========================================================

def calculate_volatility(
    data,
    period=20
):
    """
    Calculate annualized historical volatility percentage.
    """

    period = _validate_period(
        period
    )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce"
    )

    daily_returns = close.pct_change()

    return (
        daily_returns.rolling(
            window=period,
            min_periods=period
        ).std(
            ddof=0
        )
        * np.sqrt(_annualization_factor(data))
        * 100
    )


# ==========================================================
# TECHNICAL SIGNAL
# ==========================================================

def calculate_technical_signal(data):
    """
    Produce a simple technical score and signal.

    This does not replace professional investment analysis.
    It summarizes several commonly used technical indicators.
    """

    if data is None or data.empty:
        return 0, "NEUTRAL"

    latest = data.iloc[-1]

    score = 0

    close = latest.get(
        "Close"
    )

    sma_20 = latest.get(
        "SMA_20"
    )

    sma_50 = latest.get(
        "SMA_50"
    )

    rsi = latest.get(
        "RSI"
    )

    macd = latest.get(
        "MACD"
    )

    macd_signal = latest.get(
        "MACD_Signal"
    )

    if pd.notna(close) and pd.notna(sma_20):
        score += 1 if close > sma_20 else -1

    if pd.notna(sma_20) and pd.notna(sma_50):
        score += 1 if sma_20 > sma_50 else -1

    if pd.notna(rsi):
        if 50 <= rsi < 70:
            score += 1

        elif 30 < rsi < 50:
            score -= 1

        elif rsi >= 70:
            score -= 1

        elif rsi <= 30:
            score += 1

    if (
        pd.notna(macd)
        and pd.notna(macd_signal)
    ):
        score += (
            1
            if macd > macd_signal
            else -1
        )

    if score >= 3:
        signal = "STRONG BUY"

    elif score >= 1:
        signal = "BUY"

    elif score <= -3:
        signal = "STRONG SELL"

    elif score <= -1:
        signal = "SELL"

    else:
        signal = "NEUTRAL"

    return (
        score,
        signal
    )



# ==========================================================
# ADVANCED VOLUME / TREND INDICATORS
# ==========================================================

def has_traded_volume(data) -> bool:
    """Report whether the series carries usable traded volume.

    Exchange index series (for example ``NSE_INDEX|Nifty 50`` or
    ``BSE_INDEX|SENSEX``) are published without traded volume, so every
    volume-weighted indicator is mathematically undefined for them. Callers use
    this flag to select an explicitly documented unweighted fallback instead of
    emitting NaN columns that would silently empty a supervised training frame.
    """
    frame = _validate_data(data)
    volume = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0)
    return bool((volume > 0).any())


def calculate_vwap(data):
    """Calculate VWAP, resetting each session for intraday DatetimeIndex data.

    When the instrument publishes no traded volume the volume-weighted average
    degrades to the equally weighted cumulative mean of the typical price. The
    fallback is deterministic, uses only current and earlier rows, and is
    reported to callers through ``has_traded_volume``.
    """
    frame = _validate_data(data)
    typical_price = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    volume = frame["Volume"].fillna(0)
    volume_available = bool((volume > 0).any())
    weights = volume if volume_available else pd.Series(1.0, index=frame.index)
    value = typical_price * weights
    if isinstance(frame.index, pd.DatetimeIndex) and len(frame) > 2:
        # Intraday data has multiple bars on the same calendar session. For daily
        # bars every date is unique, preserving cumulative historical VWAP behavior.
        dates = pd.Series(frame.index.date, index=frame.index)
        if dates.duplicated().any():
            cumulative_value = value.groupby(dates).cumsum()
            cumulative_weight = weights.groupby(dates).cumsum().replace(0, np.nan)
            return cumulative_value / cumulative_weight
    cumulative_weight = weights.cumsum().replace(0, np.nan)
    return value.cumsum() / cumulative_weight


def calculate_cmf(data, period=20):
    """Calculate Chaikin Money Flow.

    Without traded volume the money-flow multiplier is averaged with unit
    weights, which keeps the indicator on its native -1..1 scale and preserves
    the intrabar close-location information that the weighted form carries.
    """
    period = _validate_period(period)
    frame = _validate_data(data)
    spread = (frame["High"] - frame["Low"]).replace(0, np.nan)
    multiplier = ((frame["Close"] - frame["Low"]) - (frame["High"] - frame["Close"])) / spread
    volume = frame["Volume"].fillna(0)
    weights = volume if bool((volume > 0).any()) else pd.Series(1.0, index=frame.index)
    money_flow_volume = multiplier.fillna(0) * weights
    weight_sum = weights.rolling(period, min_periods=period).sum().replace(0, np.nan)
    return money_flow_volume.rolling(period, min_periods=period).sum() / weight_sum


def calculate_mfi(data, period=14):
    """Calculate Money Flow Index."""
    period = _validate_period(period)
    frame = _validate_data(data)
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    raw_flow = typical * frame["Volume"]
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0)
    negative = raw_flow.where(direction < 0, 0.0)
    positive_sum = positive.rolling(period, min_periods=period).sum()
    negative_sum = negative.rolling(period, min_periods=period).sum()
    ratio = positive_sum / negative_sum.replace(0, np.nan)
    mfi = 100 - 100 / (1 + ratio)
    mfi = mfi.where(negative_sum != 0, 100)
    mfi = mfi.where(positive_sum != 0, 0)
    return mfi.clip(0, 100)


def calculate_adx(data, period=14):
    """Calculate Average Directional Index and directional components."""
    period = _validate_period(period)
    frame = _validate_data(data)
    high_diff = frame["High"].diff()
    low_diff = -frame["Low"].diff()
    plus_dm = pd.Series(np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0), index=frame.index)
    atr = calculate_true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def calculate_cci(data, period=20):
    """Calculate Commodity Channel Index."""
    period = _validate_period(period)
    frame = _validate_data(data)
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    average = typical.rolling(period, min_periods=period).mean()
    mean_deviation = typical.rolling(period, min_periods=period).apply(
        lambda values: float(np.mean(np.abs(values - np.mean(values)))), raw=True
    )
    return (typical - average) / (0.015 * mean_deviation.replace(0, np.nan))


def calculate_williams_r(data, period=14):
    """Calculate Williams %R."""
    period = _validate_period(period)
    frame = _validate_data(data)
    highest = frame["High"].rolling(period, min_periods=period).max()
    lowest = frame["Low"].rolling(period, min_periods=period).min()
    return -100 * (highest - frame["Close"]) / (highest - lowest).replace(0, np.nan)


def calculate_donchian(data, period=20):
    """Calculate Donchian upper, middle and lower channels."""
    period = _validate_period(period)
    frame = _validate_data(data)
    upper = frame["High"].rolling(period, min_periods=period).max()
    lower = frame["Low"].rolling(period, min_periods=period).min()
    return upper, (upper + lower) / 2.0, lower


# ==========================================================
# ADD ALL INDICATORS
# ==========================================================

def add_indicators(data):
    """
    Add all supported technical indicators to a copy of the
    stock DataFrame.
    """

    result = _validate_data(
        data
    )

    # Moving averages
    result["SMA_20"] = calculate_sma(
        result,
        20
    )

    result["SMA_50"] = calculate_sma(
        result,
        50
    )

    result["SMA_200"] = calculate_sma(
        result,
        200
    )

    result["EMA_20"] = calculate_ema(
        result,
        20
    )

    result["EMA_50"] = calculate_ema(
        result,
        50
    )


    result["SMA_10"] = calculate_sma(result, 10)
    result["SMA_100"] = calculate_sma(result, 100)
    result["EMA_10"] = calculate_ema(result, 10)
    result["EMA_100"] = calculate_ema(result, 100)

    # Relative Strength Index
    result["RSI"] = calculate_rsi(
        result,
        14
    )


    result["RSI_7"] = calculate_rsi(result, 7)
    result["RSI_21"] = calculate_rsi(result, 21)

    # MACD
    (
        result["MACD"],
        result["MACD_Signal"],
        result["MACD_Histogram"]
    ) = calculate_macd(
        result
    )

    # Bollinger Bands
    (
        result["BB_Upper"],
        result["BB_Middle"],
        result["BB_Lower"]
    ) = calculate_bollinger_bands(
        result,
        20,
        2
    )

    result["BB_Width"] = (
        (
            result["BB_Upper"]
            - result["BB_Lower"]
        )
        / result["BB_Middle"].replace(
            0,
            np.nan
        )
    ) * 100

    # Volatility and momentum
    result["ATR"] = calculate_atr(
        result,
        14
    )

    result["ROC"] = calculate_roc(
        result,
        12
    )

    result["OBV"] = calculate_obv(
        result
    )

    (
        result["Stochastic_K"],
        result["Stochastic_D"]
    ) = calculate_stochastic(
        result,
        14,
        3
    )


    result["VWAP"] = calculate_vwap(result)
    result["CMF"] = calculate_cmf(result, 20)
    result["MFI"] = calculate_mfi(result, 14)
    result["ADX"], result["Plus_DI"], result["Minus_DI"] = calculate_adx(result, 14)
    result["CCI"] = calculate_cci(result, 20)
    result["Williams_R"] = calculate_williams_r(result, 14)
    result["Donchian_Upper"], result["Donchian_Middle"], result["Donchian_Lower"] = calculate_donchian(result, 20)

    result["Daily_Return"] = calculate_returns(
        result
    )

    result["Volatility"] = calculate_volatility(
        result,
        20
    )
    annualization = _annualization_factor(result)

    # Multi-horizon, scale-aware features used by the forecasting engine.
    # Every value is computed from the current or earlier rows only.
    close = result["Close"].replace(0, np.nan)
    open_price = result["Open"].replace(0, np.nan)
    previous_close = result["Close"].shift(1).replace(0, np.nan)
    previous_volume = result["Volume"].shift(1).replace(0, np.nan)
    raw_previous_volume = result["Volume"].shift(1)

    result["Return_5D"] = close.pct_change(5) * 100
    result["Return_20D"] = close.pct_change(20) * 100
    result["Volatility_5D"] = (
        close.pct_change().rolling(5, min_periods=5).std(ddof=0)
        * np.sqrt(annualization)
        * 100
    )
    result["Volatility_60D"] = (
        close.pct_change().rolling(60, min_periods=60).std(ddof=0)
        * np.sqrt(annualization)
        * 100
    )
    result["Volume_Change"] = ((result["Volume"] / previous_volume - 1) * 100).mask(
        # A previous bar with no trades gives no base to measure growth against.
        # The relative change is undefined rather than missing, so it is reported
        # as no measurable change. The first bar of the series stays NaN.
        raw_previous_volume.notna() & (raw_previous_volume == 0),
        0.0,
    )
    result["Price_Range_Pct"] = (result["High"] - result["Low"]) / close * 100
    result["Gap_Pct"] = (open_price / previous_close - 1) * 100
    result["Close_to_SMA20_Pct"] = (close / result["SMA_20"] - 1) * 100
    result["Close_to_SMA50_Pct"] = (close / result["SMA_50"] - 1) * 100
    result["ATR_Pct"] = result["ATR"] / close * 100

    result["Return_2D"] = close.pct_change(2) * 100
    result["Return_10D"] = close.pct_change(10) * 100
    result["Return_60D"] = close.pct_change(60) * 100
    result["Volatility_10D"] = close.pct_change().rolling(10, min_periods=10).std(ddof=0) * np.sqrt(annualization) * 100
    result["Volatility_30D"] = close.pct_change().rolling(30, min_periods=30).std(ddof=0) * np.sqrt(annualization) * 100
    result["Momentum_5"] = close - close.shift(5)
    result["Momentum_10"] = close - close.shift(10)
    result["Momentum_20"] = close - close.shift(20)
    bar_range = (result["High"] - result["Low"])
    # A zero-range bar prices every trade at one level, so the close sits at the
    # midpoint by definition. Leaving it NaN would discard an otherwise complete
    # feature row and is the reason intraday forecasting used to fail.
    result["Close_Location"] = ((close - result["Low"]) / bar_range.replace(0, np.nan)).mask(bar_range == 0, 0.5)
    result["Volume_SMA20"] = result["Volume"].rolling(20, min_periods=20).mean()
    result["Volume_Ratio20"] = result["Volume"] / result["Volume_SMA20"].replace(0, np.nan)
    volume_std20 = result["Volume"].rolling(20, min_periods=20).std(ddof=0).replace(0, np.nan)
    result["Volume_ZScore20"] = (result["Volume"] - result["Volume_SMA20"]) / volume_std20
    return_series = close.pct_change()
    result["Return_Skew20"] = return_series.rolling(20, min_periods=20).skew()
    result["Return_Kurt20"] = return_series.rolling(20, min_periods=20).kurt()
    result["Drawdown_20"] = close / close.rolling(20, min_periods=20).max() - 1
    result["Drawdown_60"] = close / close.rolling(60, min_periods=60).max() - 1
    result["Close_to_VWAP_Pct"] = (close / result["VWAP"] - 1) * 100
    result["Donchian_Position"] = (close - result["Donchian_Lower"]) / (result["Donchian_Upper"] - result["Donchian_Lower"]).replace(0, np.nan)

    # Exchange index series carry no traded volume, so the relative-volume
    # features below have no defined value. Substituting the documented neutral
    # constant keeps the feature matrix rectangular; leaving NaN would empty the
    # supervised training frame and make forecasting impossible for every index.
    # Price-derived columns are never substituted.
    if not bool((result["Volume"].fillna(0) > 0).any()):
        result["Volume_Change"] = 0.0
        result["Volume_Ratio20"] = 1.0
        result["Volume_ZScore20"] = 0.0
    result.attrs["has_traded_volume"] = bool((result["Volume"].fillna(0) > 0).any())

    # Trend labels
    result["Price_Above_SMA20"] = (
        result["Close"]
        > result["SMA_20"]
    )

    result["Price_Above_SMA50"] = (
        result["Close"]
        > result["SMA_50"]
    )

    result["Golden_Cross"] = (
        result["SMA_50"]
        > result["SMA_200"]
    )

    return result