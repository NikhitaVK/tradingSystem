# Module 1 — Data Pipeline

**Status**: Complete  
**Isolation test**: `tests/test_data_pipeline.py` — 6/6 passing  
**Depends on**: Nothing (foundation module)

## Purpose

Parse BlackBull MT4/MT5 CSV exports into SQLite, poll live OHLCV via CCXT, provide a knowledge base CRUD layer used by all agents, and own all SQLite table definitions.

## Responsibilities

- Ingest BlackBull CSV files into `ohlcv_history`
- Poll live Binance Testnet candles into `live_candles`
- CRUD operations on the knowledge base
- Own all SQLite schema definitions and initialisation

## Inputs / Outputs

| Input | Output |
|---|---|
| BlackBull CSV file path + symbol + timeframe | Rows in `ohlcv_history` |
| CCXT Binance Testnet connection | `live_candles` refreshed on interval |
| `init_db()` call | All tables created (idempotent) |
| `write_finding()` call | Row in `knowledge_base` |
| `query_relevant()` call | Matching KB rows ordered by recency |

## Key Files

### `src/data/schema.py`
- `init_db(db_path)` — creates all tables with `IF NOT EXISTS`
- `get_connection(db_path)` — returns sqlite3 connection with Row factory + WAL mode + foreign keys
- Called **once** at startup in `main.py` — nowhere else

### `src/data/ingestor.py`
- `ingest_csv(csv_path, symbol, timeframe, db_path) -> int`
- BlackBull format: separate `Date` and `Time` columns (dot-separated date `YYYY.MM.DD`, colon time `HH:MM`)
- Merges into UTC timestamp (Unix milliseconds)
- Duplicate rows silently skipped via `UNIQUE(symbol, timeframe, timestamp)` constraint

### `src/data/ccxt_feed.py`
- `CCXTFeed(symbol, timeframe, db_path)` — uses CCXT Binance sandbox mode
- `start_polling(interval_seconds=60)` — starts polling thread
- `get_latest_candles(n=50) -> pd.DataFrame`
- `stop()` — gracefully stops polling

### `src/data/knowledge_base.py`
- `write_finding(category, content, db_path, strategy_id=None) -> int`
- `query_relevant(keywords, db_path, limit=10, category=None) -> list[dict]`

## SQLite Schema

Six tables: `ohlcv_history`, `live_candles`, `strategies`, `trades`, `performance`, `knowledge_base`, `reasoning_logs`.

Key conventions:
- All timestamps are Unix milliseconds UTC
- `volume` in `ohlcv_history` is **MT4 tick volume** (price changes per bar), not real exchange volume
- `knowledge_base.category` values: `failure_diagnosis | market_regime | parameter_insight | general`

## Known Issues

- None reported

## Planned Improvements

- KB schema will be extended with `regime`, `mechanism`, `conditions`, `layer`, `importance` columns (Phase 2 of PLANNED_IMPROVEMENTS.md)
- `query_relevant()` will get regime-filtered and importance-scored variants
- See `.claude/PLANNED_IMPROVEMENTS.md` for the full roadmap


## Related

- MOC: [[_modules]]
- [[2026-05-11-sqlite-as-system-database]]
- [[2026-05-11-sqlite-wal-and-foreign-keys]]
- [[2026-05-11-separate-ohlcv-history-and-live-tables]]
- [[2026-05-11-unix-milliseconds-timestamps]]
- [[2026-05-11-json-blobs-for-strategy-spec]]
- [[2026-05-11-insert-or-ignore-dedup]]
- [[2026-05-11-parameterised-queries-only]]
- [[2026-05-29-repository-pattern-refactor]]
