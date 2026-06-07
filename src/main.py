"""
main.py — Outer loop: init_db → Loop 1 → Loop 2 → restart on degradation.

This is the single entry point for the trading system. It:
  1. Initialises the database (once)
  2. Runs Loop 1 to discover a validated strategy
  3. Runs Loop 2 to trade it live on Binance Testnet
  4. On degradation: restarts Loop 1 with KB context from reflection
  5. On SIGTERM/SIGINT: marks open trades as interrupted and exits cleanly
"""
import logging
import signal
import sys
import threading

from config.settings import DB_PATH
from src.data.schema import init_db, get_connection
from src.loop1 import run_loop1, MaxAttemptsExceeded
from src.loop2 import run_loop2, StrategyDegradedException

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Initialising database at %s", DB_PATH)
    init_db(DB_PATH)

    while not _shutdown_event.is_set():
        # ── Loop 1: Strategy Discovery ──────────────────────────────
        try:
            logger.info("Starting Loop 1 — strategy discovery")
            strategy = run_loop1(DB_PATH)
            logger.info(
                "Loop 1 complete — strategy: %s (id=%s)",
                strategy.get("name"), strategy.get("id"),
            )
        except MaxAttemptsExceeded as e:
            logger.critical("Loop 1 exhausted all attempts: %s", e)
            logger.info("Waiting 60s before retrying Loop 1...")
            _shutdown_event.wait(60)
            continue
        except Exception as e:
            logger.critical("Loop 1 unexpected error: %s", e, exc_info=True)
            break

        if _shutdown_event.is_set():
            break

        # ── Loop 2: Live Execution ──────────────────────────────────
        try:
            logger.info("Starting Loop 2 — live execution")
            run_loop2(strategy, DB_PATH, stop_event=_shutdown_event)
        except StrategyDegradedException as e:
            logger.info("Degradation detected: %s — restarting Loop 1", e)
            continue
        except Exception as e:
            logger.critical("Loop 2 unexpected error: %s", e, exc_info=True)
            break

    # ── Cleanup ─────────────────────────────────────────────────────
    n = _mark_open_trades_interrupted(DB_PATH)
    if n:
        logger.info("Marked %d open trades as interrupted on shutdown", n)
    logger.info("Trading system stopped")


def _handle_shutdown(signum, frame) -> None:
    """SIGTERM/SIGINT handler — signal all loops to stop."""
    logger.info("Shutdown signal received (signal %d)", signum)
    _shutdown_event.set()


def _mark_open_trades_interrupted(db_path: str) -> int:
    """Mark all outcome='open' trades as 'interrupted' on shutdown."""
    try:
        conn = get_connection(db_path)
        cursor = conn.execute(
            "UPDATE trades SET outcome = 'interrupted' WHERE outcome = 'open'"
        )
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.error("Failed to mark open trades: %s", e)
        return 0


if __name__ == "__main__":
    main()
