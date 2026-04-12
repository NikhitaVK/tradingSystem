"""
ccxt_feed.py — Poll live OHLCV data from Binance Testnet via CCXT.

Runs as a background thread. Writes new candles to live_candles table.
Exposes get_latest_candles() for signal detection in Loop 2.

Setup:
    feed = CCXTFeed(symbol="BTC/USDT", timeframe="1h", db_path="./trading_system.db")
    feed.start_polling()
    ...
    candles = feed.get_latest_candles(n=50)
    feed.stop()
"""
import logging
import threading
import time

import ccxt
import pandas as pd

from config.settings import (
    BINANCE_TESTNET_API_KEY,
    BINANCE_TESTNET_SECRET,
    CCXT_POLL_INTERVAL_SECONDS,
    LIVE_CANDLES_BUFFER,
)
from src.data.schema import get_connection, init_db

logger = logging.getLogger(__name__)


def _build_exchange() -> ccxt.Exchange:
    """
    Instantiate and return a Binance exchange object pointed at the Testnet.
    set_sandbox_mode(True) must be called immediately after init — this is enforced here.
    """
    exchange = ccxt.binance(
        {
            "apiKey": BINANCE_TESTNET_API_KEY,
            "secret": BINANCE_TESTNET_SECRET,
            "enableRateLimit": True,
        }
    )
    exchange.set_sandbox_mode(True)
    return exchange


class CCXTFeed:
    """
    Polls OHLCV candles from Binance Testnet on a configurable interval and
    stores them in the live_candles SQLite table.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        db_path: str,
        poll_interval: int = CCXT_POLL_INTERVAL_SECONDS,
        buffer_size: int = LIVE_CANDLES_BUFFER,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.buffer_size = buffer_size

        self._exchange = _build_exchange()
        self._stop_event = threading.Event()
        self._thread: threading.Thread = None

        init_db(db_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def start_polling(self) -> None:
        """Start the background polling thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("CCXTFeed already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="CCXTFeed"
        )
        self._thread.start()
        logger.info("CCXTFeed started: %s %s", self.symbol, self.timeframe)

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("CCXTFeed stopped")

    def get_latest_candles(self, n: int = 50) -> pd.DataFrame:
        """
        Return the most recent N candles from live_candles for this feed's
        symbol and timeframe. Ordered ascending by timestamp.

        Returns an empty DataFrame if no candles have been collected yet.
        """
        conn = get_connection(self.db_path)
        try:
            df = pd.read_sql_query(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM live_candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                conn,
                params=(self.symbol, self.timeframe, n),
            )
        finally:
            conn.close()

        return df.sort_values("timestamp").reset_index(drop=True)

    def fetch_once(self) -> int:
        """
        Fetch the latest candles from CCXT and write new ones to the DB.
        Returns the number of new rows inserted.
        Useful for testing without running the background thread.
        """
        return self._fetch_and_store()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                new_rows = self._fetch_and_store()
                if new_rows:
                    logger.debug("CCXTFeed: %d new candle(s) for %s %s",
                                 new_rows, self.symbol, self.timeframe)
                self._trim_buffer()
                consecutive_errors = 0
            except ccxt.NetworkError as exc:
                consecutive_errors += 1
                logger.warning("CCXTFeed network error (%d consecutive, will retry): %s",
                               consecutive_errors, exc)
                if consecutive_errors >= 3:
                    logger.warning("CCXTFeed: recreating exchange after %d consecutive errors",
                                   consecutive_errors)
                    self._exchange = _build_exchange()
                    consecutive_errors = 0
            except ccxt.ExchangeError as exc:
                logger.error("CCXTFeed exchange error: %s", exc)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("CCXTFeed unexpected error: %s", exc)

            self._stop_event.wait(self.poll_interval)

    def _fetch_and_store(self) -> int:
        """
        Fetch only candles newer than the last stored timestamp using CCXT's
        `since` parameter, then insert them. Returns number of new rows inserted.
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM live_candles WHERE symbol = ? AND timeframe = ?",
                (self.symbol, self.timeframe),
            ).fetchone()
            last_ts = row[0] if row and row[0] is not None else 0
        finally:
            conn.close()

        # Fetch candles strictly after last_ts. limit=10 is sufficient for any
        # timeframe — we only need the handful of candles that closed since the
        # last poll. Falls back to 100 on first run (last_ts == 0).
        fetch_limit = 100 if last_ts == 0 else 10
        raw = self._exchange.fetch_ohlcv(
            self.symbol, self.timeframe, since=last_ts or None, limit=fetch_limit
        )
        # raw format: [[timestamp_ms, open, high, low, close, volume], ...]

        if not raw:
            return 0

        rows = [
            (self.symbol, self.timeframe, ts, o, h, l, c, v)
            for ts, o, h, l, c, v in raw
        ]

        conn = get_connection(self.db_path)
        try:
            with conn:
                count_before = conn.execute(
                    "SELECT COUNT(*) FROM live_candles WHERE symbol = ? AND timeframe = ?",
                    (self.symbol, self.timeframe),
                ).fetchone()[0]

                conn.executemany(
                    """
                    INSERT OR IGNORE INTO live_candles
                        (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

                count_after = conn.execute(
                    "SELECT COUNT(*) FROM live_candles WHERE symbol = ? AND timeframe = ?",
                    (self.symbol, self.timeframe),
                ).fetchone()[0]
        finally:
            conn.close()

        return count_after - count_before

    def _trim_buffer(self) -> None:
        """
        Keep only the most recent buffer_size candles in live_candles for this
        symbol/timeframe. Prevents unbounded table growth.
        """
        conn = get_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    DELETE FROM live_candles
                    WHERE symbol = ? AND timeframe = ? AND id NOT IN (
                        SELECT id FROM live_candles
                        WHERE symbol = ? AND timeframe = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                    """,
                    (self.symbol, self.timeframe,
                     self.symbol, self.timeframe, self.buffer_size),
                )
        finally:
            conn.close()
