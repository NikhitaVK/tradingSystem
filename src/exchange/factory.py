"""
factory.py — Build the correct exchange adapter based on EXECUTION_MODE.

Usage:
    from src.exchange.factory import build_exchange
    exchange = build_exchange(db_path)

Returns either a PaperExchange (simulated) or a real CCXT exchange.
The rest of the system doesn't know which one it got.
"""
from __future__ import annotations

import logging

from config.settings import (
    EXECUTION_MODE,
    PAPER_STARTING_BALANCE,
    DB_PATH,
)

logger = logging.getLogger(__name__)


def build_exchange(db_path: str = None):
    """
    Build an exchange adapter based on EXECUTION_MODE setting.

    Args:
        db_path: Path to SQLite DB (required for paper mode balance tracking).

    Returns:
        Exchange object with CCXT-compatible interface.
    """
    db_path = db_path or DB_PATH

    if EXECUTION_MODE == "paper":
        from src.exchange.paper_exchange import PaperExchange
        logger.info(
            "Paper mode: starting balance $%.2f", PAPER_STARTING_BALANCE,
        )
        return PaperExchange(
            starting_balance_usdt=PAPER_STARTING_BALANCE,
            db_path=db_path,
        )

    elif EXECUTION_MODE == "live":
        # Future: build real CCXT futures exchange here.
        # from src.data.ccxt_feed import _build_exchange
        # return _build_exchange()
        raise NotImplementedError(
            "Live execution mode requires futures API keys. "
            "Set EXECUTION_MODE=paper for simulated trading."
        )

    else:
        raise ValueError(f"Unknown EXECUTION_MODE: {EXECUTION_MODE!r}. Use 'paper' or 'live'.")
