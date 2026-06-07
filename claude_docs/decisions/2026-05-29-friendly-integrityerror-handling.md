# Decision: Catch `sqlite3.IntegrityError` with friendly messages instead of letting it bubble

**Date**: 2026-05-29

## Decision

Wrap repository writes in try/except around `IntegrityError` to convert FK/UNIQUE violations into readable errors.

## Reason

AS91906 Excellence requires "responds to any input without crashing." Raw SQLite errors are not user-meaningful.

## Alternatives Considered

None recorded.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-parameterised-queries-only]]
