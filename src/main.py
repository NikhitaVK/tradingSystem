"""
main.py — Outer loop: init_db → Loop 1 → Loop 2 → restart on degradation.

This is the single entry point for the trading system. It:
  1. Initialises the database (once)
  2. Runs Loop 1 to discover a validated strategy
  3. Runs Loop 2 to trade it live on Binance Testnet
  4. On degradation: restarts Loop 1 with KB context from reflection
  5. On SIGTERM/SIGINT: marks open trades as interrupted and exits cleanly
"""
import argparse
import logging
import signal
import sys
import threading

from config.settings import DB_PATH
from src.data.schema import init_db, get_connection
from src.loop1 import run_loop1, MaxAttemptsExceeded
from src.loop2 import run_loop2, StrategyDegradedException
from src.monitor import status_bus
from src.monitor.status_bus import emit, DONE, FAILED, RUNNING

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit-events", action="store_true",
        help="stream stage-progress events to stdout as JSON lines "
             "(used by the status GUI). Off by default, so normal runs are "
             "byte-for-byte unchanged.",
    )
    args = parser.parse_args()

    # Logs go to stderr, events to stdout — so a parent process reading events
    # never has to disentangle them from log text.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    if args.emit_events:
        status_bus.use_stdout()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Initialising database at %s", DB_PATH)
    emit("system", "startup", RUNNING, f"Initialising database at {DB_PATH}")
    init_db(DB_PATH)

    while not _shutdown_event.is_set():
        # ── Loop 1: Strategy Discovery ──────────────────────────────
        try:
            logger.info("Starting Loop 1 — strategy discovery")
            emit("loop1", "loop_start", RUNNING, "Loop 1 — strategy discovery")
            strategy = run_loop1(DB_PATH)
            logger.info(
                "Loop 1 complete — strategy: %s (id=%s)",
                strategy.get("name"), strategy.get("id"),
            )
            emit("loop1", "loop_start", DONE,
                 f"Loop 1 complete — {strategy.get('name')} (id={strategy.get('id')})",
                 result="COMPLETE")
        except MaxAttemptsExceeded as e:
            logger.critical("Loop 1 exhausted all attempts: %s", e)
            logger.info("Waiting 60s before retrying Loop 1...")
            emit("loop1", "loop_start", FAILED,
                 f"Exhausted all attempts: {e}"[:300], result="EXHAUSTED")
            emit("system", "retry_wait", RUNNING, "Waiting 60s before retrying Loop 1")
            _shutdown_event.wait(60)
            continue
        except Exception as e:
            logger.critical("Loop 1 unexpected error: %s", e, exc_info=True)
            emit("loop1", "loop_start", FAILED,
                 f"{type(e).__name__}: {e}"[:300], result="ERROR")
            break

        if _shutdown_event.is_set():
            break

        # ── Loop 2: Live Execution ──────────────────────────────────
        try:
            logger.info("Starting Loop 2 — live execution")
            emit("loop2", "loop_start", RUNNING,
                 f"Loop 2 — live execution of {strategy.get('name')}")
            run_loop2(strategy, DB_PATH, stop_event=_shutdown_event)
            emit("loop2", "loop_start", DONE, "Loop 2 stopped", result="STOPPED")
        except StrategyDegradedException as e:
            logger.info("Degradation detected: %s — restarting Loop 1", e)
            emit("loop2", "degraded", DONE,
                 f"Degradation detected: {e}"[:300], result="DEGRADED")
            continue
        except Exception as e:
            logger.critical("Loop 2 unexpected error: %s", e, exc_info=True)
            emit("loop2", "loop_start", FAILED,
                 f"{type(e).__name__}: {e}"[:300], result="ERROR")
            break

    # ── Cleanup ─────────────────────────────────────────────────────
    n = _mark_open_trades_interrupted(DB_PATH)
    if n:
        logger.info("Marked %d open trades as interrupted on shutdown", n)
    logger.info("Trading system stopped")
    emit("system", "stopped", DONE,
         f"Trading system stopped ({n} open trades marked interrupted)"
         if n else "Trading system stopped",
         result="STOPPED")


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
