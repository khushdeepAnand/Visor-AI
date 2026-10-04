import pandas as pd


def get_rsi_status(rsi):

    if pd.isna(rsi):
        return "Insufficient Data ⚪"

    if rsi < 30:
        return "Oversold 🟢"

    if rsi > 70:
        return "Overbought 🔴"

    return "Neutral 🟡"


def get_macd_status(macd, signal):

    if pd.isna(macd) or pd.isna(signal):
        return "Insufficient Data ⚪"

    if macd > signal:
        return "Bullish 🟢"

    return "Bearish 🔴"


def get_sma_status(sma20, sma50):

    if pd.isna(sma20) or pd.isna(sma50):
        return "Insufficient Data ⚪"

    if sma20 > sma50:
        return "Bullish 🟢"

    return "Bearish 🔴"


def get_ema_status(price, ema20):

    if pd.isna(ema20):
        return "Insufficient Data ⚪"

    if price > ema20:
        return "Above EMA 🟢"

    return "Below EMA 🔴"


def calculate_technical_score(price, data):

    score = 0

    latest = data.iloc[-1]

    rsi = latest["RSI"]
    macd = latest["MACD"]
    signal = latest["MACD_Signal"]
    sma20 = latest["SMA_20"]
    sma50 = latest["SMA_50"]
    ema20 = latest["EMA_20"]

    if not pd.isna(rsi):

        if rsi < 30:
            score += 25

        elif rsi <= 60:
            score += 15

    if not pd.isna(macd) and not pd.isna(signal):

        if macd > signal:
            score += 25

    if not pd.isna(sma20) and not pd.isna(sma50):

        if sma20 > sma50:
            score += 25

    if not pd.isna(ema20):

        if price > ema20:
            score += 25

    return score


def get_overall_signal(score):

    if score >= 75:
        return "BUY 🟢"

    if score >= 50:
        return "HOLD 🟡"

    return "SELL 🔴"