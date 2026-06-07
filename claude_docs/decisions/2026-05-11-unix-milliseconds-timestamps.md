# Decision: Use Unix milliseconds (not seconds) for all timestamps

**Date**: 2026-05-11

## Decision

All `timestamp` columns are Unix milliseconds UTC across `ohlcv_history`, `live_candles`, etc.

## Reason

CCXT returns OHLCV timestamps in milliseconds — matching this avoids per-read conversion and a class of "off-by-1000" bugs at the seam between Module 1 and Module 2.

## Alternatives Considered

- **Seconds** — rejected: would force conversions at every CCXT boundary.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-separate-ohlcv-history-and-live-tables]]
