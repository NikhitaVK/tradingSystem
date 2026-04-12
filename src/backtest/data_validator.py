"""
data_validator.py — Pre-backtest OHLCV data quality checks.

Called from run_backtest() before slicing. Drops rows that are physically
impossible (bad data), flags gaps and spikes as warnings, and raises if the
data is too corrupt to backtest reliably.

Checks (in order):
  1. OHLCV integrity  — drop rows where high < low, close/open outside [low, high]
  2. Gap detection    — warn when consecutive timestamp gap > 2× expected interval
  3. Spike flagging   — warn on close-to-close changes > SPIKE_THRESHOLD (don't drop)
  4. Zero volume      — drop rows with volume <= 0

Returns a dict:
    {
        'clean_df':    pd.DataFrame,   # cleaned copy ready for backtesting
        'rows_dropped': int,
        'warnings':    list[str],
    }

Raises ValueError if >1% of rows are bad (corrupt source data).
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Close-to-close % change threshold for spike flagging (does not drop, only warns)
_SPIKE_THRESHOLD = 0.15   # 15%


# ── Timeframe → expected milliseconds per bar ─────────────────────────────────

_TIMEFRAME_MS = {
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
    "2h":  7_200_000,
    "4h":  14_400_000,
    "6h":  21_600_000,
    "12h": 43_200_000,
    "1d":  86_400_000,
}


def validate_ohlcv(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> dict:
    """
    Validate and clean an OHLCV DataFrame before backtesting.

    Args:
        df:         DataFrame with columns: timestamp, open, high, low, close, volume.
                    Must be sorted ascending by timestamp.
        symbol:     Trading pair for log messages (e.g. 'BTC/USDT').
        timeframe:  Bar timeframe string (e.g. '1h').

    Returns:
        {'clean_df': pd.DataFrame, 'rows_dropped': int, 'warnings': list[str]}

    Raises:
        ValueError: If >1% of rows are physically impossible (bad data source).
    """
    warnings: list[str] = []
    df = df.copy().reset_index(drop=True)
    original_len = len(df)

    if original_len == 0:
        return {"clean_df": df, "rows_dropped": 0, "warnings": ["Empty DataFrame"]}

    # ── Check 1: OHLCV integrity ──────────────────────────────────────────────
    bad_mask = (
        (df["high"] < df["low"])
        | (df["close"] > df["high"])
        | (df["close"] < df["low"])
        | (df["open"] > df["high"])
        | (df["open"] < df["low"])
    )
    bad_count = int(bad_mask.sum())
    if bad_count > 0:
        bad_dates = df.loc[bad_mask, "timestamp"].head(3).tolist()
        warnings.append(
            f"OHLCV integrity: {bad_count} row(s) with impossible price relationships "
            f"(e.g. high < low). First offenders at timestamps: {bad_dates}. Dropped."
        )
        df = df[~bad_mask].reset_index(drop=True)

    # ── Check 2: Gap detection ────────────────────────────────────────────────
    expected_ms = _TIMEFRAME_MS.get(timeframe)
    if expected_ms and len(df) > 1:
        diffs = df["timestamp"].diff().dropna()
        gap_mask = diffs > 2 * expected_ms
        gap_count = int(gap_mask.sum())
        if gap_count > 0:
            gap_indices = gap_mask[gap_mask].index
            missing_total = int(((diffs[gap_mask] - expected_ms) / expected_ms).sum())
            first_gap_ts = int(df.loc[gap_indices[0] - 1, "timestamp"])
            warnings.append(
                f"Gaps: {gap_count} gap(s) detected totalling ~{missing_total} missing bars. "
                f"First gap after timestamp {first_gap_ts}. "
                f"Data NOT interpolated — gaps preserved as-is."
            )

    # ── Check 3: Price spike flagging (warn only, do NOT drop) ────────────────
    if len(df) > 1:
        pct_change = df["close"].pct_change().abs()
        spike_mask = pct_change > _SPIKE_THRESHOLD
        spike_count = int(spike_mask.sum())
        if spike_count > 0:
            spike_ts = df.loc[spike_mask, "timestamp"].head(3).tolist()
            warnings.append(
                f"Price spikes: {spike_count} bar(s) with >{_SPIKE_THRESHOLD*100:.0f}% "
                f"close-to-close change. May be legitimate volatility events. "
                f"First occurrences at timestamps: {spike_ts}. NOT dropped."
            )

    # ── Check 4: Zero/null volume ─────────────────────────────────────────────
    zero_vol_mask = df["volume"] <= 0
    zero_vol_count = int(zero_vol_mask.sum())
    if zero_vol_count > 0:
        warnings.append(
            f"Zero volume: {zero_vol_count} bar(s) with volume <= 0. Dropped."
        )
        df = df[~zero_vol_mask].reset_index(drop=True)

    # ── Bad row threshold check ───────────────────────────────────────────────
    rows_dropped = original_len - len(df)
    drop_pct = rows_dropped / original_len if original_len > 0 else 0.0
    if drop_pct > 0.01:
        raise ValueError(
            f"Data quality too poor for backtesting {symbol} {timeframe}: "
            f"{rows_dropped}/{original_len} rows dropped ({drop_pct*100:.1f}%). "
            f"Re-fetch or repair the data before backtesting."
        )

    if warnings:
        for w in warnings:
            logger.warning("[%s %s] %s", symbol, timeframe, w)

    return {
        "clean_df": df,
        "rows_dropped": rows_dropped,
        "warnings": warnings,
    }
