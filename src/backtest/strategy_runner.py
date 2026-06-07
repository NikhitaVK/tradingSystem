"""
strategy_runner.py — Parse a strategy spec and produce a signal Series.

build_signals() is the only public function. It:
  1. Computes all indicators declared in the spec
  2. Evaluates entry and exit conditions
  3. Applies a .shift(1) so signals execute at the NEXT bar's open (no look-ahead)
  4. Masks the indicator warm-up period with zeros

Returns a pandas Series:
    1  = enter long (execute at next bar's open)
   -1  = exit long  (execute at next bar's open)
    0  = hold / no action
"""
import logging

import pandas as pd

from src.backtest.indicators import (
    compute_rsi,
    compute_ema,
    compute_macd,
    compute_bb,
    compute_atr,
    compute_adx,
    indicator_lookback,
)
from src.backtest.mtf_confirmer import apply_mtf_confirm, get_higher_timeframes

logger = logging.getLogger(__name__)


# ── Column name builders ──────────────────────────────────────────────────────

def _indicator_columns(spec: dict) -> dict:
    """
    Return a map of indicator_key → column name(s) for a single indicator spec entry.
    e.g. {"type": "RSI", "period": 14} → {"RSI_14": "RSI_14"}
    """
    itype = spec["type"].upper()
    period = spec.get("period", 14)

    if itype == "RSI":
        key = f"RSI_{period}"
        return {key: key}

    if itype == "EMA":
        key = f"EMA_{period}"
        return {key: key}

    if itype == "MACD":
        fast = spec.get("fast", 12)
        slow = spec.get("slow", 26)
        signal = spec.get("signal", 9)
        base = f"MACD_{fast}_{slow}_{signal}"
        return {
            base: base,
            f"{base}_signal": f"{base}_signal",
            f"{base}_hist": f"{base}_hist",
        }

    if itype == "BB":
        return {
            f"BB_upper_{period}": f"BB_upper_{period}",
            f"BB_mid_{period}": f"BB_mid_{period}",
            f"BB_lower_{period}": f"BB_lower_{period}",
        }

    if itype == "ATR":
        key = f"ATR_{period}"
        return {key: key}

    if itype == "ADX":
        key = f"ADX_{period}"
        return {key: key}

    raise ValueError(f"Unknown indicator type: {itype}")


# ── Indicator computation ─────────────────────────────────────────────────────

def _add_indicators(df: pd.DataFrame, indicators_spec: list) -> pd.DataFrame:
    """
    Add all indicator columns declared in strategy_spec['indicators'] to df.
    Returns a copy — does not mutate the input.
    """
    df = df.copy()
    for spec in indicators_spec:
        itype = spec["type"].upper()
        period = spec.get("period", 14)

        if itype == "RSI":
            df[f"RSI_{period}"] = compute_rsi(df["close"], period)

        elif itype == "EMA":
            df[f"EMA_{period}"] = compute_ema(df["close"], period)

        elif itype == "MACD":
            fast = spec.get("fast", 12)
            slow = spec.get("slow", 26)
            signal = spec.get("signal", 9)
            result = compute_macd(df["close"], fast, slow, signal)
            base = f"MACD_{fast}_{slow}_{signal}"
            df[base] = result["macd"]
            df[f"{base}_signal"] = result["signal"]
            df[f"{base}_hist"] = result["hist"]

        elif itype == "BB":
            std_dev = spec.get("std_dev", 2.0)
            result = compute_bb(df["close"], period, std_dev)
            df[f"BB_upper_{period}"] = result["upper"]
            df[f"BB_mid_{period}"] = result["mid"]
            df[f"BB_lower_{period}"] = result["lower"]

        elif itype == "ATR":
            df[f"ATR_{period}"] = compute_atr(df["high"], df["low"], df["close"], period)

        elif itype == "ADX":
            df[f"ADX_{period}"] = compute_adx(df["high"], df["low"], df["close"], period)

        else:
            raise ValueError(f"Unknown indicator type: {itype}")

    return df


# ── Condition evaluation ──────────────────────────────────────────────────────

def _resolve_value(df: pd.DataFrame, value) -> pd.Series:
    """
    Resolve a condition value — either a scalar or a column name reference.
    e.g. value=30 → scalar 30
         value="EMA_50" → df["EMA_50"]
         value="price"  → df["close"]
    """
    if isinstance(value, str):
        if value == "price":
            return df["close"]
        return df[value]
    return value  # scalar


def _evaluate_condition(df: pd.DataFrame, condition: dict) -> pd.Series:
    """
    Evaluate a single condition dict against the DataFrame.
    Returns a boolean Series.
    """
    # Stop-loss and take-profit are handled at the trade simulation level, not here.
    # They live in the exit conditions block but have no operator — skip them.
    if condition.get("type") in ("stop_loss_pct", "take_profit_pct"):
        return pd.Series(False, index=df.index)

    operator = condition["operator"]

    indicator_key = condition.get("indicator", "price")
    if indicator_key == "price":
        lhs = df["close"]
    else:
        lhs = df[indicator_key]

    rhs = _resolve_value(df, condition["value"])

    if operator == "<":
        return lhs < rhs
    if operator == ">":
        return lhs > rhs
    if operator == "<=":
        return lhs <= rhs
    if operator == ">=":
        return lhs >= rhs
    if operator == "==":
        return lhs == rhs
    if operator == "crosses_above":
        return (lhs.shift(1) <= rhs) & (lhs > rhs)
    if operator == "crosses_below":
        return (lhs.shift(1) >= rhs) & (lhs < rhs)

    raise ValueError(f"Unknown operator: {operator}")


def _evaluate_conditions(df: pd.DataFrame, conditions_spec: dict) -> pd.Series:
    """
    Evaluate a conditions block (with 'logic' AND/OR and a 'conditions' list).
    Returns a boolean Series.
    """
    logic = conditions_spec.get("logic", "AND").upper()
    results = [_evaluate_condition(df, c) for c in conditions_spec["conditions"]]

    if not results:
        return pd.Series(False, index=df.index)

    combined = results[0]
    for r in results[1:]:
        if logic == "AND":
            combined = combined & r
        else:  # OR
            combined = combined | r

    return combined


# ── Public API ────────────────────────────────────────────────────────────────

def build_signals(
    ohlcv: pd.DataFrame,
    strategy_spec: dict,
    use_mtf_confirm: bool = False,
    confirm_timeframe: str = None,
    db_path: str = None,
) -> pd.Series:
    """
    Interpret a strategy spec and return a signal Series aligned to ohlcv.index.

    Signal values:
        1  = enter long  (execute at open of NEXT bar)
       -1  = exit long   (execute at open of NEXT bar)
        0  = hold

    Look-ahead prevention:
        - The raw entry/exit boolean is shifted forward by 1 bar.
          A signal generated on bar t is therefore acted on at bar t+1's open.
        - Signals during the indicator warm-up period are forced to 0.

    Args:
        ohlcv:              DataFrame with columns: open, high, low, close, volume
                            and timestamp. Must be sorted ascending by timestamp.
        strategy_spec:      Strategy spec dict (see module2_backtest.md for schema).
        use_mtf_confirm:    If True, gate long entries with a higher-TF trend filter.
        confirm_timeframe:  Higher timeframe to use for MTF filter (e.g. '4h').
                            Auto-selected from spec's timeframe if not provided.
        db_path:            Path to SQLite DB (required when use_mtf_confirm=True).

    Returns:
        pd.Series of int8 with values -1, 0, 1.
    """
    if ohlcv.empty:
        return pd.Series(dtype="int8")

    df = _add_indicators(ohlcv.reset_index(drop=True), strategy_spec.get("indicators", []))

    # Compute warm-up length — max lookback across all indicators in spec
    min_valid_bar = max(
        (indicator_lookback(ind) for ind in strategy_spec.get("indicators", [])),
        default=0,
    )

    # Evaluate entry and exit conditions
    entry_mask = _evaluate_conditions(df, strategy_spec["entry"])
    exit_mask = _evaluate_conditions(df, strategy_spec["exit"])

    # Build raw signal: 1 on entry, -1 on exit, 0 otherwise
    # Entry takes precedence if both fire on the same bar (shouldn't happen in practice)
    raw = pd.Series(0, index=df.index, dtype="int8")
    raw[exit_mask] = -1
    raw[entry_mask] = 1

    # Shift forward by 1 — signal fires at NEXT bar's open
    shifted = raw.shift(1).fillna(0).astype("int8")

    # Zero out warm-up period (signals before indicators are valid are noise)
    # +2: one for the shift itself, one for the first bar which may have noisy RSI=100
    shifted.iloc[: min_valid_bar + 2] = 0

    # Optional multi-timeframe trend confirmation filter (Phase 9)
    if use_mtf_confirm and db_path:
        symbol = strategy_spec.get("symbol", "BTC/USDT")
        base_tf = strategy_spec.get("timeframe", "1h")
        higher_tf = confirm_timeframe
        if higher_tf is None:
            candidates = get_higher_timeframes(base_tf)
            higher_tf = candidates[0] if candidates else None
        if higher_tf:
            shifted = apply_mtf_confirm(shifted, ohlcv.reset_index(drop=True), symbol, higher_tf, db_path)

    return shifted
