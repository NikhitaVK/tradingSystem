"""
execution_agent.py — Market orders on Binance Testnet, SL/TP polling, trade logging.

Design principles (from production bot research):
  - Track trade in DB BEFORE placing exchange order (Hummingbot pattern)
  - Order amounts in BASE currency, not USDT
  - Try OCO for SL/TP, fall back to polling (Testnet rejects OCO frequently)
  - Use order['filled'] for exit amount, not requested amount
  - On startup, reconcile open trades against exchange state (NautilusTrader)
"""
from __future__ import annotations

import logging
import math
import time

import pandas as pd

from config.settings import (
    ATR_PERIOD,
    ATR_MULTIPLIER,
    RISK_PER_TRADE_PCT,
    OCO_POLL_INTERVAL_SECONDS,
    OCO_MAX_WAIT_SECONDS,
    PROBATION_PROMOTE_WINS,
    PROBATION_DEMOTE_LOSSES,
)
from src.backtest.indicators import compute_atr
from src.data.schema import get_connection

logger = logging.getLogger(__name__)


# ── Position Sizing ─────────────────────────────────────────────────────────


def compute_position_size(
    candles_df: pd.DataFrame,
    balance_usdt: float,
    calibration: dict,
) -> float:
    """
    ATR-based position sizing using live candles and calibration params.

    Falls back to a fixed fraction of balance if ATR is NaN (insufficient candles).

    Args:
        candles_df:    Recent candles with 'high', 'low', 'close' columns.
        balance_usdt:  Current free USDT balance.
        calibration:   Dict with 'position_sizing' sub-dict from Loop 1.

    Returns:
        Position size in USDT.
    """
    sizing = calibration.get("position_sizing", {})
    atr_period = sizing.get("atr_period", ATR_PERIOD)
    atr_mult = sizing.get("atr_multiplier", ATR_MULTIPLIER)
    risk_pct = sizing.get("risk_per_trade_pct", RISK_PER_TRADE_PCT)

    if len(candles_df) < atr_period + 1:
        # Not enough candles — fall back to fixed fraction.
        return balance_usdt * risk_pct * 10  # rough fallback

    atr = compute_atr(
        candles_df["high"], candles_df["low"], candles_df["close"], atr_period,
    )
    current_atr = atr.iloc[-1]
    current_price = candles_df["close"].iloc[-1]

    if pd.isna(current_atr) or current_atr <= 0 or current_price <= 0:
        return balance_usdt * risk_pct * 10

    stop_distance_pct = (current_atr * atr_mult) / current_price
    risk_amount = balance_usdt * risk_pct
    position_usdt = risk_amount / stop_distance_pct

    return position_usdt


# ── SL/TP Extraction ────────────────────────────────────────────────────────


def extract_sl_tp(strategy_spec: dict) -> tuple:
    """
    Parse stop_loss_pct and take_profit_pct from the exit conditions.

    Returns:
        (stop_loss_pct, take_profit_pct) as floats, e.g. (3.0, 9.0).
    """
    exit_block = strategy_spec.get("exit", {})
    conditions = exit_block.get("conditions", [])

    sl = None
    tp = None
    for cond in conditions:
        if cond.get("type") == "stop_loss_pct":
            sl = float(cond["value"])
        elif cond.get("type") == "take_profit_pct":
            tp = float(cond["value"])

    if sl is None:
        sl = 3.0  # defensive default
        logger.warning("No stop_loss_pct in spec — defaulting to %.1f%%", sl)
    if tp is None:
        tp = 9.0
        logger.warning("No take_profit_pct in spec — defaulting to %.1f%%", tp)

    return sl, tp


# ── Trade Placement ─────────────────────────────────────────────────────────


def place_trade(
    symbol: str,
    side: str,
    amount_usdt: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    exchange,
    db_path: str,
    strategy_id: int,
    stop_event=None,
) -> dict:
    """
    Place a market order on Binance Testnet with SL/TP management.

    Steps:
      1. Insert trade row FIRST (outcome='open') — track before exchange call
      2. Fetch ticker → convert to base currency
      3. Place market order → store order_id
      4. Try OCO for SL/TP → fall back to polling if rejected
      5. Return trade record dict

    Args:
        symbol:          e.g. 'BTC/USDT'
        side:            'buy' or 'sell'
        amount_usdt:     Position size in USDT
        stop_loss_pct:   Stop loss percentage (e.g. 3.0 for 3%)
        take_profit_pct: Take profit percentage (e.g. 9.0 for 9%)
        exchange:        CCXT exchange instance
        db_path:         Path to SQLite DB
        strategy_id:     Strategy FK
        stop_event:      Optional threading.Event for graceful shutdown

    Returns:
        Trade record dict with keys: trade_id, order_id, entry_price, outcome.
    """
    now_ms = int(time.time() * 1000)

    # 1. Insert trade row BEFORE exchange call (Hummingbot pattern).
    conn = get_connection(db_path)
    cursor = conn.execute(
        "INSERT INTO trades (strategy_id, symbol, side, amount_usdt, outcome, entry_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (strategy_id, symbol, side, amount_usdt, now_ms),
    )
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # 2. Fetch ticker and convert to base currency.
    ticker = exchange.fetch_ticker(symbol)
    current_price = ticker["last"]
    amount_base = amount_usdt / current_price

    # 3. Place market order.
    order = exchange.create_order(symbol, "market", side, amount_base)
    order_id = order.get("id")
    entry_price = order.get("average") or order.get("price") or current_price
    filled_base = order.get("filled", amount_base)

    # Update trade row with entry details.
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE trades SET entry_price = ?, order_id = ? WHERE id = ?",
        (entry_price, order_id, trade_id),
    )
    conn.commit()
    conn.close()

    logger.info(
        "Trade %d placed: %s %s %.6f @ %.2f (order %s)",
        trade_id, side, symbol, filled_base, entry_price, order_id,
    )

    # 4. Compute SL/TP price levels.
    if side == "buy":
        sl_price = entry_price * (1 - stop_loss_pct / 100)
        tp_price = entry_price * (1 + take_profit_pct / 100)
    else:
        sl_price = entry_price * (1 + stop_loss_pct / 100)
        tp_price = entry_price * (1 - take_profit_pct / 100)

    # 5. Try OCO, fall back to polling.
    oco_placed = False
    try:
        _try_oco(exchange, symbol, side, filled_base, sl_price, tp_price)
        oco_placed = True
        logger.info("OCO placed for trade %d: SL=%.2f TP=%.2f", trade_id, sl_price, tp_price)
    except Exception as e:
        logger.info("OCO rejected (expected on Testnet): %s — falling back to polling", e)

    # 6. Poll for SL/TP hit.
    result = _poll_sl_tp(
        exchange=exchange,
        symbol=symbol,
        side=side,
        filled_base=filled_base,
        sl_price=sl_price,
        tp_price=tp_price,
        trade_id=trade_id,
        db_path=db_path,
        stop_event=stop_event,
    )

    return {
        "trade_id": trade_id,
        "order_id": order_id,
        "entry_price": entry_price,
        "outcome": result,
    }


def _try_oco(exchange, symbol, side, amount_base, sl_price, tp_price) -> None:
    """Attempt an OCO order for SL/TP. Raises on failure (expected on Testnet)."""
    exit_side = "sell" if side == "buy" else "buy"
    exchange.create_order(
        symbol, "limit", exit_side, amount_base,
        tp_price,
        params={
            "stopPrice": sl_price,
            "type": "OCO",
        },
    )


def _poll_sl_tp(
    exchange,
    symbol: str,
    side: str,
    filled_base: float,
    sl_price: float,
    tp_price: float,
    trade_id: int,
    db_path: str,
    stop_event=None,
) -> str:
    """
    Poll ticker until SL or TP is hit, then close with a market order.

    Returns 'win' or 'loss' or 'timeout'.
    """
    exit_side = "sell" if side == "buy" else "buy"
    start = time.time()

    while True:
        # Check for graceful shutdown.
        if stop_event is not None and stop_event.is_set():
            logger.info("Trade %d: stop_event set — leaving open", trade_id)
            return "open"

        elapsed = time.time() - start
        if elapsed > OCO_MAX_WAIT_SECONDS:
            logger.warning("Trade %d: max wait exceeded — force closing", trade_id)
            _close_trade(exchange, symbol, exit_side, filled_base, trade_id, db_path, "timeout")
            return "timeout"

        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker["last"]
        except Exception as e:
            logger.debug("Ticker fetch failed: %s", e)
            _interruptible_sleep(OCO_POLL_INTERVAL_SECONDS, stop_event)
            continue

        if side == "buy":
            if price <= sl_price:
                _close_trade(exchange, symbol, exit_side, filled_base, trade_id, db_path, "loss")
                return "loss"
            if price >= tp_price:
                _close_trade(exchange, symbol, exit_side, filled_base, trade_id, db_path, "win")
                return "win"
        else:
            if price >= sl_price:
                _close_trade(exchange, symbol, exit_side, filled_base, trade_id, db_path, "loss")
                return "loss"
            if price <= tp_price:
                _close_trade(exchange, symbol, exit_side, filled_base, trade_id, db_path, "win")
                return "win"

        _interruptible_sleep(OCO_POLL_INTERVAL_SECONDS, stop_event)


def _close_trade(
    exchange, symbol, exit_side, amount_base, trade_id, db_path, outcome,
) -> None:
    """Place exit market order and update DB."""
    try:
        order = exchange.create_order(symbol, "market", exit_side, amount_base)
        exit_price = order.get("average") or order.get("price") or 0
    except Exception as e:
        logger.error("Exit order failed for trade %d: %s", trade_id, e)
        exit_price = 0

    now_ms = int(time.time() * 1000)
    conn = get_connection(db_path)

    # Compute PnL%.
    row = conn.execute("SELECT entry_price, side FROM trades WHERE id = ?", (trade_id,)).fetchone()
    pnl_pct = 0.0
    if row and row["entry_price"] and exit_price:
        if row["side"] == "buy":
            pnl_pct = (exit_price - row["entry_price"]) / row["entry_price"]
        else:
            pnl_pct = (row["entry_price"] - exit_price) / row["entry_price"]

    conn.execute(
        "UPDATE trades SET exit_price = ?, pnl_pct = ?, outcome = ?, exit_at = ? WHERE id = ?",
        (exit_price, pnl_pct, outcome, now_ms, trade_id),
    )
    # Fetch strategy_id for feedback-driven KB boost.
    strategy_row = conn.execute(
        "SELECT strategy_id FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    kb_ids = []
    strategy_id = strategy_row["strategy_id"] if strategy_row else None
    if strategy_row and outcome == "win":
        kb_rows = conn.execute(
            "SELECT id FROM knowledge_base WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()
        kb_ids = [r["id"] for r in kb_rows]

    # Probation counters: increment win/loss tallies, then auto-promote or
    # auto-demote once thresholds are hit.
    if strategy_id is not None and outcome in ("win", "loss"):
        _update_probation_counters(conn, strategy_id, outcome)
    conn.commit()
    conn.close()

    # RL feedback loop: winning trades boost importance of contributing KB entries.
    if kb_ids:
        from src.data.memory_feedback import update_importance_from_feedback
        update_importance_from_feedback(kb_ids, outcome, db_path)

    logger.info(
        "Trade %d closed: %s exit=%.2f pnl=%.4f",
        trade_id, outcome, exit_price, pnl_pct,
    )


def _update_probation_counters(conn, strategy_id: int, outcome: str) -> None:
    """
    Increment probation win/loss counters and auto-promote / auto-demote once
    thresholds are hit. No-op if the strategy is not currently on probation.

    Uses the shared conn + commit flow in _close_trade — do not commit here.
    """
    row = conn.execute(
        "SELECT status, probation_wins, probation_losses FROM strategies WHERE id = ?",
        (strategy_id,),
    ).fetchone()
    if not row or row["status"] != "probation":
        return

    wins = (row["probation_wins"] or 0) + (1 if outcome == "win" else 0)
    losses = (row["probation_losses"] or 0) + (1 if outcome == "loss" else 0)

    if outcome == "win" and wins >= PROBATION_PROMOTE_WINS:
        conn.execute(
            "UPDATE strategies SET status = 'active', probation_wins = 0, "
            "probation_losses = 0 WHERE id = ?",
            (strategy_id,),
        )
        _write_probation_kb(
            conn, strategy_id,
            f"Auto-promoted strategy {strategy_id} after {wins} probation wins.",
        )
        logger.info("Strategy %d auto-promoted from probation (%d wins)", strategy_id, wins)
        return

    if outcome == "loss" and losses >= PROBATION_DEMOTE_LOSSES:
        conn.execute(
            "UPDATE strategies SET status = 'degraded', probation_losses = ? "
            "WHERE id = ?",
            (losses, strategy_id),
        )
        _write_probation_kb(
            conn, strategy_id,
            f"Auto-demoted strategy {strategy_id} after {losses} probation losses.",
        )
        logger.warning("Strategy %d auto-demoted from probation (%d losses)", strategy_id, losses)
        return

    conn.execute(
        "UPDATE strategies SET probation_wins = ?, probation_losses = ? WHERE id = ?",
        (wins, losses, strategy_id),
    )


def _write_probation_kb(conn, strategy_id: int, content: str) -> None:
    """Write a probation status-change entry to the knowledge_base table."""
    conn.execute(
        "INSERT INTO knowledge_base (category, strategy_id, content, created_at) "
        "VALUES ('parameter_insight', ?, ?, ?)",
        (strategy_id, content, int(time.time() * 1000)),
    )


def _interruptible_sleep(seconds: float, stop_event=None) -> None:
    """Sleep that can be interrupted by a stop_event."""
    if stop_event is not None:
        stop_event.wait(seconds)
    else:
        time.sleep(seconds)


# ── Startup Reconciliation ──────────────────────────────────────────────────


def reconcile_open_trades(exchange, db_path: str, strategy_id: int) -> int:
    """
    On startup: check DB for outcome='open' trades and reconcile against
    exchange state (NautilusTrader pattern).

    - If trade has order_id: try to verify on exchange
    - If no order_id: entry never reached exchange → mark 'failed'
    - If position is gone on exchange: mark 'interrupted'

    Returns count of reconciled trades.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT id, order_id, symbol FROM trades WHERE strategy_id = ? AND outcome = 'open'",
        (strategy_id,),
    ).fetchall()

    reconciled = 0
    now_ms = int(time.time() * 1000)

    for row in rows:
        trade_id = row["id"]
        order_id = row["order_id"]

        if not order_id:
            # Entry never reached exchange.
            conn.execute(
                "UPDATE trades SET outcome = 'failed', exit_at = ? WHERE id = ?",
                (now_ms, trade_id),
            )
            reconciled += 1
            logger.info("Trade %d reconciled: no order_id → marked 'failed'", trade_id)
            continue

        # Try to fetch the order from exchange.
        try:
            order = exchange.fetch_order(order_id, row["symbol"])
            status = order.get("status", "unknown")
            if status in ("closed", "canceled", "cancelled", "expired"):
                conn.execute(
                    "UPDATE trades SET outcome = 'interrupted', exit_at = ? WHERE id = ?",
                    (now_ms, trade_id),
                )
                reconciled += 1
                logger.info("Trade %d reconciled: exchange status=%s → marked 'interrupted'", trade_id, status)
        except Exception as e:
            # Can't verify — mark interrupted to be safe.
            conn.execute(
                "UPDATE trades SET outcome = 'interrupted', exit_at = ? WHERE id = ?",
                (now_ms, trade_id),
            )
            reconciled += 1
            logger.info("Trade %d reconciled: fetch failed (%s) → marked 'interrupted'", trade_id, e)

    if reconciled:
        conn.commit()
    conn.close()

    logger.info("Reconciled %d open trades for strategy %d", reconciled, strategy_id)
    return reconciled
