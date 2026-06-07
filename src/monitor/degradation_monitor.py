"""
degradation_monitor.py — Background daemon thread watching rolling win rate.

Reads completed trades from the `trades` table and sets a flag when the
strategy's win rate drops below its calibrated degradation threshold.
Loop 2 checks the flag each iteration and triggers analyst reflection.

Also implements a stale-strategy check: if no trades complete for
STALE_STRATEGY_HOURS, the flag is set regardless of win rate — a strategy
that never fires is indistinguishable from a broken one.
"""
from __future__ import annotations

import logging
import threading
import time

from config.settings import (
    DEGRADATION_WINDOW,
    DEGRADATION_THRESHOLD_FLOOR,
    PROBATION_THRESHOLD_BUMP,
    STALE_STRATEGY_HOURS,
)
from src.data.schema import get_connection

logger = logging.getLogger(__name__)


class DegradationMonitor:
    """
    Daemon thread that periodically queries the trades table and sets
    ``self.flag`` when degradation is detected.

    Usage::

        monitor = DegradationMonitor(strategy_id=1, threshold=0.45, db_path=db)
        monitor.start()
        # ... in loop2 iteration:
        if monitor.flag.is_set():
            # handle degradation
        monitor.stop()
    """

    def __init__(
        self,
        strategy_id: int,
        threshold: float,
        window: int = DEGRADATION_WINDOW,
        check_interval_seconds: int = 30,
        db_path: str | None = None,
        stale_hours: int = STALE_STRATEGY_HOURS,
        probation: bool = False,
    ):
        self.strategy_id = strategy_id
        effective_threshold = threshold + PROBATION_THRESHOLD_BUMP if probation else threshold
        self.threshold = max(effective_threshold, DEGRADATION_THRESHOLD_FLOOR)
        self.window = window
        self.check_interval = check_interval_seconds
        self.db_path = db_path
        self.stale_hours = max(1, stale_hours // 2) if probation else stale_hours
        self.probation = probation

        self.flag = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch the daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "DegradationMonitor started: strategy=%d threshold=%.2f window=%d",
            self.strategy_id, self.threshold, self.window,
        )

    def stop(self) -> None:
        """Signal the daemon thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def check_once(self) -> bool:
        """
        Run a single degradation check against the DB.

        Returns True if the strategy is degraded (win rate below threshold
        or stale — no trades for stale_hours).
        """
        if self.db_path is None:
            return False

        conn = get_connection(self.db_path)
        try:
            return self._check(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Background loop: check periodically until stopped or degraded."""
        while not self._stop_event.is_set():
            try:
                degraded = self.check_once()
                if degraded:
                    self.flag.set()
                    logger.warning(
                        "DegradationMonitor: strategy %d flagged as degraded",
                        self.strategy_id,
                    )
                    return  # stop checking — Loop 2 will handle it
            except Exception as e:
                logger.debug("DegradationMonitor check error: %s", e)
            self._stop_event.wait(self.check_interval)

    def _check(self, conn) -> bool:
        """Core check logic. Returns True if degraded."""
        # Fetch the most recent `window` completed trades for this strategy.
        rows = conn.execute(
            "SELECT outcome FROM trades "
            "WHERE strategy_id = ? AND outcome IN ('win', 'loss') "
            "ORDER BY exit_at DESC LIMIT ?",
            (self.strategy_id, self.window),
        ).fetchall()

        total = len(rows)

        # Not enough completed trades yet — check for staleness instead.
        if total < self.window:
            return self._check_stale(conn)

        wins = sum(1 for r in rows if r["outcome"] == "win")
        win_rate = wins / total

        # Write a performance snapshot.
        self._write_snapshot(conn, win_rate, total)

        if win_rate < self.threshold:
            logger.info(
                "Degradation detected: win_rate=%.2f < threshold=%.2f (%d trades)",
                win_rate, self.threshold, total,
            )
            return True

        return False

    def _check_stale(self, conn) -> bool:
        """
        If no trades have completed recently, the strategy may be dead.
        Check the most recent trade exit_at or strategy created_at.
        """
        now_ms = int(time.time() * 1000)
        stale_ms = self.stale_hours * 3600 * 1000

        # Most recent completed trade exit time.
        row = conn.execute(
            "SELECT MAX(exit_at) as last_exit FROM trades "
            "WHERE strategy_id = ? AND outcome IN ('win', 'loss')",
            (self.strategy_id,),
        ).fetchone()

        last_exit = row["last_exit"] if row and row["last_exit"] else None

        if last_exit is not None:
            if now_ms - last_exit > stale_ms:
                logger.info(
                    "Stale strategy: no completed trade for %dh (strategy %d)",
                    self.stale_hours, self.strategy_id,
                )
                return True
            return False

        # No completed trades at all — check strategy creation time.
        row = conn.execute(
            "SELECT created_at FROM strategies WHERE id = ?",
            (self.strategy_id,),
        ).fetchone()

        if row and row["created_at"]:
            if now_ms - row["created_at"] > stale_ms:
                logger.info(
                    "Stale strategy: no trades since creation %dh ago (strategy %d)",
                    self.stale_hours, self.strategy_id,
                )
                return True

        return False

    def _write_snapshot(self, conn, win_rate: float, total: int) -> None:
        """Write a performance snapshot row."""
        try:
            conn.execute(
                "INSERT INTO performance (strategy_id, timestamp, rolling_win_rate, rolling_trades) "
                "VALUES (?, ?, ?, ?)",
                (self.strategy_id, int(time.time() * 1000), win_rate, total),
            )
            conn.commit()
        except Exception as e:
            logger.debug("Failed to write performance snapshot: %s", e)
