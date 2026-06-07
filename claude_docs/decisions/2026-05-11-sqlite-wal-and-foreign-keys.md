# Decision: Enable SQLite WAL mode and foreign_keys pragma at connection time

**Date**: 2026-05-11

## Decision

`get_connection()` sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` on every connection.

## Reason

WAL allows concurrent reads while a write is in progress (matters when a background degradation monitor reads while the main loop writes). `foreign_keys=ON` is required because SQLite does not enforce FKs by default.

## Alternatives Considered

None recorded.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-sqlite-as-system-database]]
