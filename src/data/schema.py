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
                -- status values: active | probation | degraded | archived
                CREATE TABLE IF NOT EXISTS strategies (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                  TEXT,
                    spec                  TEXT    NOT NULL,  -- JSON strategy spec dict
                    performance           TEXT,              -- JSON backtest summary dict
                    degradation_threshold REAL,              -- from walk-forward win rate distribution
                    position_sizing       TEXT,              -- JSON ATR-based sizing params
                    status                TEXT    NOT NULL DEFAULT 'active',
                    probation_wins        INTEGER NOT NULL DEFAULT 0,
                    probation_losses      INTEGER NOT NULL DEFAULT 0,
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
                -- layer values:    shallow | intermediate | deep  (FinMem layered memory)
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category    TEXT    NOT NULL,
                    strategy_id INTEGER,              -- nullable: some findings are not strategy-specific
                    content     TEXT    NOT NULL,
                    created_at  INTEGER NOT NULL,
                    regime      TEXT,                 -- e.g. 'trending_bull', 'sideways'
                    mechanism   TEXT,                 -- e.g. 'mean_reversion', 'momentum'
                    conditions  TEXT,                 -- JSON: e.g. '{"atr": 0.8, "adx": 18}'
                    layer       TEXT    DEFAULT 'shallow',  -- shallow | intermediate | deep
                    importance  INTEGER DEFAULT 50    -- 0-100 FinMem importance score
                );

                CREATE INDEX IF NOT EXISTS idx_kb_category
                    ON knowledge_base(category);
                CREATE INDEX IF NOT EXISTS idx_kb_created
                    ON knowledge_base(created_at DESC);

                -- Strategy evolution tracking across Loop 1 retry attempts (RL layer).
                CREATE TABLE IF NOT EXISTS strategy_evolutions (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_a        INTEGER NOT NULL,
                    attempt_b        INTEGER NOT NULL,
                    strategy_id      INTEGER,
                    spec_delta       TEXT,              -- JSON: what changed in the spec
                    performance_delta TEXT,             -- JSON: {win_rate_change, sharpe_change}
                    outcome          TEXT,              -- improved | degraded | unchanged
                    diagnosis        TEXT,
                    kb_entries_used  TEXT,              -- JSON array of KB entry IDs
                    created_at       INTEGER,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
                );

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
        # Migrate existing knowledge_base: add new columns if missing.
        # Must run BEFORE creating indexes on those columns.
        _migrate_kb_columns(conn)
        # Migrate trades table: add order_id for exchange reconciliation (Module 4).
        _migrate_trades_columns(conn)
        # Migrate strategies table: add probation counters for probationary tier.
        _migrate_strategies_columns(conn)
        # Create indexes on migrated columns (safe now that columns are guaranteed to exist)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_regime ON knowledge_base(regime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_layer  ON knowledge_base(layer)")
        conn.commit()
        logger.info("Database initialised at %s", db_path)
    finally:
        conn.close()


def _migrate_kb_columns(conn: sqlite3.Connection) -> None:
    """
    Add new knowledge_base columns to existing DBs that were created before
    Phase 2. Safe to call on a fresh DB (columns already exist, nothing to do).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_base)")}
    migrations = [
        ("regime",     "ALTER TABLE knowledge_base ADD COLUMN regime TEXT"),
        ("mechanism",  "ALTER TABLE knowledge_base ADD COLUMN mechanism TEXT"),
        ("conditions", "ALTER TABLE knowledge_base ADD COLUMN conditions TEXT"),
        ("layer",      "ALTER TABLE knowledge_base ADD COLUMN layer TEXT DEFAULT 'shallow'"),
        ("importance", "ALTER TABLE knowledge_base ADD COLUMN importance INTEGER DEFAULT 50"),
    ]
    for col_name, sql in migrations:
        if col_name not in existing:
            conn.execute(sql)
            logger.info("Migrated: added knowledge_base.%s", col_name)


def _migrate_trades_columns(conn: sqlite3.Connection) -> None:
    """
    Add order_id column to trades table for exchange-side order tracking
    and startup reconciliation (Module 4).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "order_id" not in existing:
        conn.execute("ALTER TABLE trades ADD COLUMN order_id TEXT")
        logger.info("Migrated: added trades.order_id")


def _migrate_strategies_columns(conn: sqlite3.Connection) -> None:
    """
    Add probation_wins / probation_losses counters to strategies table.
    Used by the probationary tier (auto-promote after N wins,
    auto-demote after N losses).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(strategies)")}
    migrations = [
        ("probation_wins",   "ALTER TABLE strategies ADD COLUMN probation_wins INTEGER NOT NULL DEFAULT 0"),
        ("probation_losses", "ALTER TABLE strategies ADD COLUMN probation_losses INTEGER NOT NULL DEFAULT 0"),
    ]
    for col_name, sql in migrations:
        if col_name not in existing:
            conn.execute(sql)
            logger.info("Migrated: added strategies.%s", col_name)
