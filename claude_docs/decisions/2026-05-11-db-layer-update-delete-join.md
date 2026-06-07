# Decision: Add UPDATE, DELETE, COUNT/GROUP BY, and JOIN operations to the DB layer

**Date**: 2026-05-11

## Decision

Extend `knowledge_base.py` with `update_finding()`, `delete_finding()`, `count_by_category()`, and create a new `src/data/strategies.py` with a JOIN query between `strategies` and `trades`.

## Reason

Pathway C requires demonstrated INSERT/UPDATE/DELETE behaviour, JOIN correctness, and aggregate calculations. The existing module only does INSERT + SELECT.

## Alternatives Considered

None recorded.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-as91906-db-layer-scope]]
- [[2026-05-29-repository-pattern-refactor]]
