"""
loop2.py — Continuous execution loop orchestrator.

Takes a validated strategy from Loop 1 and trades it live on Binance Testnet.
Polls for signals, gates through risk agent + analyst brief, places trades,
and monitors for degradation. Raises StrategyDegradedException when the
degradation monitor fires, causing main.py to restart Loop 1.

Flow per iteration:
  1. Check degradation flag → reflect + raise if set
  2. Fetch candles → build signals
  3. Signal? → compute size → risk review → analyst CP2 → place trade
  4. Sleep until next candle close
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time

import pandas as pd

from config.settings import (
    DB_PATH,
    DEGRADATION_WINDOW,
    PROBATION_SIZE_MULTIPLIER,
    STALE_STRATEGY_HOURS,
)
from src.agents.analyst_agent import evaluate_brief, reflect
from src.agents.execution_agent import (
    compute_position_size,
    extract_sl_tp,
    place_trade,
    reconcile_open_trades,
)
from src.agents.risk_agent import RiskAgent
from src.backtest.strategy_runner import build_signals
from src.data.ccxt_feed import CCXTFeed
from src.exchange.factory import build_exchange
from src.data.schema import get_connection
from src.monitor.degradation_monitor import DegradationMonitor
from src.monitor.status_bus import stage, emit, DONE, RUNNING

logger = logging.getLogger(__name__)

# Timeframe → milliseconds per candle.
_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class StrategyDegradedException(Exception):
    """Raised when the degradation monitor detects the strategy has failed."""
    pass


def run_loop2(
    strategy: dict,
    db_path: str,
    exchange=None,
    client=None,
    stop_event: threading.Event | None = None,
    max_iterations: int | None = None,
) -> None:
    """
    Continuous execution loop for a validated strategy.

    Args:
        strategy:       Strategy dict from Loop 1 (id, spec, calibration, etc.).
        db_path:        Path to SQLite DB.
        exchange:       CCXT exchange instance (built if None).
        client:         ClaudeClient instance (built if None).
        stop_event:     Optional event for graceful shutdown.
        max_iterations: None=infinite (production), int for testing.

    Raises:
        StrategyDegradedException: When degradation is detected.
    """
    if stop_event is None:
        stop_event = threading.Event()

    # ── Unpack strategy ─────────────────────────────────────────────────
    strategy_id = strategy["id"]
    spec = strategy["spec"]
    symbol = spec.get("symbol", strategy.get("symbol", "BTC/USDT"))
    timeframe = spec.get("timeframe", strategy.get("timeframe", "1h"))
    calibration = strategy.get("calibration", {})
    threshold = calibration.get(
        "degradation_threshold",
        strategy.get("degradation_threshold", 0.45),
    )

    # ── Build dependencies ──────────────────────────────────────────────
    if exchange is None:
        exchange = build_exchange(db_path)

    if client is None:
        from src.agents.claude_client import ClaudeClient
        client = ClaudeClient(db_path=db_path)

    risk_agent = RiskAgent()

    feed = CCXTFeed(symbol=symbol, timeframe=timeframe, db_path=db_path)

    is_probation = _load_current_status(strategy_id, db_path) == "probation"
    monitor = DegradationMonitor(
        strategy_id=strategy_id,
        threshold=threshold,
        window=DEGRADATION_WINDOW,
        db_path=db_path,
        stale_hours=STALE_STRATEGY_HOURS,
        probation=is_probation,
    )

    sl_pct, tp_pct = extract_sl_tp(spec)
    direction = spec.get("direction", "long")
    trade_side = "buy" if direction == "long" else "sell"

    # ── Startup reconciliation ──────────────────────────────────────────
    reconcile_open_trades(exchange, db_path, strategy_id)

    # ── Start background services ───────────────────────────────────────
    feed.start_polling()
    monitor.start()

    logger.info(
        "Loop 2 started: strategy=%d symbol=%s timeframe=%s threshold=%.2f",
        strategy_id, symbol, timeframe, threshold,
    )

    def _wait(reason: str) -> None:
        """
        Sleep until the next candle, announcing why first.

        Without this the two quiet branches (no signal, too few candles) were
        completely silent — the system would sit for an hour with nothing to
        show, which is indistinguishable from a hang to anyone watching.
        """
        emit("loop2", "waiting", DONE, reason, result=timeframe)
        _sleep_until_next_candle(timeframe, stop_event)

    iteration = 0
    try:
        while not stop_event.is_set():
            if max_iterations is not None and iteration >= max_iterations:
                return

            # 1. Check degradation (monitor flag OR status flipped to degraded).
            current_status = _load_current_status(strategy_id, db_path)
            if current_status == "degraded" or monitor.flag.is_set():
                _handle_degradation(
                    strategy, strategy_id, client, db_path,
                )

            # 2. Fetch candles.
            emit("loop2", "fetch_candles", RUNNING, f"Polling {symbol} {timeframe}")
            candles_df = feed.get_latest_candles(200)
            if candles_df is None or len(candles_df) < 30:
                n = len(candles_df) if candles_df is not None else 0
                logger.debug("Insufficient candles (%s) — waiting", n)
                emit("loop2", "fetch_candles", DONE,
                     f"Only {n} candles available", result=f"{n} candles")
                _wait("Too few candles to evaluate — waiting for more history")
                iteration += 1
                continue

            # Drop incomplete current candle.
            candles_df = _drop_incomplete_candle(candles_df, timeframe)
            if len(candles_df) < 30:
                emit("loop2", "fetch_candles", DONE, "Too few closed candles",
                     result=f"{len(candles_df)} candles")
                _wait("Too few closed candles after dropping the open bar")
                iteration += 1
                continue

            emit("loop2", "fetch_candles", DONE,
                 f"{len(candles_df)} closed candles for {symbol}",
                 result=f"{len(candles_df)} candles")

            # 3. Check for entry signal.
            emit("loop2", "signal_detection", RUNNING, "Evaluating entry conditions")
            signals = build_signals(candles_df, spec)
            last_signal = signals.iloc[-1] if len(signals) > 0 else 0

            if last_signal != 1:
                emit("loop2", "signal_detection", DONE,
                     "Entry conditions not met", result="no signal")
                _wait("No entry signal on the last closed candle")
                iteration += 1
                continue

            logger.info("Entry signal detected on %s", symbol)
            emit("loop2", "signal_detection", DONE,
                 f"Entry signal on {symbol}", result="SIGNAL")

            # 4. Compute position size.
            emit("loop2", "position_sizing", RUNNING, "ATR-based size calculation")
            balance = _get_balance(exchange)
            if balance <= 0:
                logger.warning("Zero balance — skipping trade")
                emit("loop2", "position_sizing", DONE, "Zero balance",
                     result="no funds")
                _wait("Zero balance — cannot size a position")
                iteration += 1
                continue

            position_usdt = compute_position_size(candles_df, balance, calibration)
            current_status = _load_current_status(strategy_id, db_path)
            if current_status == "probation":
                position_usdt *= PROBATION_SIZE_MULTIPLIER
                logger.debug(
                    "Probation size multiplier %.2f applied → %.2f USDT",
                    PROBATION_SIZE_MULTIPLIER, position_usdt,
                )

            emit("loop2", "position_sizing", DONE,
                 f"Proposed {position_usdt:.2f} USDT on a {balance:.2f} balance",
                 result=f"{position_usdt:.2f} USDT")

            # 5. Risk agent review.
            # This stage is the reason an event bus was needed at all: the risk
            # agent is deterministic arithmetic, makes no LLM call and writes no
            # row, so it is invisible to anything polling the database.
            open_count = _get_open_position_count(strategy_id, db_path)
            daily_pnl = _get_daily_pnl_pct(strategy_id, db_path, balance)
            recent = _get_recent_outcomes(strategy_id, db_path, 10)

            with stage("loop2", "risk_review",
                       "Evaluating position sizing and risk limits") as st:
                risk_result = risk_agent.review(
                    proposed_size_usdt=position_usdt,
                    balance_usdt=balance,
                    open_positions=open_count,
                    daily_pnl_pct=daily_pnl,
                    recent_outcomes=recent,
                )
                if not risk_result["approved"]:
                    st.result("REJECTED")
                elif risk_result["adjusted_size"] < position_usdt:
                    st.result("ADJUSTED")
                else:
                    st.result("APPROVED")
                st.detail(risk_result.get("reason")
                          or f"approved at {risk_result['adjusted_size']:.2f} USDT")

            if not risk_result["approved"]:
                logger.info("Risk agent rejected: %s", risk_result["reason"])
                _wait(f"Risk agent rejected the trade: {risk_result['reason']}")
                iteration += 1
                continue

            adjusted_size = risk_result["adjusted_size"]

            # 6. Analyst brief (CP2).
            candles_for_analyst = candles_df.tail(20).to_dict("records")
            proposed_trade = {
                "symbol": symbol,
                "side": trade_side,
                "amount_usdt": adjusted_size,
                "stop_loss_pct": sl_pct,
                "take_profit_pct": tp_pct,
            }

            with stage("loop2", "analyst_brief", "Second opinion (debate CP2)") as st:
                brief = evaluate_brief(spec, candles_for_analyst, proposed_trade, client)
                st.result("CONFIRMED" if brief.get("confirm") else "REJECTED")
                st.detail(brief.get("note", "")[:200])

            if not brief.get("confirm", False):
                logger.info("Analyst CP2 rejected: %s", brief.get("note", ""))
                _wait(f"Analyst declined the trade: {brief.get('note', '')[:120]}")
                iteration += 1
                continue

            # 7. Place trade.
            logger.info("Placing trade: %s %s %.2f USDT", symbol, trade_side, adjusted_size)
            with stage("loop2", "place_trade",
                       f"Placing {trade_side} {adjusted_size:.2f} USDT on {symbol}") as st:
                trade_result = place_trade(
                    symbol=symbol,
                    side=trade_side,
                    amount_usdt=adjusted_size,
                    stop_loss_pct=sl_pct,
                    take_profit_pct=tp_pct,
                    exchange=exchange,
                    db_path=db_path,
                    strategy_id=strategy_id,
                    stop_event=stop_event,
                )
                st.result(str(trade_result.get("outcome", "")).upper())
                st.detail(f"trade id={trade_result.get('trade_id')} "
                          f"outcome={trade_result.get('outcome')}")

            logger.info(
                "Trade completed: id=%s outcome=%s",
                trade_result.get("trade_id"), trade_result.get("outcome"),
            )

            _wait("Trade complete — waiting for the next candle close")
            iteration += 1

    finally:
        feed.stop()
        monitor.stop()
        logger.info("Loop 2 stopped for strategy %d", strategy_id)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _handle_degradation(strategy, strategy_id, client, db_path):
    """Run analyst reflection and raise StrategyDegradedException."""
    logger.warning("Degradation detected for strategy %d — running reflection", strategy_id)

    conn = get_connection(db_path)
    recent_trades = [
        dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE strategy_id = ? ORDER BY entry_at DESC LIMIT 30",
            (strategy_id,),
        ).fetchall()
    ]
    perf_history = [
        dict(r) for r in conn.execute(
            "SELECT * FROM performance WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT 20",
            (strategy_id,),
        ).fetchall()
    ]

    # Mark strategy as degraded.
    conn.execute(
        "UPDATE strategies SET status = 'degraded' WHERE id = ?",
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    diagnosis = reflect(strategy, recent_trades, perf_history, client, db_path)

    raise StrategyDegradedException(
        f"Strategy {strategy_id} degraded: {diagnosis}"
    )


def _load_current_status(strategy_id: int, db_path: str) -> str:
    """Fetch the live status for a strategy. Returns 'active' on lookup failure."""
    try:
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
        conn.close()
        if row and row["status"]:
            return row["status"]
    except Exception as e:
        logger.debug("Status lookup failed for strategy %d: %s", strategy_id, e)
    return "active"


def _get_balance(exchange) -> float:
    """Fetch USDT free balance from exchange."""
    try:
        balance = exchange.fetch_balance()
        return float(balance.get("free", {}).get("USDT", 0))
    except Exception as e:
        logger.error("Failed to fetch balance: %s", e)
        return 0.0


def _get_open_position_count(strategy_id: int, db_path: str) -> int:
    """Count outcome='open' trades for this strategy."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM trades WHERE strategy_id = ? AND outcome = 'open'",
        (strategy_id,),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def _get_daily_pnl_pct(strategy_id: int, db_path: str, balance: float) -> float:
    """Sum today's closed trade PnL as a fraction of balance."""
    if balance <= 0:
        return 0.0

    today_start_ms = int(
        (time.time() // 86400) * 86400 * 1000
    )  # midnight UTC in ms

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT SUM(pnl_pct * amount_usdt) as total_pnl "
        "FROM trades WHERE strategy_id = ? AND exit_at >= ? AND outcome IN ('win', 'loss')",
        (strategy_id, today_start_ms),
    ).fetchone()
    conn.close()

    total_pnl_usdt = row["total_pnl"] if row and row["total_pnl"] else 0.0
    return total_pnl_usdt / balance


def _get_recent_outcomes(strategy_id: int, db_path: str, n: int = 10) -> list:
    """Last N trade outcomes newest-first for StoplossGuard."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT outcome FROM trades "
        "WHERE strategy_id = ? AND outcome IN ('win', 'loss') "
        "ORDER BY exit_at DESC LIMIT ?",
        (strategy_id, n),
    ).fetchall()
    conn.close()
    return [r["outcome"] for r in rows]


def _drop_incomplete_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop the last candle if it's still forming (incomplete)."""
    tf_ms = _TF_MS.get(timeframe, 3_600_000)
    now_ms = int(time.time() * 1000)

    if len(df) == 0:
        return df

    last_ts = df["timestamp"].iloc[-1]
    if isinstance(last_ts, float):
        last_ts = int(last_ts)

    if now_ms - last_ts < tf_ms:
        return df.iloc[:-1]

    return df


def _sleep_until_next_candle(timeframe: str, stop_event: threading.Event) -> None:
    """Interruptible sleep until the next candle close."""
    tf_ms = _TF_MS.get(timeframe, 3_600_000)
    tf_seconds = tf_ms / 1000
    now = time.time()
    # Next candle boundary.
    next_boundary = (math.floor(now / tf_seconds) + 1) * tf_seconds
    sleep_seconds = max(next_boundary - now + 5, 10)  # +5s buffer for exchange lag
    stop_event.wait(sleep_seconds)
