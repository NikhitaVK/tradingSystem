# Decision: Separate `ohlcv_history` and `live_candles` tables

**Date**: 2026-05-11

## Decision

Keep historical CSV data and live CCXT data in two separate tables with identical schema rather than merging them.

## Reason

Historical data is stable and trusted; live data is constantly being refreshed in a rolling buffer. Mixing the two would complicate queries that need to distinguish "research" from "production" candles.

## Alternatives Considered

- **Single unified candles table with a `source` column** — rejected: complicates every read query and risks accidentally backtesting on partial live candles.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-unix-milliseconds-timestamps]]
