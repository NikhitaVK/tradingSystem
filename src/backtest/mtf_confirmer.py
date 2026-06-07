"""
mtf_confirmer.py — Multi-timeframe trend confirmation filter (Phase 9).

Provides a higher-timeframe trend gate that can be applied to signals from
any strategy spec. The filter requires both:
  1. Price is above (long) or below (short) a higher-TF EMA
  2. ADX on the higher TF is above threshold (confirming a trend exists)

This prevents mean-reversion entries from firing into strong counter-trends
and reduces whipsaws in sideways regimes by gating out low-ADX environments.
"""
import logging
import sqlite3

import pandas as pd

from src.backtest.indicators import compute_ema, compute_adx

logger = logging.getLogger(__name__)

# Default parameters for the MTF trend filter
_DEFAULT_EMA_PERIOD = 50
_DEFAULT_ADX_PERIOD = 14
_DEFAULT_ADX_THRESHOLD = 20.0

# Timeframe hierarchy for selecting a higher timeframe
_TF_HIERARCHY = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def get_higher_timeframes(timeframe: str) -> list:
    """
    Return timeframes that are one and two steps above the given timeframe.

    Examples:
        "1h"  → ["4h", "1d"]
        "15m" → ["1h", "4h"]
        "4h"  → ["1d"]

    Returns empty list if timeframe is at or above the top of the hierarchy.
    """
    if timeframe not in _TF_HIERARCHY:
        return []
    idx = _TF_HIERARCHY.index(timeframe)
    higher = _TF_HIERARCHY[idx + 1 :]
    return higher[:2]  # at most two levels up


def load_mtf_ohlcv(symbol: str, timeframe: str, db_path: str) -> pd.DataFrame:
    """
    Load OHLCV from ohlcv_history (falls back to live_candles) for a given
    symbol/timeframe pair. Returns DataFrame sorted ascending by timestamp.
    Returns empty DataFrame if no data found.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv_history
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp ASC
            """,
            (symbol, timeframe),
        ).fetchall()

        if not rows:
            rows = conn.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM live_candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp ASC
                """,
                (symbol, timeframe),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df.reset_index(drop=True)


def build_mtf_trend_filter(
    mtf_ohlcv: pd.DataFrame,
    ema_period: int = _DEFAULT_EMA_PERIOD,
    adx_period: int = _DEFAULT_ADX_PERIOD,
    adx_threshold: float = _DEFAULT_ADX_THRESHOLD,
) -> pd.DataFrame:
    """
    Compute EMA and ADX on the higher-timeframe OHLCV and return a DataFrame
    with columns:
        timestamp   — Unix ms timestamp of each higher-TF bar
        trend_up    — bool: close > EMA AND ADX > threshold (bullish trend)
        trend_down  — bool: close < EMA AND ADX > threshold (bearish trend)
        adx         — raw ADX value

    Returns empty DataFrame if mtf_ohlcv is too short.
    """
    min_bars = max(ema_period, adx_period * 2) + 5
    if len(mtf_ohlcv) < min_bars:
        return pd.DataFrame(columns=["timestamp", "trend_up", "trend_down", "adx"])

    ema = compute_ema(mtf_ohlcv["close"], ema_period)
    adx = compute_adx(mtf_ohlcv["high"], mtf_ohlcv["low"], mtf_ohlcv["close"], adx_period)

    trend_up = (mtf_ohlcv["close"] > ema) & (adx > adx_threshold)
    trend_down = (mtf_ohlcv["close"] < ema) & (adx > adx_threshold)

    return pd.DataFrame({
        "timestamp": mtf_ohlcv["timestamp"],
        "trend_up": trend_up.fillna(False),
        "trend_down": trend_down.fillna(False),
        "adx": adx,
    }).reset_index(drop=True)


def apply_mtf_confirm(
    signals: pd.Series,
    base_ohlcv: pd.DataFrame,
    symbol: str,
    higher_tf: str,
    db_path: str,
    ema_period: int = _DEFAULT_EMA_PERIOD,
    adx_period: int = _DEFAULT_ADX_PERIOD,
    adx_threshold: float = _DEFAULT_ADX_THRESHOLD,
) -> pd.Series:
    """
    Gate long entry signals (value == 1) by higher-timeframe trend confirmation.
    Entry signals are suppressed when the higher-TF trend is down or trendless.
    Exit signals (-1) are always passed through unchanged.

    Args:
        signals:     Signal Series aligned to base_ohlcv (values -1, 0, 1).
        base_ohlcv:  Base timeframe OHLCV DataFrame with a 'timestamp' column.
        symbol:      Trading pair, e.g. 'BTC/USDT'.
        higher_tf:   Higher timeframe to load, e.g. '4h'.
        db_path:     Path to SQLite DB.
        ema_period:  EMA period for trend filter.
        adx_period:  ADX period for trend strength filter.
        adx_threshold: Minimum ADX value to consider a trend valid.

    Returns:
        Filtered signal Series. Returns original signals unchanged if higher-TF
        data is unavailable (graceful degradation).
    """
    mtf_ohlcv = load_mtf_ohlcv(symbol, higher_tf, db_path)
    if mtf_ohlcv.empty:
        logger.debug("MTF confirm: no data for %s %s — signals unfiltered", symbol, higher_tf)
        return signals

    trend_filter = build_mtf_trend_filter(mtf_ohlcv, ema_period, adx_period, adx_threshold)
    if trend_filter.empty:
        logger.debug("MTF confirm: insufficient bars for %s %s — signals unfiltered", symbol, higher_tf)
        return signals

    # For each base-TF bar, find the most recent completed higher-TF bar
    # and check whether trend_up is True. Use merge_asof (backward fill).
    base_ts = base_ohlcv["timestamp"].reset_index(drop=True)
    filter_sorted = trend_filter.sort_values("timestamp").reset_index(drop=True)

    merged = pd.merge_asof(
        pd.DataFrame({"timestamp": base_ts}),
        filter_sorted[["timestamp", "trend_up"]],
        on="timestamp",
        direction="backward",
    )

    trend_up_aligned = merged["trend_up"].fillna(False).values

    filtered = signals.copy()
    # Suppress long entries where higher-TF trend is not up
    filtered[(signals == 1) & ~trend_up_aligned] = 0

    suppressed = int((signals == 1).sum() - (filtered == 1).sum())
    if suppressed > 0:
        logger.debug("MTF confirm (%s): suppressed %d/%d long entries", higher_tf, suppressed, int((signals == 1).sum()))

    return filtered
