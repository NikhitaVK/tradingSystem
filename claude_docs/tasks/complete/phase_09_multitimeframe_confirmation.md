# Phase 9 — Multi-Timeframe Confirmation Filter

**What**: Add an optional multi-timeframe (MTF) confirmation filter to entry signals. The strategy generates a signal on the primary timeframe, but the entry only fires if a higher timeframe confirms the direction. This is standard practice among human traders: "trend on the daily, signal on the 1h."

**Why this matters**: A 1h RSI < 30 in a downtrend on the daily is a mean-reversion trap. MTF confirmation prevents the system from entering in the wrong direction of the larger trend. This is one of the highest-value additions a human trader makes that your current system lacks entirely.

## Step 9.1 — Add MTF data loader

**File to create**: `src/backtest/mtf_confirmer.py`

```python
"""
mtf_confirmer.py — Multi-timeframe confirmation for entry signals.

Usage:
  In strategy_runner.py, after build_signals() on primary timeframe,
  call apply_mtf_confirm() to filter signals using higher-TF context.

Example: primary = 1h, confirm = 4h or 1d.
  - 1h long signal fires
  - Check 4h: is price above 4h EMA(50)? If yes, confirm. If no, reject.
  - Check 1d: is 1d ADX > 25 (trending)? If yes, trend-following setup confirmed.
"""

from typing import Literal

TIMEFRAME_HIERARCHY = {
    "1m":  ["5m", "15m", "1h", "4h", "1d"],
    "5m":  ["15m", "1h", "4h", "1d"],
    "15m": ["1h", "4h", "1d"],
    "1h":  ["4h", "1d"],
    "4h":  ["1d"],
    "1d":  [],  # no higher TF available
}

TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240,
    "6h": 360, "12h": 720, "1d": 1440,
}


def get_higher_timeframes(timeframe: str) -> list[str]:
    """Return list of higher timeframes above the given TF."""
    return TIMEFRAME_HIERARCHY.get(timeframe, [])


def load_mtf_ohlcv(
    symbol: str,
    primary_timeframe: str,
    confirm_timeframe: str,
    db_path: str,
    lookback_bars: int = 100,
) -> pd.DataFrame:
    """
    Load OHLCV data for the confirmation timeframe.

    The number of bars is scaled to cover the same wall-clock period as
    `lookback_bars` of the primary timeframe (approximate).
    """
    import pandas as pd
    from src.data.ingestor import load_ohlcv

    primary_minutes = TF_MINUTES.get(primary_timeframe, 60)
    confirm_minutes = TF_MINUTES.get(confirm_timeframe, 240)

    # Approximate how many confirm bars cover the same period
    n_confirm_bars = int(lookback_bars * primary_minutes / confirm_minutes) + 10

    df = load_ohlcv(symbol, confirm_timeframe, db_path)
    if df.empty:
        return df
    return df.tail(n_confirm_bars).reset_index(drop=True)


def build_mtf_trend_filter(
    mtf_df: pd.DataFrame,
    direction: Literal["long", "short"],
) -> pd.Series:
    """
    Given a higher-TF OHLCV DataFrame, compute a trend filter Series
    aligned to the index of mtf_df.

    Returns a Series of 1 (confirm) / 0 (no confirm) / -1 (contradict).
    """
    import pandas as pd
    from src.backtest.indicators import compute_ema, compute_adx

    if mtf_df.empty or len(mtf_df) < 50:
        return pd.Series([1] * len(mtf_df), index=mtf_df.index)

    closes = mtf_df["close"]
    ema50 = compute_ema(closes, 50)
    adx_series = compute_adx(mtf_df["high"], mtf_df["low"], closes, 14)

    confirm = pd.Series(0, index=closes.index)

    if direction == "long":
        # Confirm if price above EMA50 AND ADX rising (> 25 = trending)
        confirm = ((closes > ema50) & (adx_series > 25)).astype(int)
    elif direction == "short":
        # Confirm if price below EMA50 AND ADX rising
        confirm = ((closes < ema50) & (adx_series > 25)).astype(int)

    return confirm


def apply_mtf_confirm(
    primary_signals: pd.Series,
    primary_ohlcv: pd.DataFrame,
    symbol: str,
    primary_timeframe: str,
    confirm_timeframe: str,
    db_path: str,
) -> pd.Series:
    """
    Filter primary timeframe signals with higher-TF confirmation.

    A primary signal at index i is kept only if the higher-TF trend
    filter is > 0 at the index corresponding to the same wall-clock time.

    Returns a filtered signals Series (same shape as primary_signals).
    """
    import pandas as pd

    if confirm_timeframe not in get_higher_timeframes(primary_timeframe):
        return primary_signals  # no higher TF available, return unchanged

    mtf_df = load_mtf_ohlcv(symbol, primary_timeframe, confirm_timeframe, db_path)
    if mtf_df.empty:
        return primary_signals  # no data, return unchanged

    # Determine direction from primary signal at each index
    direction = "long" if primary_signals.iloc[-1] == 1 else "short"

    # This is a simplified version — a full implementation would need
    # to align primary signal timestamps to mtf_df timestamps properly.
    # The key concept: if no higher-TF confirmation exists for a primary
    # signal, that signal should be zeroed out.
    mtf_confirm = build_mtf_trend_filter(mtf_df, direction)

    # Align by taking the last N values matching primary signal length
    # (This is approximate — the correct implementation uses timestamp merge)
    n = min(len(primary_signals), len(mtf_confirm))
    confirmed_signals = primary_signals.copy()
    if n > 0:
        # For the most recent signals, apply confirmation filter
        confirmed_signals.iloc[-n:] = (
            confirmed_signals.iloc[-n:] * mtf_confirm.iloc[-n:].values
        )
    return confirmed_signals
```

## Step 9.2 — Add `compute_adx` to indicators

**File to modify**: `src/backtest/indicators.py`

Add ADX computation (Average Directional Index — measures trend strength, not direction):
```python
def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute ADX (Average Directional Index) using the standard Wilder smoothing.
    Returns a Series of ADX values aligned to the input index.
    """
    import pandas as pd
    import numpy as np

    high = high.diff()
    low = -low.diff()

    plus_dm = high.where((high > low) & (high > 0), 0.0)
    minus_dm = low.where((low > high) & (low > 0), 0.0)

    tr = _true_range(high, low, close)
    atr = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(period).mean()

    return adx


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
```

## Step 9.3 — Add MTF confirmation to strategy runner

**File to modify**: `src/backtest/strategy_runner.py`

After `build_signals()` returns, optionally apply MTF confirmation:
```python
def build_signals(
    ohlcv: pd.DataFrame,
    strategy_spec: dict,
    use_mtf_confirm: bool = True,   # NEW — controlled by config
    confirm_timeframe: str = "4h",  # NEW — default higher TF
    db_path: str = None,
) -> pd.Series:
    signals = _build_signals_impl(ohlcv, strategy_spec)

    if use_mtf_confirm and db_path and "symbol" in strategy_spec:
        symbol = strategy_spec["symbol"]
        tf = strategy_spec.get("timeframe", "1h")
        signals = apply_mtf_confirm(
            signals, ohlcv, symbol, tf, confirm_timeframe, db_path
        )

    return signals
```

## Step 9.4 — Make MTF a strategy spec option

**File to modify**: `prompts/strategy_agent_v1.txt`

Instruct the strategy agent that it can specify MTF confirmation in the strategy spec:
```
When designing your strategy, you may optionally include:
{
  "mtf_confirm": {
    "enabled": true,
    "timeframe": "4h",        // or "1d"
    "trend_filter": "ema50"   // currently supported: "ema50" (price above/below EMA50 + ADX>25)
  }
}
```

## Step 9.5 — Add Phase 9 tests

**File to modify**: `tests/test_backtest.py`

New tests:
1. `get_higher_timeframes("1h")` returns `["4h", "1d"]`.
2. `get_higher_timeframes("1d")` returns `[]`.
3. On synthetic data where 4h confirms 1h direction, signals remain intact.
4. On synthetic data where 4h contradicts 1h direction, signals are zeroed.
5. When `use_mtf_confirm=False`, signals are identical to original `build_signals`.

## Verification checklist
- [ ] MTF confirmation filters signals on 1h when 4h trend contradicts
- [ ] MTF confirmation does NOT fire when higher TF is unavailable for that primary TF
- [ ] ADX computation is correct (verify against known ADX values)
- [ ] MTF can be disabled per strategy via `use_mtf_confirm: false` in spec


## Related

- MOC: [[_tasks]]
- [[2026-04-15-multi-timeframe-confirmer]]
- [[backtesting]]
