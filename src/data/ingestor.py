"""
ingestor.py — Ingest BlackBull MT4/MT5 CSV files into the ohlcv_history table.

BlackBull exports from MT4/MT5 History Center in this format:
    Date,Time,Open,High,Low,Close,Volume
    2025.04.09,14:00,1.08552,1.08585,1.08520,1.08545,852

Date format: YYYY.MM.DD (dots)
Time format: HH:MM
Volume: MT4 tick volume (price changes per bar), not real exchange volume.

Usage:
    python -m src.data.ingestor --csv data/BTCUSD_H1.csv --symbol BTC/USDT --timeframe 1h
"""
import argparse
import logging
import sqlite3
import time
from pathlib import Path

import pandas as pd

from src.data.schema import get_connection, init_db

logger = logging.getLogger(__name__)


def parse_csv(csv_path: str) -> pd.DataFrame:
    """
    Read a BlackBull MT4/MT5 CSV and return a clean DataFrame with columns:
        timestamp (int, Unix ms UTC), open, high, low, close, volume
    Raises ValueError if required columns are missing or parsing fails.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Normalise column names (strip whitespace, lowercase)
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"date", "time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}. "
            f"Expected BlackBull MT4/MT5 format: Date,Time,Open,High,Low,Close,Volume"
        )

    # Merge Date + Time into a single UTC timestamp.
    # Date format: YYYY.MM.DD  Time format: HH:MM
    try:
        df["timestamp"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].astype(str),
            format="%Y.%m.%d %H:%M",
            utc=True,
        )
    except Exception as exc:
        raise ValueError(
            f"Failed to parse Date/Time columns. "
            f"Expected formats: Date='YYYY.MM.DD', Time='HH:MM'. Error: {exc}"
        ) from exc

    # Convert to Unix milliseconds
    df["timestamp"] = df["timestamp"].astype("int64") // 1_000_000

    # Volume is optional (some exports omit it) — process before column selection
    has_volume = "volume" in df.columns
    if has_volume:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("float64")

    # Retain only what we store
    cols = ["timestamp", "open", "high", "low", "close"]
    if has_volume:
        cols.append("volume")
    df = df[cols].copy()

    # Cast OHLC to float
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    # Drop rows with any null in critical columns
    before = len(df)
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
    dropped = before - len(df)
    if dropped:
        logger.warning("Dropped %d rows with null OHLC values", dropped)

    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def ingest_csv(csv_path: str, symbol: str, timeframe: str, db_path: str) -> int:
    """
    Parse a BlackBull CSV and insert rows into ohlcv_history.

    Duplicates are silently skipped via INSERT OR IGNORE (UNIQUE constraint on
    symbol + timeframe + timestamp).

    Returns:
        int: Number of new rows inserted.
    """
    init_db(db_path)
    df = parse_csv(csv_path)

    if df.empty:
        logger.warning("CSV produced no valid rows: %s", csv_path)
        return 0

    has_volume = "volume" in df.columns
    rows = [
        (
            symbol, timeframe,
            row.timestamp, row.open, row.high, row.low, row.close,
            row.volume if has_volume else None,
        )
        for row in df.itertuples(index=False)
    ]

    conn = get_connection(db_path)
    try:
        with conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM ohlcv_history WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()[0]

            conn.executemany(
                """
                INSERT OR IGNORE INTO ohlcv_history
                    (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            count_after = conn.execute(
                "SELECT COUNT(*) FROM ohlcv_history WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()[0]

        inserted = count_after - count_before
    finally:
        conn.close()

    logger.info(
        "Ingested %s | %s: %d new rows inserted (%d total in CSV)",
        symbol, timeframe, inserted, len(df)
    )
    return inserted


def load_ohlcv(
    symbol: str,
    timeframe: str,
    db_path: str,
    limit: int = None,
) -> pd.DataFrame:
    """
    Load historical OHLCV data for a symbol/timeframe from ohlcv_history.

    Returns a DataFrame with columns: timestamp, open, high, low, close, volume
    Ordered by timestamp ascending.
    """
    conn = get_connection(db_path)
    try:
        if limit:
            # Subquery selects the MOST RECENT `limit` rows (DESC), outer query
            # re-sorts ascending so callers always receive chronological order.
            query = """
                SELECT timestamp, open, high, low, close, volume FROM (
                    SELECT timestamp, open, high, low, close, volume
                    FROM ohlcv_history
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ) ORDER BY timestamp ASC
            """
            params = [symbol, timeframe, limit]
        else:
            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM ohlcv_history
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp ASC
            """
            params = [symbol, timeframe]

        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    return df


# ── CLI entry point ──────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Ingest a BlackBull MT4/MT5 CSV into the trading system database."
    )
    parser.add_argument("--csv", required=True, help="Path to the CSV file")
    parser.add_argument("--symbol", required=True, help="Trading symbol, e.g. BTC/USDT")
    parser.add_argument("--timeframe", required=True, help="Timeframe, e.g. 1h")
    parser.add_argument("--db", default="./trading_system.db", help="Path to SQLite database")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    inserted = ingest_csv(args.csv, args.symbol, args.timeframe, args.db)
    print(f"Done. {inserted} new rows inserted.")


if __name__ == "__main__":
    _cli()
