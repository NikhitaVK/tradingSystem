# Decision: Parameterised `?` queries everywhere (no string formatting)

**Date**: 2026-05-11

## Decision

All DB calls use `?` placeholders, never f-strings or `%` formatting.

## Reason

SQL injection prevention plus correct binding of Python types (especially `None` → `NULL`) without manual quoting.

## Alternatives Considered

None recorded.


## Related

- MOC: [[data_pipeline]]
- [[coding_rules]]
- [[2026-05-29-friendly-integrityerror-handling]]
