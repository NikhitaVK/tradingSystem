"""
engine.py — Walk-forward backtesting engine.

run_backtest() is the only public function. It:
  1. Loads ohlcv_history for the strategy's symbol/timeframe
  2. Validates data quality (drops corrupt rows, warns on gaps/spikes)
  3. Splits into n_slices non-overlapping chunks
  4. For each slice: runs signals on the full slice, simulates trades on OOS portion,
     also simulates trades on IS portion (for Walk-Forward Efficiency)
  5. Computes per-slice and aggregate metrics (incl. Sortino, profit factor, regime)
  6. Calibrates degradation threshold and ATR position sizing
  7. Returns a standardised results dict

This results dict is the contract between the backtest engine and all agents.
Never change its shape without updating both sides.
"""
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from config.settings import (
    BACKTEST_MIN_TRADES_PER_SLICE,
    BACKTEST_N_SLICES,
    BACKTEST_TRAIN_RATIO,
    ATR_PERIOD,
    ATR_MULTIPLIER,
    RISK_PER_TRADE_PCT,
    SLIPPAGE_PER_SIDE,
    EXCHANGE_FEE_PER_SIDE,
    STOP_LOSS_EXTRA_SLIPPAGE,
    VOLUME_SLIPPAGE_THRESHOLD,
    VOLUME_SLIPPAGE_MAX_MULTIPLIER,
)
from src.data.ingestor import load_ohlcv
from src.backtest.strategy_runner import build_signals
from src.backtest.indicators import compute_atr
from src.backtest.data_validator import validate_ohlcv

logger = logging.getLogger(__name__)

# Annualisation factors by timeframe — sqrt(periods_per_year)
_PERIODS_PER_YEAR = {
    "1m": 252 * 24 * 60,
    "5m": 252 * 24 * 12,
    "15m": 252 * 24 * 4,
    "30m": 252 * 24 * 2,
    "1h": 252 * 24,
    "2h": 252 * 12,
    "4h": 252 * 6,
    "6h": 252 * 4,
    "12h": 252 * 2,
    "1d": 252,
}


# ── Volume-proportional slippage ──────────────────────────────────────────────

def _volume_adjusted_slippage(base: float, position_usdt: float, bar_volume_usdt: float) -> float:
    """
    Scale slippage linearly when position size exceeds VOLUME_SLIPPAGE_THRESHOLD
    fraction of bar volume. Caps at VOLUME_SLIPPAGE_MAX_MULTIPLIER × base.

    Prevents assuming large orders fill at the same cost as small retail orders.
    """
    if bar_volume_usdt <= 0 or position_usdt <= 0:
        return base
    fill_pct = min(position_usdt / bar_volume_usdt, 1.0)
    if fill_pct <= VOLUME_SLIPPAGE_THRESHOLD:
        return base
    scale = 1.0 + (
        (fill_pct - VOLUME_SLIPPAGE_THRESHOLD)
        / (1.0 - VOLUME_SLIPPAGE_THRESHOLD)
        * (VOLUME_SLIPPAGE_MAX_MULTIPLIER - 1.0)
    )
    return base * scale


# ── Regime tagging ────────────────────────────────────────────────────────────

def _tag_regime(oos_df: pd.DataFrame) -> str:
    """
    Tag an OOS window as 'bull', 'bear', or 'sideways' based on the price
    return across the window. Thresholds: >+5% = bull, <-5% = bear.
    """
    if len(oos_df) < 2:
        return "sideways"
    start = float(oos_df["close"].iloc[0])
    end = float(oos_df["close"].iloc[-1])
    if start == 0:
        return "sideways"
    pct = (end - start) / start
    if pct > 0.05:
        return "bull"
    if pct < -0.05:
        return "bear"
    return "sideways"


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_trades(
    ohlcv: pd.DataFrame,
    signals: pd.Series,
    stop_loss_pct: float,
    take_profit_pct: float,
    position_size_usdt: float = 10_000.0,
) -> list:
    """
    Simulate long trades on OHLCV given a signal Series.

    Entry:  signal == 1 → buy at this bar's open with costs
    Exit:   signal == -1 OR stop-loss OR take-profit hit

    Stop-loss fills include STOP_LOSS_EXTRA_SLIPPAGE to model gap-through fills.
    All fills use volume-proportional slippage scaling.

    Returns list of trade dicts: {entry_price, exit_price, pnl_pct, win}
    """
    total_cost_per_side = SLIPPAGE_PER_SIDE + EXCHANGE_FEE_PER_SIDE
    trades = []
    in_trade = False
    entry_price = None

    df = ohlcv.reset_index(drop=True)
    sig = signals.reset_index(drop=True)

    for i in range(len(df)):
        row = df.iloc[i]
        bar_volume_usdt = float(row["close"]) * float(row["volume"])

        if in_trade:
            stop_price = entry_price * (1 - stop_loss_pct / 100)
            tp_price = entry_price * (1 + take_profit_pct / 100)

            exit_triggered = False
            exit_price = None

            if row["low"] <= stop_price:
                # Stop-loss: incur extra slippage for gap-through fills
                exit_slip = _volume_adjusted_slippage(
                    total_cost_per_side + STOP_LOSS_EXTRA_SLIPPAGE,
                    position_size_usdt,
                    bar_volume_usdt,
                )
                exit_price = stop_price * (1 - exit_slip)
                exit_triggered = True
            elif row["high"] >= tp_price:
                # Take-profit: limit order, no extra slippage
                exit_slip = _volume_adjusted_slippage(
                    total_cost_per_side, position_size_usdt, bar_volume_usdt
                )
                exit_price = tp_price * (1 - exit_slip)
                exit_triggered = True
            elif sig.iloc[i] == -1:
                # Signal exit at bar open
                exit_slip = _volume_adjusted_slippage(
                    total_cost_per_side, position_size_usdt, bar_volume_usdt
                )
                exit_price = row["open"] * (1 - exit_slip)
                exit_triggered = True

            if exit_triggered:
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append({
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "win": pnl_pct > 0,
                })
                in_trade = False
                entry_price = None

        else:
            if sig.iloc[i] == 1 and i + 1 < len(df):
                entry_slip = _volume_adjusted_slippage(
                    total_cost_per_side, position_size_usdt, bar_volume_usdt
                )
                entry_price = row["open"] * (1 + entry_slip)
                in_trade = True

    # Close any open trade at last bar's close
    if in_trade and len(df) > 0:
        row = df.iloc[-1]
        bar_volume_usdt = float(row["close"]) * float(row["volume"])
        exit_slip = _volume_adjusted_slippage(
            total_cost_per_side, position_size_usdt, bar_volume_usdt
        )
        last_close = row["close"] * (1 - exit_slip)
        pnl_pct = (last_close - entry_price) / entry_price
        trades.append({
            "entry_price": entry_price,
            "exit_price": last_close,
            "pnl_pct": pnl_pct,
            "win": pnl_pct > 0,
        })

    return trades


# ── Per-slice metrics ─────────────────────────────────────────────────────────

def _compute_slice_metrics(
    trades: list,
    timeframe: str,
    slice_id: int,
    start_ts: int,
    end_ts: int,
    regime: str = "sideways",
) -> dict:
    """
    Compute metrics for a single slice from its trade list.

    Returns the slice result dict including:
      Core:    win_rate, sharpe, max_drawdown, total_trades, pnl_pct
      Extended: sortino, profit_factor, expectancy, avg_win_loss_ratio,
                max_consecutive_losses, regime
    """
    def _ts_to_date(ts_ms: int) -> str:
        return datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")

    total_trades = len(trades)
    degenerate = total_trades < BACKTEST_MIN_TRADES_PER_SLICE

    if degenerate or total_trades == 0:
        return {
            "slice_id": slice_id,
            "start_date": _ts_to_date(start_ts),
            "end_date": _ts_to_date(end_ts),
            "regime": regime,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "total_trades": total_trades,
            "pnl_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win_loss_ratio": 0.0,
            "max_consecutive_losses": 0,
            "degenerate": True,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [t["win"] for t in trades]

    win_rate = sum(wins) / total_trades
    total_pnl = sum(pnls)

    pos_pnls = [p for p in pnls if p > 0]
    neg_pnls = [p for p in pnls if p <= 0]

    # Sharpe — annualised, timeframe-aware
    periods_per_year = _PERIODS_PER_YEAR.get(timeframe, 252)
    mean_ret = float(np.mean(pnls))
    std_ret = float(np.std(pnls, ddof=1)) if len(pnls) > 1 else 0.0
    sharpe = (mean_ret / std_ret) * np.sqrt(periods_per_year) if std_ret > 0 else 0.0

    # Sortino — penalises downside volatility only
    if len(neg_pnls) > 1:
        downside_std = float(np.std(neg_pnls, ddof=1))
        sortino = (mean_ret / downside_std) * np.sqrt(periods_per_year) if downside_std > 0 else 0.0
    else:
        sortino = sharpe  # no downside volatility → use Sharpe as proxy

    # Max drawdown — peak-to-trough on cumulative PnL
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    drawdowns = peak - cum
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Profit factor — gross profit / gross loss
    gross_profit = sum(pos_pnls)
    gross_loss = abs(sum(neg_pnls))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Expectancy — average expected return per trade
    avg_win = float(np.mean(pos_pnls)) if pos_pnls else 0.0
    avg_loss = float(abs(np.mean(neg_pnls))) if neg_pnls else 0.0
    loss_rate = 1.0 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    # Win/loss ratio
    avg_win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    # Max consecutive losses
    max_consec = cur_consec = 0
    for w in wins:
        cur_consec = cur_consec + 1 if not w else 0
        max_consec = max(max_consec, cur_consec)

    return {
        "slice_id": slice_id,
        "start_date": _ts_to_date(start_ts),
        "end_date": _ts_to_date(end_ts),
        "regime": regime,
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 4),
        "total_trades": total_trades,
        "pnl_pct": round(total_pnl, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
        "expectancy": round(expectancy, 6),
        "avg_win_loss_ratio": round(avg_win_loss_ratio, 4) if avg_win_loss_ratio != float("inf") else 999.0,
        "max_consecutive_losses": max_consec,
        "degenerate": False,
    }


# ── Calibration ───────────────────────────────────────────────────────────────

def _calibrate(
    oos_slices: list,
    is_slices: list,
    full_df: pd.DataFrame,
    last_oos_end_idx: int,
    notional_account: float = 10_000.0,
) -> dict:
    """
    Compute degradation threshold, ATR-based position sizing, and
    Walk-Forward Efficiency from slice results and the full dataset.

    Args:
        oos_slices:       Out-of-sample slice result dicts.
        is_slices:        In-sample slice result dicts (for WFE).
        full_df:          Full OHLCV DataFrame (used for ATR at last OOS end).
        last_oos_end_idx: Index into full_df where the last OOS window ends.
        notional_account: Account size in USDT for position sizing.
    """
    # Degradation threshold
    win_rates = [s["win_rate"] for s in oos_slices if not s.get("degenerate", False)]
    if not win_rates:
        degradation_threshold = 0.30
    else:
        mean_wr = float(np.mean(win_rates))
        std_wr = float(np.std(win_rates, ddof=1)) if len(win_rates) > 1 else 0.0
        degradation_threshold = max(mean_wr - std_wr, 0.30)

    # ATR position sizing — use ATR at end of last OOS window (not end of all-time data)
    sizing_df = full_df.iloc[: last_oos_end_idx + 1] if last_oos_end_idx < len(full_df) - 1 else full_df
    atr_series = compute_atr(sizing_df["high"], sizing_df["low"], sizing_df["close"], ATR_PERIOD)
    last_atr = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else 1.0
    last_price = float(sizing_df["close"].iloc[-1])

    stop_distance = last_atr * ATR_MULTIPLIER
    risk_amount = notional_account * RISK_PER_TRADE_PCT
    position_size_usdt = risk_amount / (stop_distance / last_price) if stop_distance > 0 else 0.0

    # Walk-Forward Efficiency: OOS Sharpe / IS Sharpe
    oos_sharpes = [s["sharpe"] for s in oos_slices if not s.get("degenerate", False)]
    is_sharpes = [s["sharpe"] for s in is_slices if not s.get("degenerate", False)]
    oos_sharpe_mean = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
    is_sharpe_mean = float(np.mean(is_sharpes)) if is_sharpes else 0.0

    if is_sharpe_mean > 0:
        wfe = round(oos_sharpe_mean / is_sharpe_mean, 4)
    else:
        wfe = None  # IS Sharpe non-positive → WFE undefined

    if wfe is not None and wfe < 0.5:
        logger.warning(
            "Low Walk-Forward Efficiency (%.2f) — strategy may be overfitted to in-sample data. "
            "The analyst agent should scrutinise this result.",
            wfe,
        )

    return {
        "degradation_threshold": round(degradation_threshold, 4),
        "walk_forward_efficiency": wfe,
        "position_sizing": {
            "method": "atr",
            "atr_period": ATR_PERIOD,
            "atr_multiplier": ATR_MULTIPLIER,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "position_size_usdt": round(position_size_usdt, 2),
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_backtest(
    strategy_spec: dict,
    db_path: str,
    n_slices: int = BACKTEST_N_SLICES,
) -> dict:
    """
    Run a walk-forward backtest for a strategy spec.

    Args:
        strategy_spec:  Strategy spec dict. Must include 'symbol', 'timeframe',
                        'indicators', 'entry', 'exit'. See module2_backtest.md.
        db_path:        Path to SQLite database with ohlcv_history table.
        n_slices:       Number of walk-forward slices (default from settings).

    Returns:
        Results dict with keys: 'slices', 'aggregate', 'calibration', 'viable'.
        viable=False if any slice is degenerate or aggregate Sharpe <= 0.

    Raises:
        ValueError: If insufficient data or data quality too poor.
    """
    symbol = strategy_spec["symbol"]
    timeframe = strategy_spec["timeframe"]

    # Pull stop-loss / take-profit from exit conditions
    stop_loss_pct = 2.0
    take_profit_pct = 4.0
    for cond in strategy_spec.get("exit", {}).get("conditions", []):
        if cond.get("type") == "stop_loss_pct":
            stop_loss_pct = cond["value"]
        if cond.get("type") == "take_profit_pct":
            take_profit_pct = cond["value"]

    # Load data
    full_df = load_ohlcv(symbol, timeframe, db_path)
    if full_df.empty:
        raise ValueError(f"No data found for {symbol} {timeframe} in {db_path}")

    # Validate and clean data
    validation = validate_ohlcv(full_df, symbol, timeframe)
    full_df = validation["clean_df"]
    if validation["rows_dropped"] > 0:
        logger.info(
            "Dropped %d corrupt rows from %s %s before backtesting",
            validation["rows_dropped"], symbol, timeframe,
        )

    # Data sufficiency check
    train_ratio = BACKTEST_TRAIN_RATIO
    min_bars = n_slices * int(1 / (1 - train_ratio)) * BACKTEST_MIN_TRADES_PER_SLICE * 10
    if len(full_df) < min_bars:
        raise ValueError(
            f"Insufficient data for {symbol} {timeframe}: "
            f"{len(full_df)} bars available, need ~{min_bars} "
            f"({n_slices} slices × {BACKTEST_MIN_TRADES_PER_SLICE} min trades)."
        )

    logger.info(
        "Backtesting %s %s | %d bars | %d slices",
        symbol, timeframe, len(full_df), n_slices,
    )

    slice_size = len(full_df) // n_slices
    oos_results = []
    is_results = []
    last_oos_end_idx = 0

    for i in range(n_slices):
        start_idx = i * slice_size
        end_idx = start_idx + slice_size if i < n_slices - 1 else len(full_df)
        slice_df = full_df.iloc[start_idx:end_idx].reset_index(drop=True)

        split_idx = int(len(slice_df) * train_ratio)
        in_sample = slice_df.iloc[:split_idx].reset_index(drop=True)
        out_of_sample = slice_df.iloc[split_idx:].reset_index(drop=True)

        # Signals on full slice (indicators need warm-up from in-sample)
        all_signals = build_signals(slice_df, strategy_spec)
        oos_signals = all_signals.iloc[split_idx:].reset_index(drop=True)
        is_signals = all_signals.iloc[:split_idx].reset_index(drop=True)

        # Position size for volume-scaling (use calibrated value, default 10k)
        pos_size = 10_000.0

        # OOS trade simulation
        oos_trades = _simulate_trades(
            out_of_sample, oos_signals, stop_loss_pct, take_profit_pct, pos_size
        )
        # IS trade simulation (for Walk-Forward Efficiency)
        is_trades = _simulate_trades(
            in_sample, is_signals, stop_loss_pct, take_profit_pct, pos_size
        )

        oos_start_ts = int(out_of_sample.iloc[0]["timestamp"])
        oos_end_ts = int(out_of_sample.iloc[-1]["timestamp"])
        regime = _tag_regime(out_of_sample)

        oos_result = _compute_slice_metrics(
            oos_trades, timeframe, i + 1, oos_start_ts, oos_end_ts, regime
        )
        is_result = _compute_slice_metrics(
            is_trades, timeframe, i + 1,
            int(in_sample.iloc[0]["timestamp"]),
            int(in_sample.iloc[-1]["timestamp"]),
        )

        oos_results.append(oos_result)
        is_results.append(is_result)
        last_oos_end_idx = end_idx - 1  # global index of last OOS bar

        logger.debug(
            "Slice %d [%s]: %d trades, win=%.2f, sharpe=%.2f, sortino=%.2f, "
            "pf=%.2f, regime=%s, degenerate=%s",
            i + 1, regime,
            oos_result["total_trades"], oos_result["win_rate"],
            oos_result["sharpe"], oos_result["sortino"],
            oos_result["profit_factor"], regime,
            oos_result.get("degenerate", False),
        )

    # Aggregate across OOS slices
    non_degen = [s for s in oos_results if not s.get("degenerate", False)]
    any_degenerate = len(non_degen) < n_slices

    if non_degen:
        win_rate_mean = float(np.mean([s["win_rate"] for s in non_degen]))
        sharpe_mean = float(np.mean([s["sharpe"] for s in non_degen]))
        sortino_mean = float(np.mean([s["sortino"] for s in non_degen]))
        max_dd_worst = float(max(s["max_drawdown"] for s in oos_results))
        total_trades = sum(s["total_trades"] for s in oos_results)
        profit_factor_mean = float(np.mean([s["profit_factor"] for s in non_degen]))
    else:
        win_rate_mean = sharpe_mean = sortino_mean = max_dd_worst = 0.0
        total_trades = 0
        profit_factor_mean = 0.0

    aggregate = {
        "win_rate_mean": round(win_rate_mean, 4),
        "sharpe_mean": round(sharpe_mean, 4),
        "sortino_mean": round(sortino_mean, 4),
        "max_drawdown_worst": round(max_dd_worst, 4),
        "total_trades": total_trades,
        "profit_factor_mean": round(profit_factor_mean, 4),
        "regime_breakdown": {
            "bull":     [s["slice_id"] for s in oos_results if s.get("regime") == "bull"],
            "bear":     [s["slice_id"] for s in oos_results if s.get("regime") == "bear"],
            "sideways": [s["slice_id"] for s in oos_results if s.get("regime") == "sideways"],
        },
    }

    calibration = _calibrate(oos_results, is_results, full_df, last_oos_end_idx)
    viable = not any_degenerate and sharpe_mean > 0

    result = {
        "slices": oos_results,
        "aggregate": aggregate,
        "calibration": calibration,
        "viable": viable,
    }

    logger.info(
        "Backtest complete: viable=%s | win=%.2f | sharpe=%.2f | sortino=%.2f | "
        "pf=%.2f | trades=%d | WFE=%s",
        viable, win_rate_mean, sharpe_mean, sortino_mean,
        profit_factor_mean, total_trades,
        f"{calibration.get('walk_forward_efficiency'):.2f}"
        if calibration.get("walk_forward_efficiency") is not None else "N/A",
    )

    return result
