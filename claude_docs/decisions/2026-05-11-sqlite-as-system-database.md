# Decision: Use SQLite as the system database

**Date**: 2026-05-11

## Decision

Keep the project on SQLite as a single-file database rather than moving to a locally-hosted server (PostgreSQL/MySQL).

## Reason

Zero configuration, the `.db` file is portable, the system is single-writer so SQLite locking is sufficient, and built-in `sqlite3` avoids a network-socket dependency.

## Alternatives Considered

- **Locally-hosted PostgreSQL/MySQL** — rejected: requires a background server process, network sockets, more setup; concurrency benefits unnecessary for this single-process system.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-sqlite-wal-and-foreign-keys]]
