# Decision: Use `INSERT OR IGNORE` plus a UNIQUE constraint for de-duplication

**Date**: 2026-05-11

## Decision

`ingest_csv()` uses `INSERT OR IGNORE` against a `UNIQUE(symbol, timeframe, timestamp)` constraint instead of checking for existing rows first.

## Reason

Single round-trip to SQLite per row, atomically idempotent. Allows re-ingesting the same CSV without producing duplicates and without needing an application-level check.

## Alternatives Considered

- **Pre-query for existing timestamps before inserting** — rejected: doubles the I/O and races with concurrent writers.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-separate-ohlcv-history-and-live-tables]]
