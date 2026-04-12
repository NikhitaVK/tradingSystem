"""
indicators.py — Pure pandas/numpy indicator functions.

All functions are causal: output at index t uses only data up to and including t.
No ta-lib dependency — all implemented natively for portability.

Naming convention for columns added to DataFrames:
    RSI_14, EMA_50, MACD_12_26_9, MACD_signal_12_26_9, MACD_hist_12_26_9,
    BB_upper_20, BB_mid_20, BB_lower_20, ATR_14
"""
import numpy as np
import pandas as pd


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing (EWM with alpha=1/period).
    Returns NaN for the first `period` bars during warm-up.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    # Avoid division by zero: if avg_loss is 0 RSI = 100
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, other=100.0)
    return rsi


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.
    Returns NaN for the first `period - 1` bars during warm-up.
    """
    return close.ewm(span=period, min_periods=period, adjust=False).mean()


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    """
    MACD indicator.
    Returns dict with keys:
        'macd'   — MACD line (fast EMA - slow EMA)
        'signal' — Signal line (EMA of MACD line)
        'hist'   — Histogram (macd - signal)
    All Series are NaN during warm-up (first `slow + signal - 1` bars).
    """
    ema_fast = close.ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "hist": histogram}


def compute_bb(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict:
    """
    Bollinger Bands.
    Returns dict with keys: 'upper', 'mid', 'lower'.
    All Series are NaN during warm-up (first `period - 1` bars).
    """
    mid = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return {"upper": upper, "mid": mid, "lower": lower}


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range using Wilder's smoothing (EWM with alpha=1/period).
    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    Returns NaN for the first `period` bars during warm-up.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


# ── Lookback helpers ──────────────────────────────────────────────────────────

def indicator_lookback(indicator_spec: dict) -> int:
    """
    Return the number of warm-up bars an indicator needs before producing
    valid values. Used by strategy_runner to set the NaN mask boundary.
    """
    itype = indicator_spec["type"].upper()
    period = indicator_spec.get("period", 14)

    if itype == "RSI":
        return period
    if itype == "EMA":
        return period
    if itype == "MACD":
        fast = indicator_spec.get("fast", 12)
        slow = indicator_spec.get("slow", 26)
        signal = indicator_spec.get("signal", 9)
        return slow + signal - 1
    if itype == "BB":
        return period
    if itype == "ATR":
        return period
    return period
