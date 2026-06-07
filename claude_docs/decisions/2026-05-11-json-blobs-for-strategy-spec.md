# Decision: Store strategy specs and performance as JSON blobs in TEXT columns

**Date**: 2026-05-11

## Decision

`strategies.spec`, `strategies.performance`, and `strategies.position_sizing` are stored as JSON-encoded TEXT rather than normalised into child tables.

## Reason

The spec shape is heterogeneous (indicators are a list of variable structures, entry/exit are nested conditions); normalising would create many tiny tables. JSON keeps the spec atomic and matches the agent contract.

## Alternatives Considered

- **Fully normalised schema for indicators / conditions** — rejected: heavy schema migration cost every time the spec evolves.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-sqlite-as-system-database]]
