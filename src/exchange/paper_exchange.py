"""
paper_exchange.py — Simulated exchange with CCXT-compatible interface.

Tracks positions in SQLite, uses real market prices from CCXT public
endpoints (no auth required). Supports both long and short trades.

Switch to real money by swapping this for a real CCXT exchange instance —
the rest of the system doesn't know the difference.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import ccxt

from config.settings import (
    BINANCE_TESTNET_API_KEY,
    BINANCE_TESTNET_SECRET,
)
from src.data.schema import get_connection

logger = logging.getLogger(__name__)

# Monotonic order ID counter for paper trades.
_NEXT_ORDER_ID = int(time.time() * 1000)


def _gen_order_id() -> str:
    global _NEXT_ORDER_ID
    _NEXT_ORDER_ID += 1
    return f"paper_{_NEXT_ORDER_ID}"


class PaperExchange:
    """
    Drop-in replacement for a CCXT exchange object.

    Implements the subset of the CCXT unified API that the trading system
    uses: fetch_ticker, fetch_ohlcv, fetch_balance, create_order,
    fetch_order, fetch_open_orders, fetch_positions.

    All execution is simulated against real market prices.
    """

    def __init__(self, starting_balance_usdt: float, db_path: str):
        self._starting_balance = starting_balance_usdt
        self._db_path = db_path

        # Real CCXT for public market data — no auth needed for public endpoints.
        self._market = ccxt.binance({"enableRateLimit": True})
        # Use testnet market data if keys are available (same price feed).
        if BINANCE_TESTNET_API_KEY:
            self._market = ccxt.binance({
                "apiKey": BINANCE_TESTNET_API_KEY,
                "secret": BINANCE_TESTNET_SECRET,
                "enableRateLimit": True,
            })
            self._market.set_sandbox_mode(True)

        # In-memory order book keyed by order_id.
        self._orders: dict[str, dict] = {}

    # ── Market Data (delegates to real CCXT) ────────────────────────────

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch real market ticker."""
        return self._market.fetch_ticker(symbol)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    since: Optional[int] = None, limit: Optional[int] = None) -> list:
        """Fetch real OHLCV candles."""
        return self._market.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    # ── Balance ─────────────────────────────────────────────────────────

    def fetch_balance(self) -> dict:
        """
        Compute balance from starting capital minus open position value.
        Returns CCXT-shaped balance dict.
        """
        conn = get_connection(self._db_path)
        # Sum USDT locked in open trades.
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usdt), 0) as locked "
            "FROM trades WHERE outcome = 'open'"
        ).fetchone()
        locked = row["locked"] if row else 0

        # Sum realised PnL from closed trades.
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_pct * amount_usdt), 0) as realised "
            "FROM trades WHERE outcome IN ('win', 'loss')"
        ).fetchone()
        realised = row["realised"] if row else 0

        total = self._starting_balance + realised
        free = total - locked

        conn.close()
        return {
            "free": {"USDT": max(free, 0)},
            "total": {"USDT": max(total, 0)},
            "used": {"USDT": locked},
        }

    # ── Order Execution (simulated) ─────────────────────────────────────

    def create_order(self, symbol: str, type: str, side: str,
                     amount: float, price: float = None, params: dict = None) -> dict:
        """
        Simulate order execution at current market price.

        For market orders: fills instantly at ticker['last'].
        For limit/stop orders: stored but not executed (OCO simulation).

        Returns CCXT-shaped order dict.
        """
        params = params or {}
        order_id = _gen_order_id()
        now = int(time.time() * 1000)

        if type == "market":
            ticker = self.fetch_ticker(symbol)
            fill_price = ticker["last"]

            order = {
                "id": order_id,
                "symbol": symbol,
                "type": "market",
                "side": side,
                "amount": amount,
                "filled": amount,
                "remaining": 0,
                "price": fill_price,
                "average": fill_price,
                "status": "closed",
                "timestamp": now,
                "datetime": None,
                "cost": amount * fill_price,
                "info": {"paper": True},
            }
            logger.info(
                "Paper fill: %s %s %.6f %s @ %.2f",
                side, type, amount, symbol, fill_price,
            )
        else:
            # Limit / stop-loss-limit / OCO — store as open, won't actually trigger
            # (the polling loop in execution_agent handles exits).
            order = {
                "id": order_id,
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "filled": 0,
                "remaining": amount,
                "price": price,
                "average": None,
                "status": "open",
                "timestamp": now,
                "datetime": None,
                "cost": 0,
                "stopPrice": params.get("stopPrice"),
                "info": {"paper": True},
            }

        self._orders[order_id] = order
        return order

    def fetch_order(self, order_id: str, symbol: str = None) -> dict:
        """Fetch a paper order by ID."""
        order = self._orders.get(order_id)
        if order is None:
            raise ccxt.OrderNotFound(f"Order {order_id} not found")
        return order

    def fetch_open_orders(self, symbol: str = None) -> list:
        """Return all open paper orders, optionally filtered by symbol."""
        result = []
        for order in self._orders.values():
            if order["status"] != "open":
                continue
            if symbol and order["symbol"] != symbol:
                continue
            result.append(order)
        return result

    def fetch_positions(self, symbols: list = None) -> list:
        """
        Return open positions from the trades table.
        Each open trade is treated as a position.
        """
        conn = get_connection(self._db_path)
        query = "SELECT * FROM trades WHERE outcome = 'open'"
        rows = conn.execute(query).fetchall()
        conn.close()

        positions = []
        for r in rows:
            side = "long" if r["side"] == "buy" else "short"
            positions.append({
                "symbol": r["symbol"],
                "side": side,
                "contracts": r["amount_usdt"],
                "entryPrice": r["entry_price"],
                "info": {"trade_id": r["id"]},
            })
        return positions

    # ── Convenience ─────────────────────────────────────────────────────

    def set_sandbox_mode(self, enabled: bool) -> None:
        """No-op for interface compatibility."""
        pass

    def load_markets(self) -> dict:
        """Delegate to real CCXT for market info."""
        return self._market.load_markets()
