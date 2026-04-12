"""
schema.py — SQLite table definitions and database initialisation.

Call init_db(db_path) once at startup. All tables use IF NOT EXISTS so
calling it multiple times is safe (idempotent).
"""
import sqlite3
import logging

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to Row for dict-like access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """
    Create all tables if they do not already exist.
    Safe to call multiple times — uses IF NOT EXISTS throughout.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript("""
                -- Historical OHLCV data from BlackBull MT4/MT5 CSV exports.
                -- Volume column is MT4 tick volume (price change count per bar),
                -- not real exchange volume. Acceptable for signal generation.
                CREATE TABLE IF NOT EXISTS ohlcv_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    timeframe   TEXT    NOT NULL,
                    timestamp   INTEGER NOT NULL,  -- Unix milliseconds, UTC
                    open        REAL    NOT NULL,
                    high        REAL    NOT NULL,
                    low         REAL    NOT NULL,
                    close       REAL    NOT NULL,
                    volume      REAL,
                    UNIQUE(symbol, timeframe, timestamp)
                );

                CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_ts
                    ON ohlcv_history(symbol, timeframe, timestamp);

                -- Rolling buffer of recent live candles polled via CCXT.
                CREATE TABLE IF NOT EXISTS live_candles (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    timeframe   TEXT    NOT NULL,
                    timestamp   INTEGER NOT NULL,
                    open        REAL    NOT NULL,
                    high        REAL    NOT NULL,
                    low         REAL    NOT NULL,
                    close       REAL    NOT NULL,
                    volume      REAL,
                    UNIQUE(symbol, timeframe, timestamp)
                );

                CREATE INDEX IF NOT EXISTS idx_live_symbol_tf_ts
                    ON live_candles(symbol, timeframe, timestamp);

                -- Validated strategies with calibrated risk metadata.
                CREATE TABLE IF NOT EXISTS strategies (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                  TEXT,
                    spec                  TEXT    NOT NULL,  -- JSON strategy spec dict
                    performance           TEXT,              -- JSON backtest summary dict
                    degradation_threshold REAL,              -- from walk-forward win rate distribution
                    position_sizing       TEXT,              -- JSON ATR-based sizing params
                    status                TEXT    NOT NULL DEFAULT 'active',
                    created_at            INTEGER NOT NULL
                );

                -- All paper trade records placed via Binance Testnet.
                CREATE TABLE IF NOT EXISTS trades (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id  INTEGER NOT NULL,
                    symbol       TEXT    NOT NULL,
                    side         TEXT    NOT NULL,   -- buy | sell
                    entry_price  REAL,
                    exit_price   REAL,
                    amount_usdt  REAL,
                    pnl_pct      REAL,
                    outcome      TEXT    NOT NULL DEFAULT 'open',  -- open | win | loss
                    entry_at     INTEGER,
                    exit_at      INTEGER,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
                );

                CREATE INDEX IF NOT EXISTS idx_trades_strategy_id
                    ON trades(strategy_id);

                -- Per-strategy rolling performance snapshots written by degradation monitor.
                CREATE TABLE IF NOT EXISTS performance (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id      INTEGER NOT NULL,
                    timestamp        INTEGER NOT NULL,
                    rolling_win_rate REAL,
                    rolling_trades   INTEGER,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
                );

                -- Knowledge base: findings written by agents.
                -- category values: failure_diagnosis | market_regime | parameter_insight | general
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category    TEXT    NOT NULL,
                    strategy_id INTEGER,              -- nullable: some findings are not strategy-specific
                    content     TEXT    NOT NULL,
                    created_at  INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_kb_category
                    ON knowledge_base(category);
                CREATE INDEX IF NOT EXISTS idx_kb_created
                    ON knowledge_base(created_at DESC);

                -- Full agent reasoning traces stored for audit and prompt debugging.
                -- thinking column may be large (~8000 tokens of text).
                -- agent values: strategy_agent | analyst_eval | analyst_reflect | loop1 | loop2
                CREATE TABLE IF NOT EXISTS reasoning_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent       TEXT    NOT NULL,
                    strategy_id INTEGER,
                    thinking    TEXT,
                    response    TEXT,
                    created_at  INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_rlog_strategy
                    ON reasoning_logs(strategy_id);
                CREATE INDEX IF NOT EXISTS idx_rlog_agent
                    ON reasoning_logs(agent);
            """)
        logger.info("Database initialised at %s", db_path)
    finally:
        conn.close()
